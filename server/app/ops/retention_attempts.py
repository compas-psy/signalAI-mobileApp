"""Durable, PostgreSQL-serialized execution boundary for retention unlinks."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

from sqlalchemy import select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from ..models import RetentionAttemptIntent, RetentionAttemptOutcome
from .pressure import PressureAssessment
from .retention import RetentionResult, RetentionStatus, RetentionTarget, run_safe_retention


log = logging.getLogger(__name__)
_GLOBAL_RETENTION_LOCK = int.from_bytes(
    hashlib.sha256(b"signalai:retention:global-owner-budget:v1").digest()[:8],
    byteorder="big",
    signed=True,
)
_RETENTION_ATTEMPT_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://signalai.local/retention-attempt/v1"
)


class RetentionAttemptStatus(str, Enum):
    EXECUTED = "EXECUTED"
    REPLAYED = "REPLAYED"
    LOCK_UNAVAILABLE = "LOCK_UNAVAILABLE"
    AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"
    OUTCOME_UNPERSISTED = "OUTCOME_UNPERSISTED"
    UNRESOLVED_INTENT = "UNRESOLVED_INTENT"


@dataclass(frozen=True, slots=True)
class RetentionAttemptResult:
    status: RetentionAttemptStatus
    attempt_id: uuid.UUID
    retention: RetentionResult
    config_hash: str
    metadata: dict


def retention_attempt_metadata(
    *, targets: tuple[RetentionTarget, ...] | list[RetentionTarget]
) -> dict:
    """Canonical, path-redacted owner authorization included in the intent."""

    rows = [
        {
            "root_hash": _root_hash(target),
            "min_age_seconds": int(target.min_age.total_seconds()),
            "max_delete_files": target.max_delete_files,
            "max_delete_bytes": target.max_delete_bytes,
        }
        for target in targets
    ]
    rows.sort(key=lambda row: row["root_hash"])
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "config_hash": hashlib.sha256(canonical).hexdigest(),
        "root_hashes": [row["root_hash"] for row in rows],
        "targets": rows,
        "owner_budget_files": sum(row["max_delete_files"] for row in rows),
        "owner_budget_bytes": sum(row["max_delete_bytes"] for row in rows),
    }


def derive_retention_attempt_id(
    *,
    targets: tuple[RetentionTarget, ...] | list[RetentionTarget],
    now: datetime,
    budget_period: timedelta,
) -> uuid.UUID:
    """Bind one destructive budget to a stable config/time period identity."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not isinstance(budget_period, timedelta) or budget_period <= timedelta(0):
        raise ValueError("budget_period must be positive")
    period_seconds = int(budget_period.total_seconds())
    if period_seconds <= 0:
        raise ValueError("budget_period must be at least one second")
    epoch_seconds = int(now.astimezone(UTC).timestamp())
    period_start = epoch_seconds - epoch_seconds % period_seconds
    config_hash = retention_attempt_metadata(targets=targets)["config_hash"]
    return uuid.uuid5(
        _RETENTION_ATTEMPT_NAMESPACE,
        f"{config_hash}:{period_seconds}:{period_start}",
    )


def execute_retention_attempt(
    session: Session,
    *,
    assessment: PressureAssessment,
    targets: tuple[RetentionTarget, ...] | list[RetentionTarget],
    now: datetime,
    dry_run: bool,
    attempt_id: uuid.UUID | None = None,
) -> RetentionAttemptResult:
    """Commit intent, hold the global lock, unlink, then commit one outcome.

    This boundary is deliberately unavailable outside PostgreSQL: a local lock
    cannot prove the owner budget across a deploy overlap or second process.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if dry_run:
        raise ValueError("non-dry-run retention attempt required")
    attempt = attempt_id or uuid.uuid4()
    if not isinstance(attempt, uuid.UUID):
        raise ValueError("attempt_id must be UUID")
    metadata = retention_attempt_metadata(targets=targets)
    unavailable = _failed_retention("retention audit or PostgreSQL lock unavailable")

    engine = _engine_for(session)
    if engine is None or engine.dialect.name != "postgresql":
        return RetentionAttemptResult(
            RetentionAttemptStatus.AUDIT_UNAVAILABLE,
            attempt,
            unavailable,
            metadata["config_hash"],
            metadata,
        )

    try:
        with engine.connect() as connection:
            if not _try_global_lock(connection):
                return RetentionAttemptResult(
                    RetentionAttemptStatus.LOCK_UNAVAILABLE,
                    attempt,
                    _failed_retention("retention global lock unavailable"),
                    metadata["config_hash"],
                    metadata,
                )
            try:
                existing = _outcome(connection, attempt)
                if existing is not None:
                    return RetentionAttemptResult(
                        RetentionAttemptStatus.REPLAYED,
                        attempt,
                        _retention_from_json(existing.result_json),
                        metadata["config_hash"],
                        metadata,
                    )
                if _intent_exists(connection, attempt):
                    return RetentionAttemptResult(
                        RetentionAttemptStatus.REPLAYED,
                        attempt,
                        _failed_retention("retention attempt has durable intent but no outcome"),
                        metadata["config_hash"],
                        metadata,
                    )
                unresolved = _unresolved_intent(connection)
                if unresolved is not None:
                    return RetentionAttemptResult(
                        RetentionAttemptStatus.UNRESOLVED_INTENT,
                        attempt,
                        _failed_retention(
                            "an earlier retention attempt has durable intent but no outcome"
                        ),
                        metadata["config_hash"],
                        metadata,
                    )
                if not _commit_intent(connection, attempt, now, metadata):
                    return RetentionAttemptResult(
                        RetentionAttemptStatus.AUDIT_UNAVAILABLE,
                        attempt,
                        unavailable,
                        metadata["config_hash"],
                        metadata,
                    )

                retention = run_safe_retention(
                    assessment=assessment,
                    targets=targets,
                    now=now,
                    dry_run=False,
                )
                if not _commit_outcome(connection, attempt, now, retention):
                    return RetentionAttemptResult(
                        RetentionAttemptStatus.OUTCOME_UNPERSISTED,
                        attempt,
                        retention,
                        metadata["config_hash"],
                        metadata,
                    )
                return RetentionAttemptResult(
                    RetentionAttemptStatus.EXECUTED,
                    attempt,
                    retention,
                    metadata["config_hash"],
                    metadata,
                )
            finally:
                _release_global_lock(connection)
    except Exception:
        log.exception("retention attempt failed before a safe destructive boundary")
        return RetentionAttemptResult(
            RetentionAttemptStatus.AUDIT_UNAVAILABLE,
            attempt,
            unavailable,
            metadata["config_hash"],
            metadata,
        )


def _engine_for(session: Session) -> Engine | None:
    bind = session.get_bind()
    if isinstance(bind, Engine):
        return bind
    if isinstance(bind, Connection):
        return bind.engine
    return None


def _try_global_lock(connection: Connection) -> bool:
    acquired = bool(
        connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": _GLOBAL_RETENTION_LOCK}
        ).scalar_one()
    )
    connection.commit()
    return acquired


def _release_global_lock(connection: Connection) -> None:
    try:
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _GLOBAL_RETENTION_LOCK})
        connection.commit()
    except Exception:
        connection.rollback()
        log.exception("retention advisory lock release failed")


def _intent_exists(connection: Connection, attempt_id: uuid.UUID) -> bool:
    with Session(bind=connection, future=True) as db:
        return db.get(RetentionAttemptIntent, attempt_id) is not None


def _outcome(connection: Connection, attempt_id: uuid.UUID) -> RetentionAttemptOutcome | None:
    with Session(bind=connection, future=True) as db:
        return db.execute(
            select(RetentionAttemptOutcome).where(
                RetentionAttemptOutcome.attempt_id == attempt_id
            )
        ).scalar_one_or_none()


def _unresolved_intent(connection: Connection) -> uuid.UUID | None:
    with Session(bind=connection, future=True) as db:
        return db.execute(
            select(RetentionAttemptIntent.attempt_id)
            .outerjoin(
                RetentionAttemptOutcome,
                RetentionAttemptOutcome.attempt_id == RetentionAttemptIntent.attempt_id,
            )
            .where(RetentionAttemptOutcome.attempt_id.is_(None))
            .order_by(RetentionAttemptIntent.occurred_at, RetentionAttemptIntent.attempt_id)
            .limit(1)
        ).scalar_one_or_none()


def _commit_intent(connection: Connection, attempt_id: uuid.UUID, now: datetime, metadata: dict) -> bool:
    try:
        with Session(bind=connection, future=True) as db:
            db.add(
                RetentionAttemptIntent(
                    attempt_id=attempt_id,
                    occurred_at=now,
                    config_hash=metadata["config_hash"],
                    owner_budget_files=metadata["owner_budget_files"],
                    owner_budget_bytes=metadata["owner_budget_bytes"],
                    root_hashes_json=metadata["root_hashes"],
                    config_json={"targets": metadata["targets"]},
                )
            )
            db.commit()
        return True
    except Exception:
        connection.rollback()
        log.exception("retention intent commit failed")
        return False


def _commit_outcome(
    connection: Connection,
    attempt_id: uuid.UUID,
    now: datetime,
    retention: RetentionResult,
) -> bool:
    try:
        with Session(bind=connection, future=True) as db:
            db.add(
                RetentionAttemptOutcome(
                    attempt_id=attempt_id,
                    occurred_at=now,
                    status=retention.status.value,
                    result_json=_retention_json(retention),
                )
            )
            db.commit()
        return True
    except Exception:
        connection.rollback()
        log.exception("retention outcome commit failed after destructive action")
        return False


def _root_hash(target: RetentionTarget) -> str:
    return hashlib.sha256(str(target.root).encode("utf-8")).hexdigest()


def _retention_json(result: RetentionResult) -> dict:
    return {
        "status": result.status.value,
        "candidate_files": result.candidate_files,
        "candidate_bytes": result.candidate_bytes,
        "deleted_files": result.deleted_files,
        "deleted_bytes": result.deleted_bytes,
        "errors": list(result.errors),
    }


def _retention_from_json(payload: dict) -> RetentionResult:
    return RetentionResult(
        status=RetentionStatus(payload["status"]),
        candidate_files=int(payload["candidate_files"]),
        candidate_bytes=int(payload["candidate_bytes"]),
        deleted_files=int(payload["deleted_files"]),
        deleted_bytes=int(payload["deleted_bytes"]),
        errors=tuple(payload.get("errors", ())),
    )


def _failed_retention(error: str) -> RetentionResult:
    return RetentionResult(status=RetentionStatus.FAILED, errors=(error,))


__all__ = [
    "RetentionAttemptResult",
    "RetentionAttemptStatus",
    "derive_retention_attempt_id",
    "execute_retention_attempt",
    "retention_attempt_metadata",
]
