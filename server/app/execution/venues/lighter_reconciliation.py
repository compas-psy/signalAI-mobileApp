"""Evidence-driven reconciliation for ambiguous Lighter actions (SAI-073/083).

The provider lookup result is treated as evidence only. This module never
submits or retries provider transactions and never mutates SignalAI's generic
ExecutionIntent/ExecutionOrder lifecycle. Its only mutable Lighter state change
is retiring an already-reserved explicit nonce when provider facts prove that
nonce can no longer be safely reused.

The SAI-083 orchestration boundary additionally applies a one-way execution
HALT only when a *new* reconciliation fact proves ambiguity for an action that
was already durably ``SUBMITTING`` while lifecycle mode is ``CANARY``. Historical
evidence replay, pre-submit ``RESERVED`` state and non-Canary reconciliation do
not gain global kill-switch authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ...models.lighter_execution import (
    LighterNonceReservation,
    LighterOrderActionBinding,
    LighterReconciliationEvidence,
)
from ..automatic_safety import _automatic_halt_new_entries_locked
from ..enums import ExecutionLifecycleMode
from ..kill_switch import execution_control_lock
from ..mode import get_execution_mode
from .lighter_replay import LighterReplayError, mark_lighter_nonce_consumed

_INT64_MAX = (1 << 63) - 1
_RECONCILIATION_AMBIGUITY = frozenset({"AMBIGUOUS", "CONSUMED_UNKNOWN"})
_RECONCILIATION_SAFETY_TRIGGER = "NONCE_RECONCILIATION_AMBIGUITY"


class LighterReconciliationError(RuntimeError):
    """Base fail-closed reconciliation error."""


class LighterReconciliationStateError(LighterReconciliationError):
    """Provider evidence conflicts with the durable local action identity."""


@dataclass(frozen=True, slots=True)
class LighterProviderOrderFact:
    owner_account_index: int
    market_index: int
    client_order_index: int
    nonce: int
    order_id: str
    status: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class LighterProviderTransactionFact:
    account_index: int
    api_key_index: int
    nonce: int
    tx_hash: str
    status: int
    executed_at: datetime


@dataclass(frozen=True, slots=True)
class LighterReconciliationSnapshot:
    account_index: int
    api_key_index: int
    provider_next_nonce: int
    observed_at: datetime
    order: LighterProviderOrderFact | None = None
    transaction: LighterProviderTransactionFact | None = None


@dataclass(frozen=True, slots=True)
class LighterReconciliationResult:
    outcome: str
    provider_order_id: str | None = None
    provider_status: str | None = None
    provider_tx_hash: str | None = None
    provider_tx_status: int | None = None
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LighterReconciliationSafetyResult:
    """Committed reconciliation plus monotonic automatic-safety outcome."""

    reconciliation: LighterReconciliationResult
    evidence_created: bool
    automatic_halt_applied: bool
    automatic_safety_trigger: str | None


@dataclass(frozen=True, slots=True)
class _ReconciliationMetadata:
    result: LighterReconciliationResult
    evidence_created: bool
    nonce_was_submitting: bool


def _aware(field: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LighterReconciliationStateError(f"{field} must be timezone-aware")
    return value


def _non_negative_int(field: str, value: int, *, maximum: int = _INT64_MAX) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise LighterReconciliationStateError(f"{field} is outside allowed integer range")
    return value


def _advisory_key(namespace: str, identity: str) -> int:
    digest = hashlib.sha256(f"{namespace}:{identity}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], byteorder="big", signed=False) & _INT64_MAX
    return value or 1


def _lock(db: Session, namespace: str, identity: str) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _advisory_key(namespace, identity)},
    )


def _snapshot_payload(action_key: str, snapshot: LighterReconciliationSnapshot) -> dict:
    def normalize(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        return value

    return normalize(
        {
            "action_key": action_key,
            "account_index": snapshot.account_index,
            "api_key_index": snapshot.api_key_index,
            "provider_next_nonce": snapshot.provider_next_nonce,
            "observed_at": snapshot.observed_at,
            "order": asdict(snapshot.order) if snapshot.order is not None else None,
            "transaction": (
                asdict(snapshot.transaction) if snapshot.transaction is not None else None
            ),
        }
    )


def _evidence_key(action_key: str, snapshot: LighterReconciliationSnapshot) -> str:
    rendered = json.dumps(
        _snapshot_payload(action_key, snapshot),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _result_from_evidence(row: LighterReconciliationEvidence) -> LighterReconciliationResult:
    return LighterReconciliationResult(
        outcome=row.outcome,
        provider_order_id=row.provider_order_id,
        provider_status=row.provider_order_status,
        provider_tx_hash=row.provider_tx_hash,
        provider_tx_status=row.provider_tx_status,
        observed_at=row.observed_at,
    )


def _validate_snapshot_scope(
    binding: LighterOrderActionBinding,
    reservation: LighterNonceReservation,
    snapshot: LighterReconciliationSnapshot,
) -> None:
    _aware("observed_at", snapshot.observed_at)
    _non_negative_int("account_index", snapshot.account_index)
    _non_negative_int("api_key_index", snapshot.api_key_index, maximum=253)
    _non_negative_int("provider_next_nonce", snapshot.provider_next_nonce)

    if (
        snapshot.account_index != binding.account_index
        or snapshot.account_index != reservation.account_index
        or snapshot.api_key_index != binding.api_key_index
        or snapshot.api_key_index != reservation.api_key_index
    ):
        raise LighterReconciliationStateError(
            "provider snapshot scope does not match durable Lighter action scope"
        )
    if snapshot.provider_next_nonce < reservation.nonce:
        raise LighterReconciliationStateError(
            "provider_next_nonce moved behind the reserved local nonce"
        )


def _validate_order_fact(
    binding: LighterOrderActionBinding,
    reservation: LighterNonceReservation,
    order: LighterProviderOrderFact,
    *,
    require_action_nonce: bool,
) -> None:
    _aware("order.updated_at", order.updated_at)
    for field, value in (
        ("order.owner_account_index", order.owner_account_index),
        ("order.market_index", order.market_index),
        ("order.client_order_index", order.client_order_index),
        ("order.nonce", order.nonce),
    ):
        _non_negative_int(field, value)
    if order.owner_account_index != binding.account_index:
        raise LighterReconciliationStateError("provider order account mismatch")
    if order.market_index != binding.market_index:
        raise LighterReconciliationStateError("provider order market mismatch")
    if order.client_order_index != binding.client_order_index:
        raise LighterReconciliationStateError("provider order client identity mismatch")
    if require_action_nonce and order.nonce != reservation.nonce:
        raise LighterReconciliationStateError("provider order nonce mismatch")
    if not isinstance(order.order_id, str) or not order.order_id.strip():
        raise LighterReconciliationStateError("provider order id is missing")
    if not isinstance(order.status, str) or not order.status.strip():
        raise LighterReconciliationStateError("provider order status is missing")


def _validate_transaction_fact(
    reservation: LighterNonceReservation,
    transaction: LighterProviderTransactionFact,
) -> None:
    _aware("transaction.executed_at", transaction.executed_at)
    _non_negative_int("transaction.account_index", transaction.account_index)
    _non_negative_int("transaction.api_key_index", transaction.api_key_index, maximum=253)
    _non_negative_int("transaction.nonce", transaction.nonce)
    if (
        transaction.account_index != reservation.account_index
        or transaction.api_key_index != reservation.api_key_index
    ):
        raise LighterReconciliationStateError("provider transaction scope mismatch")
    if transaction.nonce != reservation.nonce:
        raise LighterReconciliationStateError("provider transaction nonce mismatch")
    if not isinstance(transaction.tx_hash, str) or not transaction.tx_hash.strip():
        raise LighterReconciliationStateError("provider transaction hash is missing")
    if isinstance(transaction.status, bool) or not isinstance(transaction.status, int):
        raise LighterReconciliationStateError("provider transaction status must be integer")


def _persist_evidence(
    db: Session,
    *,
    key: str,
    action_key: str,
    outcome: str,
    reservation: LighterNonceReservation,
    snapshot: LighterReconciliationSnapshot,
    order: LighterProviderOrderFact | None,
    transaction: LighterProviderTransactionFact | None,
) -> LighterReconciliationEvidence:
    existing = db.scalar(
        select(LighterReconciliationEvidence).where(
            LighterReconciliationEvidence.evidence_key == key
        )
    )
    if existing is not None:
        return existing

    row = LighterReconciliationEvidence(
        evidence_key=key,
        action_key=action_key,
        outcome=outcome,
        account_index=reservation.account_index,
        api_key_index=reservation.api_key_index,
        reserved_nonce=reservation.nonce,
        provider_next_nonce=snapshot.provider_next_nonce,
        provider_order_id=order.order_id if order is not None else None,
        provider_order_status=order.status if order is not None else None,
        provider_tx_hash=transaction.tx_hash if transaction is not None else None,
        provider_tx_status=transaction.status if transaction is not None else None,
        observed_at=snapshot.observed_at,
    )
    db.add(row)
    db.flush()
    return row


def _reconcile_lighter_action(
    db: Session,
    *,
    action_key: str,
    snapshot: LighterReconciliationSnapshot,
) -> _ReconciliationMetadata:
    """Reconcile and retain causality metadata for SAI-083 safety decisions."""

    if not isinstance(action_key, str) or not action_key or len(action_key) > 192:
        raise LighterReconciliationStateError("action_key must be non-empty")
    if not isinstance(snapshot, LighterReconciliationSnapshot):
        raise LighterReconciliationStateError("snapshot has invalid type")

    binding = db.scalar(
        select(LighterOrderActionBinding).where(
            LighterOrderActionBinding.action_key == action_key
        )
    )
    if binding is None:
        raise LighterReconciliationStateError("unknown durable Lighter action")

    # Match the global SAI-069/070 lock order: identity -> replay -> nonce scope.
    # Binding is immutable. Reservation is deliberately re-read *after* these
    # transaction locks so an action that changed while we waited cannot leave
    # stale ORM state behind the safety decision.
    _lock(db, "lighter-client-order-id", binding.client_order_id)
    _lock(db, "lighter-replay-key", action_key)
    _lock(
        db,
        "lighter-nonce-scope",
        f"{binding.account_index}:{binding.api_key_index}",
    )
    reservation = db.execute(
        select(LighterNonceReservation)
        .where(LighterNonceReservation.replay_key == action_key)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if reservation is None:
        raise LighterReconciliationStateError("unknown durable Lighter action")

    _validate_snapshot_scope(binding, reservation, snapshot)
    nonce_was_submitting = reservation.state == "SUBMITTING"
    key = _evidence_key(action_key, snapshot)
    prior = db.scalar(
        select(LighterReconciliationEvidence).where(
            LighterReconciliationEvidence.evidence_key == key
        )
    )
    if prior is not None:
        return _ReconciliationMetadata(
            result=_result_from_evidence(prior),
            evidence_created=False,
            nonce_was_submitting=nonce_was_submitting,
        )

    order = snapshot.order
    transaction = snapshot.transaction
    if order is not None:
        _validate_order_fact(
            binding,
            reservation,
            order,
            require_action_nonce=binding.action_type != "CANCEL",
        )
    if transaction is not None:
        _validate_transaction_fact(reservation, transaction)

    resolved_order: LighterProviderOrderFact | None = None
    resolved_tx: LighterProviderTransactionFact | None = None
    if binding.action_type == "CANCEL":
        if transaction is not None:
            outcome = "TX_FOUND"
            resolved_tx = transaction
        elif snapshot.provider_next_nonce > reservation.nonce:
            outcome = "CONSUMED_UNKNOWN"
        else:
            outcome = "AMBIGUOUS"
    elif order is not None:
        outcome = "ORDER_FOUND"
        resolved_order = order
    elif transaction is not None:
        outcome = "TX_FOUND"
        resolved_tx = transaction
    elif snapshot.provider_next_nonce > reservation.nonce:
        outcome = "CONSUMED_UNKNOWN"
    else:
        outcome = "AMBIGUOUS"

    if outcome in {"ORDER_FOUND", "TX_FOUND", "CONSUMED_UNKNOWN"}:
        try:
            reservation = mark_lighter_nonce_consumed(
                db,
                replay_key=action_key,
                consumed_at=snapshot.observed_at,
            )
        except LighterReplayError as exc:
            raise LighterReconciliationStateError(str(exc)) from exc

    row = _persist_evidence(
        db,
        key=key,
        action_key=action_key,
        outcome=outcome,
        reservation=reservation,
        snapshot=snapshot,
        order=resolved_order,
        transaction=resolved_tx,
    )
    return _ReconciliationMetadata(
        result=_result_from_evidence(row),
        evidence_created=True,
        nonce_was_submitting=nonce_was_submitting,
    )


def reconcile_lighter_action(
    db: Session,
    *,
    action_key: str,
    snapshot: LighterReconciliationSnapshot,
) -> LighterReconciliationResult:
    """Reconcile one durable action from already-fetched provider facts.

    A missing order is never interpreted as safe-to-resend. If the provider
    nonce has not advanced the action stays unresolved; if it has advanced the
    nonce is retired but the semantic outcome remains explicitly unknown.

    This foundational boundary keeps its original transaction ownership. Use
    ``reconcile_lighter_action_with_automatic_safety`` for the CANARY runtime
    orchestration that must commit reconciliation and HALT atomically under the
    shared execution-control serialization boundary.
    """

    return _reconcile_lighter_action(
        db,
        action_key=action_key,
        snapshot=snapshot,
    ).result


def reconcile_lighter_action_with_automatic_safety(
    db: Session,
    *,
    action_key: str,
    snapshot: LighterReconciliationSnapshot,
) -> LighterReconciliationSafetyResult:
    """Commit authoritative reconciliation and monotonic CANARY HALT together.

    The shared execution-control lock is acquired *before* replay/nonce locks,
    matching the future live submit lock order. Only newly-created evidence for
    a nonce that was already SUBMITTING can prove provider-I/O ambiguity. This
    prevents replay of historical snapshots or pre-submit local state from
    becoming a global kill-switch DoS primitive.
    """

    with execution_control_lock(db):
        try:
            metadata = _reconcile_lighter_action(
                db,
                action_key=action_key,
                snapshot=snapshot,
            )
            trigger: str | None = None
            automatic_halt_applied = False
            if (
                metadata.evidence_created
                and metadata.nonce_was_submitting
                and metadata.result.outcome in _RECONCILIATION_AMBIGUITY
                and get_execution_mode(db).mode is ExecutionLifecycleMode.CANARY
            ):
                trigger = _RECONCILIATION_SAFETY_TRIGGER
                halt = _automatic_halt_new_entries_locked(
                    db,
                    reason=(
                        "Lighter Canary reconciliation ambiguity: "
                        f"{metadata.result.outcome}"
                    ),
                )
                automatic_halt_applied = halt.changed

            # The locked halt helper commits when it changes state. A final
            # commit is intentional and harmless in that case; otherwise it is
            # what makes reconciliation evidence durable before the global lock
            # is released, including when a stronger owner switch already exists.
            db.commit()
            return LighterReconciliationSafetyResult(
                reconciliation=metadata.result,
                evidence_created=metadata.evidence_created,
                automatic_halt_applied=automatic_halt_applied,
                automatic_safety_trigger=trigger,
            )
        except Exception:
            db.rollback()
            raise


__all__ = [
    "LighterProviderOrderFact",
    "LighterProviderTransactionFact",
    "LighterReconciliationError",
    "LighterReconciliationResult",
    "LighterReconciliationSafetyResult",
    "LighterReconciliationSnapshot",
    "LighterReconciliationStateError",
    "reconcile_lighter_action",
    "reconcile_lighter_action_with_automatic_safety",
]
