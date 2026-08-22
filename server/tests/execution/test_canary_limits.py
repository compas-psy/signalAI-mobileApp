from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest


def _policy(*, now: datetime | None = None) -> dict:
    moment = now or datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    return {
        "schema_version": 1,
        "venue": "LIGHTER",
        "environment": "mainnet",
        "market_allowlist": [1, 2],
        "instrument_allowlist": ["CRYPTO:PERP:BTCUSDT", "CRYPTO:PERP:ETHUSDT"],
        "capital_amount": "10000",
        "hard_caps": {
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
        "valid_until": (moment + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
    }


def _inputs():
    from app.execution.canary_limits import (
        CanaryDynamicLimits,
        CanaryEntryProposal,
        CanaryExposureState,
    )

    proposal = CanaryEntryProposal(
        instrument_id="CRYPTO:PERP:BTCUSDT",
        market_index=1,
        order_notional=Decimal("1000"),
        leverage=Decimal("1.5"),
        max_loss_amount=Decimal("100"),
        creates_position=False,
    )
    exposure = CanaryExposureState(
        gross_notional=Decimal("2000"),
        instrument_notional=Decimal("500"),
        open_positions=1,
        entry_orders=0,
        order_count=3,
        trade_count=2,
        daily_loss=Decimal("100"),
        total_loss=Decimal("200"),
    )
    dynamic = CanaryDynamicLimits(
        risk_engine_order_notional=Decimal("2200"),
        account_order_notional=Decimal("1800"),
        provider_order_notional=Decimal("1600"),
    )
    return proposal, exposure, dynamic


def _evaluate(policy: dict, *, proposal=None, exposure=None, dynamic=None, now=None):
    from app.execution.canary_limits import evaluate_canary_entry_limits

    base_proposal, base_exposure, base_dynamic = _inputs()
    return evaluate_canary_entry_limits(
        policy,
        proposal=proposal or base_proposal,
        exposure=exposure or base_exposure,
        dynamic_limits=dynamic or base_dynamic,
        now=now or datetime(2026, 8, 22, 12, 30, tzinfo=timezone.utc),
    )


def test_valid_entry_uses_minimum_of_policy_risk_account_and_provider_caps() -> None:
    decision = _evaluate(_policy())

    assert decision.allowed is True
    assert decision.blockers == ()
    assert decision.effective_order_notional_cap == Decimal("1600")


@pytest.mark.parametrize(
    ("field", "value", "blocker"),
    [
        ("market_index", 99, "MARKET_NOT_ALLOWED"),
        ("instrument_id", "CRYPTO:PERP:SOLUSDT", "INSTRUMENT_NOT_ALLOWED"),
    ],
)
def test_allowlists_are_default_deny(field: str, value, blocker: str) -> None:
    proposal, exposure, dynamic = _inputs()
    decision = _evaluate(
        _policy(),
        proposal=replace(proposal, **{field: value}),
        exposure=exposure,
        dynamic=dynamic,
    )

    assert decision.allowed is False
    assert blocker in decision.blockers


def test_prospective_notional_leverage_and_capital_bounds_fail_closed() -> None:
    proposal, exposure, dynamic = _inputs()

    order = _evaluate(
        _policy(),
        proposal=replace(proposal, order_notional=Decimal("1700")),
        exposure=exposure,
        dynamic=dynamic,
    )
    assert "ORDER_NOTIONAL_LIMIT" in order.blockers

    instrument = _evaluate(
        _policy(),
        proposal=replace(proposal, order_notional=Decimal("1000")),
        exposure=replace(exposure, instrument_notional=Decimal("4500")),
        dynamic=replace(
            dynamic,
            risk_engine_order_notional=Decimal("5000"),
            account_order_notional=Decimal("5000"),
            provider_order_notional=Decimal("5000"),
        ),
    )
    assert "INSTRUMENT_NOTIONAL_LIMIT" in instrument.blockers

    gross = _evaluate(
        _policy(),
        exposure=replace(exposure, gross_notional=Decimal("9500")),
        dynamic=replace(
            dynamic,
            risk_engine_order_notional=Decimal("5000"),
            account_order_notional=Decimal("5000"),
            provider_order_notional=Decimal("5000"),
        ),
    )
    assert "GROSS_NOTIONAL_LIMIT" in gross.blockers

    leverage = _evaluate(
        _policy(),
        proposal=replace(proposal, leverage=Decimal("2.1")),
    )
    assert "LEVERAGE_LIMIT" in leverage.blockers

    capital_policy = _policy()
    capital_policy["hard_caps"] = {
        **capital_policy["hard_caps"],
        "max_order_notional": "5000",
        "max_instrument_notional": "50000",
        "max_gross_notional": "50000",
    }
    capital = _evaluate(
        capital_policy,
        exposure=replace(exposure, gross_notional=Decimal("19500")),
        dynamic=replace(
            dynamic,
            risk_engine_order_notional=Decimal("5000"),
            account_order_notional=Decimal("5000"),
            provider_order_notional=Decimal("5000"),
        ),
    )
    assert "CAPITAL_LEVERAGE_LIMIT" in capital.blockers


def test_prospective_counts_and_losses_include_the_new_entry() -> None:
    proposal, exposure, dynamic = _inputs()

    counts = _evaluate(
        _policy(),
        proposal=replace(proposal, creates_position=True),
        exposure=replace(
            exposure,
            open_positions=2,
            entry_orders=2,
            order_count=10,
            trade_count=5,
        ),
    )
    assert {
        "OPEN_POSITIONS_LIMIT",
        "ENTRY_ORDERS_LIMIT",
        "ORDER_COUNT_LIMIT",
        "TRADE_COUNT_LIMIT",
    }.issubset(set(counts.blockers))

    losses = _evaluate(
        _policy(),
        proposal=replace(proposal, max_loss_amount=Decimal("100")),
        exposure=replace(
            exposure,
            daily_loss=Decimal("450"),
            total_loss=Decimal("950"),
        ),
    )
    assert "DAILY_LOSS_LIMIT" in losses.blockers
    assert "TOTAL_LOSS_LIMIT" in losses.blockers


def test_expired_or_malformed_policy_blocks_without_guessing() -> None:
    expired = _policy()
    expired["valid_until"] = "2026-08-22T12:00:00Z"
    decision = _evaluate(expired)
    assert decision.allowed is False
    assert "POLICY_EXPIRED" in decision.blockers

    malformed = _policy()
    del malformed["hard_caps"]["max_gross_notional"]
    decision = _evaluate(malformed)
    assert decision.allowed is False
    assert "POLICY_INVALID" in decision.blockers


def test_invalid_numeric_facts_and_missing_dynamic_limits_fail_closed() -> None:
    proposal, exposure, dynamic = _inputs()

    invalid_proposal = _evaluate(
        _policy(),
        proposal=replace(proposal, order_notional=Decimal("NaN")),
    )
    assert "PROPOSAL_INVALID" in invalid_proposal.blockers

    invalid_exposure = _evaluate(
        _policy(),
        exposure=replace(exposure, gross_notional=Decimal("-1")),
    )
    assert "EXPOSURE_INVALID" in invalid_exposure.blockers

    invalid_dynamic = _evaluate(
        _policy(),
        dynamic=replace(dynamic, provider_order_notional=None),
    )
    assert invalid_dynamic.allowed is False
    assert "DYNAMIC_LIMIT_MISSING_OR_INVALID" in invalid_dynamic.blockers
