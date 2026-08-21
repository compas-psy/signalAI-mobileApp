from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.execution.venues.lighter_auth import LighterServerCredentials
from app.experiments.venue_shadow_scorecard_v1 import (
    VenueMetricDelta,
    VenueMetricPairCounts,
    VenueMetricSummary,
    VenueShadowScorecardResult,
    VenueShadowStatus,
)


def _eligible_shadow() -> VenueShadowScorecardResult:
    summary = VenueMetricSummary(
        total_cost_bps=Decimal("10"),
        ack_latency_ms=Decimal("100"),
        fill_slippage_bps=Decimal("2"),
        protection_latency_ms=Decimal("150"),
        ambiguity_rate=Decimal("0"),
        unavailable_rate=Decimal("0"),
        duplicate_execution_incidents=0,
        unprotected_execution_incidents=0,
    )
    return VenueShadowScorecardResult(
        status=VenueShadowStatus.PASS_EVIDENCE,
        reasons=("PASS_EVIDENCE",),
        paired_opportunities=1,
        bybit=summary,
        lighter=summary,
        lighter_minus_bybit=VenueMetricDelta(
            total_cost_bps=Decimal("0"),
            ack_latency_ms=Decimal("0"),
            fill_slippage_bps=Decimal("0"),
            protection_latency_ms=Decimal("0"),
            ambiguity_rate=Decimal("0"),
        ),
        metric_pairs=VenueMetricPairCounts(
            total_cost_bps=1,
            ack_latency_ms=1,
            fill_slippage_bps=1,
            protection_latency_ms=1,
            ambiguity_rate=1,
        ),
        weighted_score=None,
        eligible_for_testnet=True,
    )


class SameScopeTransport:
    base_url = "https://testnet.zklighter.elliot.ai"
    chain_id = 300
    account_index = 42
    api_key_index = 3

    def __init__(self) -> None:
        self.check_calls = 0
        self.nonce_calls = 0
        self.create_calls = 0
        self.cancel_calls = 0

    def check_client(self) -> str | None:
        self.check_calls += 1
        return None

    def next_nonce(self) -> int:
        self.nonce_calls += 1
        return 100 + self.nonce_calls - 1

    def create_order(self, **kwargs):
        self.create_calls += 1
        raise AssertionError("foreign transport must not reach provider create")

    def cancel_order(self, **kwargs):
        self.cancel_calls += 1
        raise AssertionError("foreign transport must not reach provider cancel")


def test_admission_cannot_move_to_another_transport_with_identical_public_scope() -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetAdmissionError,
        run_lighter_testnet_create_cancel_smoke,
        verify_lighter_testnet_admission,
    )

    verified_transport = SameScopeTransport()
    admission = verify_lighter_testnet_admission(
        credentials=LighterServerCredentials(
            account_index=42,
            api_key_index=3,
            api_private_key="ab" * 32,
            environment="testnet",
            purpose="trade",
        ),
        shadow_result=_eligible_shadow(),
        transport=verified_transport,
        observed_at=datetime(2026, 8, 21, 19, 30, tzinfo=UTC),
    )
    assert admission.eligible_for_order_smoke is True
    assert verified_transport.check_calls == 1
    assert verified_transport.nonce_calls == 1

    foreign_same_scope = SameScopeTransport()
    with pytest.raises(LighterTestnetAdmissionError, match="transport"):
        run_lighter_testnet_create_cancel_smoke(
            admission=admission,
            session_factory=lambda: (_ for _ in ()).throw(
                AssertionError("foreign transport must be rejected before database access")
            ),
            transport=foreign_same_scope,
            market=None,  # type: ignore[arg-type]
            client_order_id="sai075-foreign-same-scope",
            quantity=Decimal("0.01"),
            price=Decimal("4000"),
            is_ask=False,
        )

    assert foreign_same_scope.check_calls == 0
    assert foreign_same_scope.nonce_calls == 0
    assert foreign_same_scope.create_calls == 0
    assert foreign_same_scope.cancel_calls == 0


def test_cancel_recovery_rejects_foreign_same_scope_transport_before_action_io() -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetAdmissionError,
        run_lighter_testnet_cancel_recovery,
        verify_lighter_testnet_admission,
    )

    verified_transport = SameScopeTransport()
    admission = verify_lighter_testnet_admission(
        credentials=LighterServerCredentials(
            account_index=42,
            api_key_index=3,
            api_private_key="ab" * 32,
            environment="testnet",
            purpose="trade",
        ),
        shadow_result=_eligible_shadow(),
        transport=verified_transport,
        observed_at=datetime(2026, 8, 21, 19, 30, tzinfo=UTC),
    )
    foreign_same_scope = SameScopeTransport()

    with pytest.raises(LighterTestnetAdmissionError, match="transport"):
        run_lighter_testnet_cancel_recovery(
            admission=admission,
            session_factory=lambda: (_ for _ in ()).throw(
                AssertionError(
                    "foreign transport must be rejected before database access"
                )
            ),
            transport=foreign_same_scope,
            market=None,  # type: ignore[arg-type]
            client_order_id="sai075-recovery-foreign-same-scope",
            create_tx_hash="0xcreate",
        )

    assert foreign_same_scope.check_calls == 0
    assert foreign_same_scope.nonce_calls == 0
    assert foreign_same_scope.create_calls == 0
    assert foreign_same_scope.cancel_calls == 0


def test_cancel_recovery_requires_nonblank_create_hash_before_action_io() -> None:
    from app.execution.venues.lighter_testnet_verification import (
        LighterTestnetAdmissionError,
        run_lighter_testnet_cancel_recovery,
        verify_lighter_testnet_admission,
    )

    assert (
        inspect.signature(run_lighter_testnet_cancel_recovery)
        .parameters["create_tx_hash"]
        .default
        is inspect.Parameter.empty
    )
    transport = SameScopeTransport()
    admission = verify_lighter_testnet_admission(
        credentials=LighterServerCredentials(
            account_index=42,
            api_key_index=3,
            api_private_key="ab" * 32,
            environment="testnet",
            purpose="trade",
        ),
        shadow_result=_eligible_shadow(),
        transport=transport,
        observed_at=datetime(2026, 8, 21, 19, 30, tzinfo=UTC),
    )

    for create_tx_hash in (None, "", "  "):
        with pytest.raises(LighterTestnetAdmissionError, match="create hash"):
            run_lighter_testnet_cancel_recovery(
                admission=admission,
                session_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("invalid create hash must not reach database")
                ),
                transport=transport,
                market=None,  # type: ignore[arg-type]
                client_order_id="sai075-recovery-missing-hash",
                create_tx_hash=create_tx_hash,
            )

    assert transport.check_calls == 1
    assert transport.nonce_calls == 1
    assert transport.create_calls == 0
    assert transport.cancel_calls == 0
