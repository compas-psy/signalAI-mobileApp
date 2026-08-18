from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.backtest.costs import CostModel, HistoricalCostOverride, VenueCostProfile


BASE = datetime(2026, 8, 18, 9, tzinfo=UTC)


def model(**overrides) -> CostModel:
    values = {
        "maker_fee_bps": Decimal("1"),
        "taker_fee_bps": Decimal("2"),
        "entry_slippage_bps": Decimal("3"),
        "exit_slippage_bps": Decimal("4"),
        "funding_bps_per_interval": Decimal("0.5"),
        "spread_bps": Decimal("2"),
    }
    values.update(overrides)
    return CostModel(**values)


def test_cost_model_converts_execution_friction_to_r_units():
    costs = model()
    # entry=100, risk=5 -> 20 notional-risk ratio. Taker 2bps + entry
    # slippage 3bps + half-spread 1bp = 6bps => 20 * 0.0006 = 0.012R.
    assert costs.entry_cost_r(
        price=Decimal("100"), risk_per_unit=Decimal("5"), maker=False
    ) == Decimal("0.012")
    # exit at 110: taker 2bps + exit slippage 4bps + half-spread 1bp.
    assert costs.exit_cost_r(
        price=Decimal("110"), risk_per_unit=Decimal("5"), maker=False
    ) == Decimal("0.0154")
    assert costs.funding_cost_r(
        reference_price=Decimal("100"), risk_per_unit=Decimal("5"), intervals=3
    ) == Decimal("0.003")


def test_stress_multiplier_scales_every_cost_component_without_mutating_base():
    base = model()
    stressed = base.stressed(Decimal("1.5"))

    assert stressed.taker_fee_bps == Decimal("3.0")
    assert stressed.entry_slippage_bps == Decimal("4.5")
    assert stressed.funding_bps_per_interval == Decimal("0.75")
    assert stressed.spread_bps == Decimal("3.0")
    assert base.taker_fee_bps == Decimal("2")


@pytest.mark.parametrize("multiplier", [Decimal("0"), Decimal("-1")])
def test_stress_multiplier_must_be_positive(multiplier: Decimal):
    with pytest.raises(ValueError, match="stress multiplier"):
        model().stressed(multiplier)


def test_historical_override_is_point_in_time_and_default_remains_available():
    default = model(taker_fee_bps=Decimal("2"))
    historical = model(taker_fee_bps=Decimal("7"))
    profile = VenueCostProfile(
        venue="BYBIT",
        default=default,
        overrides=(
            HistoricalCostOverride(
                effective_from=datetime(2026, 8, 1, tzinfo=UTC),
                effective_to=datetime(2026, 8, 10, tzinfo=UTC),
                model=historical,
                source_ref="fee-snapshot-2026-08-01",
            ),
        ),
    )

    resolved = profile.resolve(datetime(2026, 8, 5, tzinfo=UTC))
    assert resolved.model.taker_fee_bps == Decimal("7")
    assert resolved.source_ref == "fee-snapshot-2026-08-01"

    default_resolved = profile.resolve(BASE)
    assert default_resolved.model == default
    assert default_resolved.source_ref == "venue-default:BYBIT"


def test_overlapping_historical_overrides_are_rejected():
    first = HistoricalCostOverride(
        effective_from=datetime(2026, 8, 1, tzinfo=UTC),
        effective_to=datetime(2026, 8, 10, tzinfo=UTC),
        model=model(),
        source_ref="first",
    )
    second = HistoricalCostOverride(
        effective_from=datetime(2026, 8, 9, tzinfo=UTC),
        effective_to=datetime(2026, 8, 12, tzinfo=UTC),
        model=model(),
        source_ref="second",
    )

    with pytest.raises(ValueError, match="overlap"):
        VenueCostProfile(venue="BYBIT", default=model(), overrides=(first, second))


def test_negative_execution_cost_inputs_are_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        model(entry_slippage_bps=Decimal("-0.1"))
