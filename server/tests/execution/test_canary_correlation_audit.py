from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.config import get_config
from app.execution.canary_policy import (
    CanaryPolicy,
    persist_canary_policy_snapshot,
    record_lighter_trade_generation,
)
from app.execution.enums import ExecutionLifecycleMode
from app.execution.intent_service import (
    ExecutionIntentGate,
    ExecutionIntentRequest,
    create_execution_intent,
)
from app.execution.mode import ModeChangeAuthorization, change_execution_mode
from app.models import (
    CanaryEvidenceReference,
    ExecutionFill,
    ExecutionModeActivationRequest,
    ExecutionModeEvent,
    ExecutionOrder,
    ExecutionProtection,
    ExecutionReconciliationEvent,
)
from app.models.ideas import TradeIdea
from app.models.risk import RiskSnapshot
from tests.conftest import idea_kwargs


NOW = datetime(2026, 8, 23, 5, 20, tzinfo=UTC)
CATEGORIES = (
    "strategy_performance",
    "shadow",
    "testnet",
    "protection_reconciliation",
    "kill_switch_drill",
    "security_scan",
    "operational_health",
)


def _snapshot(session, *, instrument_id: str = "CRYPTO:PERP:BTCUSDT"):
    generation = record_lighter_trade_generation(
        session,
        action="CREATED",
        actor="audit-test",
        account_index=42,
        api_key_index=7,
    )
    refs = {category: f"audit-{category}" for category in CATEGORIES}
    policy = CanaryPolicy(
        policy_version="canary-v1",
        source_sha="a" * 40,
        engine_config_hash=get_config().config_hash,
        strategy_family="TREND_PULLBACK",
        strategy_version="trend-pullback-v2",
        credential_generation_id=generation.generation_id,
        account_index=42,
        api_key_index=7,
        market_allowlist=(1,),
        instrument_allowlist=(instrument_id,),
        capital_amount=Decimal("10000"),
        capital_currency="RUB",
        valuation_source="owner_preapproved",
        valuation_observed_at=NOW - timedelta(minutes=1),
        valuation_rule="fixed_preapproved_rub",
        hard_caps={
            "max_order_notional": "2500",
            "max_instrument_notional": "5000",
            "max_gross_notional": "10000",
            "max_open_positions": 2,
            "max_entry_orders": 2,
            "max_leverage": "2",
            "daily_loss_limit": "500",
            "total_loss_limit": "1000",
            "max_order_count": 10,
            "max_trade_count": 5,
        },
        evidence_refs=refs,
        valid_until=NOW + timedelta(hours=1),
    )
    snapshot = persist_canary_policy_snapshot(
        session,
        policy,
        actor="audit-test",
        correlation_id="canary-audit-chain-1",
    )
    session.flush()
    return snapshot, refs


def _evidence(session, snapshot, refs) -> None:
    for category, evidence_ref in refs.items():
        session.add(
            CanaryEvidenceReference(
                category=category,
                evidence_ref=evidence_ref,
                source="audit-test",
                artifact_sha256="b" * 64,
                verdict="VERIFIED",
                source_sha=snapshot.source_sha,
                engine_config_hash=snapshot.engine_config_hash,
                strategy_family=snapshot.strategy_family,
                strategy_version=snapshot.strategy_version,
                venue="LIGHTER",
                observed_at=NOW - timedelta(minutes=2),
                fresh_until=NOW + timedelta(minutes=30),
            )
        )
    session.flush()


def _set_canary(session) -> None:
    change_execution_mode(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
        actor="test",
        reason="audit setup sandbox",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test setup",
            detail_json={"test_only": True},
        ),
    )
    change_execution_mode(
        session,
        target=ExecutionLifecycleMode.CANARY,
        actor="test",
        reason="audit setup canary",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test setup",
            detail_json={"test_only": True},
        ),
    )
    session.flush()


def _execution_chain(session, instrument, snapshot) -> None:
    _set_canary(session)

    # Production mode events use the database clock. The outer PostgreSQL test
    # transaction deliberately gives every server-default now() the same
    # transaction timestamp, so use that exact clock here instead of a later
    # process wall clock.
    bound_event = ExecutionModeEvent(
        from_mode=ExecutionLifecycleMode.SANDBOX,
        to_mode=ExecutionLifecycleMode.CANARY,
        actor="owner",
        reason="owner approved exact Canary snapshot",
        detail_json={
            "canary_policy_snapshot_hash": snapshot.snapshot_hash,
            "correlation_id": snapshot.correlation_id,
            "source_sha": snapshot.source_sha,
            "engine_config_hash": snapshot.engine_config_hash,
        },
    )
    session.add(bound_event)
    session.flush()
    event_time = bound_event.occurred_at

    session.add(
        ExecutionModeActivationRequest(
            preview_hash=snapshot.snapshot_hash,
            from_mode=ExecutionLifecycleMode.SANDBOX,
            target_mode=ExecutionLifecycleMode.CANARY,
            venue="LIGHTER",
            account=str(snapshot.account_index),
            capital_rub=Decimal(str(snapshot.payload_json["capital_amount"])),
            hard_caps_json=dict(snapshot.payload_json["hard_caps"]),
            blockers_json=[],
            config_hash=snapshot.engine_config_hash,
            status="APPLIED",
            idempotency_key="audit-confirm-1",
            outcome_mode=ExecutionLifecycleMode.CANARY,
            owner_confirmed_at=event_time,
        )
    )
    session.flush()

    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            event_time + timedelta(seconds=1),
            status="TRIGGERED",
            quality_status="PASS",
            score=Decimal("82"),
            strategy_version=snapshot.strategy_version,
        )
    )
    risk = RiskSnapshot(risk_equity=Decimal("100000"))
    session.add_all([idea, risk])
    session.flush()
    intent = create_execution_intent(
        session,
        request=ExecutionIntentRequest(
            idea_id=idea.id,
            instrument_id=idea.instrument_id,
            strategy_version=idea.strategy_version,
            risk_policy_snapshot_id=risk.id,
            risk_override_id=None,
            venue="LIGHTER",
            account=str(snapshot.account_index),
            planned_quantity=Decimal("1"),
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
    ).intent
    session.flush()

    order = ExecutionOrder(
        intent_id=intent.id,
        client_order_id=f"audit-order-{intent.id.hex}",
        provider_order_id="provider-order-audit",
        side="BUY",
        order_type="ENTRY",
        status="ACKNOWLEDGED",
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
        stop_price=None,
        submitted_at=event_time + timedelta(seconds=2),
        acknowledged_at=event_time + timedelta(seconds=2, milliseconds=100),
    )
    session.add(order)
    session.flush()
    session.add_all(
        [
            ExecutionFill(
                intent_id=intent.id,
                order_id=order.id,
                provider_fill_id="audit-fill-1",
                quantity=Decimal("1"),
                price=Decimal("101"),
                fee_amount=Decimal("0.1"),
                fee_currency="USDC",
                filled_at=event_time + timedelta(seconds=3),
            ),
            ExecutionProtection(
                intent_id=intent.id,
                order_id=order.id,
                protection_type="STOP",
                status="ACTIVE",
                provider_order_id="provider-stop-audit",
                quantity=Decimal("1"),
                stop_price=Decimal("95"),
                armed_at=event_time + timedelta(seconds=3, milliseconds=200),
                last_reconciled_at=event_time + timedelta(seconds=4),
            ),
            ExecutionReconciliationEvent(
                intent_id=intent.id,
                event_type="POST_PROTECTION",
                outcome="MATCHED",
                detail_json={"audit": "matched"},
                occurred_at=event_time + timedelta(seconds=4),
            ),
        ]
    )
    session.flush()


def test_correlation_rejects_malformed_or_unknown_snapshot(session) -> None:
    from app.execution.canary_correlation import (
        CanaryCorrelationError,
        build_canary_correlation_report,
    )

    for value in ("bad", "f" * 64):
        with pytest.raises(CanaryCorrelationError):
            build_canary_correlation_report(session, snapshot_hash=value)


def test_correlation_is_incomplete_without_owner_activation_and_execution(session) -> None:
    from app.execution.canary_correlation import build_canary_correlation_report

    snapshot, refs = _snapshot(session)
    _evidence(session, snapshot, refs)

    report = build_canary_correlation_report(
        session,
        snapshot_hash=snapshot.snapshot_hash,
    )

    assert report.status == "INCOMPLETE"
    assert report.snapshot_hash == snapshot.snapshot_hash
    assert report.correlation_id == snapshot.correlation_id
    assert report.credential_generation_found is True
    assert report.verified_evidence_ref_count == 7
    assert report.activation_request_id is None
    assert report.mode_event_id is None
    assert report.execution_intent_count == 0
    assert "ACTIVATION_REQUEST_BINDING_MISSING" in report.blockers
    assert "CANARY_MODE_EVENT_BINDING_MISSING" in report.blockers
    assert "CANARY_EXECUTION_EVIDENCE_MISSING" in report.blockers


def test_correlation_detects_missing_or_mismatched_evidence_reference(session) -> None:
    from app.execution.canary_correlation import build_canary_correlation_report

    snapshot, refs = _snapshot(session)
    reduced = dict(refs)
    reduced.pop("security_scan")
    _evidence(session, snapshot, reduced)

    report = build_canary_correlation_report(
        session,
        snapshot_hash=snapshot.snapshot_hash,
    )

    assert report.status == "INCOMPLETE"
    assert report.verified_evidence_ref_count == 6
    assert "CANARY_EVIDENCE_BINDING_INCOMPLETE" in report.blockers


def test_correlation_complete_chain_is_exact_hash_scope_and_secret_free(session, instrument) -> None:
    from app.execution.canary_correlation import build_canary_correlation_report

    snapshot, refs = _snapshot(session, instrument_id=instrument.instrument_id)
    _evidence(session, snapshot, refs)
    _execution_chain(session, instrument, snapshot)

    report = build_canary_correlation_report(
        session,
        snapshot_hash=snapshot.snapshot_hash,
    )

    assert report.status == "COMPLETE", report.blockers
    assert report.blockers == ()
    assert report.activation_request_id is not None
    assert report.mode_event_id is not None
    assert report.execution_intent_count == 1
    assert report.order_count == 1
    assert report.fill_count == 1
    assert report.active_protection_count == 1
    assert report.reconciliation_event_count == 1
    public = report.to_public_dict()
    text = repr(public).lower()
    assert "private_key" not in text
    assert "api_private_key" not in text
    assert "signed_payload" not in text
    assert public["source_sha"] == snapshot.source_sha
    assert public["engine_config_hash"] == snapshot.engine_config_hash
