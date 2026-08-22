from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.execution.venues.lighter_actions import LighterActionAck
from app.execution.venues.lighter_auth import LighterServerCredentials
from app.execution.venues.lighter_facts import LighterMarketFact


_PRIVATE_KEY = "ab" * 32


def _market() -> LighterMarketFact:
    return LighterMarketFact(
        market_id=0,
        symbol="ETH",
        status="active",
        min_base_amount=Decimal("0.001"),
        min_quote_amount=Decimal("5"),
        size_decimals=4,
        price_decimals=2,
        quote_decimals=2,
        maker_fee_pct=Decimal("0.0001"),
        taker_fee_pct=Decimal("0.0004"),
        liquidation_fee_pct=Decimal("0.005"),
        order_quote_limit=Decimal("1000000"),
        multiplier=Decimal("1"),
        observed_at=datetime(2026, 8, 22, 2, 0, tzinfo=UTC),
    )


def _credentials() -> LighterServerCredentials:
    return LighterServerCredentials(
        account_index=42,
        api_key_index=3,
        api_private_key=_PRIVATE_KEY,
        environment="testnet",
        purpose="trade",
    )


def _shadow_document(*, eligible: bool = True) -> dict[str, object]:
    common = {
        "opportunity_key": "o1",
        "market_snapshot_hash": "a" * 64,
        "status": "EVALUATED",
        "total_cost_bps": "10",
        "ack_latency_ms": "100",
        "fill_slippage_bps": "2",
        "protection_latency_ms": "150",
        "reconciliation_outcome": "EXACT",
        "duplicate_execution_incident": False,
        "unprotected_execution_incident": False,
    }
    lighter = dict(common)
    lighter["venue"] = "LIGHTER"
    lighter["duplicate_execution_incident"] = not eligible
    bybit = dict(common)
    bybit["venue"] = "BYBIT"
    return {
        "schema": "signalai.lighter.shadow-evidence.v1",
        "generated_at": "2026-08-22T02:00:00+00:00",
        "policy": {
            "min_paired_opportunities": 1,
            "min_metric_pairs": 1,
            "max_lighter_cost_delta_bps": "2",
            "max_lighter_ack_latency_delta_ms": "50",
            "max_lighter_fill_slippage_delta_bps": "1",
            "max_lighter_protection_latency_delta_ms": "50",
            "max_lighter_ambiguity_rate_delta": "0.10",
            "max_lighter_unavailable_rate": "0.10",
        },
        "observations": [bybit, lighter],
    }


def _write_shadow(path: Path, *, eligible: bool = True) -> None:
    path.write_text(json.dumps(_shadow_document(eligible=eligible)), encoding="utf-8")


class FakeTransport:
    base_url = "https://testnet.zklighter.elliot.ai"
    chain_id = 300
    account_index = 42
    api_key_index = 3

    def __init__(self, *, fail_cancel_once: bool = False) -> None:
        self.nonces = [100, 100, 101, 102]
        self.fail_cancel_once = fail_cancel_once
        self.create_calls: list[dict[str, object]] = []
        self.cancel_calls: list[dict[str, object]] = []

    def check_client(self) -> str | None:
        return None

    def next_nonce(self) -> int:
        return self.nonces.pop(0)

    def create_order(self, **kwargs) -> LighterActionAck:
        self.create_calls.append(dict(kwargs))
        return LighterActionAck(code=200, tx_hash="0xcreate", message=None)

    def cancel_order(self, **kwargs) -> LighterActionAck:
        self.cancel_calls.append(dict(kwargs))
        if self.fail_cancel_once:
            self.fail_cancel_once = False
            raise RuntimeError(f"provider {_PRIVATE_KEY}")
        return LighterActionAck(code=200, tx_hash="0xcancel", message=None)

    def close(self) -> None:
        pass


def test_shadow_evidence_is_recomputed_from_saved_observations_not_trusted_flag(tmp_path: Path) -> None:
    from app.execution.venues.lighter_testnet_operator import (
        LighterTestnetOperatorError,
        load_lighter_shadow_evidence,
    )

    path = tmp_path / "shadow.json"
    document = _shadow_document(eligible=False)
    document["eligible_for_testnet"] = True
    path.write_text(json.dumps(document), encoding="utf-8")

    evidence = load_lighter_shadow_evidence(path)
    assert evidence.scorecard.eligible_for_testnet is False
    assert evidence.sha256
    assert evidence.generated_at == datetime(2026, 8, 22, 2, 0, tzinfo=UTC)

    document["observations"] = []
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(LighterTestnetOperatorError, match="observations"):
        load_lighter_shadow_evidence(path)


def test_operator_uses_exact_testnet_secret_slot_and_persists_redacted_success_evidence(
    tmp_path: Path,
    session,
) -> None:
    from app.execution.venues.lighter_testnet_operator import (
        LIGHTER_TESTNET_TRADE_SLOT,
        run_lighter_testnet_operator_smoke,
    )

    shadow_path = tmp_path / "shadow.json"
    result_path = tmp_path / "smoke.json"
    _write_shadow(shadow_path)
    transport = FakeTransport()
    requested_slots: list[str] = []

    def load_credentials(_db, slot: str):
        requested_slots.append(slot)
        return _credentials()

    result = run_lighter_testnet_operator_smoke(
        db=session,
        session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        shadow_evidence_path=shadow_path,
        result_evidence_path=result_path,
        market=_market(),
        client_order_id="sai077-smoke-1",
        quantity=Decimal("0.01"),
        price=Decimal("4000"),
        is_ask=False,
        observed_at=datetime(2026, 8, 22, 2, 5, tzinfo=UTC),
        credential_loader=load_credentials,
        transport_factory=lambda credentials: transport,
    )

    assert requested_slots == [LIGHTER_TESTNET_TRADE_SLOT]
    assert result.status == "SUCCESS"
    assert result.create_tx_hash == "0xcreate"
    assert result.cancel_tx_hash == "0xcancel"
    assert result.eligible_for_live is False
    assert result_path.exists()
    payload = result_path.read_text(encoding="utf-8")
    assert _PRIVATE_KEY not in payload
    saved = json.loads(payload)
    assert saved["schema"] == "signalai.lighter.testnet-smoke-evidence.v1"
    assert saved["status"] == "SUCCESS"
    assert saved["shadow_evidence_sha256"] == result.shadow_evidence_sha256
    assert saved["account_index"] == 42
    assert saved["api_key_index"] == 3
    assert saved["eligible_for_live"] is False
    assert transport.create_calls and transport.cancel_calls


def test_operator_fails_closed_before_order_when_shadow_gate_is_not_eligible(
    tmp_path: Path,
    session,
) -> None:
    from app.execution.venues.lighter_testnet_operator import (
        LighterTestnetOperatorError,
        run_lighter_testnet_operator_smoke,
    )

    shadow_path = tmp_path / "shadow.json"
    result_path = tmp_path / "smoke.json"
    _write_shadow(shadow_path, eligible=False)
    transport = FakeTransport()
    credential_calls = 0

    def load_credentials(_db, _slot: str):
        nonlocal credential_calls
        credential_calls += 1
        return _credentials()

    with pytest.raises(LighterTestnetOperatorError, match="shadow evidence"):
        run_lighter_testnet_operator_smoke(
            db=session,
            session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
            shadow_evidence_path=shadow_path,
            result_evidence_path=result_path,
            market=_market(),
            client_order_id="blocked",
            quantity=Decimal("0.01"),
            price=Decimal("4000"),
            is_ask=False,
            observed_at=datetime(2026, 8, 22, 2, 5, tzinfo=UTC),
            credential_loader=load_credentials,
            transport_factory=lambda credentials: transport,
        )

    assert credential_calls == 0
    assert transport.create_calls == []
    assert not result_path.exists()


def test_cancel_failure_persists_recoverable_redacted_evidence_and_recovery_is_cancel_only(
    tmp_path: Path,
    session,
) -> None:
    from app.execution.venues.lighter_testnet_operator import (
        recover_lighter_testnet_operator_cancel,
        run_lighter_testnet_operator_smoke,
    )

    shadow_path = tmp_path / "shadow.json"
    result_path = tmp_path / "smoke.json"
    _write_shadow(shadow_path)
    transport = FakeTransport(fail_cancel_once=True)
    kwargs = dict(
        db=session,
        session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        shadow_evidence_path=shadow_path,
        result_evidence_path=result_path,
        market=_market(),
        client_order_id="sai077-recovery-1",
        quantity=Decimal("0.01"),
        price=Decimal("4000"),
        is_ask=False,
        observed_at=datetime(2026, 8, 22, 2, 5, tzinfo=UTC),
        credential_loader=lambda _db, _slot: _credentials(),
        transport_factory=lambda credentials: transport,
    )

    failed = run_lighter_testnet_operator_smoke(**kwargs)
    assert failed.status == "CANCEL_FAILED"
    assert failed.create_tx_hash == "0xcreate"
    assert failed.cancel_tx_hash is None
    payload = result_path.read_text(encoding="utf-8")
    assert _PRIVATE_KEY not in payload
    assert json.loads(payload)["status"] == "CANCEL_FAILED"
    assert len(transport.create_calls) == 1

    recovered = recover_lighter_testnet_operator_cancel(
        db=session,
        session_factory=kwargs["session_factory"],
        shadow_evidence_path=shadow_path,
        result_evidence_path=result_path,
        market=_market(),
        observed_at=datetime(2026, 8, 22, 2, 6, tzinfo=UTC),
        credential_loader=lambda _db, _slot: _credentials(),
        transport_factory=lambda credentials: transport,
    )
    assert recovered.status == "SUCCESS"
    assert recovered.create_tx_hash == "0xcreate"
    assert recovered.cancel_tx_hash == "0xcancel"
    assert len(transport.create_calls) == 1
    assert len(transport.cancel_calls) == 2


def test_missing_testnet_secret_is_blocked_without_transport_construction(
    tmp_path: Path,
    session,
) -> None:
    from app.execution.venues.lighter_testnet_operator import (
        LighterTestnetOperatorError,
        run_lighter_testnet_operator_smoke,
    )

    shadow_path = tmp_path / "shadow.json"
    _write_shadow(shadow_path)
    factory_calls = 0

    def transport_factory(credentials):
        nonlocal factory_calls
        factory_calls += 1
        return FakeTransport()

    with pytest.raises(LighterTestnetOperatorError, match="credential"):
        run_lighter_testnet_operator_smoke(
            db=session,
            session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
            shadow_evidence_path=shadow_path,
            result_evidence_path=tmp_path / "smoke.json",
            market=_market(),
            client_order_id="missing-secret",
            quantity=Decimal("0.01"),
            price=Decimal("4000"),
            is_ask=False,
            observed_at=datetime(2026, 8, 22, 2, 5, tzinfo=UTC),
            credential_loader=lambda _db, _slot: None,
            transport_factory=transport_factory,
        )

    assert factory_calls == 0
