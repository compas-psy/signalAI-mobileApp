from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market.derivatives import CryptoCarryMarketFacts, FundingObservation
from app.models import (
    Bar,
    ExecutionIntent,
    Instrument,
    NotificationOutbox,
    PaperTrade,
    ShadowObservation,
    TradeIdea,
)
from app.models.enums import AssetClass, Timeframe, Venue
from app.shadow.collector_v1 import (
    ShadowSupplementalFacts,
    collect_shadow,
)
from app.shadow.runtime_v1 import ShadowEvidenceStatus


AT = datetime(2026, 8, 20, 20, 0, tzinfo=UTC)


def _instrument(session, instrument_id: str = "CRYPTO:BTCUSDT") -> Instrument:
    row = Instrument(
        instrument_id=instrument_id,
        symbol="BTCUSDT",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        currency="USDT",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("5"),
        contract_multiplier=Decimal("1"),
        is_tradable=True,
        in_universe=True,
        metadata_json={"spread_snapshot": "0.0005"},
    )
    session.add(row)
    session.flush()
    return row


def _bar(
    session,
    instrument_id: str,
    timeframe: Timeframe,
    open_time: datetime,
    close: Decimal,
    *,
    closed: bool = True,
) -> None:
    session.add(
        Bar(
            instrument_id=instrument_id,
            timeframe=timeframe,
            open_time=open_time,
            open=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
            volume_units=Decimal("1000"),
            volume_notional=Decimal("100000"),
            open_interest=Decimal("10000"),
            is_closed=closed,
            source="fixture",
            quality_flags=[],
        )
    )


def _history(session, instrument_id: str) -> None:
    # Enough D1/H1 history for momentum/mean-reversion/breakout evaluation.
    for index in range(80):
        _bar(
            session,
            instrument_id,
            Timeframe.D1,
            AT - timedelta(days=80 - index),
            Decimal("100") + Decimal(index) / Decimal("10"),
        )
    for index in range(240):
        _bar(
            session,
            instrument_id,
            Timeframe.H1,
            AT - timedelta(hours=240 - index),
            Decimal("100") + Decimal(index) / Decimal("100"),
        )
    session.flush()


def _facts(_instrument: Instrument, _evaluated_at: datetime) -> ShadowSupplementalFacts:
    # Measurement identity is explicit; no hidden cost assumptions are created.
    return ShadowSupplementalFacts(
        cost_model_hash="b" * 64,
        spread_bps=Decimal("5"),
        round_trip_cost_bps=Decimal("12"),
        crypto_carry_facts=None,
        carry_unavailable_reason="FUNDING_FACTS_UNAVAILABLE",
    )


def _carry_facts() -> CryptoCarryMarketFacts:
    history = tuple(
        FundingObservation(
            rate=Decimal("0.0010"),
            settled_at=AT - timedelta(hours=8 * offset),
            tradable_at=AT - timedelta(hours=8 * offset),
            source="fixture",
        )
        for offset in range(6, 0, -1)
    )
    return CryptoCarryMarketFacts(
        instrument_id="CRYPTO:BTCUSDT",
        mark_price=Decimal("100"),
        index_price=Decimal("100"),
        current_funding_rate=Decimal("0.0010"),
        funding_interval_minutes=480,
        funding_history=history,
        observed_at=AT,
        tradable_at=AT,
        source="bybit-v5-public",
    )


def test_collector_persists_one_denominator_observation_per_r4_candidate(session) -> None:
    instrument = _instrument(session)
    _history(session, instrument.instrument_id)

    report = collect_shadow(session, evaluated_at=AT, facts_provider=_facts)
    session.flush()

    rows = (
        session.query(ShadowObservation)
        .filter_by(instrument_id=instrument.instrument_id)
        .all()
    )
    assert report.instruments == 1
    assert report.observations == 4
    assert len(rows) == 4
    assert {row.strategy_version for row in rows} == {
        "momentum_v2",
        "mean_reversion_v1",
        "crypto_carry_v1",
        "breakout_v2",
    }
    carry = next(row for row in rows if row.strategy_version == "crypto_carry_v1")
    assert carry.evidence_status == ShadowEvidenceStatus.INPUT_UNAVAILABLE.value
    assert carry.reason_code == "FUNDING_FACTS_UNAVAILABLE"


def test_default_provider_resolves_public_bybit_carry_facts(session, monkeypatch) -> None:
    instrument = _instrument(session)
    _history(session, instrument.instrument_id)
    calls: list[tuple[str, datetime]] = []

    def fake_carry_market_facts(symbol: str, *, evaluated_at: datetime, **_kwargs):
        calls.append((symbol, evaluated_at))
        return _carry_facts(), ()

    monkeypatch.setattr(
        "app.shadow.collector_v1.crypto.carry_market_facts",
        fake_carry_market_facts,
    )

    collect_shadow(session, evaluated_at=AT)
    session.flush()

    carry = session.query(ShadowObservation).filter_by(
        instrument_id=instrument.instrument_id,
        strategy_version="crypto_carry_v1",
    ).one()
    assert calls == [("BTCUSDT", AT)]
    assert carry.evidence_status == ShadowEvidenceStatus.EVALUATED.value
    assert carry.reason_code is None
    assert carry.signal_emitted is True


def test_default_provider_keeps_bybit_failure_explicit(session, monkeypatch) -> None:
    instrument = _instrument(session)
    _history(session, instrument.instrument_id)

    def fail_carry_market_facts(*_args, **_kwargs):
        raise ValueError("Bybit funding history unavailable")

    monkeypatch.setattr(
        "app.shadow.collector_v1.crypto.carry_market_facts",
        fail_carry_market_facts,
    )

    collect_shadow(session, evaluated_at=AT)
    session.flush()

    carry = session.query(ShadowObservation).filter_by(
        instrument_id=instrument.instrument_id,
        strategy_version="crypto_carry_v1",
    ).one()
    assert carry.evidence_status == ShadowEvidenceStatus.INPUT_UNAVAILABLE.value
    assert carry.reason_code == "BYBIT_CARRY_FACTS_UNAVAILABLE"
    assert carry.signal_emitted is False


def test_collector_is_idempotent_for_same_point_in_time_snapshot(session) -> None:
    instrument = _instrument(session)
    _history(session, instrument.instrument_id)

    first = collect_shadow(session, evaluated_at=AT, facts_provider=_facts)
    session.flush()
    second = collect_shadow(session, evaluated_at=AT, facts_provider=_facts)
    session.flush()

    assert first.observations == 4
    assert second.observations == 4
    assert session.query(ShadowObservation).count() == 4


def test_future_and_forming_bars_do_not_change_shadow_snapshot_identity(session) -> None:
    instrument = _instrument(session)
    _history(session, instrument.instrument_id)
    first = collect_shadow(session, evaluated_at=AT, facts_provider=_facts)
    session.flush()
    keys_before = {
        row.strategy_version: (row.opportunity_key, row.market_snapshot_hash)
        for row in session.query(ShadowObservation).all()
    }

    _bar(
        session,
        instrument.instrument_id,
        Timeframe.H1,
        AT + timedelta(hours=1),
        Decimal("999"),
        closed=True,
    )
    _bar(
        session,
        instrument.instrument_id,
        Timeframe.H1,
        AT,
        Decimal("888"),
        closed=False,
    )
    session.flush()

    second = collect_shadow(session, evaluated_at=AT, facts_provider=_facts)
    session.flush()
    keys_after = {
        row.strategy_version: (row.opportunity_key, row.market_snapshot_hash)
        for row in session.query(ShadowObservation).all()
    }

    assert first.observations == second.observations == 4
    assert keys_after == keys_before
    assert session.query(ShadowObservation).count() == 4


def test_insufficient_bar_history_is_input_unavailable_not_evaluated_no_signal(session) -> None:
    instrument = _instrument(session)
    for index in range(5):
        _bar(
            session,
            instrument.instrument_id,
            Timeframe.H1,
            AT - timedelta(hours=5 - index),
            Decimal("100"),
        )
    session.flush()

    collect_shadow(session, evaluated_at=AT, facts_provider=_facts)
    session.flush()
    rows = session.query(ShadowObservation).all()

    assert len(rows) == 4
    assert all(row.evidence_status == ShadowEvidenceStatus.INPUT_UNAVAILABLE.value for row in rows)
    assert all(row.reason_code is not None for row in rows)


def test_collector_cannot_touch_owner_or_execution_lifecycle_tables(session) -> None:
    instrument = _instrument(session)
    _history(session, instrument.instrument_id)
    before = {
        "ideas": session.query(TradeIdea).count(),
        "paper": session.query(PaperTrade).count(),
        "notifications": session.query(NotificationOutbox).count(),
        "execution": session.query(ExecutionIntent).count(),
    }

    collect_shadow(session, evaluated_at=AT, facts_provider=_facts)
    session.flush()

    after = {
        "ideas": session.query(TradeIdea).count(),
        "paper": session.query(PaperTrade).count(),
        "notifications": session.query(NotificationOutbox).count(),
        "execution": session.query(ExecutionIntent).count(),
    }
    assert after == before