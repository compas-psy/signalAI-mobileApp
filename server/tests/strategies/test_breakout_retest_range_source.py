from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import app.strategies.breakout_retest as breakout_retest
from app.detectors.wyckoff import TradingRange
from app.market.candles import Candle
from app.models.enums import DerivativesFlow, LiquidityRegime, TrendRegime, VolatilityRegime
from app.regime.classifier import RegimeResult
from app.strategies.base import SetupContext


def _bars(count: int = 30) -> tuple[Candle, ...]:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    return tuple(
        Candle(
            open_time=start + timedelta(hours=4 * index),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume_units=Decimal("10"),
            volume_notional=Decimal("1000"),
            source="test",
        )
        for index in range(count)
    )


def _context() -> SetupContext:
    bars = _bars()
    return SetupContext(
        instrument_id="CRYPTO:PERP:BTCUSDT",
        context_bars=bars,
        setup_bars=bars,
        trigger_bars=bars,
        context_tf="D1",
        setup_tf="H4",
        trigger_tf="H1",
        regime=RegimeResult(
            trend=TrendRegime.RANGE,
            trend_score=0,
            volatility=VolatilityRegime.NORMAL,
            liquidity=LiquidityRegime.NORMAL,
            derivatives_flow=DerivativesFlow.NEUTRAL,
        ),
        horizon_days=3,
        tick_size=Decimal("0.1"),
        wyckoff=None,
        atr_setup=Decimal("1"),
        atr_trigger=Decimal("1"),
    )


def test_breakout_uses_raw_range_when_wyckoff_reading_is_confidence_filtered(
    monkeypatch,
) -> None:
    raw_range = TradingRange(
        start_index=0,
        end_index=29,
        support=Decimal("95"),
        resistance=Decimal("105"),
        support_touches=3,
        resistance_touches=3,
        inside_ratio=0.95,
        width_atr=5.0,
    )
    calls = 0

    def fake_detect_range(_candles):
        nonlocal calls
        calls += 1
        return raw_range

    # The raw range is a lower-level fact than a confidence-gated Wyckoff
    # phase reading. A breakout must not disappear merely because no Spring,
    # climax or LPS has enough confidence for the Wyckoff strategy itself.
    monkeypatch.setattr(breakout_retest, "detect_range", fake_detect_range, raising=False)

    outcome = breakout_retest.build(_context())

    assert calls == 1
    assert outcome.checks[0].name == "range"
    assert outcome.checks[0].passed is True
