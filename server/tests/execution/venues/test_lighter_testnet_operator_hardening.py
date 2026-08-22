from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.execution.venues.lighter_actions import LighterActionAck
from app.execution.venues.lighter_auth import LighterServerCredentials
from app.execution.venues.lighter_facts import LighterMarketFact


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


def _credentials(*, account_index: int = 42, api_key_index: int = 3):
    return LighterServerCredentials(
        account_index=account_index,
        api_key_index=api_key_index,
        api_private_key="ab" * 32,
        environment="testnet",
        purpose="trade",
    )


def _write_shadow(path: Path) -> None:
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
    path.write_text(
        json.dumps(
            {
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
                "observations": [
                    {"venue": "BYBIT", **common},
                    {"venue": "LIGHTER", **common},
                ],
            }
        ),
        encoding="utf-8",
    )


class Transport:
    base_url = "https://testnet.zklighter.elliot.ai"
    chain_id = 300

    def __init__(self, *, account_index: int = 42, api_key_index: int = 3) -> None:
        self.account_index = account_index
        self.api_key_index = api_key_index
        self.nonces = [100, 100, 101, 102]
        self.create_calls = 0
        self.cancel_calls = 0
        self.closed = 0
        self.fail_cancel = True

    def check_client(self):
        return None

    def next_nonce(self):
        return self.nonces.pop(0)

    def create_order(self, **kwargs):
        self.create_calls += 1
        return LighterActionAck(code=200, tx_hash="0xcreate", message=None)

    def cancel_order(self, **kwargs):
        self.cancel_calls += 1
        if self.fail_cancel:
            self.fail_cancel = False
            raise RuntimeError("provider-private-detail")
        return LighterActionAck(code=200, tx_hash="0xcancel", message=None)

    def close(self):
        self.closed += 1


def _create_cancel_failed(tmp_path: Path, session):
    from app.execution.venues.lighter_testnet_operator import (
        run_lighter_testnet_operator_smoke,
    )

    shadow = tmp_path / "shadow.json"
    result = tmp_path / "result.json"
    _write_shadow(shadow)
    transport = Transport()
    smoke = run_lighter_testnet_operator_smoke(
        db=session,
        session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        shadow_evidence_path=shadow,
        result_evidence_path=result,
        market=_market(),
        client_order_id="sai077-hardening",
        quantity=Decimal("0.01"),
        price=Decimal("4000"),
        is_ask=False,
        observed_at=datetime(2026, 8, 22, 2, 5, tzinfo=UTC),
        credential_loader=lambda _db, _slot: _credentials(),
        transport_factory=lambda _credentials: transport,
    )
    assert smoke.status == "CANCEL_FAILED"
    return shadow, result, transport


def test_cancel_recovery_rejects_tampered_evidence_hash_before_vault_access(tmp_path, session):
    from app.execution.venues.lighter_testnet_operator import (
        LighterTestnetOperatorError,
        recover_lighter_testnet_operator_cancel,
    )

    shadow, result, _ = _create_cancel_failed(tmp_path, session)
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["create_tx_hash"] = "0xtampered"
    result.write_text(json.dumps(payload), encoding="utf-8")
    credential_calls = 0

    def credentials(_db, _slot):
        nonlocal credential_calls
        credential_calls += 1
        return _credentials()

    with pytest.raises(LighterTestnetOperatorError, match="integrity"):
        recover_lighter_testnet_operator_cancel(
            db=session,
            session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
            shadow_evidence_path=shadow,
            result_evidence_path=result,
            market=_market(),
            observed_at=datetime(2026, 8, 22, 2, 6, tzinfo=UTC),
            credential_loader=credentials,
            transport_factory=lambda _credentials: Transport(),
        )
    assert credential_calls == 0


def test_cancel_recovery_requires_same_account_api_key_endpoint_and_chain(tmp_path, session):
    from app.execution.venues.lighter_testnet_operator import (
        LighterTestnetOperatorError,
        recover_lighter_testnet_operator_cancel,
    )

    shadow, result, _ = _create_cancel_failed(tmp_path, session)
    for credentials, transport in (
        (_credentials(account_index=43), Transport(account_index=43)),
        (_credentials(api_key_index=4), Transport(api_key_index=4)),
    ):
        with pytest.raises(LighterTestnetOperatorError, match="scope"):
            recover_lighter_testnet_operator_cancel(
                db=session,
                session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
                shadow_evidence_path=shadow,
                result_evidence_path=result,
                market=_market(),
                observed_at=datetime(2026, 8, 22, 2, 6, tzinfo=UTC),
                credential_loader=lambda _db, _slot, value=credentials: value,
                transport_factory=lambda _credentials, value=transport: value,
            )
        assert transport.cancel_calls == 0


def test_result_evidence_digest_matches_canonical_payload_without_digest(tmp_path, session):
    _, result, _ = _create_cancel_failed(tmp_path, session)
    document = json.loads(result.read_text(encoding="utf-8"))
    stored = document.pop("evidence_sha256")
    canonical = (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    assert stored == hashlib.sha256(canonical).hexdigest()


def test_transport_close_failure_never_overrides_persisted_smoke_outcome(tmp_path, session):
    from app.execution.venues.lighter_testnet_operator import run_lighter_testnet_operator_smoke

    class CloseFailingTransport(Transport):
        def close(self):
            raise RuntimeError("provider secret detail on close")

    shadow = tmp_path / "shadow.json"
    result = tmp_path / "result.json"
    _write_shadow(shadow)
    transport = CloseFailingTransport()
    transport.fail_cancel = False
    outcome = run_lighter_testnet_operator_smoke(
        db=session,
        session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        shadow_evidence_path=shadow,
        result_evidence_path=result,
        market=_market(),
        client_order_id="sai077-close",
        quantity=Decimal("0.01"),
        price=Decimal("4000"),
        is_ask=False,
        observed_at=datetime(2026, 8, 22, 2, 5, tzinfo=UTC),
        credential_loader=lambda _db, _slot: _credentials(),
        transport_factory=lambda _credentials: transport,
    )
    assert outcome.status == "SUCCESS"
    assert json.loads(result.read_text(encoding="utf-8"))["status"] == "SUCCESS"
