from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import RetentionAttemptIntent, RetentionAttemptOutcome
from app.ops.pressure import PressureAssessment, PressureState
from app.ops.retention import (
    RETENTION_MARKER,
    RETENTION_MARKER_CONTENT,
    RetentionStatus,
    RetentionTarget,
)
from app.ops.retention_attempts import (
    RetentionAttemptStatus,
    derive_retention_attempt_id,
    execute_retention_attempt,
    retention_attempt_metadata,
)


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _disk_pressure() -> PressureAssessment:
    return PressureAssessment(
        state=PressureState.PRESSURE,
        score=5,
        reasons=("disk_headroom_pressure",),
        active_dimensions=1,
    )


def _marked_root(tmp_path):
    root = tmp_path / "retention"
    root.mkdir()
    (root / RETENTION_MARKER).write_text(RETENTION_MARKER_CONTENT, encoding="utf-8")
    return root


def _make_old(path):
    path.write_text("old", encoding="utf-8")
    os.utime(path, (NOW.timestamp() - 1, NOW.timestamp() - 1))


def test_attempt_metadata_hashes_each_canonical_root_and_records_owner_budgets(tmp_path):
    root = _marked_root(tmp_path)
    target = RetentionTarget(
        root=root,
        min_age=timedelta(hours=24),
        max_delete_files=3,
        max_delete_bytes=4096,
    )

    metadata = retention_attempt_metadata(targets=(target,))

    assert metadata["root_hashes"] == [
        hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    ]
    assert metadata["targets"] == [
        {
            "min_age_seconds": 86400,
            "max_delete_files": 3,
            "max_delete_bytes": 4096,
            "root_hash": hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
        }
    ]
    assert len(metadata["config_hash"]) == 64


def test_attempt_id_is_stable_within_owner_budget_period_and_changes_after_it(tmp_path):
    root = _marked_root(tmp_path)
    target = RetentionTarget(root=root, min_age=timedelta(hours=24))

    first = derive_retention_attempt_id(
        targets=(target,), now=NOW, budget_period=timedelta(hours=24)
    )
    retry = derive_retention_attempt_id(
        targets=(target,), now=NOW + timedelta(hours=11), budget_period=timedelta(hours=24)
    )
    next_period = derive_retention_attempt_id(
        targets=(target,), now=NOW + timedelta(hours=24), budget_period=timedelta(hours=24)
    )

    assert retry == first
    assert next_period != first


def test_unresolved_older_intent_blocks_new_period_before_unlink(session: Session, tmp_path):
    root = _marked_root(tmp_path)
    old = root / "old.log"
    _make_old(old)
    target = RetentionTarget(root=root, min_age=timedelta(seconds=0))
    metadata = retention_attempt_metadata(targets=(target,))
    unresolved_id = derive_retention_attempt_id(
        targets=(target,), now=NOW, budget_period=timedelta(hours=1)
    )
    next_id = derive_retention_attempt_id(
        targets=(target,), now=NOW + timedelta(hours=1), budget_period=timedelta(hours=1)
    )
    session.add(
        RetentionAttemptIntent(
            attempt_id=unresolved_id,
            occurred_at=NOW,
            config_hash=metadata["config_hash"],
            owner_budget_files=metadata["owner_budget_files"],
            owner_budget_bytes=metadata["owner_budget_bytes"],
            root_hashes_json=metadata["root_hashes"],
            config_json={"targets": metadata["targets"]},
        )
    )
    session.commit()

    result = execute_retention_attempt(
        session,
        assessment=_disk_pressure(),
        targets=(target,),
        now=NOW + timedelta(hours=1),
        dry_run=False,
        attempt_id=next_id,
    )

    assert result.status is RetentionAttemptStatus.UNRESOLVED_INTENT
    assert old.exists()
    assert session.get(RetentionAttemptIntent, next_id) is None


def test_non_dry_run_commits_append_only_intent_before_unlink_and_appends_outcome(
    session: Session, tmp_path
):
    root = _marked_root(tmp_path)
    old = root / "old.log"
    _make_old(old)
    target = RetentionTarget(root=root, min_age=timedelta(seconds=0))
    attempt_id = uuid.uuid4()

    result = execute_retention_attempt(
        session,
        assessment=_disk_pressure(),
        targets=(target,),
        now=NOW,
        dry_run=False,
        attempt_id=attempt_id,
    )

    assert result.status is RetentionAttemptStatus.EXECUTED
    assert result.retention.status is RetentionStatus.CLEANED
    assert not old.exists()
    intent = session.get(RetentionAttemptIntent, attempt_id)
    assert intent is not None
    assert intent.config_hash == result.config_hash
    assert intent.root_hashes_json == result.metadata["root_hashes"]
    outcome = session.execute(
        select(RetentionAttemptOutcome).where(
            RetentionAttemptOutcome.attempt_id == attempt_id
        )
    ).scalar_one()
    assert outcome.status == "CLEANED"
    assert outcome.result_json["deleted_files"] == 1


def test_retry_of_same_attempt_id_does_not_unlink_a_second_file(session: Session, tmp_path):
    root = _marked_root(tmp_path)
    old = root / "old.log"
    _make_old(old)
    target = RetentionTarget(root=root, min_age=timedelta(seconds=0))
    attempt_id = uuid.uuid4()

    first = execute_retention_attempt(
        session,
        assessment=_disk_pressure(),
        targets=(target,),
        now=NOW,
        dry_run=False,
        attempt_id=attempt_id,
    )
    (root / "new-old.log").write_text("must survive retry", encoding="utf-8")
    replay = execute_retention_attempt(
        session,
        assessment=_disk_pressure(),
        targets=(target,),
        now=NOW,
        dry_run=False,
        attempt_id=attempt_id,
    )

    assert first.status is RetentionAttemptStatus.EXECUTED
    assert replay.status is RetentionAttemptStatus.REPLAYED
    assert (root / "new-old.log").exists()
    outcomes = session.execute(
        select(RetentionAttemptOutcome).where(
            RetentionAttemptOutcome.attempt_id == attempt_id
        )
    ).scalars().all()
    assert len(outcomes) == 1


def test_postgresql_global_lock_failure_fails_closed_without_unlink(
    session: Session, engine, tmp_path
):
    root = _marked_root(tmp_path)
    old = root / "old.log"
    _make_old(old)
    target = RetentionTarget(root=root, min_age=timedelta(seconds=0))

    from app.ops import retention_attempts

    with engine.connect() as holder:
        holder.execute(
            text("SELECT pg_advisory_lock(:key)"),
            {"key": retention_attempts._GLOBAL_RETENTION_LOCK},
        )
        holder.commit()
        try:
            result = execute_retention_attempt(
                session,
                assessment=_disk_pressure(),
                targets=(target,),
                now=NOW,
                dry_run=False,
            )
        finally:
            holder.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": retention_attempts._GLOBAL_RETENTION_LOCK},
            )
            holder.commit()

    assert result.status is RetentionAttemptStatus.LOCK_UNAVAILABLE
    assert old.exists()
    assert session.get(RetentionAttemptIntent, result.attempt_id) is None


def test_intent_commit_failure_fails_closed_without_unlink(session: Session, tmp_path, monkeypatch):
    root = _marked_root(tmp_path)
    old = root / "old.log"
    _make_old(old)
    target = RetentionTarget(root=root, min_age=timedelta(seconds=0))

    monkeypatch.setattr(
        "app.ops.retention_attempts._commit_intent",
        lambda *args, **kwargs: False,
    )
    result = execute_retention_attempt(
        session,
        assessment=_disk_pressure(),
        targets=(target,),
        now=NOW,
        dry_run=False,
    )

    assert result.status is RetentionAttemptStatus.AUDIT_UNAVAILABLE
    assert old.exists()


def test_outcome_failure_after_unlink_leaves_committed_intent_for_forensics(
    session: Session, tmp_path, monkeypatch
):
    root = _marked_root(tmp_path)
    old = root / "old.log"
    _make_old(old)
    target = RetentionTarget(root=root, min_age=timedelta(seconds=0))
    attempt_id = uuid.uuid4()

    monkeypatch.setattr(
        "app.ops.retention_attempts._commit_outcome",
        lambda *args, **kwargs: False,
    )
    result = execute_retention_attempt(
        session,
        assessment=_disk_pressure(),
        targets=(target,),
        now=NOW,
        dry_run=False,
        attempt_id=attempt_id,
    )

    assert result.status is RetentionAttemptStatus.OUTCOME_UNPERSISTED
    assert not old.exists()
    assert session.get(RetentionAttemptIntent, attempt_id) is not None
    assert session.get(RetentionAttemptOutcome, attempt_id) is None
