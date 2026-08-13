from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.api.v1.live_market import display_bars
from app.api.v1.market import get_bars
from app.market.candles import Candle
from app.models import Bar, Instrument
from app.models.enums import AssetClass, Timeframe, Venue

NOW = datetime(2026, 8, 9, 12, 30, tzinfo=UTC)


def test_crypto_chart_contains_current_display_candle_without_polluting_db(
    session, monkeypatch
):
    instrument = Instrument(
        instrument_id="CRYPTO:PERP:DOGEUSDT",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        symbol="DOGEUSDT",
        title="DOGE / USDT perpetual",
        currency="USDT",
        tick_size=Decimal("0.0001"),
        tick_value=Decimal("1"),
        lot_size=1,
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        in_universe=True,
    )
    session.add(instrument)
    # ``Bar.instrument_id`` points to the business key rather than the ORM
    # relationship, so make the FK parent explicit before inserting the bar.
    session.flush()
    session.add(
        Bar(
            instrument_id=instrument.instrument_id,
            timeframe=Timeframe.H1,
            open_time=NOW - timedelta(hours=2),
            open=Decimal("0.098"),
            high=Decimal("0.100"),
            low=Decimal("0.097"),
            close=Decimal("0.099"),
            is_closed=True,
            source="bybit",
        )
    )
    session.flush()

    forming = Candle(
        open_time=NOW.replace(minute=0, second=0, microsecond=0),
        open=Decimal("0.100"),
        high=Decimal("0.104"),
        low=Decimal("0.099"),
        close=Decimal("0.103"),
        is_closed=False,
        source="bybit",
    )

    def fake_klines(*_args, **_kwargs):
        return [forming], object()

    monkeypatch.setattr("app.api.v1.market.crypto.klines", fake_klines)

    rows = get_bars(
        instrument.instrument_id,
        timeframe=Timeframe.H1,
        limit=300,
        closed_only=True,
        display_live=True,
        db=session,
    )

    assert rows[-1].open_time == forming.open_time
    assert rows[-1].close == forming.close
    assert rows[-1].is_closed is False
    assert rows[-1].source == "bybit-live-display"
    assert "DISPLAY_ONLY" in rows[-1].quality_flags

    # The overlay is a UI fact, not a canonical calculation input.
    persisted = session.query(Bar).filter_by(instrument_id=instrument.instrument_id).all()
    assert len(persisted) == 1


def test_empty_moex_h4_display_chart_falls_back_to_h1_without_mislabeling(
    session, monkeypatch
):
    instrument = Instrument(
        instrument_id="MOEX:FUT:TESTU6",
        venue=Venue.MOEX,
        asset_class=AssetClass.FUTURES,
        symbol="TESTU6",
        title="Test future",
        currency="RUB",
        tick_size=Decimal("1"),
        tick_value=Decimal("1"),
        lot_size=1,
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        in_universe=True,
    )
    session.add(instrument)
    session.flush()

    h1 = Candle(
        open_time=NOW.replace(minute=0, second=0, microsecond=0),
        open=Decimal("100"),
        high=Decimal("103"),
        low=Decimal("99"),
        close=Decimal("102"),
        is_closed=False,
        source="moex",
    )

    def fake_live(_instrument, timeframe, *, limit, now):
        assert limit == 200
        assert now.tzinfo is not None
        return [h1] if timeframe is Timeframe.H1 else []

    monkeypatch.setattr("app.api.v1.live_market._moex_live", fake_live)

    result = display_bars(
        instrument.instrument_id,
        timeframe=Timeframe.H4,
        limit=200,
        db=session,
    )

    assert result.timeframe is Timeframe.H1
    assert result.live_overlay is True
    assert len(result.bars) == 1
    assert result.bars[0].open_time == h1.open_time
    assert result.bars[0].close == h1.close
    assert "DISPLAY_ONLY" in result.bars[0].quality_flags
