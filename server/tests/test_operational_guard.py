from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import Bar, PaperTrade, TradeIdea
from app.models.enums import IdeaStatus, PaperStatus, Timeframe
from app.operational_guard import reconcile_operational_lifecycle
from app.paper.tracker import approve_for
from tests.conftest import idea_kwargs

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def _idea(session, instrument, **overrides) -> TradeIdea:
    values = idea_kwargs(
        instrument.instrument_id,
        NOW,
        status=IdeaStatus.TRIGGERED,
        quality_status="ACTIVE",
        was_presented=True,
        **overrides,
    )
    idea = TradeIdea(**values)
    session.add(idea)
    session.flush()
    return idea


def test_existing_pending_paper_repairs_triggered_card_to_active(session, instrument):
    """Confirmed trade must never keep saying «можно действовать»."""
    idea = _idea(session, instrument)
    trade, created = approve_for(session, idea, now=NOW)
    assert created is True
    assert trade.status is PaperStatus.PENDING
    assert idea.status is IdeaStatus.TRIGGERED

    changed = reconcile_operational_lifecycle(session, now=NOW + timedelta(minutes=5))

    assert changed >= 1
    assert idea.status is IdeaStatus.ACTIVE
    assert trade.status is PaperStatus.PENDING


def test_pending_entry_is_missed_when_market_reaches_target_before_entry(session, instrument):
    """HYPE case: a return to an old entry must not resurrect a completed thesis."""
    idea = _idea(session, instrument)
    trade, _ = approve_for(session, idea, now=NOW)
    tp1 = Decimal(str(trade.tp_prices[0]))
    # Stay strictly above the LONG entry while crossing the actual paper TP1.
    low = trade.entry + Decimal("100")
    high = max(tp1 + Decimal("100"), low + Decimal("100"))
    session.add(
        Bar(
            instrument_id=instrument.instrument_id,
            timeframe=Timeframe.H1,
            open_time=NOW + timedelta(hours=1),
            open=low,
            high=high,
            low=low,
            close=high,
            volume_units=Decimal("1000"),
            is_closed=True,
            source="test",
        )
    )
    session.flush()

    reconcile_operational_lifecycle(session, now=NOW + timedelta(hours=2))

    assert trade.status is PaperStatus.CANCELLED
    assert trade.outcome == "MISS"
    assert "без входа" in trade.close_reason
    # Once the owner approved it, the plan is an ACTIVE execution object;
    # a missed target closes that approved plan as CANCELLED, not as a fresh
    # pre-decision MISSED card.
    assert idea.status is IdeaStatus.CANCELLED


def test_h1_pending_entry_times_out_after_one_session_even_without_bars(session, instrument):
    """No market data is not permission to keep an old H1 limit alive for days."""
    idea = _idea(session, instrument)
    trade, _ = approve_for(session, idea, now=NOW)

    reconcile_operational_lifecycle(session, now=NOW + timedelta(hours=7))

    assert trade.status is PaperStatus.CANCELLED
    assert trade.outcome == "TIMEOUT"
    assert "окно сделки истекло" in trade.close_reason
    assert idea.status is IdeaStatus.CANCELLED


def test_entry_touch_before_target_keeps_pending_for_normal_tracker(session, instrument):
    """Guard never rewrites a trade whose promised entry really was available."""
    idea = _idea(session, instrument)
    trade, _ = approve_for(session, idea, now=NOW)
    session.add_all(
        [
            Bar(
                instrument_id=instrument.instrument_id,
                timeframe=Timeframe.H1,
                open_time=NOW + timedelta(hours=1),
                open=Decimal("90300"),
                high=Decimal("90400"),
                low=Decimal("90050"),
                close=Decimal("90200"),
                volume_units=Decimal("1000"),
                is_closed=True,
                source="test",
            ),
            Bar(
                instrument_id=instrument.instrument_id,
                timeframe=Timeframe.H1,
                open_time=NOW + timedelta(hours=2),
                open=Decimal("90500"),
                high=Decimal("91200"),
                low=Decimal("90400"),
                close=Decimal("91100"),
                volume_units=Decimal("1000"),
                is_closed=True,
                source="test",
            ),
        ]
    )
    session.flush()

    reconcile_operational_lifecycle(session, now=NOW + timedelta(hours=3))

    assert trade.status is PaperStatus.PENDING
    assert idea.status is IdeaStatus.ACTIVE


def test_old_trigger_without_trade_expires_as_decision_not_standing_advice(session, instrument):
    idea = _idea(
        session,
        instrument,
        signal_time=NOW - timedelta(hours=8),
        expires_at=NOW + timedelta(days=4),
    )

    reconcile_operational_lifecycle(session, now=NOW)

    assert idea.status is IdeaStatus.TIMED_OUT
    assert session.execute(
        select(PaperTrade).where(PaperTrade.idea_id == idea.id)
    ).scalar_one_or_none() is None
