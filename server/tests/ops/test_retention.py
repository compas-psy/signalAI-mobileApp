from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from app.ops.pressure import PressureAssessment, PressureState
from app.ops.retention import (
    RETENTION_MARKER,
    RETENTION_MARKER_CONTENT,
    RetentionStatus,
    RetentionTarget,
    run_safe_retention,
)


NOW = datetime(2026, 8, 18, 17, 30, tzinfo=UTC)


def _assessment(*reasons: str, state: PressureState = PressureState.PRESSURE):
    return PressureAssessment(
        state=state,
        score=5,
        reasons=tuple(reasons),
        active_dimensions=2,
    )


def _mark(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / RETENTION_MARKER).write_text(RETENTION_MARKER_CONTENT, encoding="utf-8")


def _write_at(path: Path, *, age: timedelta, size: int = 8) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    timestamp = (NOW - age).timestamp()
    os.utime(path, (timestamp, timestamp))


def test_non_disk_pressure_never_deletes_files(tmp_path):
    root = tmp_path / "cache"
    _mark(root)
    old = root / "old.cache"
    _write_at(old, age=timedelta(days=10))

    result = run_safe_retention(
        assessment=_assessment("memory_headroom_pressure"),
        targets=(RetentionTarget(root=root),),
        now=NOW,
    )

    assert result.status is RetentionStatus.NOT_REQUIRED
    assert old.exists()
    assert result.deleted_files == 0


def test_disk_pressure_deletes_only_old_regular_files_in_marked_root(tmp_path):
    root = tmp_path / "cache"
    _mark(root)
    old = root / "old.cache"
    nested_old = root / "nested" / "old.log"
    fresh = root / "fresh.cache"
    _write_at(old, age=timedelta(days=8), size=11)
    _write_at(nested_old, age=timedelta(days=9), size=13)
    _write_at(fresh, age=timedelta(hours=2), size=17)

    result = run_safe_retention(
        assessment=_assessment("disk_headroom_pressure"),
        targets=(RetentionTarget(root=root, min_age=timedelta(days=7)),),
        now=NOW,
    )

    assert result.status is RetentionStatus.CLEANED
    assert result.deleted_files == 2
    assert result.deleted_bytes == 24
    assert not old.exists()
    assert not nested_old.exists()
    assert fresh.exists()
    assert (root / RETENTION_MARKER).exists()
    assert (root / "nested").is_dir()


def test_unmarked_root_is_rejected_without_deleting_anything(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    old = root / "old.cache"
    _write_at(old, age=timedelta(days=10))

    result = run_safe_retention(
        assessment=_assessment("disk_headroom_critical", state=PressureState.CRITICAL),
        targets=(RetentionTarget(root=root),),
        now=NOW,
    )

    assert result.status is RetentionStatus.FAILED
    assert old.exists()
    assert result.deleted_files == 0
    assert any("marker" in error.lower() for error in result.errors)


def test_symlink_root_and_symlink_files_are_never_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "do-not-delete.log"
    _write_at(outside_file, age=timedelta(days=30))

    real_root = tmp_path / "real-cache"
    _mark(real_root)
    link_root = tmp_path / "cache-link"
    link_root.symlink_to(real_root, target_is_directory=True)
    linked_file = real_root / "external.log"
    linked_file.symlink_to(outside_file)

    rejected = run_safe_retention(
        assessment=_assessment("disk_headroom_pressure"),
        targets=(RetentionTarget(root=link_root),),
        now=NOW,
    )
    safe = run_safe_retention(
        assessment=_assessment("disk_headroom_pressure"),
        targets=(RetentionTarget(root=real_root),),
        now=NOW,
    )

    assert rejected.status is RetentionStatus.FAILED
    assert safe.status in {RetentionStatus.CLEANED, RetentionStatus.NO_CANDIDATES}
    assert outside_file.exists()
    assert linked_file.is_symlink()


def test_delete_budgets_are_hard_caps_and_oldest_files_go_first(tmp_path):
    root = tmp_path / "cache"
    _mark(root)
    oldest = root / "oldest"
    middle = root / "middle"
    newest = root / "newest"
    _write_at(oldest, age=timedelta(days=12), size=10)
    _write_at(middle, age=timedelta(days=11), size=10)
    _write_at(newest, age=timedelta(days=10), size=10)

    result = run_safe_retention(
        assessment=_assessment("disk_headroom_pressure"),
        targets=(
            RetentionTarget(
                root=root,
                min_age=timedelta(days=7),
                max_delete_files=2,
                max_delete_bytes=20,
            ),
        ),
        now=NOW,
    )

    assert result.deleted_files == 2
    assert result.deleted_bytes == 20
    assert not oldest.exists()
    assert not middle.exists()
    assert newest.exists()


def test_dry_run_reports_candidates_without_mutation(tmp_path):
    root = tmp_path / "cache"
    _mark(root)
    old = root / "old.cache"
    _write_at(old, age=timedelta(days=10), size=21)

    result = run_safe_retention(
        assessment=_assessment("disk_headroom_pressure"),
        targets=(RetentionTarget(root=root),),
        now=NOW,
        dry_run=True,
    )

    assert result.status is RetentionStatus.DRY_RUN
    assert result.deleted_files == 0
    assert result.deleted_bytes == 0
    assert result.candidate_files == 1
    assert result.candidate_bytes == 21
    assert old.exists()


def test_target_rejects_dangerous_root_and_invalid_budgets(tmp_path):
    try:
        RetentionTarget(root=Path("/"))
    except ValueError as exc:
        assert "root" in str(exc)
    else:
        raise AssertionError("filesystem root must be rejected")

    try:
        RetentionTarget(root=tmp_path / "cache", max_delete_files=0)
    except ValueError as exc:
        assert "max_delete_files" in str(exc)
    else:
        raise AssertionError("zero delete-file budget must be rejected")


def test_compose_automatically_caps_all_long_running_container_logs():
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    for service_name in ("postgres", "redis", "api", "ollama", "scheduler", "execution"):
        logging = compose["services"][service_name]["logging"]
        assert logging["driver"] == "json-file"
        assert logging["options"]["max-size"] == "10m"
        assert logging["options"]["max-file"] == "5"
