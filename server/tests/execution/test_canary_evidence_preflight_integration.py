from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.execution.canary_evidence import CanaryEvidenceBindingResult
from app.execution.canary_policy import (
    CanaryPolicy,
    current_lighter_trade_generation,
    persist_canary_policy_snapshot,
)
from app.execution.enums import ExecutionLifecycleMode
from app.execution.mode import ModeChangeAuthorization, change_execution_mode, get_execution_mode
from app.integration_secrets import BY_SLOT, save_secret


SOURCE_SHA = "a" * 40
CONFIG_HASH = "b" * 64


def _set_sandbox(session) -> None:
    current = get_execution_mode(session).mode
    if current == ExecutionLifecycleMode.SANDBOX:
        return
    change_execution_mode(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
        actor="test",
        reason="preflight integration setup",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test-only setup",
            detail_json={"test_only": True},
        ),
    )
    session.flush()


def _persist_policy(session):
    save_secret(
        session,
        BY_SLOT["lighter_trade"],
        {
            "account_index": "42",
            "api_key_index": "7",
            "api_private_key": "ab" * 32,
        },
        actor="owner_step_up_test",
    )
    generation = current_lighter_trade_generation(session)
    assert generation is not None
    now = datetime.now(UTC)
    refs = {
        key: str(uuid.uuid4())
        for key in (
            "strategy_performance",
            "shadow",
            "testnet",
            "protection_reconciliation",
            "kill_switch_drill",
            "security_scan",
            "operational_health",
        )
    }
    policy = CanaryPolicy(
        policy_version="canary-v1",
        source_sha=SOURCE_SHA,
        engine_config_hash=CONFIG_HASH,
        strategy_family="TREND_PULLBACK",
        strategy_version="trend-pullback-v2",
        credential_generation_id=generation.generation_id,
        account_index=42,
        api_key_index=7,
        market_allowlist=(1,),
        instrument_allowlist=("CRYPTO:PERP:BTCUSDT",),
        capital_amount=Decimal("10000"),
        capital_currency="RUB",
        valuation_source="owner_preapproved",
        valuation_observed_at=now - timedelta(minutes=1),
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
        valid_until=now + timedelta(hours=1),
    )
    snapshot = persist_canary_policy_snapshot(
        session,
        policy,
        actor="owner_step_up_test",
        correlation_id=f"evidence-integration-{uuid.uuid4()}",
    )
    session.flush()
    return snapshot, refs


def _context():
    from app.execution.canary_preflight import CanaryRuntimeContext

    return CanaryRuntimeContext(
        source_sha=SOURCE_SHA,
        config_hash=CONFIG_HASH,
        paper_only=False,
    )


def test_preflight_consumes_exact_evidence_refs_but_still_cannot_authorize_canary(
    session, monkeypatch
) -> None:
    import app.execution.canary_preflight as preflight

    snapshot, refs = _persist_policy(session)
    _set_sandbox(session)
    seen = {}

    def evidence_result(db, **kwargs):
        assert db is session
        seen.update(kwargs)
        return CanaryEvidenceBindingResult(
            ready=True,
            blockers=(),
            snapshot_ids=tuple(refs.values()),
        )

    monkeypatch.setattr(preflight, "evaluate_canary_evidence_bindings", evidence_result)
    result = preflight.evaluate_canary_preflight(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=_context,
    )

    assert result.eligible_for_canary is False
    assert result.structural_checks_passed is True
    assert result.blockers == (
        "ADR_0002_NOT_ACCEPTED",
        "CANARY_OWNER_STEP_UP_NOT_IMPLEMENTED",
    )
    assert seen["evidence_refs"] == refs
    assert seen["source_sha"] == SOURCE_SHA
    assert seen["config_hash"] == CONFIG_HASH
    assert seen["strategy_family"] == "TREND_PULLBACK"
    assert seen["strategy_version"] == "trend-pullback-v2"
    assert seen["venue"] == "LIGHTER"


def test_preflight_propagates_evidence_blocker_as_structural_failure(
    session, monkeypatch
) -> None:
    import app.execution.canary_preflight as preflight

    snapshot, _ = _persist_policy(session)
    _set_sandbox(session)
    monkeypatch.setattr(
        preflight,
        "evaluate_canary_evidence_bindings",
        lambda *args, **kwargs: CanaryEvidenceBindingResult(
            ready=False,
            blockers=("CANARY_EVIDENCE_SECURITY_SCAN_STALE",),
            snapshot_ids=(),
        ),
    )

    result = preflight.evaluate_canary_preflight(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=_context,
    )
    assert result.eligible_for_canary is False
    assert result.structural_checks_passed is False
    assert result.blockers == ("CANARY_EVIDENCE_SECURITY_SCAN_STALE",)
