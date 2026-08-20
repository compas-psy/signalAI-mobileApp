from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtest.costs import CostModel
from app.market import crypto
from app.market.derivatives import CryptoCarryMarketFacts, FundingObservation
from app.market.http import FetchReport
from app.models.enums import Direction
from app.strategies.crypto_carry_v1 import evaluate_crypto_carry_v1
from app.strategies.result_v2 import StrategyResultV2


EVALUATED_AT = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)


def _report(url: str) -> FetchReport:
    return FetchReport(url=url, status=200, elapsed_ms=1, bytes_read=1, ok=True)


def _bybit_fetch(url: str):
    if "/v5/market/tickers" in url:
        return (
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "lastPrice": "100.8",
                            "markPrice": "100.5",
                            "indexPrice": "100",
                            "fundingRate": "0.0008",
                            "nextFundingTime": "1787241600000",
                            "basis": "0.5",
                            "basisRate": "0.005",
                            "bid1Price": "100.4",
                            "ask1Price": "100.6",
                        }
                    ]
                },
            },
            _report(url),
        )
    if "/v5/market/instruments-info" in url:
        return (
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "status": "Trading",
                            "contractType": "LinearPerpetual",
                            "baseCoin": "BTC",
                            "quoteCoin": "USDT",
                            "fundingInterval": 480,
                            "priceFilter": {"tickSize": "0.1"},
                            "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"},
                            "launchTime": "1700000000000",
                            "deliveryTime": "0",
                        }
                    ]
                },
            },
            _report(url),
        )
    if "/v5/market/funding/history" in url:
        # Bybit commonly returns newest first. The adapter must normalize order.
        return (
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "fundingRate": "0.0008",
                            "fundingRateTimestamp": "1787241600000",
                        },
                        {
                            "symbol": "BTCUSDT",
                            "fundingRate": "0.0007",
                            "fundingRateTimestamp": "1787212800000",
                        },
                    ]
                },
            },
            _report(url),
        )
    raise AssertionError(f"unexpected URL: {url}")


def test_bybit_carry_facts_use_only_public_point_in_time_market_data() -> None:
    asked: list[str] = []

    def fetch(url: str):
        asked.append(url)
        return _bybit_fetch(url)

    facts, reports = crypto.carry_market_facts(
        "BTCUSDT",
        evaluated_at=EVALUATED_AT,
        funding_limit=24,
        fetch=fetch,
    )

    assert isinstance(facts, CryptoCarryMarketFacts)
    assert facts.instrument_id == "CRYPTO:BTCUSDT"
    assert facts.mark_price == Decimal("100.5")
    assert facts.index_price == Decimal("100")
    assert facts.current_funding_rate == Decimal("0.0008")
    assert facts.funding_interval_minutes == 480
    assert facts.mark_index_basis_rate == Decimal("0.005")
    assert [item.rate for item in facts.funding_history] == [
        Decimal("0.0007"),
        Decimal("0.0008"),
    ]
    assert all(item.tradable_at <= EVALUATED_AT for item in facts.funding_history)
    assert facts.source == "bybit-v5-public"
    assert len(reports) == 3
    assert any("/v5/market/funding/history" in url for url in asked)
    assert all("symbol=BTCUSDT" in url for url in asked)
    # Point-in-time query must have an upper bound; future funding must not leak in.
    funding_url = next(url for url in asked if "/funding/history" in url)
    assert "endTime=" in funding_url


def test_canonical_cost_model_exposes_auditable_round_trip_bps_projection() -> None:
    model = CostModel(
        maker_fee_bps=Decimal("1"),
        taker_fee_bps=Decimal("2"),
        entry_slippage_bps=Decimal("3"),
        exit_slippage_bps=Decimal("4"),
        funding_bps_per_interval=Decimal("0.5"),
        spread_bps=Decimal("6"),
    )

    assert model.round_trip_bps(entry_maker=False, exit_maker=False) == Decimal("17")
    assert model.round_trip_bps(
        entry_maker=True,
        exit_maker=True,
        funding_intervals=2,
    ) == Decimal("16")


def _facts(
    rates: tuple[str, ...],
    *,
    current: str | None = None,
    mark: str = "100.05",
    index: str = "100",
    interval_minutes: int = 480,
    last_age_intervals: int = 0,
) -> CryptoCarryMarketFacts:
    interval = timedelta(minutes=interval_minutes)
    last = EVALUATED_AT - interval * last_age_intervals
    history = tuple(
        FundingObservation(
            rate=Decimal(rate),
            settled_at=last - interval * (len(rates) - index_ - 1),
            tradable_at=last - interval * (len(rates) - index_ - 1),
            source="fixture",
        )
        for index_, rate in enumerate(rates)
    )
    return CryptoCarryMarketFacts(
        instrument_id="CRYPTO:BTCUSDT",
        mark_price=Decimal(mark),
        index_price=Decimal(index),
        current_funding_rate=Decimal(current if current is not None else rates[-1]),
        funding_interval_minutes=interval_minutes,
        funding_history=history,
        observed_at=EVALUATED_AT,
        tradable_at=EVALUATED_AT,
        source="fixture",
    )


def _execution_bps() -> Decimal:
    # Two hedged legs, both projected from the same canonical CostModel contract.
    perp = CostModel(
        maker_fee_bps=Decimal("0.1"),
        taker_fee_bps=Decimal("0.2"),
        entry_slippage_bps=Decimal("0.1"),
        exit_slippage_bps=Decimal("0.1"),
        funding_bps_per_interval=Decimal("0"),
        spread_bps=Decimal("0.2"),
    )
    hedge = CostModel(
        maker_fee_bps=Decimal("0.1"),
        taker_fee_bps=Decimal("0.2"),
        entry_slippage_bps=Decimal("0.1"),
        exit_slippage_bps=Decimal("0.1"),
        funding_bps_per_interval=Decimal("0"),
        spread_bps=Decimal("0.2"),
    )
    return perp.round_trip_bps(entry_maker=False, exit_maker=False) + hedge.round_trip_bps(
        entry_maker=False, exit_maker=False
    )


@pytest.mark.parametrize(
    ("rates", "current", "mark", "expected_direction"),
    [
        (("0.0007", "0.0008", "0.0009", "0.0008", "0.0007", "0.0008"), "0.0008", "100.05", Direction.SHORT),
        (("-0.0007", "-0.0008", "-0.0009", "-0.0008", "-0.0007", "-0.0008"), "-0.0008", "99.95", Direction.LONG),
    ],
)
def test_crypto_carry_v1_requires_persistent_funding_and_returns_perp_leg_direction(
    rates: tuple[str, ...],
    current: str,
    mark: str,
    expected_direction: Direction,
) -> None:
    result = evaluate_crypto_carry_v1(
        facts=_facts(rates, current=current, mark=mark),
        execution_cost_bps=_execution_bps(),
        hedge_carry_bps_per_interval=Decimal("0.20"),
        funding_uncertainty_bps_per_interval=Decimal("0.20"),
        evaluated_at=EVALUATED_AT,
    )

    assert isinstance(result, StrategyResultV2)
    assert result.strategy_family == "CRYPTO_CARRY"
    assert result.strategy_version == "crypto_carry_v1"
    assert result.direction is expected_direction
    assert result.entry_hypothesis.kind == "HEDGED_CARRY"
    assert result.raw_edge_score > 0
    assert {feature.name for feature in result.feature_provenance} >= {
        "median_funding_rate",
        "funding_same_sign_ratio",
        "mark_index_basis_rate",
        "projected_funding_bps",
        "execution_cost_bps",
        "hedge_carry_bps",
        "basis_convergence_risk_bps",
        "net_carry_bps",
    }
    assert all(feature.tradable_at <= EVALUATED_AT for feature in result.feature_provenance)


def test_crypto_carry_v1_does_not_trade_one_extreme_funding_print() -> None:
    facts = _facts(("0.00001", "0.00001", "0.00001", "0.00001", "0.00001", "0.0100"))

    assert (
        evaluate_crypto_carry_v1(
            facts=facts,
            execution_cost_bps=Decimal("1"),
            hedge_carry_bps_per_interval=Decimal("0"),
            funding_uncertainty_bps_per_interval=Decimal("0"),
            evaluated_at=EVALUATED_AT,
        )
        is None
    )


def test_crypto_carry_v1_rejects_gross_edge_that_does_not_survive_all_costs() -> None:
    facts = _facts(("0.0003",) * 6, mark="100.04")

    assert (
        evaluate_crypto_carry_v1(
            facts=facts,
            execution_cost_bps=Decimal("8"),
            hedge_carry_bps_per_interval=Decimal("0.5"),
            funding_uncertainty_bps_per_interval=Decimal("0.5"),
            evaluated_at=EVALUATED_AT,
        )
        is None
    )


def test_crypto_carry_v1_fails_closed_on_stale_history() -> None:
    facts = _facts(("0.0008",) * 6, last_age_intervals=3)

    assert (
        evaluate_crypto_carry_v1(
            facts=facts,
            execution_cost_bps=Decimal("1"),
            hedge_carry_bps_per_interval=Decimal("0"),
            funding_uncertainty_bps_per_interval=Decimal("0"),
            evaluated_at=EVALUATED_AT,
        )
        is None
    )


def test_crypto_carry_v1_ignores_future_funding_observations() -> None:
    baseline = _facts(("0.0008",) * 6)
    future = FundingObservation(
        rate=Decimal("-0.50"),
        settled_at=EVALUATED_AT + timedelta(hours=8),
        tradable_at=EVALUATED_AT + timedelta(hours=8),
        source="future-fixture",
    )
    contaminated = CryptoCarryMarketFacts(
        instrument_id=baseline.instrument_id,
        mark_price=baseline.mark_price,
        index_price=baseline.index_price,
        current_funding_rate=baseline.current_funding_rate,
        funding_interval_minutes=baseline.funding_interval_minutes,
        funding_history=(*baseline.funding_history, future),
        observed_at=baseline.observed_at,
        tradable_at=baseline.tradable_at,
        source=baseline.source,
    )

    kwargs = dict(
        execution_cost_bps=Decimal("1"),
        hedge_carry_bps_per_interval=Decimal("0"),
        funding_uncertainty_bps_per_interval=Decimal("0"),
        evaluated_at=EVALUATED_AT,
    )
    assert evaluate_crypto_carry_v1(facts=contaminated, **kwargs) == evaluate_crypto_carry_v1(
        facts=baseline, **kwargs
    )
