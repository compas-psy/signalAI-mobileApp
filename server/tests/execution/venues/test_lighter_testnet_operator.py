from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.execution.venues.lighter_actions import LighterActionAck
from app.execution.venues.lighter_auth import LighterServerCredentials


NOW = datetime(2026, 8, 22, 2, 0, tzinfo=UTC)
PRIVATE_KEY = "ab" * 32


def _credentials() -> LighterServerCredentials:
    return LighterServerCredentials(
        account_index=42,
        api_key_index=3,
        api_private_key=PRIVATE_KEY,
        environment="testnet",
        purpose="trade",
    )


def _artifact(*, observed_at: datetime | None = None, lighter_incident: bool = False) -> bytes:
    moment = observed_at or (NOW - timedelta(minutes=5))
    common = {
        "opportunity_key": "shadow-1",
        "market_snapshot_hash": "a" * 64,
        "status": "EVALUATED",
        "total_cost_bps": "10",
        "ack_latency_ms": "100",
        "fill_slippage_bps": "2",
        "protection_latency_ms": "150",
        "reconciliation_outcome": "EXACT",
    }
    payload = {
        "observed_at": moment.isoformat(),
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
            {
                "venue": "LIGHTER",
                "duplicate_execution_incident": lighter_incident,
                **common,
            },
        ],
        "market": {
            "market_id": 0,
            "symbol": "ETH",
            "market_type": "perp",
            "status": "active",
            "min_base_amount": "0.001",
            "min_quote_amount": "5",
            "supported_size_decimals": 4,
            "supported_price_decimals": 2,
            "supported_quote_decimals": 2,
            "maker_fee": "0.0001",
            "taker_fee": "0.0004",
            "liquidation_fee": "0.005",
            "order_quote_limit": "1000000",
            "multiplier": "1",
        },
        "order": {
            "client_order_id": "sai077-eth-smoke-1",
            "quantity": "0.001",
            "price": "5000.00",
            "is_ask": True,
        },
    }
    return json.dumps(payload, sort_keys=True).encode("utf-8")


class FakeTransport:
    base_url = "https://testnet.zklighter.elliot.ai"
    chain_id = 300
    account_index = 42
    api_key_index = 3

    def __init__(self, *, reject_cancel_once: bool = False) -> None:
        self.closed = False
        self.check_calls = 0
        self.next_nonce_calls = 0
        self.create_calls: list[dict[str, object]] = []
        self.cancel_calls: list[dict[str, object]] = []
        self._nonce = 100
        self._reject_cancel_once = reject_cancel_once

    @property
    def eligible_for_live(self) -> bool:
        return False

    def check_client(self) -> str | None:
        self.check_calls += 1
        return None

    def next_nonce(self) -> int:
        self.next_nonce_calls += 1
        value = self._nonce
        self._nonce += 1
        return value

    def create_order(self, **kwargs) -> LighterActionAck:
        self.create_calls.append(dict(kwargs))
        return LighterActionAck(code=200, tx_hash="0xcreate", message=None)

    def cancel_order(self, **kwargs) -> LighterActionAck:
        self.cancel_calls.append(dict(kwargs))
        if self._reject_cancel_once:
            self._reject_cancel_once = False
            return LighterActionAck(
                code=400,
                tx_hash="",
                message=f"provider rejected {PRIVATE_KEY}",
            )
        return LighterActionAck(code=200, tx_hash="0xcancel", message=None)

    def close(self) -> None:
        self.closed = True


def _events(session, run_key: str):
    return session.execute(
        text(
            "SELECT event_type, reason_code, create_tx_hash, cancel_tx_hash, "
            "eligible_for_live FROM lighter_testnet_smoke_evidence "
            "WHERE run_key = :run_key ORDER BY created_at, id"
        ),
        {"run_key": run_key},
    ).all()


def test_artifact_recomputes_scorecard_and_rejects_precomputed_result() -> None:
    from app.execution.venues.lighter_testnet_operator import (
        LighterTestnetOperatorError,
        parse_lighter_testnet_smoke_artifact,
    )

    parsed = parse_lighter_testnet_smoke_artifact(_artifact())
    assert parsed.shadow_result.eligible_for_testnet is True
    assert parsed.shadow_result.weighted_score is None
    assert parsed.market.symbol == "ETH"
    assert parsed.quantity == Decimal("0.001")

    payload = json.loads(_artifact())
    payload["scorecard_result"] = {"eligible_for_testnet": True}
    try:
        parse_lighter_testnet_smoke_artifact(json.dumps(payload).encode())
    except LighterTestnetOperatorError as exc:
        assert "artifact fields" in str(exc)
    else:
        raise AssertionError("precomputed eligibility must not be accepted")


def test_stale_or_failed_shadow_evidence_blocks_before_credentials_or_provider(session) -> None:
    from app.execution.venues.lighter_testnet_operator import (
        LighterTestnetOperatorStatus,
        execute_lighter_testnet_smoke_artifact,
    )

    credential_calls: list[str] = []
    transport_calls: list[object] = []

    def load_credentials(db, slot):
        credential_calls.append(slot)
        return _credentials()

    def build_transport(credentials):
        transport_calls.append(credentials)
        return FakeTransport()

    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    stale = execute_lighter_testnet_smoke_artifact(
        session,
        raw_artifact=_artifact(observed_at=NOW - timedelta(hours=25)),
        session_factory=factory,
        now=NOW,
        credential_loader=load_credentials,
        transport_factory=build_transport,
    )
    assert stale.status is LighterTestnetOperatorStatus.BLOCKED
    assert stale.reason_code == "SHADOW_EVIDENCE_STALE"

    failed = execute_lighter_testnet_smoke_artifact(
        session,
        raw_artifact=_artifact(lighter_incident=True),
        session_factory=factory,
        now=NOW,
        credential_loader=load_credentials,
        transport_factory=build_transport,
    )
    assert failed.status is LighterTestnetOperatorStatus.BLOCKED
    assert failed.reason_code == "SHADOW_GATE_NOT_ELIGIBLE"
    assert credential_calls == []
    assert transport_calls == []
    assert failed.eligible_for_live is False


def test_missing_testnet_secret_blocks_without_constructing_transport(session) -> None:
    from app.execution.venues.lighter_auth import LIGHTER_TESTNET_TRADE_SLOT
    from app.execution.venues.lighter_testnet_operator import (
        LighterTestnetOperatorStatus,
        execute_lighter_testnet_smoke_artifact,
    )

    slots: list[str] = []
    transport_calls: list[object] = []

    def missing(db, slot):
        slots.append(slot)
        return None

    result = execute_lighter_testnet_smoke_artifact(
        session,
        raw_artifact=_artifact(),
        session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        now=NOW,
        credential_loader=missing,
        transport_factory=lambda credentials: transport_calls.append(credentials),
    )

    assert result.status is LighterTestnetOperatorStatus.BLOCKED
    assert result.reason_code == "TESTNET_TRADE_CREDENTIALS_REQUIRED"
    assert slots == [LIGHTER_TESTNET_TRADE_SLOT]
    assert transport_calls == []


def test_success_uses_one_transport_closes_it_and_persists_sanitized_evidence(session) -> None:
    from app.execution.venues.lighter_testnet_operator import (
        LighterTestnetOperatorStatus,
        execute_lighter_testnet_smoke_artifact,
    )

    transport = FakeTransport()
    transports: list[FakeTransport] = []

    def build_transport(credentials):
        assert credentials == _credentials()
        transports.append(transport)
        return transport

    result = execute_lighter_testnet_smoke_artifact(
        session,
        raw_artifact=_artifact(),
        session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        now=NOW,
        credential_loader=lambda db, slot: _credentials(),
        transport_factory=build_transport,
    )
    session.flush()

    assert result.status is LighterTestnetOperatorStatus.SUCCESS
    assert result.create_tx_hash == "0xcreate"
    assert result.cancel_tx_hash == "0xcancel"
    assert result.eligible_for_live is False
    assert transports == [transport]
    assert transport.closed is True
    assert len(transport.create_calls) == 1
    assert len(transport.cancel_calls) == 1

    rows = _events(session, result.run_key)
    assert [(row.event_type, row.reason_code) for row in rows] == [("SUCCESS", None)]
    assert rows[0].create_tx_hash == "0xcreate"
    assert rows[0].cancel_tx_hash == "0xcancel"
    assert rows[0].eligible_for_live is False
    rendered = str(rows)
    assert PRIVATE_KEY not in rendered
    assert "provider rejected" not in rendered


def test_completed_run_is_idempotent_and_never_touches_credentials_again(session) -> None:
    from app.execution.venues.lighter_testnet_operator import execute_lighter_testnet_smoke_artifact

    first_transport = FakeTransport()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    first = execute_lighter_testnet_smoke_artifact(
        session,
        raw_artifact=_artifact(),
        session_factory=factory,
        now=NOW,
        credential_loader=lambda db, slot: _credentials(),
        transport_factory=lambda credentials: first_transport,
    )
    session.flush()

    credential_calls: list[str] = []
    second = execute_lighter_testnet_smoke_artifact(
        session,
        raw_artifact=_artifact(),
        session_factory=factory,
        now=NOW + timedelta(minutes=1),
        credential_loader=lambda db, slot: credential_calls.append(slot),
        transport_factory=lambda credentials: (_ for _ in ()).throw(
            AssertionError("transport must not be constructed")
        ),
    )

    assert second.run_key == first.run_key
    assert second.status == first.status
    assert second.create_tx_hash == first.create_tx_hash
    assert second.cancel_tx_hash == first.cancel_tx_hash
    assert credential_calls == []
    assert len(_events(session, first.run_key)) == 1


def test_cancel_failure_is_sanitized_and_next_run_performs_cancel_only_recovery(session) -> None:
    from app.execution.venues.lighter_testnet_operator import (
        LighterTestnetOperatorStatus,
        execute_lighter_testnet_smoke_artifact,
    )

    first_transport = FakeTransport(reject_cancel_once=True)
    second_transport = FakeTransport()
    transports = iter((first_transport, second_transport))
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

    failed = execute_lighter_testnet_smoke_artifact(
        session,
        raw_artifact=_artifact(),
        session_factory=factory,
        now=NOW,
        credential_loader=lambda db, slot: _credentials(),
        transport_factory=lambda credentials: next(transports),
    )
    session.flush()

    assert failed.status is LighterTestnetOperatorStatus.CANCEL_FAILED
    assert failed.reason_code == "CANCEL_FAILED"
    assert failed.create_tx_hash == "0xcreate"
    assert failed.cancel_tx_hash is None
    assert first_transport.closed is True
    assert PRIVATE_KEY not in repr(failed)

    recovered = execute_lighter_testnet_smoke_artifact(
        session,
        raw_artifact=_artifact(),
        session_factory=factory,
        now=NOW + timedelta(minutes=1),
        credential_loader=lambda db, slot: _credentials(),
        transport_factory=lambda credentials: next(transports),
    )
    session.flush()

    assert recovered.status is LighterTestnetOperatorStatus.RECOVERY_SUCCESS
    assert recovered.create_tx_hash == "0xcreate"
    assert recovered.cancel_tx_hash == "0xcancel"
    assert second_transport.create_calls == []
    assert len(second_transport.cancel_calls) == 1
    assert second_transport.closed is True
    rows = _events(session, recovered.run_key)
    assert [row.event_type for row in rows] == ["CANCEL_FAILED", "RECOVERY_SUCCESS"]
    assert PRIVATE_KEY not in str(rows)
