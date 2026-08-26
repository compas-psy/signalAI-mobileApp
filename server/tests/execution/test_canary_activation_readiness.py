from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.config import get_config
from app.execution.enums import ExecutionLifecycleMode
from app.execution.mode import ModeChangeAuthorization, change_execution_mode, get_execution_mode
from app.integration_secrets import BY_SLOT, save_secret
from app.models import ExecutionModeActivationRequest, ExecutionModeState


def _set_sandbox(session) -> None:
    change_execution_mode(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
        actor="test",
        reason="canary readiness setup",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test setup authorization",
            detail_json={"test_only": True},
        ),
    )
    session.flush()


def _snapshot(session, *, source_sha: str = "a" * 40):
    from app.execution.canary_policy import (
        CanaryPolicy,
        persist_canary_policy_snapshot,
        record_lighter_trade_generation,
    )

    save_secret(
        session,
        BY_SLOT["lighter_trade"],
        {"account_index": "42", "api_key_index": "7", "api_private_key": "ab" * 32},
        actor="owner_step_up_test",
    )
    generation = record_lighter_trade_generation(
        session,
        action="CREATED",
        actor="owner_step_up_test",
        account_index=42,
        api_key_index=7,
    )
    now = datetime.now(UTC)
    policy = CanaryPolicy(
        policy_version="canary-v1",
        source_sha=source_sha,
        engine_config_hash=get_config().config_hash,
        strategy_family="TREND_PULLBACK",
        strategy_version="trend-pullback-v2",
        credential_generation_id=generation.generation_id,
        account_index=42,
        api_key_index=7,
        market_allowlist=(1,),
        instrument_allowlist=("CRYPTO:PERP:BTCUSDT",),
        capital_amount=Decimal("100"),
        capital_currency="USDC",
        valuation_source="lighter_account_equity_usdc",
        valuation_observed_at=now - timedelta(minutes=1),
        valuation_rule="direct_usdc_collateral",
        hard_caps={
            "max_order_notional": "10",
            "max_instrument_notional": "25",
            "max_gross_notional": "25",
            "max_open_positions": 1,
            "max_entry_orders": 1,
            "max_leverage": "1",
            "daily_loss_limit": "3",
            "total_loss_limit": "7",
            "max_order_count": 20,
            "max_trade_count": 6,
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
    snapshot = persist_canary_policy_snapshot(
        session,
        policy,
        actor="owner_step_up_test",
        correlation_id="activation-readiness-test",
    )
    session.flush()
    return snapshot


def test_readiness_binds_approved_profile_but_preserves_missing_evidence(session) -> None:
    from app.execution.canary_activation import build_canary_activation_readiness
    from app.execution.canary_preflight import CanaryRuntimeContext

    _set_sandbox(session)
    snapshot = _snapshot(session)
    mode_before = get_execution_mode(session)

    result = build_canary_activation_readiness(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: CanaryRuntimeContext(
            source_sha=snapshot.source_sha,
            config_hash=snapshot.engine_config_hash,
            paper_only=False,
        ),
    )

    assert result.snapshot_hash == snapshot.snapshot_hash
    assert result.from_mode is ExecutionLifecycleMode.SANDBOX
    assert result.target_mode is ExecutionLifecycleMode.CANARY
    assert result.venue == "LIGHTER"
    assert result.instrument_allowlist == ("CRYPTO:PERP:BTCUSDT",)
    assert result.capital_amount == Decimal("100")
    assert result.capital_currency == "USDC"
    assert result.hard_caps["max_order_notional"] == "10"
    assert result.hard_caps["max_gross_notional"] == "25"
    assert result.challenge_ttl_seconds == 300
    assert result.challenge_issuable is False
    assert "ADR_0002_NOT_ACCEPTED" not in result.blockers
    assert "CANARY_OWNER_STEP_UP_NOT_IMPLEMENTED" not in result.blockers
    assert "CANARY_EVIDENCE_MISSING:strategy_performance" in result.blockers
    assert "FINAL_OWNER_ACTIVATION_REQUIRED" not in result.blockers
    assert get_execution_mode(session) == mode_before
    assert session.query(ExecutionModeActivationRequest).count() == 0


def test_structurally_complete_approved_profile_stops_only_at_final_owner_activation(
    session, monkeypatch
) -> None:
    import app.execution.canary_activation as activation
    from app.execution.canary_preflight import CanaryPreflightResult, CanaryRuntimeContext

    _set_sandbox(session)
    snapshot = _snapshot(session)
    monkeypatch.setattr(
        activation,
        "evaluate_canary_preflight",
        lambda *args, **kwargs: CanaryPreflightResult(
            eligible_for_canary=False,
            structural_checks_passed=True,
            blockers=("ADR_0002_NOT_ACCEPTED", "CANARY_OWNER_STEP_UP_NOT_IMPLEMENTED"),
        ),
    )

    result = activation.build_canary_activation_readiness(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: CanaryRuntimeContext(
            source_sha=snapshot.source_sha,
            config_hash=snapshot.engine_config_hash,
            paper_only=False,
        ),
    )

    assert result.structural_checks_passed is True
    assert result.challenge_ttl_seconds == 300
    assert result.challenge_issuable is False
    assert result.blockers == ("FINAL_OWNER_ACTIVATION_REQUIRED",)
    assert session.query(ExecutionModeActivationRequest).count() == 0
    assert get_execution_mode(session).mode is ExecutionLifecycleMode.SANDBOX


def test_readiness_preserves_structural_preflight_blockers_and_never_authorizes(session) -> None:
    from app.execution.canary_activation import build_canary_activation_readiness
    from app.execution.canary_preflight import CanaryRuntimeContext

    _set_sandbox(session)
    snapshot = _snapshot(session, source_sha="b" * 40)

    result = build_canary_activation_readiness(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: CanaryRuntimeContext(
            source_sha="c" * 40,
            config_hash=snapshot.engine_config_hash,
            paper_only=False,
        ),
    )

    assert result.structural_checks_passed is False
    assert result.challenge_issuable is False
    assert "DEPLOYED_SOURCE_SHA_MISMATCH" in result.blockers
    assert "FINAL_OWNER_ACTIVATION_REQUIRED" not in result.blockers
    assert session.query(ExecutionModeActivationRequest).count() == 0
    assert get_execution_mode(session).mode is ExecutionLifecycleMode.SANDBOX


def test_readiness_rejects_malformed_or_unknown_snapshot_without_mutation(session) -> None:
    from app.execution.canary_activation import CanaryActivationReadinessError, build_canary_activation_readiness
    from app.execution.canary_preflight import CanaryRuntimeContext

    _set_sandbox(session)

    provider = lambda: CanaryRuntimeContext(
        source_sha="a" * 40,
        config_hash=get_config().config_hash,
        paper_only=False,
    )
    for bad_hash in ("bad", "f" * 64):
        try:
            build_canary_activation_readiness(
                session,
                snapshot_hash=bad_hash,
                context_provider=provider,
            )
        except CanaryActivationReadinessError:
            pass
        else:
            raise AssertionError("invalid or unknown snapshot must fail closed")

    assert session.query(ExecutionModeActivationRequest).count() == 0
    assert get_execution_mode(session).mode is ExecutionLifecycleMode.SANDBOX


def test_readiness_does_not_materialize_execution_mode_singleton_on_read(session) -> None:
    from app.execution.canary_activation import build_canary_activation_readiness
    from app.execution.canary_preflight import CanaryRuntimeContext

    assert session.get(ExecutionModeState, 1) is None
    snapshot = _snapshot(session)
    assert session.get(ExecutionModeState, 1) is None

    result = build_canary_activation_readiness(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: CanaryRuntimeContext(
            source_sha=snapshot.source_sha,
            config_hash=snapshot.engine_config_hash,
            paper_only=False,
        ),
    )

    assert result.from_mode is ExecutionLifecycleMode.PAPER
    assert "EXECUTION_MODE_NOT_SANDBOX" in result.blockers
    assert result.challenge_issuable is False
    assert session.get(ExecutionModeState, 1) is None
    assert session.query(ExecutionModeActivationRequest).count() == 0
