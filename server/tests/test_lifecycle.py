"""Жизненный цикл §18 и отбор дня §16."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.journal.lifecycle import (
    ALLOWED,
    TransitionError,
    TransitionRequest,
    can_transition,
    history,
    record_initial,
    terminal_states,
    transition,
)
from app.models import TradeIdea
from app.models.enums import IdeaStatus, LiquidityRegime, QualityStatus
from app.scoring.selection import (
    RankedIdea,
    RankingWeights,
    rank,
    select_daily,
)
from tests.conftest import idea_kwargs

S = IdeaStatus


def make(session, instrument, now, status=S.DISCOVERED) -> TradeIdea:
    idea = TradeIdea(
        **idea_kwargs(instrument.instrument_id, now, status=status.value)
    )
    session.add(idea)
    session.flush()
    return idea


# ─── Таблица переходов §18 ────────────────────────────────────────────────


def test_every_status_is_in_the_table():
    """Состояние, забытое в таблице, вело бы себя как терминальное молча."""
    assert set(ALLOWED) == set(IdeaStatus)


def test_terminal_states_are_the_expected_ones():
    assert terminal_states() == frozenset({S.CANCELLED, S.MISSED, S.CLOSED})


def test_position_states_cannot_jump_back_to_watch():
    """Из открытой позиции нельзя вернуться в наблюдение."""
    for status in (S.FILLED, S.TP1_HIT, S.MANAGING, S.TP2_HIT):
        assert not can_transition(status, S.WATCH)


def test_blocked_states_are_reversible():
    """Лимит освободится, данные восстановятся — блокировка не приговор."""
    assert can_transition(S.RISK_BLOCKED, S.WATCH)
    assert can_transition(S.DATA_BLOCKED, S.WATCH)


def test_cannot_skip_from_watch_to_filled():
    assert not can_transition(S.WATCH, S.FILLED)


# ─── Запись переходов ─────────────────────────────────────────────────────


def test_initial_event_is_written_even_for_unpresented_idea(session, instrument, now):
    """§12: хранится любая идея, включая непоказанную."""
    idea = make(session, instrument, now)
    event = record_initial(session, idea, reason_code="scan")
    assert event.sequence == 1
    assert event.old_status is None
    assert event.new_status == S.DISCOVERED


def test_transition_appends_and_updates_status(session, instrument, now):
    idea = make(session, instrument, now)
    record_initial(session, idea)
    transition(session, idea, TransitionRequest(S.WATCH, "no_trigger", "триггера нет"))
    transition(session, idea, TransitionRequest(S.TRIGGERED, "trigger", "поглощение в OB"))

    assert idea.status == S.TRIGGERED
    events = history(session, idea.id)
    assert [e.sequence for e in events] == [1, 2, 3]
    assert [e.new_status for e in events] == [S.DISCOVERED, S.WATCH, S.TRIGGERED]
    assert events[-1].old_status == S.WATCH


def test_illegal_transition_leaves_no_trace(session, instrument, now):
    """Недопустимый переход не должен попадать в журнал.

    Иначе история будет содержать состояния, в которых система не была.
    """
    idea = make(session, instrument, now)
    record_initial(session, idea)
    before = len(history(session, idea.id))

    with pytest.raises(TransitionError, match="не разрешён §18"):
        transition(session, idea, TransitionRequest(S.TP2_HIT, "bug"))

    assert len(history(session, idea.id)) == before
    assert idea.status == S.DISCOVERED


def test_transition_to_same_status_is_rejected(session, instrument, now):
    idea = make(session, instrument, now)
    record_initial(session, idea)
    with pytest.raises(TransitionError, match="ничего не меняет"):
        transition(session, idea, TransitionRequest(S.DISCOVERED, "noop"))


def test_error_message_lists_allowed_targets(session, instrument, now):
    idea = make(session, instrument, now, status=S.CLOSED)
    with pytest.raises(TransitionError, match="терминальное"):
        transition(session, idea, TransitionRequest(S.WATCH, "bug"))


def test_events_keep_probability_before_and_after(session, instrument, now):
    idea = make(session, instrument, now)
    record_initial(session, idea)
    transition(
        session, idea,
        TransitionRequest(S.WATCH, "recalc", probability_after=Decimal("0.61")),
    )
    last = history(session, idea.id)[-1]
    assert last.probability_before == Decimal("0.58")
    assert last.probability_after == Decimal("0.61")


def test_recorded_history_cannot_be_edited(session, instrument, now):
    """Триггер базы защищает журнал и от собственного кода тоже."""
    from sqlalchemy.exc import DBAPIError

    idea = make(session, instrument, now)
    record_initial(session, idea)
    transition(session, idea, TransitionRequest(S.WATCH, "no_trigger"))
    with pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE idea_events SET reason_code='подделка' WHERE idea_id=:i"),
            {"i": idea.id},
        )


# ─── Отбор дня §16 ────────────────────────────────────────────────────────


def candidate(
    name: str, *, ev="0.4", p="0.6", conf="0.6", score="80",
    cluster=None, status=QualityStatus.ACTIVE, liq=LiquidityRegime.GOOD,
) -> RankedIdea:
    return RankedIdea(
        idea_id=name, instrument_id=name, cluster=cluster, quality_status=status,
        expected_r=Decimal(ev), probability=Decimal(p), confidence=Decimal(conf),
        score=Decimal(score), liquidity=liq,
    )


def test_ranking_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="сумма весов"):
        rank([candidate("a")], RankingWeights(expected_value=Decimal("0.9")))


def test_ranking_explains_itself():
    """«Эта идея выше той» должно объясняться, иначе порядок — произвол."""
    results = rank([candidate("a"), candidate("b", ev="0.1")])
    assert results[0].idea.idea_id == "a"
    assert set(results[0].parts) == {
        "expected_value", "confidence_adjusted_p", "score",
        "liquidity", "diversification", "execution",
    }


def test_no_more_than_three_cards():
    """Ровно три места, за них конкурируют."""
    picked = select_daily([candidate(f"i{i}") for i in range(10)])
    assert len(picked.trade_now) == 3
    assert all(r.dropped_reason for r in picked.dropped)


def test_cluster_limit_drops_second_idea_of_same_cluster():
    """Три лонга по нефти — это одна сделка тройного размера."""
    picked = select_daily(
        [
            candidate("brent", cluster="energy"),
            candidate("gas", cluster="energy", ev="0.3"),
            candidate("gold", cluster="metals", ev="0.2"),
        ],
        cluster_risk_exceeded={"energy"},
    )
    ids = [r.idea.idea_id for r in picked.trade_now]
    assert "brent" in ids and "gas" not in ids
    assert any("кластер energy" in r.dropped_reason for r in picked.dropped)


def test_cluster_without_exceeded_risk_is_allowed():
    picked = select_daily(
        [candidate("brent", cluster="energy"), candidate("gas", cluster="energy")]
    )
    assert len(picked.trade_now) == 2


def test_watch_fills_the_remaining_slots():
    picked = select_daily(
        [
            candidate("a"),
            candidate("b", status=QualityStatus.WATCH),
            candidate("c", status=QualityStatus.WATCH),
            candidate("d", status=QualityStatus.WATCH),
        ]
    )
    assert len(picked.trade_now) == 1
    assert len(picked.wait_for_trigger) == 2
    assert len(picked.trade_now) + len(picked.wait_for_trigger) == 3


def test_empty_day_says_why():
    """«Сделок нет» — валидный результат и обязан выглядеть как результат."""
    picked = select_daily([])
    assert picked.trade_now == () and picked.wait_for_trigger == ()
    assert "не создаёт сделки ради нормы" in picked.no_trade_reason


def test_only_watch_explains_the_absence_of_trades():
    picked = select_daily([candidate("a", status=QualityStatus.WATCH)])
    assert picked.trade_now == ()
    assert "ожидающие триггера" in picked.no_trade_reason


def test_rejected_ideas_never_reach_the_cards():
    picked = select_daily([candidate("a", status=QualityStatus.REJECTED)])
    assert picked.trade_now == () and picked.wait_for_trigger == ()
    assert any("REJECTED" in r.dropped_reason for r in picked.dropped)


def test_diversification_lowers_repeated_cluster():
    """Идея из уже представленного кластера ценится ниже при прочих равных."""
    results = rank(
        [
            candidate("a", cluster="energy"),
            candidate("b", cluster="energy"),
            candidate("c", cluster="metals"),
        ]
    )
    metals = next(r for r in results if r.idea.idea_id == "c")
    energy = next(r for r in results if r.idea.idea_id == "a")
    assert metals.parts["diversification"] > energy.parts["diversification"]
