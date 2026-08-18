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

        cutoff = (now - target.min_age).timestamp()
        candidates = _collect_candidates(root, cutoff=cutoff)
        candidate_files += len(candidates)
        candidate_bytes += sum(candidate.size for candidate in candidates)

        if dry_run:
            continue

        target_deleted_files = 0
        target_deleted_bytes = 0
        for candidate in candidates:
            if target_deleted_files >= target.max_delete_files:
                break
            if target_deleted_bytes + candidate.size > target.max_delete_bytes:
                # Oldest-first means we do not skip an oversized older file to
                # remove younger data behind it.
                break

            size, delete_error = _safe_unlink(candidate.path, root=root)
            if delete_error is not None:
                errors.append(delete_error)
                continue
            assert size is not None

            # Re-check the byte budget against the current file size in case it
            # grew after discovery. Never exceed the configured hard cap.
            if target_deleted_bytes + size > target.max_delete_bytes:
                errors.append(
                    f"candidate changed size beyond deletion budget: {candidate.path}"
                )
                continue

            target_deleted_files += 1
            target_deleted_bytes += size

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
                _Candidate(path=path, mtime=info.st_mtime, size=info.st_size)
            )

    candidates.sort(key=lambda item: (item.mtime, str(item.path)))
    return tuple(candidates)


def _safe_unlink(path: Path, *, root: Path) -> tuple[int | None, str | None]:
    try:
        info = path.lstat()
    except OSError as exc:
        return None, f"retention candidate disappeared: {path}: {type(exc).__name__}"
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None, f"retention candidate is no longer a regular file: {path}"

    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        return None, f"retention candidate cannot be resolved: {path}: {type(exc).__name__}"
    if not resolved.is_relative_to(root) or resolved != path:
        return None, f"retention candidate escaped marked root: {path}"

    try:
        path.unlink()
    except OSError as exc:
        return None, f"retention delete failed: {path}: {type(exc).__name__}"
    return info.st_size, None


__all__ = [
    "RETENTION_MARKER",
    "RETENTION_MARKER_CONTENT",
    "RetentionResult",
    "RetentionStatus",
    "RetentionTarget",
    "run_safe_retention",
]
