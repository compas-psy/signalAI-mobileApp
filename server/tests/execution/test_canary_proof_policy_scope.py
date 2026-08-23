from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.execution.enums import ExecutionLifecycleMode
from app.models import ExecutionFill, ExecutionIntent, ExecutionOrder
from tests.execution.test_canary_correlation_audit import (
    _evidence,
    _execution_chain,
    _snapshot,
)
from tests.execution.test_canary_proof_report import BASE, _add_fill, _create_intent


def test_unbound_canary_execution_is_not_counted_as_canary_proof(session, instrument) -> None:
    from app.execution.canary_proof import build_canary_proof_report

    intent = _create_intent(session, instrument, mode=ExecutionLifecycleMode.CANARY)
    _add_fill(session, intent)

    report = build_canary_proof_report(
        session,
        as_of=BASE + timedelta(minutes=5),
    )

    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert report.policy_snapshot_hash is None
    assert report.canary_intent_count == 0
    assert report.filled_intent_count == 0
    assert report.fill_count == 0
    assert report.fees_by_currency == {}
    assert report.acceptance_ready is False
    assert "CANARY_ACTIVATION_POLICY_BINDING_MISSING" in report.blockers


def test_exact_policy_proof_excludes_foreign_account_execution(session, instrument) -> None:
    from app.execution.canary_proof import build_canary_proof_report

    snapshot, refs = _snapshot(session, instrument_id=instrument.instrument_id)
    _evidence(session, snapshot, refs)
    _execution_chain(session, instrument, snapshot)

    exact_intent = session.execute(
        select(ExecutionIntent).where(
            ExecutionIntent.execution_mode_snapshot == ExecutionLifecycleMode.CANARY,
            ExecutionIntent.venue == "LIGHTER",
            ExecutionIntent.account == str(snapshot.account_index),
            ExecutionIntent.strategy_version == snapshot.strategy_version,
            ExecutionIntent.instrument_id == instrument.instrument_id,
        )
    ).scalar_one()

    foreign = ExecutionIntent(
        identity_hash="f" * 64,
        idea_id=exact_intent.idea_id,
        instrument_id=exact_intent.instrument_id,
        strategy_version=exact_intent.strategy_version,
        risk_policy_snapshot_id=exact_intent.risk_policy_snapshot_id,
        risk_override_id=None,
        venue="LIGHTER",
        account="foreign-account",
        execution_mode_snapshot=ExecutionLifecycleMode.CANARY,
        state=exact_intent.state,
        planned_quantity=Decimal("1"),
        planned_entry_price=Decimal("100"),
        planned_stop_price=Decimal("95"),
        created_at=exact_intent.created_at,
    )
    session.add(foreign)
    session.flush()
    foreign_order = ExecutionOrder(
        intent_id=foreign.id,
        client_order_id=f"foreign-proof-{foreign.id.hex}",
        provider_order_id=f"foreign-provider-{foreign.id.hex}",
        side="BUY",
        order_type="ENTRY",
        status="ACKNOWLEDGED",
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
        stop_price=None,
        submitted_at=exact_intent.created_at + timedelta(seconds=1),
        acknowledged_at=exact_intent.created_at + timedelta(seconds=1, milliseconds=100),
    )
    session.add(foreign_order)
    session.flush()
    session.add(
        ExecutionFill(
            intent_id=foreign.id,
            order_id=foreign_order.id,
            provider_fill_id=f"foreign-fill-{foreign.id.hex}",
            quantity=Decimal("1"),
            price=Decimal("110"),
            fee_amount=Decimal("9.99"),
            fee_currency="USDC",
            filled_at=exact_intent.created_at + timedelta(seconds=2),
        )
    )
    session.flush()

    report = build_canary_proof_report(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        as_of=datetime.now(UTC) + timedelta(minutes=5),
    )

    assert report.status == "OBSERVED"
    assert report.policy_snapshot_hash == snapshot.snapshot_hash
    assert report.canary_intent_count == 1
    assert report.filled_intent_count == 1
    assert report.fill_count == 1
    assert report.fees_by_currency == {"USDC": Decimal("0.1")}
    assert report.average_fill_deviation_bps == Decimal("100.00")
    assert report.worst_fill_deviation_bps == Decimal("100.00")
    assert report.acceptance_ready is False
    assert "CANARY_PROOF_ACCEPTANCE_THRESHOLDS_NOT_APPROVED" in report.blockers
