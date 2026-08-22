from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.config import get_config
from app.execution.enums import ExecutionLifecycleMode
from app.execution.mode import ModeChangeAuthorization, change_execution_mode, get_execution_mode
from app.integration_secrets import BY_SLOT, delete_secret, save_secret


LIVE_VALUES = {
    "account_index": "42",
    "api_key_index": "7",
    "api_private_key": "ab" * 32,
}


def _set_mode(session, target: ExecutionLifecycleMode) -> None:
    current = get_execution_mode(session).mode
    if current == target:
        return
    change_execution_mode(
        session,
        target=target,
        actor="test",
        reason="preflight setup",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test setup authorization",
            detail_json={"test_only": True},
        ),
    )
    session.flush()


def _runtime(*, source_sha: str, paper_only: bool = False):
    from app.execution.canary_preflight import CanaryRuntimeContext

    return CanaryRuntimeContext(
        source_sha=source_sha,
        config_hash=get_config().config_hash,
        paper_only=paper_only,
    )


def _policy(generation_id: str, *, source_sha: str, valid_until: datetime):
    from app.execution.canary_policy import CanaryPolicy

    now = datetime.now(UTC)
    return CanaryPolicy(
        policy_version="canary-v1",
        source_sha=source_sha,
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
        valid_until=valid_until,
    )


def _persist_policy(session, *, source_sha: str, valid_until: datetime):
    from app.execution.canary_policy import (
        current_lighter_trade_generation,
        persist_canary_policy_snapshot,
    )

    save_secret(session, BY_SLOT["lighter_trade"], LIVE_VALUES, actor="owner_step_up_test")
    generation = current_lighter_trade_generation(session)
    assert generation is not None
    snapshot = persist_canary_policy_snapshot(
        session,
        _policy(
            generation.generation_id,
            source_sha=source_sha,
            valid_until=valid_until,
        ),
        actor="owner_step_up_test",
        correlation_id=f"preflight-{uuid.uuid4()}",
    )
    session.flush()
    return snapshot


def test_preflight_is_read_only_and_never_eligible_while_adr_and_step_up_are_unresolved(session) -> None:
    from app.execution.canary_preflight import evaluate_canary_preflight

    source_sha = "a" * 40
    snapshot = _persist_policy(
        session,
        source_sha=source_sha,
        valid_until=datetime.now(UTC) + timedelta(hours=1),
    )
    _set_mode(session, ExecutionLifecycleMode.SANDBOX)
    mode_before = get_execution_mode(session)

    result = evaluate_canary_preflight(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: _runtime(source_sha=source_sha),
    )

    assert result.eligible_for_canary is False
    assert result.structural_checks_passed is True
    assert result.blockers == (
        "ADR_0002_NOT_ACCEPTED",
        "CANARY_OWNER_STEP_UP_NOT_IMPLEMENTED",
        "CANARY_EVIDENCE_BINDING_NOT_IMPLEMENTED",
    )
    assert get_execution_mode(session) == mode_before


def test_preflight_fails_closed_on_missing_or_malformed_policy_hash(session) -> None:
    from app.execution.canary_preflight import CanaryPreflightError, evaluate_canary_preflight

    try:
        evaluate_canary_preflight(
            session,
            snapshot_hash="not-a-sha",
            context_provider=lambda: _runtime(source_sha="a" * 40),
        )
    except CanaryPreflightError as exc:
        assert str(exc) == "snapshot_hash must be a SHA-256 hex digest"
    else:
        raise AssertionError("malformed snapshot hash must fail closed")

    missing = evaluate_canary_preflight(
        session,
        snapshot_hash="f" * 64,
        context_provider=lambda: _runtime(source_sha="a" * 40),
    )
    assert missing.eligible_for_canary is False
    assert missing.structural_checks_passed is False
    assert "CANARY_POLICY_NOT_FOUND" in missing.blockers


def test_preflight_binds_exact_deployed_source_config_and_sandbox_mode(session) -> None:
    from app.execution.canary_preflight import evaluate_canary_preflight

    source_sha = "b" * 40
    snapshot = _persist_policy(
        session,
        source_sha=source_sha,
        valid_until=datetime.now(UTC) + timedelta(hours=1),
    )

    wrong_source = evaluate_canary_preflight(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: _runtime(source_sha="c" * 40),
    )
    assert "EXECUTION_MODE_NOT_SANDBOX" in wrong_source.blockers
    assert "DEPLOYED_SOURCE_SHA_MISMATCH" in wrong_source.blockers

    _set_mode(session, ExecutionLifecycleMode.SANDBOX)
    wrong_config = evaluate_canary_preflight(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: type(_runtime(source_sha=source_sha))(
            source_sha=source_sha,
            config_hash="d" * 64,
            paper_only=False,
        ),
    )
    assert "ENGINE_CONFIG_HASH_MISMATCH" in wrong_config.blockers


def test_preflight_blocks_unknown_runtime_sha_paper_only_and_expired_policy(session) -> None:
    from app.execution.canary_preflight import CanaryRuntimeContext, evaluate_canary_preflight

    source_sha = "e" * 40
    snapshot = _persist_policy(
        session,
        source_sha=source_sha,
        valid_until=datetime.now(UTC) - timedelta(seconds=1),
    )
    _set_mode(session, ExecutionLifecycleMode.SANDBOX)

    result = evaluate_canary_preflight(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: CanaryRuntimeContext(
            source_sha="",
            config_hash=get_config().config_hash,
            paper_only=True,
        ),
    )
    assert "DEPLOYED_SOURCE_SHA_UNKNOWN" in result.blockers
    assert "RISK_PAPER_ONLY" in result.blockers
    assert "CANARY_POLICY_EXPIRED" in result.blockers
    assert result.structural_checks_passed is False


def test_preflight_rechecks_current_live_credential_generation_without_exposing_secret(session) -> None:
    from app.execution.canary_preflight import evaluate_canary_preflight

    source_sha = "1" * 40
    snapshot = _persist_policy(
        session,
        source_sha=source_sha,
        valid_until=datetime.now(UTC) + timedelta(hours=1),
    )
    _set_mode(session, ExecutionLifecycleMode.SANDBOX)

    rotated = dict(LIVE_VALUES)
    rotated["api_private_key"] = "cd" * 32
    save_secret(session, BY_SLOT["lighter_trade"], rotated, actor="owner_step_up_test")

    result = evaluate_canary_preflight(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: _runtime(source_sha=source_sha),
    )
    assert "CREDENTIAL_GENERATION_MISMATCH" in result.blockers
    rendered = json.dumps(result.to_public_dict(), sort_keys=True).lower()
    assert LIVE_VALUES["api_private_key"] not in rendered
    assert rotated["api_private_key"] not in rendered
    assert "api_private_key" not in rendered


def test_preflight_blocks_revoked_or_missing_live_credential(session) -> None:
    from app.execution.canary_preflight import evaluate_canary_preflight

    source_sha = "2" * 40
    snapshot = _persist_policy(
        session,
        source_sha=source_sha,
        valid_until=datetime.now(UTC) + timedelta(hours=1),
    )
    _set_mode(session, ExecutionLifecycleMode.SANDBOX)
    delete_secret(session, "lighter_trade", actor="owner_step_up_test")

    result = evaluate_canary_preflight(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: _runtime(source_sha=source_sha),
    )
    assert "LIGHTER_LIVE_CREDENTIAL_NOT_CONFIGURED" in result.blockers
    assert "LIVE_CREDENTIAL_GENERATION_MISSING_OR_REVOKED" in result.blockers


def test_preflight_detects_snapshot_payload_hash_tampering_before_any_authorization(session) -> None:
    from app.execution.canary_preflight import evaluate_canary_preflight

    source_sha = "3" * 40
    snapshot = _persist_policy(
        session,
        source_sha=source_sha,
        valid_until=datetime.now(UTC) + timedelta(hours=1),
    )
    # Bypass ORM mutability only for this corruption simulation: the production
    # append-only trigger prevents UPDATE, so calculate the expected hash locally
    # and then feed the verifier a direct helper instead of mutating the row.
    tampered = dict(snapshot.payload_json)
    tampered["capital_amount"] = "999999"
    canonical = json.dumps(tampered, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(canonical).hexdigest() != snapshot.snapshot_hash

    result = evaluate_canary_preflight(
        session,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: _runtime(source_sha=source_sha),
        payload_override_for_test=tampered,
    )
    assert "CANARY_POLICY_HASH_INTEGRITY_FAILED" in result.blockers
    assert result.structural_checks_passed is False


def test_release_pipeline_exports_exact_qa_sha_to_api_and_execution_runtime() -> None:
    repo = Path(__file__).resolve().parents[3]
    compose = (repo / "server" / "docker-compose.yml").read_text(encoding="utf-8")
    deploy = (repo / ".github" / "workflows" / "deploy-release.yml").read_text(encoding="utf-8")

    assert compose.count("SIGNALAI_SOURCE_SHA: ${SIGNALAI_SOURCE_SHA:-}") >= 2
    assert "SIGNALAI_SOURCE_SHA='$SOURCE_SHA' bash /tmp/signalai-update.sh" in deploy
