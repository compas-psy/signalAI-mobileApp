from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from Crypto.Hash import SHA256
from Crypto.PublicKey import ECC
from Crypto.Signature import DSS
from sqlalchemy import select

from app.config import get_config
from app.device_enrollment import BootstrapPairingSession, pair_device
from app.execution.canary_activation import CanaryActivationReadiness
from app.execution.enums import ExecutionKillSwitchLevel, ExecutionLifecycleMode
from app.execution.kill_switch import set_execution_kill_switch
from app.execution.mode import ModeChangeAuthorization, change_execution_mode, get_execution_mode
from app.integration_secrets import BY_SLOT, save_secret
from app.models.device import DeviceCredential, OwnerStepUpChallenge
from app.models.execution import ExecutionModeEvent
from app.models.risk import AuditEvent


def _pairing(seed: str) -> BootstrapPairingSession:
    return BootstrapPairingSession(
        session_id=f"{seed:_<43}"[:43],
        verifier=(seed.encode("utf-8").hex() + "0" * 64)[:64],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        max_uses=1,
    )


def _enroll(session, suffix: str = "0001"):
    private = ECC.generate(curve="P-256")
    public_b64 = base64.b64encode(
        private.public_key().export_key(format="DER")
    ).decode("ascii")
    issued = pair_device(
        session,
        device_id=f"canary-owner-device-{suffix}",
        metadata={"platform": "android"},
        idempotency_key=f"canary-owner-pair-{suffix}",
        pairing_session=_pairing(f"canary-{suffix}"),
        owner_public_key_spki_b64=public_b64,
    )
    credential = session.scalar(
        select(DeviceCredential).where(
            DeviceCredential.device_id == issued.device_id,
            DeviceCredential.revoked_at.is_(None),
        )
    )
    assert credential is not None
    return private, credential


def _sign(private, message: str) -> str:
    signer = DSS.new(private, "fips-186-3", encoding="der")
    signature = signer.sign(SHA256.new(message.encode("utf-8")))
    return base64.b64encode(signature).decode("ascii")


def _set_sandbox(session) -> None:
    change_execution_mode(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
        actor="test",
        reason="canary owner activation setup",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test setup authorization",
            detail_json={"test_only": True},
        ),
    )
    session.flush()


def _snapshot(session):
    from app.execution.canary_policy import (
        CanaryPolicy,
        persist_canary_policy_snapshot,
        record_lighter_trade_generation,
    )

    save_secret(
        session,
        BY_SLOT["lighter_trade"],
        {"account_index": "42", "api_key_index": "7", "api_private_key": "ab" * 32},
        actor="canary_owner_test",
    )
    generation = record_lighter_trade_generation(
        session,
        action="CREATED",
        actor="canary_owner_test",
        account_index=42,
        api_key_index=7,
    )
    now = datetime.now(UTC)
    return persist_canary_policy_snapshot(
        session,
        CanaryPolicy(
            policy_version="canary-v1",
            source_sha="a" * 40,
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
        ),
        actor="canary_owner_test",
        correlation_id="canary-owner-activation-test",
    )


def _readiness(snapshot, *, blockers=("FINAL_OWNER_ACTIVATION_REQUIRED",), structural=True):
    payload = snapshot.payload_json
    return CanaryActivationReadiness(
        snapshot_hash=snapshot.snapshot_hash,
        from_mode=ExecutionLifecycleMode.SANDBOX,
        target_mode=ExecutionLifecycleMode.CANARY,
        venue="LIGHTER",
        strategy_family=str(payload["strategy_family"]),
        strategy_version=str(payload["strategy_version"]),
        account_index=int(payload["account_index"]),
        api_key_index=int(payload["api_key_index"]),
        market_allowlist=tuple(payload["market_allowlist"]),
        instrument_allowlist=tuple(payload["instrument_allowlist"]),
        capital_amount=Decimal(str(payload["capital_amount"])),
        capital_currency=str(payload["capital_currency"]),
        valuation_source=str(payload["valuation_source"]),
        valuation_observed_at=str(payload["valuation_observed_at"]),
        valuation_rule=str(payload["valuation_rule"]),
        hard_caps=dict(payload["hard_caps"]),
        valid_until=str(payload["valid_until"]),
        structural_checks_passed=structural,
        challenge_ttl_seconds=300,
        challenge_issuable=False,
        blockers=tuple(blockers),
    )


def test_challenge_binds_exact_canary_policy_and_five_minute_owner_step_up(session, monkeypatch):
    from app.execution import canary_owner_activation as activation

    _set_sandbox(session)
    private, credential = _enroll(session)
    snapshot = _snapshot(session)
    monkeypatch.setattr(
        activation,
        "build_canary_activation_readiness",
        lambda *args, **kwargs: _readiness(snapshot),
    )

    issued = activation.issue_canary_owner_activation_challenge(
        session,
        credential_id=credential.id,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: None,
    )

    row = session.get(OwnerStepUpChallenge, issued.challenge_id)
    assert row is not None
    assert row.purpose == "CANARY_V1_ACTIVATE"
    assert row.expires_at - row.issued_at == timedelta(seconds=300)
    assert issued.payload["canary_policy_snapshot_hash"] == snapshot.snapshot_hash
    assert issued.payload["capital_amount"] == "100"
    assert issued.payload["capital_currency"] == "USDC"
    assert issued.payload["instrument_allowlist"] == ["CRYPTO:PERP:BTCUSDT"]
    assert issued.payload["hard_caps"]["max_gross_notional"] == "25"
    assert issued.payload["from_mode"] == "SANDBOX"
    assert issued.payload["target_mode"] == "CANARY"
    assert issued.payload["owner_action"] == "ACTIVATE_CANARY_V1"
    assert _sign(private, issued.message)
    assert get_execution_mode(session).mode is ExecutionLifecycleMode.SANDBOX


def test_valid_signature_applies_canary_once_and_replay_returns_same_result(session, monkeypatch):
    from app.execution import canary_owner_activation as activation

    _set_sandbox(session)
    private, credential = _enroll(session, "0002")
    snapshot = _snapshot(session)
    monkeypatch.setattr(
        activation,
        "build_canary_activation_readiness",
        lambda *args, **kwargs: _readiness(snapshot),
    )

    issued = activation.issue_canary_owner_activation_challenge(
        session,
        credential_id=credential.id,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: None,
    )
    signature = _sign(private, issued.message)

    result = activation.confirm_canary_owner_activation(
        session,
        credential_id=credential.id,
        snapshot_hash=snapshot.snapshot_hash,
        challenge_id=issued.challenge_id,
        signature_b64=signature,
        context_provider=lambda: None,
    )
    assert result.status == "APPLIED"
    assert result.mode is ExecutionLifecycleMode.CANARY
    assert result.blockers == ()
    assert get_execution_mode(session).mode is ExecutionLifecycleMode.CANARY
    event_count = session.query(ExecutionModeEvent).filter(
        ExecutionModeEvent.to_mode == ExecutionLifecycleMode.CANARY
    ).count()
    audit_count = session.query(AuditEvent).filter(
        AuditEvent.action == "execution_canary_activation_confirm",
        AuditEvent.subject == str(issued.challenge_id),
    ).count()

    replay = activation.confirm_canary_owner_activation(
        session,
        credential_id=credential.id,
        snapshot_hash=snapshot.snapshot_hash,
        challenge_id=issued.challenge_id,
        signature_b64=signature,
        context_provider=lambda: None,
    )
    assert replay == result
    assert session.query(ExecutionModeEvent).filter(
        ExecutionModeEvent.to_mode == ExecutionLifecycleMode.CANARY
    ).count() == event_count
    assert session.query(AuditEvent).filter(
        AuditEvent.action == "execution_canary_activation_confirm",
        AuditEvent.subject == str(issued.challenge_id),
    ).count() == audit_count == 1


def test_valid_signature_is_consumed_but_stale_recheck_blocks_mode_change(session, monkeypatch):
    from app.execution import canary_owner_activation as activation

    _set_sandbox(session)
    private, credential = _enroll(session, "0003")
    snapshot = _snapshot(session)
    state = {"ready": True}

    def readiness(*args, **kwargs):
        if state["ready"]:
            return _readiness(snapshot)
        return _readiness(
            snapshot,
            blockers=("DEPLOYED_SOURCE_SHA_MISMATCH",),
            structural=False,
        )

    monkeypatch.setattr(activation, "build_canary_activation_readiness", readiness)
    issued = activation.issue_canary_owner_activation_challenge(
        session,
        credential_id=credential.id,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: None,
    )
    state["ready"] = False

    result = activation.confirm_canary_owner_activation(
        session,
        credential_id=credential.id,
        snapshot_hash=snapshot.snapshot_hash,
        challenge_id=issued.challenge_id,
        signature_b64=_sign(private, issued.message),
        context_provider=lambda: None,
    )
    assert result.status == "BLOCKED"
    assert result.mode is ExecutionLifecycleMode.SANDBOX
    assert "DEPLOYED_SOURCE_SHA_MISMATCH" in result.blockers
    row = session.get(OwnerStepUpChallenge, issued.challenge_id)
    assert row is not None and row.consumed_at is not None
    assert get_execution_mode(session).mode is ExecutionLifecycleMode.SANDBOX


def test_kill_switch_blocks_challenge_and_is_rechecked_before_confirmation(session, monkeypatch):
    from app.execution import canary_owner_activation as activation

    _set_sandbox(session)
    private, credential = _enroll(session, "0004")
    snapshot = _snapshot(session)
    monkeypatch.setattr(
        activation,
        "build_canary_activation_readiness",
        lambda *args, **kwargs: _readiness(snapshot),
    )

    issued = activation.issue_canary_owner_activation_challenge(
        session,
        credential_id=credential.id,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: None,
    )
    set_execution_kill_switch(
        session,
        level=ExecutionKillSwitchLevel.HALT_NEW_ENTRIES,
        actor="test",
        reason="activation safety test",
    )

    result = activation.confirm_canary_owner_activation(
        session,
        credential_id=credential.id,
        snapshot_hash=snapshot.snapshot_hash,
        challenge_id=issued.challenge_id,
        signature_b64=_sign(private, issued.message),
        context_provider=lambda: None,
    )
    assert result.status == "BLOCKED"
    assert "EXECUTION_KILL_SWITCH_NOT_CLEAR" in result.blockers
    assert get_execution_mode(session).mode is ExecutionLifecycleMode.SANDBOX

    with pytest.raises(activation.CanaryOwnerActivationRejected):
        activation.issue_canary_owner_activation_challenge(
            session,
            credential_id=credential.id,
            snapshot_hash=snapshot.snapshot_hash,
            context_provider=lambda: None,
        )


def test_wrong_signature_fails_without_consuming_challenge_or_changing_mode(session, monkeypatch):
    from app.execution import canary_owner_activation as activation

    _set_sandbox(session)
    private, credential = _enroll(session, "0005")
    wrong_private, _other_credential = _enroll(session, "0006")
    snapshot = _snapshot(session)
    monkeypatch.setattr(
        activation,
        "build_canary_activation_readiness",
        lambda *args, **kwargs: _readiness(snapshot),
    )
    issued = activation.issue_canary_owner_activation_challenge(
        session,
        credential_id=credential.id,
        snapshot_hash=snapshot.snapshot_hash,
        context_provider=lambda: None,
    )

    with pytest.raises(activation.CanaryOwnerActivationRejected):
        activation.confirm_canary_owner_activation(
            session,
            credential_id=credential.id,
            snapshot_hash=snapshot.snapshot_hash,
            challenge_id=issued.challenge_id,
            signature_b64=_sign(wrong_private, issued.message),
            context_provider=lambda: None,
        )

    row = session.get(OwnerStepUpChallenge, issued.challenge_id)
    assert row is not None and row.consumed_at is None
    assert get_execution_mode(session).mode is ExecutionLifecycleMode.SANDBOX
