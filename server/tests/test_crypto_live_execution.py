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


def _fetch_minutes(rows):
    def fake_fetch(url: str):
        assert "symbol=DOGEUSDT" in url
        assert "interval=1" in url
        return (
            {
                "retCode": 0,
                "result": {
                    "list": [
                        [
                            str(_ms(at)),
                            str(open_),
                            str(high),
                            str(low),
                            str(close),
                            "100000",
                            "10150",
                        ]
                        for at, open_, high, low, close in reversed(rows)
                    ]
                },
            },
            object(),
        )

    return fake_fetch


def test_confirmed_crypto_entry_is_seen_from_closed_minute_without_waiting_for_h1(
    session,
):
    instrument = _doge(session)
    idea = _idea(session, instrument)
    trade, created = approve_for(session, idea, now=NOW)
    assert created is True
    assert trade.status is PaperStatus.PENDING

    minute = NOW + timedelta(minutes=1)

    report = track_crypto_live(
        session,
        now=NOW + timedelta(minutes=3),
        fetch=_fetch_minutes(
            [
                (
                    minute,
                    Decimal("0.1010"),
                    Decimal("0.1020"),
                    Decimal("0.0990"),
                    Decimal("0.1015"),
                )
            ]
        ),
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

    report = track_crypto_live(
        session,
        now=NOW + timedelta(minutes=2, seconds=30),
        fetch=_fetch_minutes(
            [
                (
                    forming,
                    Decimal("0.1010"),
                    Decimal("0.1020"),
                    Decimal("0.0990"),
                    Decimal("0.1015"),
                )
            ]
        ),
    )

    assert report.reconciled == 0
    assert trade.status is PaperStatus.PENDING


def test_legacy_h1_breakeven_handoff_does_not_replay_same_hour_as_minutes(session):
    """Do not fabricate BE by replaying an H1 bar after its stop already moved.

    The old H1 tracker consumed the 12:00-13:00 bar and stored 12:00 as its
    cursor. It also moved the stop to entry after TP1. A minute replay starting
    at 12:01 would now see the earlier dip through entry using the *new* stop
    and close the trade even though that dip may have happened before TP1.
    """
    instrument = _doge(session)
    idea = _idea(session, instrument)
    trade, _ = approve_for(session, idea, now=NOW - timedelta(hours=1))
    trade.status = PaperStatus.OPEN
    trade.tps_taken = 1
    trade.current_stop = trade.entry
    trade.breakeven_at = NOW
    trade.last_reconciled_at = NOW
    session.flush()

    report = track_crypto_live(
        session,
        now=NOW + timedelta(hours=1, minutes=5),
        fetch=_fetch_minutes(
            [
                # Already consumed by the legacy H1 tracker. Its low is below
                # the stop that only became active later in that hour.
                (
                    NOW + timedelta(minutes=10),
                    Decimal("0.1010"),
                    Decimal("0.1060"),
                    Decimal("0.0990"),
                    Decimal("0.1055"),
                ),
                # First minute after the consumed hour: safely above BE.
                (
                    NOW + timedelta(hours=1),
                    Decimal("0.1060"),
                    Decimal("0.1070"),
                    Decimal("0.1055"),
                    Decimal("0.1065"),
                ),
            ]
        ),
    )

    assert report.reconciled == 1
    assert trade.status is PaperStatus.OPEN
    assert trade.outcome == ""
    assert trade.last_reconciled_at == NOW + timedelta(hours=1)


def test_real_minute_breakeven_at_hour_boundary_is_not_mistaken_for_h1_handoff(session):
    """A genuine xx:00 minute cursor remains live on the next minute."""
    instrument = _doge(session)
    idea = _idea(session, instrument)
    trade, _ = approve_for(session, idea, now=NOW - timedelta(minutes=5))
    trade.status = PaperStatus.OPEN
    trade.tps_taken = 1
    trade.current_stop = trade.entry
    trade.breakeven_at = NOW
    trade.last_reconciled_at = NOW
    session.flush()

    report = track_crypto_live(
        session,
        now=NOW + timedelta(minutes=2),
        fetch=_fetch_minutes(
            [
                (
                    NOW + timedelta(minutes=1),
                    Decimal("0.1010"),
                    Decimal("0.1020"),
                    Decimal("0.0995"),
                    Decimal("0.1002"),
                )
            ]
        ),
    )

    assert report.reconciled == 1
    assert trade.status is PaperStatus.CLOSED
    assert trade.outcome == "BE"
