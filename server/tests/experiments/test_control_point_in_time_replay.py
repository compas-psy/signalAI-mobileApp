from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.config import get_config
from app.experiments import paper_ab_runtime_v1
from app.models import Bar, Instrument
from app.models.enums import AssetClass, Timeframe, Venue
from app.pipeline.scan import _load_bars


# Historical decision time used to prove the replay never sees future bars.
BASE = datetime(2026, 8, 1, 12, tzinfo=UTC)


def _instrument() -> Instrument:
    return Instrument(
        instrument_id="CRYPTO:PERP:BTCUSDT",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        symbol="BTCUSDT",
        currency="USDT",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("5"),
        contract_multiplier=Decimal("1"),
        in_universe=True,
        is_tradable=True,
        metadata_json={},
    )


def _bar(instrument_id: str, at: datetime, close: str) -> Bar:
    value = Decimal(close)
    return Bar(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        open_time=at,
        open=value,
        high=value + Decimal("1"),
        low=value - Decimal("1"),
        close=value,
        volume_units=Decimal("100"),
        volume_notional=Decimal("1000000"),
        open_interest=None,
        is_closed=True,
        source="fixture",
        quality_flags=[],
    )


def test_scan_bar_loader_excludes_rows_after_historical_decision_time(session):
    instrument = _instrument()
    session.add(instrument)
    session.flush()
    session.add_all(
        (
            _bar(instrument.instrument_id, BASE - timedelta(hours=1), "100"),
            _bar(instrument.instrument_id, BASE + timedelta(hours=1), "200"),
        )
    )
    session.flush()

    rows = _load_bars(
        session,
        instrument.instrument_id,
        Timeframe.H1,
        10,
        as_of=BASE,
    )

    assert [row.open_time for row in rows] == [BASE - timedelta(hours=1)]
    assert [row.close for row in rows] == [Decimal("100")]


def test_paper_ab_historical_control_delegates_to_point_in_time_scan(
    session,
    monkeypatch,
):
    instrument = _instrument()
    session.add(instrument)
    session.flush()
    session.add(_bar(instrument.instrument_id, BASE + timedelta(hours=1), "200"))
    session.flush()

    calls: list[datetime] = []

    def fake_scan(
        _session,
        _instrument,
        *,
        cfg,
        risk_state,
        now,
        event_calendar,
    ):
        calls.append(now)
        return None, [], []

    monkeypatch.setattr(paper_ab_runtime_v1, "scan_instrument", fake_scan)
    monkeypatch.setattr(
        paper_ab_runtime_v1,
        "load_owned_calendar",
        lambda *, now: object(),
    )

    provider = paper_ab_runtime_v1._default_control_provider(get_config())
    result = provider(session, instrument, BASE)

    assert calls == [BASE]
    assert result.signal_emitted is False
    assert result.unavailable_reason is None
