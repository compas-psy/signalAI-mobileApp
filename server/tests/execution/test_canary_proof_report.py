from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.execution.enums import ExecutionLifecycleMode
from app.execution.intent_service import (
    ExecutionIntentGate,
    ExecutionIntentRequest,
    create_execution_intent,
)
from app.execution.mode import ModeChangeAuthorization, change_execution_mode, get_execution_mode
from app.models import (
    ExecutionFill,
    ExecutionIntent,
    ExecutionOrder,
    ExecutionProtection,
    ExecutionReconciliationEvent,
)
from app.models.ideas import TradeIdea
from app.models.risk import RiskSnapshot
from tests.conftest import idea_kwargs


BASE = datetime(2026, 8, 23, 4, 0, tzinfo=UTC)


def _set_mode(session, target: ExecutionLifecycleMode) -> None:
    current = get_execution_mode(session).mode
    if current is target:
        return
    change_execution_mode(
        session,
        target=target,
        actor="test",
        reason="canary proof setup",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test setup authorization",
            detail_json={"test_only": True},
        ),
    )
    session.flush()


def _create_intent(session, instrument, *, mode: ExecutionLifecycleMode, venue: str = "LIGHTER"):
    _set_mode(session, mode)
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            BASE,
            status="TRIGGERED",
            quality_status="PASS",
            score=Decimal("82"),
        )
    )
    risk = RiskSnapshot(risk_equity=Decimal("100000"))
    session.add_all([idea, risk])
    session.flush()
    creation = create_execution_intent(
        session,
        request=ExecutionIntentRequest(
            idea_id=idea.id,
            instrument_id=idea.instrument_id,
            strategy_version=idea.strategy_version,
            risk_policy_snapshot_id=risk.id,
            risk_override_id=None,
            venue=venue,
            account="proof-main",
            planned_quantity=Decimal("2"),
            planned_entry_price=Decimal("100"),
            planned_stop_price=Decimal("95"),
        ),
        gate=ExecutionIntentGate(
            owner_approved=True,
            risk_snapshot_verified=True,
            mode_allows_intent=True,
            kill_switch_clear=True,
            venue_capability_verified=True,
        ),
    )
    session.flush()
    return creation.intent


def _add_fill(session, intent, *, price: Decimal = Decimal("101")) -> ExecutionOrder:
    order = ExecutionOrder(
        intent_id=intent.id,
        client_order_id=f"proof-entry-{intent.id.hex}",
        provider_order_id=f"provider-entry-{intent.id.hex}",
        side="BUY",
        order_type="ENTRY",
        status="ACKNOWLEDGED",
        quantity=Decimal("2"),
        limit_price=Decimal("100"),
        stop_price=None,
        submitted_at=BASE + timedelta(seconds=1),
        acknowledged_at=BASE + timedelta(seconds=1, milliseconds=100),
    )
    session.add(order)
    session.flush()
    session.add(
        ExecutionFill(
            intent_id=intent.id,
            order_id=order.id,
            provider_fill_id=f"proof-fill-{intent.id.hex}",
            quantity=Decimal("2"),
            price=price,
            fee_amount=Decimal("0.50"),
            fee_currency="USDC",
            filled_at=BASE + timedelta(seconds=2),
        )
    )
    session.flush()
    return order


def _bound_canary_intent(session, instrument):
    from tests.execution.test_canary_correlation_audit import (
        _evidence,
        _execution_chain,
        _snapshot,
    )

    snapshot, refs = _snapshot(session, instrument_id=instrument.instrument_id)
    _evidence(session, snapshot, refs)
    _execution_chain(session, instrument, snapshot)
    intent = session.execute(
        select(ExecutionIntent).where(
            ExecutionIntent.execution_mode_snapshot == ExecutionLifecycleMode.CANARY,
            ExecutionIntent.venue == "LIGHTER",
            ExecutionIntent.account == str(snapshot.account_index),
            ExecutionIntent.strategy_version == snapshot.strategy_version,
            ExecutionIntent.instrument_id == instrument.instrument_id,
        )
    ).scalar_one()
    return snapshot, intent


def test_canary_proof_is_insufficient_without_real_canary_execution(session) -> None:
    from app.execution.canary_proof import build_canary_proof_report

    report = build_canary_proof_report(session, as_of=BASE + timedelta(minutes=5))

    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert report.canary_intent_count == 0
    assert report.filled_intent_count == 0
    assert report.fill_count == 0
    assert report.acceptance_ready is False
    assert "NO_CANARY_EXECUTION_EVIDENCE" in report.blockers
    assert "CANARY_PROOF_ACCEPTANCE_THRESHOLDS_NOT_APPROVED" in report.blockers


def test_sandbox_or_other_venue_execution_never_counts_as_canary_proof(session, instrument) -> None:
    from app.execution.canary_proof import build_canary_proof_report

    sandbox = _create_intent(session, instrument, mode=ExecutionLifecycleMode.SANDBOX)
    _add_fill(session, sandbox)
    _set_mode(session, ExecutionLifecycleMode.CANARY)
    bybit = _create_intent(session, instrument, mode=ExecutionLifecycleMode.CANARY, venue="BYBIT")
    _add_fill(session, bybit)

    report = build_canary_proof_report(session, as_of=BASE + timedelta(minutes=5))

    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert report.canary_intent_count == 0
    assert report.fill_count == 0
    assert report.fees_by_currency == {}


def test_exact_bound_canary_records_actual_fills_costs_protection_and_errors(
    session,
    instrument,
) -> None:
    from app.execution.canary_proof import build_canary_proof_report

    snapshot, intent = _bound_canary_intent(session, instrument)
    entry = session.execute(
        select(ExecutionOrder).where(
            ExecutionOrder.intent_id == intent.id,
            ExecutionOrder.order_type == "ENTRY",
        )
    ).scalar_one()
    intent.duplicate_prevention_count = 2
    session.add(
        ExecutionOrder(
            intent_id=intent.id,
            client_order_id=f"proof-reject-{intent.id.hex}",
            provider_order_id=None,
            side="BUY",
            order_type="ENTRY_RETRY",
            status="REJECTED",
            quantity=Decimal("1"),
            limit_price=Decimal("100"),
            stop_price=None,
            submitted_at=entry.submitted_at + timedelta(seconds=2),
            acknowledged_at=entry.acknowledged_at + timedelta(seconds=2),
        )
    )
    session.add(
        ExecutionReconciliationEvent(
            intent_id=intent.id,
            event_type="SUBMISSION_RECOVERY",
            outcome="UNKNOWN",
            detail_json={"reason": "provider timeout"},
            occurred_at=entry.submitted_at + timedelta(seconds=3),
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
    assert report.protection_slo_breach_count == 0
    assert report.current_unprotected_filled_intent_count == 0
    assert report.reconciliation_mismatch_count == 1
    assert report.rejected_order_count == 1
    assert report.duplicate_prevention_count == 2
    assert report.acceptance_ready is False
    assert "CANARY_PROOF_ACCEPTANCE_THRESHOLDS_NOT_APPROVED" in report.blockers


def test_non_active_current_protection_is_reported_without_inventing_acceptance_thresholds(
    session,
    instrument,
) -> None:
    from app.execution.canary_proof import build_canary_proof_report

    snapshot, intent = _bound_canary_intent(session, instrument)
    protection = session.execute(
        select(ExecutionProtection).where(ExecutionProtection.intent_id == intent.id)
    ).scalar_one()
    protection.status = "CANCELLED"
    session.flush()

    report = build_canary_proof_report(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        as_of=datetime.now(UTC) + timedelta(minutes=5),
    )

    assert report.status == "OBSERVED"
    assert report.current_unprotected_filled_intent_count == 1
    assert report.acceptance_ready is False
