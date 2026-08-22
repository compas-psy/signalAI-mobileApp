from __future__ import annotations

import contextlib
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.config import get_config
from app.execution.enums import ExecutionLifecycleMode
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
        reason="canary submit guard setup",
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

    save_secret(session, BY_SLOT["lighter_trade"], LIVE_VALUES, actor="submit_guard_test")
    generation = current_lighter_trade_generation(session)
    assert generation is not None
    snapshot = persist_canary_policy_snapshot(
        session,
        _policy(generation.generation_id),
        actor="submit_guard_test",
        correlation_id=f"submit-guard-{uuid.uuid4()}",
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


def test_clean_submit_facts_remain_non_authorizing_until_adr_and_owner_step_up(session) -> None:
    snapshot = _snapshot(session)
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    with _guard(session, snapshot) as result:
        assert result.structural_checks_passed is True
        assert result.provider_io_eligible is False
        assert result.blockers == (
            "ADR_0002_NOT_ACCEPTED",
            "CANARY_OWNER_STEP_UP_NOT_IMPLEMENTED",
        )
        assert result.effective_order_notional_cap == Decimal("1600")


def test_authoritative_runtime_and_limit_reads_happen_while_execution_lock_is_held(
    session, monkeypatch
) -> None:
    import app.execution.canary_submit_guard as guard_module

    snapshot = _snapshot(session)
    _set_mode(session, ExecutionLifecycleMode.CANARY)
    held = False
    observed: list[str] = []

    @contextlib.contextmanager
    def fake_lock(_db):
        nonlocal held
        assert held is False
        held = True
        try:
            yield
        finally:
            held = False

    def runtime():
        assert held is True
        observed.append("runtime")
        return _runtime()

    def exposure():
        assert held is True
        observed.append("exposure")
        return _exposure()

    def dynamic():
        assert held is True
        observed.append("dynamic")
        return _dynamic()

    monkeypatch.setattr(guard_module, "execution_control_lock", fake_lock)
    with guard_module.canary_submit_guard(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=runtime,
        proposal=_proposal(),
        exposure_provider=exposure,
        dynamic_limits_provider=dynamic,
    ) as result:
        assert held is True
        assert result.structural_checks_passed is True
    assert held is False
    assert observed == ["runtime", "exposure", "dynamic"]


def test_mode_and_kill_switch_are_rechecked_inside_submit_boundary(session) -> None:
    from app.execution.kill_switch import get_execution_kill_switch_level

    snapshot = _snapshot(session)
    _set_mode(session, ExecutionLifecycleMode.SANDBOX)

    with _guard(session, snapshot) as wrong_mode:
        assert wrong_mode.structural_checks_passed is False
        assert "EXECUTION_MODE_NOT_CANARY" in wrong_mode.blockers

    _set_mode(session, ExecutionLifecycleMode.CANARY)
    assert get_execution_kill_switch_level(session).value == "CLEAR"
    session.execute(
        text(
            "UPDATE risk_state SET kill_switch = true, "
            "kill_switch_level = 'HALT_NEW_ENTRIES', kill_switch_reason = 'test' "
            "WHERE id = 1"
        )
    )
    session.flush()
    with _guard(session, snapshot) as halted:
        assert halted.structural_checks_passed is False
        assert "EXECUTION_KILL_SWITCH_NOT_CLEAR" in halted.blockers


def test_source_config_and_live_credential_generation_drift_fail_closed(session) -> None:
    snapshot = _snapshot(session)
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    with _guard(
        session,
        snapshot,
        context_provider=lambda: replace(_runtime(), source_sha="b" * 40),
    ) as wrong_source:
        assert "DEPLOYED_SOURCE_SHA_MISMATCH" in wrong_source.blockers

    with _guard(
        session,
        snapshot,
        context_provider=lambda: replace(_runtime(), config_hash="c" * 64),
    ) as wrong_config:
        assert "ENGINE_CONFIG_HASH_MISMATCH" in wrong_config.blockers

    rotated = dict(LIVE_VALUES)
    rotated["api_private_key"] = "cd" * 32
    save_secret(session, BY_SLOT["lighter_trade"], rotated, actor="submit_guard_test")
    with _guard(session, snapshot) as wrong_generation:
        assert "CREDENTIAL_GENERATION_MISMATCH" in wrong_generation.blockers


def test_allowlist_caps_and_missing_dynamic_limit_are_enforced_at_submit_time(session) -> None:
    snapshot = _snapshot(session)
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    foreign = replace(_proposal(), instrument_id="CRYPTO:PERP:SOLUSDT")
    with _guard(session, snapshot, proposal=foreign) as decision:
        assert "INSTRUMENT_NOT_ALLOWED" in decision.blockers

    oversized = replace(_proposal(), order_notional=Decimal("1700"))
    with _guard(session, snapshot, proposal=oversized) as decision:
        assert "ORDER_NOTIONAL_LIMIT" in decision.blockers

    missing_provider_cap = replace(_dynamic(), provider_order_notional=None)
    with _guard(
        session,
        snapshot,
        dynamic_limits_provider=lambda: missing_provider_cap,
    ) as decision:
        assert "DYNAMIC_LIMIT_MISSING_OR_INVALID" in decision.blockers


def test_authoritative_fact_provider_failure_is_fail_closed(session) -> None:
    snapshot = _snapshot(session)
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    def broken_exposure():
        raise TimeoutError("account facts unavailable")

    with _guard(session, snapshot, exposure_provider=broken_exposure) as result:
        assert result.structural_checks_passed is False
        assert result.blockers == ("CANARY_EXPOSURE_UNAVAILABLE",)
        assert result.provider_io_eligible is False


def test_submit_guard_source_has_no_provider_sdk_or_network_sink() -> None:
    repo = Path(__file__).resolve().parents[3]
    source = (repo / "server" / "app" / "execution" / "canary_submit_guard.py").read_text(
        encoding="utf-8"
    )
    lowered = source.lower()
    assert "lighter_sdk" not in lowered
    assert "signerclient" not in lowered
    assert "sendtx" not in lowered
    assert "create_order(" not in lowered
    assert ".with_for_update()" in source
    assert "WHERE slot = 'lighter_trade' FOR UPDATE" in source
