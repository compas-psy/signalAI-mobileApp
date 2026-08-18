from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app import telegram_chart
from app.models import TradeIdea
from tests.conftest import idea_kwargs


def test_h1_countdown_uses_next_hour_boundary() -> None:
    assert telegram_chart._h1_countdown(datetime(2026, 8, 18, 9, 12, 40, tzinfo=UTC)) == "47:20"
    assert telegram_chart._h1_countdown(datetime(2026, 8, 18, 9, 59, 59, tzinfo=UTC)) == "00:01"


def test_trade_card_metrics_use_immutable_idea_values(instrument, now) -> None:
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            score=Decimal("82.5"),
            p_tp1_before_sl=Decimal("0.63"),
            quantity=Decimal("3"),
            risk_amount=Decimal("1250"),
        )
    )

    assert telegram_chart._trade_card_metrics(idea) == {
        "score": "82.5/100",
        "probability": "63%",
        "position": "3",
        "risk": "1 250 RUB",
    }


def test_trade_card_direction_is_readable_for_long_and_short(instrument, now) -> None:
    long_idea = TradeIdea(**idea_kwargs(instrument.instrument_id, now, direction="LONG"))
    short_idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            direction="SHORT",
            entry_low=Decimal("90000"),
            entry_high=Decimal("90200"),
            entry_reference=Decimal("90100"),
            stop=Decimal("90900"),
            tp1=Decimal("89200"),
            tp2=Decimal("88400"),
            tp3=Decimal("87600"),
        )
    )

    assert telegram_chart._direction_label(long_idea) == "LONG"
    assert telegram_chart._direction_label(short_idea) == "SHORT"
