"""Server execution worker for SAI-026 / B5.3 and SAI-027 / B5.4.

The worker serializes venue/account/instrument work with a PostgreSQL advisory
lock and now also persists a short worker lease plus retry due-time. Production
venue I/O remains deliberately disabled until SAI-036 installs a real adapter.
"""

from __future__ import annotations

import hashlib
import logging
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterator

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models.execution import ExecutionIntent, ExecutionOrder
from .enums import ExecutionState
from .service import (
    ExecutionFillSnapshot,
    ExecutionPort,
    ExecutionProcessOutcome,
    ExecutionProtectionAck,
    ExecutionSubmitAck,
    PreSubmitReconciliation,
    SubmissionReconciliation,
    process_execution_intent,
)


log = logging.getLogger(__name__)

_LEASE_FOR = timedelta(seconds=30)
_CLAIMABLE = (
    ExecutionState.INTENT_CREATED,
    ExecutionState.RISK_APPROVED,
    ExecutionState.READY_TO_SUBMIT,
    ExecutionState.SUBMITTING,
    ExecutionState.AMBIGUOUS,
    ExecutionState.RECONCILING,
    ExecutionState.ACKNOWLEDGED,
)


def execution_tuple_lock_key(*, venue: str, account: str, instrument_id: str) -> int:
    """Deterministic signed 64-bit key for a PostgreSQL advisory lock."""

    raw = f"{venue}\0{account}\0{instrument_id}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@contextmanager
def execution_tuple_lock(
    db: Session,
    *,
    venue: str,
    account: str,
    instrument_id: str,
) -> Iterator[bool]:
    """Try to serialize work for one venue/account/instrument tuple.

    Session-level advisory locks survive transaction boundaries but are
    released explicitly here (and by PostgreSQL if the connection dies).
    """

    key = execution_tuple_lock_key(
        venue=venue,
        account=account,
        instrument_id=instrument_id,
    )
    acquired = bool(
        db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar_one()
    )
    try:
        yield acquired
    finally:
        if acquired:
            db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


class DisabledExecutionPort(ExecutionPort):
    """Production default before SAI-036: fail closed before any submit."""

    reason = "execution adapter is not configured"

    def reconcile_before_submit(self, intent: ExecutionIntent) -> PreSubmitReconciliation:
        return PreSubmitReconciliation.unknown(self.reason)

    def reconcile_submission(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
    ) -> SubmissionReconciliation:
        return SubmissionReconciliation.unknown(self.reason)

    def submit(
        self, intent: ExecutionIntent, *, client_order_id: str
    ) -> ExecutionSubmitAck:
        raise RuntimeError(self.reason)

    def consume_fills(
        self, intent: ExecutionIntent, order: ExecutionOrder
    ) -> tuple[ExecutionFillSnapshot, ...]:
        raise RuntimeError(self.reason)

    def arm_protection(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
        *,
        filled_quantity: Decimal,
    ) -> ExecutionProtectionAck:
        raise RuntimeError(self.reason)

    def reconcile(self, intent: ExecutionIntent) -> None:
        raise RuntimeError(self.reason)

    def manage_until_close(self, intent: ExecutionIntent) -> None:
        raise RuntimeError(self.reason)


def claim_next_intent(
    db: Session,
    *,
    worker_id: str = "execution-worker",
    now: datetime | None = None,
    lease_for: timedelta = _LEASE_FOR,
) -> ExecutionIntent | None:
    """Atomically claim the oldest due intent with an expiring durable lease."""

    now = now or datetime.now(UTC)
    intent = db.execute(
        select(ExecutionIntent)
        .where(
            ExecutionIntent.state.in_(_CLAIMABLE),
            or_(
                ExecutionIntent.next_retry_at.is_(None),
                ExecutionIntent.next_retry_at <= now,
            ),
            or_(
                ExecutionIntent.lease_expires_at.is_(None),
                ExecutionIntent.lease_expires_at <= now,
            ),
        )
        .order_by(ExecutionIntent.created_at, ExecutionIntent.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if intent is None:
        return None

    intent.lease_owner = worker_id
    intent.lease_expires_at = now + lease_for
    db.flush()
    return intent


def _release_lease(db: Session, *, intent_id, worker_id: str) -> None:
    intent = db.get(ExecutionIntent, intent_id)
    if intent is None or intent.lease_owner != worker_id:
        return
    intent.lease_owner = None
    intent.lease_expires_at = None
    db.flush()


def process_next_intent(
    db: Session,
    *,
    port: ExecutionPort,
    worker_id: str = "execution-worker",
) -> ExecutionProcessOutcome:
    intent = claim_next_intent(db, worker_id=worker_id)
    if intent is None:
        return ExecutionProcessOutcome(False, "no claimable execution intent")

    intent_id = intent.id
    venue = intent.venue
    account = intent.account
    instrument_id = intent.instrument_id

    # Persist the lease before any network-capable work. A crashed worker leaves
    # a bounded lease that another worker may reclaim after expiry.
    db.commit()
    try:
        with execution_tuple_lock(
            db,
            venue=venue,
            account=account,
            instrument_id=instrument_id,
        ) as acquired:
            if not acquired:
                return ExecutionProcessOutcome(
                    False, "execution tuple is already claimed"
                )
            return process_execution_intent(db, intent_id=intent_id, port=port)
    finally:
        _release_lease(db, intent_id=intent_id, worker_id=worker_id)
        db.commit()


def main() -> None:
    """Docker execution entrypoint, deliberately idle until SAI-036."""

    logging.basicConfig(level=logging.INFO)
    port = DisabledExecutionPort()
    log.warning(
        "execution worker is fail-closed: %s; no venue requests will be made",
        port.reason,
    )
    while True:
        # Do not mutate intent state or append repeated reconciliation evidence
        # while no production adapter exists. SAI-036 replaces this disabled
        # port with an explicitly configured venue implementation.
        time.sleep(5)


if __name__ == "__main__":
    main()


__all__ = [
    "DisabledExecutionPort",
    "claim_next_intent",
    "execution_tuple_lock",
    "execution_tuple_lock_key",
    "main",
    "process_next_intent",
]
