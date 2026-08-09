from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import Instrument, TradeIdea
from app.models.enums import AssetClass, IdeaStatus, PaperStatus, Venue
from app.paper.live_tracker import track_crypto_live
from app.paper.tracker import approve_for
from tests.conftest import idea_kwargs

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _doge(session) -> Instrument:
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
        correlation_cluster="crypto_alt",
        in_universe=True,
    )
    session.add(instrument)
    session.flush()
    return instrument


def _idea(session, instrument: Instrument) -> TradeIdea:
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            NOW,
            direction="LONG",
            status=IdeaStatus.TRIGGERED,
            quality_status="ACTIVE",
            was_presented=True,
            entry_low=Decimal("0.0995"),
            entry_high=Decimal("0.1005"),
            entry_reference=Decimal("0.1000"),
            stop=Decimal("0.0950"),
            tp1=Decimal("0.1050"),
            tp2=Decimal("0.1100"),
            tp3=Decimal("0.1150"),
            risk_per_unit=Decimal("0.0050"),
            quantity=Decimal("1000"),
        )
    )
    session.add(idea)
    session.flush()
    return idea


def test_confirmed_crypto_entry_is_seen_from_closed_minute_without_waiting_for_h1(
    session,
):
    instrument = _doge(session)
    idea = _idea(session, instrument)
    trade, created = approve_for(session, idea, now=NOW)
    assert created is True
    assert trade.status is PaperStatus.PENDING

    minute = NOW + timedelta(minutes=1)

    def fake_fetch(url: str):
        assert "symbol=DOGEUSDT" in url
        assert "interval=1" in url
        return (
            {
                "retCode": 0,
                "result": {
                    "list": [
                        [
                            str(_ms(minute)),
                            "0.1010",
                            "0.1020",
                            "0.0990",
                            "0.1015",
                            "100000",
                            "10150",
                        ]
                    ]
                },
            },
            object(),
        )

    report = track_crypto_live(
        session,
        now=NOW + timedelta(minutes=3),
        fetch=fake_fetch,
    )

    assert report.checked == 1
    assert report.reconciled == 1
    assert report.filled == 1
    assert trade.status is PaperStatus.OPEN
    assert trade.last_reconciled_at == minute
    assert idea.status is IdeaStatus.FILLED


def test_forming_minute_is_not_used_as_execution_fact(session):
    instrument = _doge(session)
    idea = _idea(session, instrument)
    trade, _ = approve_for(session, idea, now=NOW)

    forming = NOW + timedelta(minutes=2)

    def fake_fetch(_url: str):
        return (
            {
                "retCode": 0,
                "result": {
                    "list": [
                        [
                            str(_ms(forming)),
                            "0.1010",
                            "0.1020",
                            "0.0990",
                            "0.1015",
                            "100000",
                            "10150",
                        ]
                    ]
                },
            },
            object(),
        )

    report = track_crypto_live(
        session,
        now=NOW + timedelta(minutes=2, seconds=30),
        fetch=fake_fetch,
    )

    assert report.reconciled == 0
    assert trade.status is PaperStatus.PENDING
