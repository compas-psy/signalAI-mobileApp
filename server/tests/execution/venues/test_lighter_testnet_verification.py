from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from app.execution.venues.lighter_actions import LighterActionAck
from app.execution.venues.lighter_auth import LighterServerCredentials
from app.execution.venues.lighter_facts import LighterMarketFact


def _credentials(*, environment: str = "testnet", purpose: str = "trade") -> LighterServerCredentials:
    return LighterServerCredentials(
        account_index=42,
        api_key_index=3,
        api_private_key="ab" * 32,
        environment=environment,
        purpose=purpose,
    )


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
        observed_at=datetime(2026, 8, 21, 13, 0, tzinfo=UTC),
    )


def _shadow(*, eligible: bool):
    from app.experiments.venue_shadow_scorecard_v1 import (
        VenueShadowObservation,
        VenueShadowScorecardPolicy,
        evaluate_venue_shadow_scorecard,
    )

    policy = VenueShadowScorecardPolicy(
        min_paired_opportunities=1,
        min_metric_pairs=1,
        max_lighter_cost_delta_bps=Decimal("2"),
        max_lighter_ack_latency_delta_ms=Decimal("50"),
        max_lighter_fill_slippage_delta_bps=Decimal("1"),
        max_lighter_protection_latency_delta_ms=Decimal("50"),
        max_lighter_ambiguity_rate_delta=Decimal("0.10"),
        max_lighter_unavailable_rate=Decimal("0.10"),
    )
    common = dict(
        opportunity_key="o1",
        market_snapshot_hash="a" * 64,
        status="EVALUATED",
        total_cost_bps=Decimal("10"),
        ack_latency_ms=Decimal("100"),
        fill_slippage_bps=Decimal("2"),
        protection_latency_ms=Decimal("150"),
        reconciliation_outcome="EXACT",
    )
    rows = (
        VenueShadowObservation(venue="BYBIT", **common),
        VenueShadowObservation(
            venue="LIGHTER",
            duplicate_execution_incident=not eligible,
            **common,
        ),
    )
    return evaluate_venue_shadow_scorecard(rows, policy=policy)


class FakeTestnetTransport:
    def __init__(
        self,
        *,
        base_url: str = "https://testnet.zklighter.elliot.ai",
        chain_id: int = 300,
        account_index: int = 42,
        api_key_index: int = 3,
        check_error: str | None = None,
        nonces: list[int] | None = None,
    ) -> None:
        self.base_url = base_url
        self.chain_id = chain_id
        self.account_index = account_index
        self.api_key_index = api_key_index
        self.check_error = check_error
        self.nonces = list(nonces or [100])
        self.check_calls = 0
        self.next_nonce_calls = 0
        self.create_calls: list[dict[str, object]] = []
        self.cancel_calls: list[dict[str, object]] = []

    def check_client(self) -> str | None:
        self.check_calls += 1
        return self.check_error

    def next_nonce(self) -> int:
        self.next_nonce_calls += 1
        if not self.nonces:
            raise AssertionError("unexpected next_nonce call")
        return self.nonces.pop(0)

    def create_order(self, **kwargs) -> LighterActionAck:
        self.create_calls.append(dict(kwargs))
        return LighterActionAck(code=200, tx_hash="0xcreate", message=None)

    def cancel_order(self, **kwargs) -> LighterActionAck:
        self.cancel_calls.append(dict(kwargs))
        return LighterActionAck(code=200, tx_hash="0xcancel", message=None)


class RejectedCreateTransport(FakeTestnetTransport):
    private_marker = "secret-private-key-marker"

    def create_order(self, **kwargs) -> LighterActionAck:
        self.create_calls.append(dict(kwargs))
        return LighterActionAck(
            code=400,
            tx_hash="",
            message=f"provider rejected {self.private_marker}",
        )


class RejectedThenSuccessfulCancelTransport(FakeTestnetTransport):
    private_marker = "secret-private-key-marker"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.reject_first_cancel = True

    def cancel_order(self, **kwargs) -> LighterActionAck:
        self.cancel_calls.append(dict(kwargs))
        if self.reject_first_cancel:
            self.reject_first_cancel = False
            return LighterActionAck(
                code=400,
                tx_hash="",
                message=f"provider rejected {self.private_marker}",
            )
        return LighterActionAck(code=200, tx_hash="0xcancel", message=None)


def test_shadow_gate_blocks_before_any_testnet_provider_call() -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetAdmissionStatus,
        verify_lighter_testnet_admission,
    )

    transport = FakeTestnetTransport()
    result = verify_lighter_testnet_admission(
        credentials=_credentials(),
        shadow_result=_shadow(eligible=False),
        transport=transport,
        observed_at=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
    )

    assert result.status is LighterTestnetAdmissionStatus.BLOCKED
    assert "SHADOW_GATE_NOT_ELIGIBLE" in result.reasons
    assert result.eligible_for_order_smoke is False
    assert result.eligible_for_live is False
    assert transport.check_calls == 0
    assert transport.next_nonce_calls == 0


def test_mainnet_endpoint_or_chain_is_blocked_before_credential_check() -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetAdmissionStatus,
        verify_lighter_testnet_admission,
    )

    for transport, expected_reason in (
        (
            FakeTestnetTransport(base_url="https://mainnet.zklighter.elliot.ai", chain_id=304),
            "TESTNET_ENDPOINT_MISMATCH",
        ),
        (FakeTestnetTransport(chain_id=304), "TESTNET_CHAIN_ID_MISMATCH"),
    ):
        result = verify_lighter_testnet_admission(
            credentials=_credentials(),
            shadow_result=_shadow(eligible=True),
            transport=transport,
            observed_at=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
        )
        assert result.status is LighterTestnetAdmissionStatus.BLOCKED
        assert expected_reason in result.reasons
        assert result.eligible_for_order_smoke is False
        assert result.eligible_for_live is False
        assert transport.check_calls == 0
        assert transport.next_nonce_calls == 0


def test_only_testnet_trade_credentials_and_matching_scope_are_admissible() -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetAdmissionStatus,
        verify_lighter_testnet_admission,
    )

    wrong_slot = verify_lighter_testnet_admission(
        credentials=_credentials(environment="live", purpose="trade"),
        shadow_result=_shadow(eligible=True),
        transport=FakeTestnetTransport(),
        observed_at=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
    )
    assert wrong_slot.status is LighterTestnetAdmissionStatus.BLOCKED
    assert "TESTNET_TRADE_CREDENTIALS_REQUIRED" in wrong_slot.reasons

    transport = FakeTestnetTransport(account_index=99)
    wrong_scope = verify_lighter_testnet_admission(
        credentials=_credentials(),
        shadow_result=_shadow(eligible=True),
        transport=transport,
        observed_at=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
    )
    assert wrong_scope.status is LighterTestnetAdmissionStatus.BLOCKED
    assert "TRANSPORT_CREDENTIAL_SCOPE_MISMATCH" in wrong_scope.reasons
    assert transport.check_calls == 0
    assert transport.next_nonce_calls == 0


def test_provider_credential_mismatch_or_invalid_nonce_blocks_fail_closed() -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetAdmissionStatus,
        verify_lighter_testnet_admission,
    )

    mismatch = FakeTestnetTransport(check_error="api key mismatch")
    mismatch_result = verify_lighter_testnet_admission(
        credentials=_credentials(),
        shadow_result=_shadow(eligible=True),
        transport=mismatch,
        observed_at=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
    )
    assert mismatch_result.status is LighterTestnetAdmissionStatus.BLOCKED
    assert "PROVIDER_CREDENTIAL_CHECK_FAILED" in mismatch_result.reasons
    assert mismatch.next_nonce_calls == 0

    invalid_nonce = FakeTestnetTransport(nonces=[-1])
    nonce_result = verify_lighter_testnet_admission(
        credentials=_credentials(),
        shadow_result=_shadow(eligible=True),
        transport=invalid_nonce,
        observed_at=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
    )
    assert nonce_result.status is LighterTestnetAdmissionStatus.BLOCKED
    assert "PROVIDER_NONCE_INVALID" in nonce_result.reasons
    assert nonce_result.eligible_for_order_smoke is False


def test_ready_admission_is_redacted_and_never_promotes_live() -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetAdmissionStatus,
        verify_lighter_testnet_admission,
    )

    credentials = _credentials()
    transport = FakeTestnetTransport(nonces=[100])
    result = verify_lighter_testnet_admission(
        credentials=credentials,
        shadow_result=_shadow(eligible=True),
        transport=transport,
        observed_at=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
    )

    assert result.status is LighterTestnetAdmissionStatus.READY
    assert result.reasons == ("TESTNET_SESSION_VERIFIED",)
    assert result.provider_next_nonce == 100
    assert result.eligible_for_order_smoke is True
    assert result.eligible_for_live is False
    assert credentials.api_private_key not in repr(result)
    assert transport.check_calls == 1
    assert transport.next_nonce_calls == 1


def test_missing_or_wrong_type_credentials_block_with_unknown_redacted_scope() -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetAdmissionStatus,
        verify_lighter_testnet_admission,
    )

    for credentials in (None, object()):
        transport = FakeTestnetTransport()
        result = verify_lighter_testnet_admission(
            credentials=credentials,
            shadow_result=_shadow(eligible=True),
            transport=transport,
            observed_at=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
        )

        assert result.status is LighterTestnetAdmissionStatus.BLOCKED
        assert result.reasons == ("TESTNET_TRADE_CREDENTIALS_REQUIRED",)
        assert result.account_index is None
        assert result.api_key_index is None
        assert result.eligible_for_order_smoke is False
        assert result.eligible_for_live is False
        assert transport.check_calls == 0
        assert transport.next_nonce_calls == 0


def test_admission_records_utc_observation_and_blocks_invalid_time_before_provider_calls(
) -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetAdmissionStatus,
        verify_lighter_testnet_admission,
    )

    transport = FakeTestnetTransport()
    ready = verify_lighter_testnet_admission(
        credentials=_credentials(),
        shadow_result=_shadow(eligible=True),
        transport=transport,
        observed_at=datetime(2026, 8, 21, 22, 0, tzinfo=timezone(timedelta(hours=3))),
    )
    assert ready.status is LighterTestnetAdmissionStatus.READY
    assert ready.observed_at == datetime(2026, 8, 21, 19, 0, tzinfo=UTC)

    for observed_at in (datetime(2026, 8, 21, 19, 0), None):
        invalid_transport = FakeTestnetTransport()
        blocked = verify_lighter_testnet_admission(
            credentials=_credentials(),
            shadow_result=_shadow(eligible=True),
            transport=invalid_transport,
            observed_at=observed_at,
        )
        assert blocked.status is LighterTestnetAdmissionStatus.BLOCKED
        assert blocked.reasons == ("OBSERVED_AT_INVALID",)
        assert blocked.observed_at is None
        assert invalid_transport.check_calls == 0
        assert invalid_transport.next_nonce_calls == 0


def test_post_only_create_cancel_smoke_reuses_replay_safe_actions(session) -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetAdmissionStatus,
        run_lighter_testnet_create_cancel_smoke,
        verify_lighter_testnet_admission,
    )

    transport = FakeTestnetTransport(nonces=[100, 100, 101])
    admission = verify_lighter_testnet_admission(
        credentials=_credentials(),
        shadow_result=_shadow(eligible=True),
        transport=transport,
        observed_at=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
    )
    assert admission.status is LighterTestnetAdmissionStatus.READY

    result = run_lighter_testnet_create_cancel_smoke(
        admission=admission,
        session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        transport=transport,
        market=_market(),
        client_order_id="sai075-smoke-1",
        quantity=Decimal("0.01"),
        price=Decimal("4000.00"),
        is_ask=False,
    )

    assert result.create_tx_hash == "0xcreate"
    assert result.cancel_tx_hash == "0xcancel"
    assert result.eligible_for_live is False
    assert transport.create_calls[0]["time_in_force"] == 2
    assert transport.create_calls[0]["reduce_only"] is False
    assert transport.cancel_calls[0]["market_index"] == 0
    assert transport.next_nonce_calls == 3


def test_smoke_refuses_blocked_or_foreign_transport_without_order_calls(session) -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetAdmissionError,
        run_lighter_testnet_create_cancel_smoke,
        verify_lighter_testnet_admission,
    )

    blocked_transport = FakeTestnetTransport()
    blocked = verify_lighter_testnet_admission(
        credentials=_credentials(),
        shadow_result=_shadow(eligible=False),
        transport=blocked_transport,
        observed_at=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
    )
    try:
        run_lighter_testnet_create_cancel_smoke(
            admission=blocked,
            session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
            transport=blocked_transport,
            market=_market(),
            client_order_id="blocked-smoke",
            quantity=Decimal("0.01"),
            price=Decimal("4000.00"),
            is_ask=False,
        )
    except LighterTestnetAdmissionError as exc:
        assert "not eligible" in str(exc).lower()
    else:
        raise AssertionError("blocked admission unexpectedly reached provider order path")
    assert blocked_transport.create_calls == []
    assert blocked_transport.cancel_calls == []

    ready_transport = FakeTestnetTransport(nonces=[100])
    ready = verify_lighter_testnet_admission(
        credentials=_credentials(),
        shadow_result=_shadow(eligible=True),
        transport=ready_transport,
        observed_at=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
    )
    foreign_transport = FakeTestnetTransport(api_key_index=4)
    try:
        run_lighter_testnet_create_cancel_smoke(
            admission=ready,
            session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
            transport=foreign_transport,
            market=_market(),
            client_order_id="foreign-smoke",
            quantity=Decimal("0.01"),
            price=Decimal("4000.00"),
            is_ask=False,
        )
    except LighterTestnetAdmissionError as exc:
        assert "transport" in str(exc).lower()
    else:
        raise AssertionError("foreign transport unexpectedly reused admission")
    assert foreign_transport.create_calls == []
    assert foreign_transport.cancel_calls == []


def test_create_rejection_is_sanitized_as_typed_smoke_stage_error(session) -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetSmokeError,
        run_lighter_testnet_create_cancel_smoke,
        verify_lighter_testnet_admission,
    )

    transport = RejectedCreateTransport(nonces=[100, 100])
    admission = verify_lighter_testnet_admission(
        credentials=_credentials(),
        shadow_result=_shadow(eligible=True),
        transport=transport,
        observed_at=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
    )

    try:
        run_lighter_testnet_create_cancel_smoke(
            admission=admission,
            session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
            transport=transport,
            market=_market(),
            client_order_id="secret-free-create-id",
            quantity=Decimal("0.01"),
            price=Decimal("4000.00"),
            is_ask=False,
        )
    except LighterTestnetSmokeError as exc:
        assert str(exc) == "Lighter testnet smoke action failed"
        assert exc.stage == "CREATE"
        assert exc.client_order_id == "secret-free-create-id"
        assert exc.create_tx_hash is None
        assert exc.__cause__ is None
        assert exc.__context__ is None
        assert transport.private_marker not in str(exc)
        assert transport.private_marker not in repr(exc)
    else:
        raise AssertionError("create rejection unexpectedly reached smoke success")


def test_cancel_failure_keeps_create_evidence_for_recovery_without_new_nonce(
    session,
) -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetSmokeError,
        run_lighter_testnet_cancel_recovery,
        run_lighter_testnet_create_cancel_smoke,
        verify_lighter_testnet_admission,
    )

    transport = RejectedThenSuccessfulCancelTransport(nonces=[100, 100, 101])
    admission = verify_lighter_testnet_admission(
        credentials=_credentials(),
        shadow_result=_shadow(eligible=True),
        transport=transport,
        observed_at=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
    )
    session_factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

    try:
        run_lighter_testnet_create_cancel_smoke(
            admission=admission,
            session_factory=session_factory,
            transport=transport,
            market=_market(),
            client_order_id="sai075-recovery-1",
            quantity=Decimal("0.01"),
            price=Decimal("4000.00"),
            is_ask=False,
        )
    except LighterTestnetSmokeError as exc:
        assert str(exc) == "Lighter testnet smoke action failed"
        assert exc.stage == "CANCEL"
        assert exc.client_order_id == "sai075-recovery-1"
        assert exc.create_tx_hash == "0xcreate"
        assert exc.__cause__ is None
        assert exc.__context__ is None
        assert transport.private_marker not in str(exc)
        assert transport.private_marker not in repr(exc)
        recovery = run_lighter_testnet_cancel_recovery(
            admission=admission,
            session_factory=session_factory,
            transport=transport,
            market=_market(),
            client_order_id=exc.client_order_id,
            create_tx_hash=exc.create_tx_hash,
        )
    else:
        raise AssertionError("cancel rejection unexpectedly reached smoke success")

    assert recovery.client_order_id == "sai075-recovery-1"
    assert recovery.create_tx_hash == "0xcreate"
    assert recovery.cancel_tx_hash == "0xcancel"
    assert recovery.eligible_for_live is False
    assert len(transport.create_calls) == 1
    assert len(transport.cancel_calls) == 2
    assert transport.next_nonce_calls == 3
