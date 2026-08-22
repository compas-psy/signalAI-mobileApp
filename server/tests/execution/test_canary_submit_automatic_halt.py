from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.config import get_config
from app.execution.enums import ExecutionKillSwitchLevel, ExecutionLifecycleMode
from app.execution.kill_switch import get_execution_kill_switch_level
from app.execution.mode import ModeChangeAuthorization, change_execution_mode, get_execution_mode
from app.integration_secrets import BY_SLOT, save_secret


LIVE_VALUES = {
    "account_index": "42",
    "api_key_index": "7",
    "api_private_key": "ab" * 32,
}
SOURCE_SHA = "a" * 40


def _set_mode(session, target: ExecutionLifecycleMode) -> None:
    current = get_execution_mode(session).mode
    if current == target:
        return
    change_execution_mode(
        session,
        target=target,
        actor="test",
        reason="canary automatic halt setup",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test setup authorization",
            detail_json={"test_only": True},
        ),
    )
    session.flush()


def _policy(generation_id: str):
    from app.execution.canary_policy import CanaryPolicy

    now = datetime.now(UTC)
    return CanaryPolicy(
        policy_version="canary-v1",
        source_sha=SOURCE_SHA,
        engine_config_hash=get_config().config_hash,
        strategy_family="TREND_PULLBACK",
        strategy_version="trend-pullback-v2",
        credential_generation_id=generation_id,
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
        evidence_refs={
            "strategy_performance": "strategy-evidence-1",
            "shadow": "shadow-evidence-1",
            "testnet": "testnet-evidence-1",
            "protection_reconciliation": "protection-evidence-1",
            "kill_switch_drill": "kill-switch-evidence-1",
            "security_scan": "security-scan-1",
            "operational_health": "ops-evidence-1",
        },
        valid_until=now + timedelta(hours=1),
    )


def _snapshot(session):
    from app.execution.canary_policy import (
        current_lighter_trade_generation,
        persist_canary_policy_snapshot,
    )

    save_secret(session, BY_SLOT["lighter_trade"], LIVE_VALUES, actor="auto_halt_test")
    generation = current_lighter_trade_generation(session)
    assert generation is not None
    snapshot = persist_canary_policy_snapshot(
        session,
        _policy(generation.generation_id),
        actor="auto_halt_test",
        correlation_id=f"auto-halt-{uuid.uuid4()}",
    )
    session.flush()
    return snapshot


def _runtime():
    from app.execution.canary_preflight import CanaryRuntimeContext

    return CanaryRuntimeContext(
        source_sha=SOURCE_SHA,
        config_hash=get_config().config_hash,
        paper_only=False,
    )


def _proposal():
    from app.execution.canary_limits import CanaryEntryProposal

    return CanaryEntryProposal(
        instrument_id="CRYPTO:PERP:BTCUSDT",
        market_index=1,
        order_notional=Decimal("1000"),
        leverage=Decimal("1.5"),
        max_loss_amount=Decimal("100"),
        creates_position=False,
    )


def _exposure():
    from app.execution.canary_limits import CanaryExposureState

    return CanaryExposureState(
        gross_notional=Decimal("2000"),
        instrument_notional=Decimal("500"),
        open_positions=1,
        entry_orders=0,
        order_count=3,
        trade_count=2,
        daily_loss=Decimal("100"),
        total_loss=Decimal("200"),
    )


def _dynamic():
    from app.execution.canary_limits import CanaryDynamicLimits

    return CanaryDynamicLimits(
        risk_engine_order_notional=Decimal("2200"),
        account_order_notional=Decimal("1800"),
        provider_order_notional=Decimal("1600"),
    )


def _guard(session, snapshot, **overrides):
    from app.execution.canary_submit_guard import canary_submit_guard

    values = {
        "snapshot_hash": snapshot.snapshot_hash,
        "context_provider": _runtime,
        "proposal": _proposal(),
        "exposure_provider": _exposure,
        "dynamic_limits_provider": _dynamic,
    }
    values.update(overrides)
    return canary_submit_guard(session, **values)


def test_source_drift_halts_new_entries_before_guard_yields_without_downshift(session) -> None:
    snapshot = _snapshot(session)
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    with _guard(
        session,
        snapshot,
        context_provider=lambda: replace(_runtime(), source_sha="b" * 40),
    ) as result:
        assert "DEPLOYED_SOURCE_SHA_MISMATCH" in result.blockers
        assert getattr(result, "automatic_halt_applied", False) is True
        assert getattr(result, "automatic_safety_triggers", ()) == (
            "SOURCE_CONFIG_POLICY_DRIFT",
        )
        assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.HALT_NEW_ENTRIES
        assert get_execution_mode(session).mode is ExecutionLifecycleMode.CANARY


def test_allowlist_or_cap_breach_halts_globally_not_just_this_order(session) -> None:
    snapshot = _snapshot(session)
    _set_mode(session, ExecutionLifecycleMode.CANARY)
    foreign = replace(_proposal(), instrument_id="CRYPTO:PERP:SOLUSDT")

    with _guard(session, snapshot, proposal=foreign) as result:
        assert "INSTRUMENT_NOT_ALLOWED" in result.blockers
        assert getattr(result, "automatic_halt_applied", False) is True
        assert getattr(result, "automatic_safety_triggers", ()) == (
            "CAP_OR_ALLOWLIST_BREACH",
        )
        assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.HALT_NEW_ENTRIES
        assert get_execution_mode(session).mode is ExecutionLifecycleMode.CANARY


def test_governance_blockers_do_not_trip_automatic_halt(session) -> None:
    snapshot = _snapshot(session)
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    with _guard(session, snapshot) as result:
        assert result.blockers == (
            "ADR_0002_NOT_ACCEPTED",
            "CANARY_OWNER_STEP_UP_NOT_IMPLEMENTED",
        )
        assert getattr(result, "automatic_halt_applied", False) is False
        assert getattr(result, "automatic_safety_triggers", ()) == ()
        assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.CLEAR


def test_untrusted_bad_snapshot_hash_cannot_dos_canary_with_global_halt(session) -> None:
    snapshot = _snapshot(session)
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    with _guard(session, snapshot, snapshot_hash="not-a-policy-hash") as result:
        assert result.blockers == ("CANARY_POLICY_HASH_INVALID",)
        assert getattr(result, "automatic_halt_applied", False) is False
        assert getattr(result, "automatic_safety_triggers", ()) == ()
        assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.CLEAR
