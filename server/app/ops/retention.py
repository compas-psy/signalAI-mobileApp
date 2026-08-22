"""Bounded, allowlisted disk retention for Resource Autopilot.

Deletion is deliberately difficult to authorize. A target must be an absolute,
non-symlink directory carrying the exact SignalAI retention marker. Only old
regular files inside that root are candidates; symlinks, directories, named
volumes, databases, configs and arbitrary paths are outside this adapter's
capability by construction.

The adapter is still not wired into the scheduler. SAI-021 will decide when to
call it and how to persist/alert on the remediation result.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from .pressure import PressureAssessment


RETENTION_MARKER = ".signalai-retention-allow"
RETENTION_MARKER_CONTENT = "signalai-retention-v1\n"
_DISK_PRESSURE_REASONS = frozenset(
    {"disk_headroom_pressure", "disk_headroom_critical"}
)
_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd
_UNLINK_SUPPORTS_DIR_FD = os.unlink in os.supports_dir_fd


class RetentionStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    NO_CANDIDATES = "NO_CANDIDATES"
    DRY_RUN = "DRY_RUN"
    CLEANED = "CLEANED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class RetentionTarget:
    root: Path
    min_age: timedelta = timedelta(days=7)
    max_delete_files: int = 100
    max_delete_bytes: int = 256 * 1024 * 1024

    def __post_init__(self) -> None:
        root = Path(self.root)
        object.__setattr__(self, "root", root)
        if root == Path(root.anchor or "/"):
            raise ValueError("retention root must not be the filesystem root")
        if self.min_age < timedelta(0):
            raise ValueError("min_age must be non-negative")
        if self.max_delete_files <= 0:
            raise ValueError("max_delete_files must be positive")
        if self.max_delete_bytes <= 0:
            raise ValueError("max_delete_bytes must be positive")


@dataclass(frozen=True, slots=True)
class RetentionAutopilotConfig:
    """Owner-authorized scheduler input; disabled and non-mutating by default."""

    enabled: bool = False
    dry_run: bool = True
    targets: tuple[RetentionTarget, ...] = ()
    budget_period: timedelta = timedelta(days=1)

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool) or not isinstance(self.dry_run, bool):
            raise ValueError("retention enabled and dry_run must be bool")
        if not isinstance(self.budget_period, timedelta) or not (
            timedelta(hours=1) <= self.budget_period <= timedelta(days=7)
        ):
            raise ValueError("retention budget_period must be between 1 hour and 7 days")
        if self.enabled and not self.targets:
            raise ValueError("enabled retention requires approved targets")
        roots: set[Path] = set()
        for target in self.targets:
            if not isinstance(target, RetentionTarget):
                raise ValueError("retention targets must be RetentionTarget")
            if not target.root.is_absolute():
                raise ValueError("retention target root must be absolute")
            if target.root in roots:
                raise ValueError("retention target roots must be unique")
            roots.add(target.root)

    @classmethod
    def from_mapping(cls, raw: object) -> "RetentionAutopilotConfig":
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError("retention config must be a mapping")
        allowed = {"enabled", "dry_run", "targets", "budget_period_hours"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"unknown retention config fields: {sorted(unknown)}")
        enabled = raw.get("enabled", False)
        dry_run = raw.get("dry_run", True)
        rows = raw.get("targets", [])
        if not isinstance(rows, list):
            raise ValueError("retention targets must be a list")
        targets: list[RetentionTarget] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("retention target must be a mapping")
            root = row.get("root")
            if not isinstance(root, str) or not root.strip():
                raise ValueError("retention target root must be a non-blank string")
            age_hours = row.get("min_age_hours", 24 * 7)
            if isinstance(age_hours, bool) or not isinstance(age_hours, int):
                raise ValueError("retention min_age_hours must be an integer")
            targets.append(
                RetentionTarget(
                    root=Path(root),
                    min_age=timedelta(hours=age_hours),
                    max_delete_files=_positive_int(row, "max_delete_files", 100),
                    max_delete_bytes=_positive_int(
                        row, "max_delete_bytes", 256 * 1024 * 1024
                    ),
                )
            )
        budget_period_hours = raw.get("budget_period_hours", 24)
        if (
            isinstance(budget_period_hours, bool)
            or not isinstance(budget_period_hours, int)
            or not 1 <= budget_period_hours <= 24 * 7
        ):
            raise ValueError("retention budget_period_hours must be between 1 and 168")
        return cls(
            enabled=enabled,
            dry_run=dry_run,
            targets=tuple(targets),
            budget_period=timedelta(hours=budget_period_hours),
        )


@dataclass(frozen=True, slots=True)
class RetentionResult:
    status: RetentionStatus
    candidate_files: int = 0
    candidate_bytes: int = 0
    deleted_files: int = 0
    deleted_bytes: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Candidate:
    path: Path
    mtime: float
    size: int
    device: int
    inode: int


def _positive_int(row: dict, key: str, default: int) -> int:
    value = row.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"retention {key} must be a positive integer")
    return value


def run_safe_retention(
    *,
    assessment: PressureAssessment,
    targets: tuple[RetentionTarget, ...] | list[RetentionTarget],
    now: datetime,
    dry_run: bool = False,
) -> RetentionResult:
    """Delete bounded old files only when disk pressure is actually observed.

    A memory/CPU/queue pressure state alone never authorizes filesystem
    mutation. Errors fail closed for cleanup and are returned as structured
    evidence rather than raised into the caller.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    if not _has_disk_pressure(assessment):
        return RetentionResult(status=RetentionStatus.NOT_REQUIRED)

    candidate_files = 0
    candidate_bytes = 0
    deleted_files = 0
    deleted_bytes = 0
    errors: list[str] = []

    for target in tuple(targets):
        root, root_error = _validated_root(target.root)
        if root_error is not None:
            errors.append(root_error)
            continue
        assert root is not None

        if dry_run:
            cutoff = (now - target.min_age).timestamp()
            candidates = _collect_candidates(root, cutoff=cutoff)
            candidate_files += len(candidates)
            candidate_bytes += sum(candidate.size for candidate in candidates)
            continue

        root_fd, root_fd_error = _open_root_dir_fd(root)
        if root_fd_error is not None:
            errors.append(root_fd_error)
            continue
        assert root_fd is not None
        try:
            cutoff = (now - target.min_age).timestamp()
            candidates = _collect_candidates(root, cutoff=cutoff)
            candidate_files += len(candidates)
            candidate_bytes += sum(candidate.size for candidate in candidates)

            target_deleted_files = 0
            target_deleted_bytes = 0
            for candidate in candidates:
                if target_deleted_files >= target.max_delete_files:
                    break
                remaining_bytes = target.max_delete_bytes - target_deleted_bytes
                if remaining_bytes <= 0 or candidate.size > remaining_bytes:
                    # Oldest-first means we do not skip an oversized older file to
                    # remove younger data behind it.
                    break

                size, delete_error = _safe_unlink(
                    candidate,
                    root=root,
                    root_fd=root_fd,
                    max_bytes=remaining_bytes,
                )
                if delete_error is not None:
                    errors.append(delete_error)
                    continue
                if size is None:
                    # The file grew after discovery and no longer fits. It was not
                    # unlinked; stop to preserve both the budget and oldest-first
                    # ordering.
                    break

                target_deleted_files += 1
                target_deleted_bytes += size
        finally:
            os.close(root_fd)

        deleted_files += target_deleted_files
        deleted_bytes += target_deleted_bytes

    if errors:
        return RetentionResult(
            status=RetentionStatus.FAILED,
            candidate_files=candidate_files,
            candidate_bytes=candidate_bytes,
            deleted_files=deleted_files,
            deleted_bytes=deleted_bytes,
            errors=tuple(errors),
        )
    if dry_run and candidate_files > 0:
        return RetentionResult(
            status=RetentionStatus.DRY_RUN,
            candidate_files=candidate_files,
            candidate_bytes=candidate_bytes,
        )
    if deleted_files > 0:
        return RetentionResult(
            status=RetentionStatus.CLEANED,
            candidate_files=candidate_files,
            candidate_bytes=candidate_bytes,
            deleted_files=deleted_files,
            deleted_bytes=deleted_bytes,
        )
    return RetentionResult(
        status=RetentionStatus.NO_CANDIDATES,
        candidate_files=candidate_files,
        candidate_bytes=candidate_bytes,
    )


def _has_disk_pressure(assessment: PressureAssessment) -> bool:
    return any(reason in _DISK_PRESSURE_REASONS for reason in assessment.reasons)


def _validated_root(root: Path) -> tuple[Path | None, str | None]:
    root = Path(root)
    if not root.is_absolute():
        return None, f"retention root must be absolute: {root}"
    if root == Path(root.anchor):
        return None, "retention root must not be the filesystem root"
    if root.is_symlink():
        return None, f"retention root must not be a symlink: {root}"
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        return None, f"retention root unavailable: {root}: {type(exc).__name__}"
    if resolved != root:
        return None, f"retention root contains symlink or non-canonical path: {root}"
    if not root.is_dir():
        return None, f"retention root is not a directory: {root}"

    marker = root / RETENTION_MARKER
    if marker.is_symlink() or not marker.is_file():
        return None, f"retention marker missing for root: {root}"
    try:
        content = marker.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"retention marker unreadable for root: {root}: {type(exc).__name__}"
    if content != RETENTION_MARKER_CONTENT:
        return None, f"retention marker invalid for root: {root}"
    return root, None


def _collect_candidates(root: Path, *, cutoff: float) -> tuple[_Candidate, ...]:
    candidates: list[_Candidate] = []
    for current_raw, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_raw)
        # os.walk does not follow directory symlinks with followlinks=False,
        # but prune them explicitly so they never enter the candidate tree.
        dirs[:] = [name for name in dirs if not (current / name).is_symlink()]

        for name in files:
            path = current / name
            if path == root / RETENTION_MARKER:
                continue
            try:
                info = path.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                continue
            if info.st_mtime > cutoff:
                continue
            candidates.append(
                _Candidate(
                    path=path,
                    mtime=info.st_mtime,
                    size=info.st_size,
                    device=info.st_dev,
                    inode=info.st_ino,
                )
            )

    candidates.sort(key=lambda item: (item.mtime, str(item.path)))
    return tuple(candidates)


def _safe_unlink(
    candidate: _Candidate,
    *,
    root: Path,
    root_fd: int,
    max_bytes: int,
) -> tuple[int | None, str | None]:
    path = candidate.path
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None, f"retention candidate escaped marked root: {path}"
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        return None, f"retention candidate path is invalid: {path}"

    parent_fd, parent_error = _open_parent_dir_fd(root_fd, relative.parts[:-1])
    if parent_error is not None:
        return None, f"retention candidate parent unavailable: {path}: {parent_error}"
    assert parent_fd is not None
    try:
        return _unlink_from_parent_fd(
            parent_fd,
            relative.name,
            candidate=candidate,
            max_bytes=max_bytes,
        )
    finally:
        os.close(parent_fd)


def _open_root_dir_fd(root: Path) -> tuple[int | None, str | None]:
    flags, error = _directory_open_flags()
    if error is not None:
        return None, error
    assert flags is not None

    fd: int | None = None
    try:
        fd = os.open(root.anchor, flags)
        for component in root.parts[1:]:
            child_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child_fd
        return fd, None
    except OSError as exc:
        if fd is not None:
            os.close(fd)
        return None, f"secure retention root open failed: {type(exc).__name__}"


def _open_parent_dir_fd(
    root_fd: int,
    ancestors: tuple[str, ...],
) -> tuple[int | None, str | None]:
    flags, error = _directory_open_flags()
    if error is not None:
        return None, error
    assert flags is not None

    fd = os.dup(root_fd)
    try:
        for component in ancestors:
            child_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child_fd
        return fd, None
    except OSError as exc:
        os.close(fd)
        return None, type(exc).__name__


def _directory_open_flags() -> tuple[int | None, str | None]:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        return None, "secure retention dirfd support is unavailable"
    if not _OPEN_SUPPORTS_DIR_FD:
        return None, "secure retention dirfd open is unavailable"
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags, None


def _unlink_from_parent_fd(
    parent_fd: int,
    name: str,
    *,
    candidate: _Candidate,
    max_bytes: int,
) -> tuple[int | None, str | None]:
    if not _STAT_SUPPORTS_DIR_FD or not _UNLINK_SUPPORTS_DIR_FD:
        return None, "secure retention unlink support is unavailable"
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_PATH"):
        return None, "secure retention file descriptor support is unavailable"

    try:
        lstat_info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        return None, (
            f"retention candidate disappeared: {candidate.path}: {type(exc).__name__}"
        )
    if stat.S_ISLNK(lstat_info.st_mode) or not stat.S_ISREG(lstat_info.st_mode):
        return None, f"retention candidate changed type before delete: {candidate.path}"
    if lstat_info.st_size > max_bytes:
        return None, None
    if (lstat_info.st_dev, lstat_info.st_ino) != (candidate.device, candidate.inode):
        return None, f"retention candidate changed inode before delete: {candidate.path}"

    file_flags = os.O_PATH | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        file_flags |= os.O_CLOEXEC
    try:
        file_fd = os.open(name, file_flags, dir_fd=parent_fd)
    except OSError as exc:
        return None, (
            "retention candidate unavailable before delete: "
            f"{candidate.path}: {type(exc).__name__}"
        )
    try:
        fstat_info = os.fstat(file_fd)
    finally:
        os.close(file_fd)
    if stat.S_ISLNK(fstat_info.st_mode) or not stat.S_ISREG(fstat_info.st_mode):
        return None, f"retention candidate changed type before delete: {candidate.path}"
    if fstat_info.st_size > max_bytes:
        return None, None
    if (fstat_info.st_dev, fstat_info.st_ino) != (candidate.device, candidate.inode):
        return None, f"retention candidate changed inode before delete: {candidate.path}"

    try:
        os.unlink(name, dir_fd=parent_fd)
    except OSError as exc:
        return None, f"retention delete failed: {candidate.path}: {type(exc).__name__}"
    return fstat_info.st_size, None


__all__ = [
    "RETENTION_MARKER",
    "RETENTION_MARKER_CONTENT",
    "RetentionResult",
    "RetentionAutopilotConfig",
    "RetentionStatus",
    "RetentionTarget",
    "run_safe_retention",
]
