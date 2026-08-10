from app.api.v1.journal import misses
from app.journal.lifecycle import TransitionRequest, record_initial, transition
from app.models import TradeIdea
from app.models.enums import IdeaStatus
from app.paper.tracker import approve_for
from conftest import idea_kwargs


def _idea(session, instrument, now, **overrides):
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            was_presented=True,
            presentation_rank=0,
            **overrides,
        )
    )
    session.add(idea)
    session.flush()
    record_initial(session, idea)
    return idea


def test_presented_cancelled_without_paper_stays_in_journal(session, instrument, now):
    idea = _idea(session, instrument, now)
    transition(
        session,
        idea,
        TransitionRequest(
            new_status=IdeaStatus.CANCELLED,
            reason_code="stopped_after_entry",
            reason_detail="цена вошла в зону и затем прошла стоп",
        ),
    )

    rows = misses(limit=100, db=session)

    assert len(rows) == 1
    row = rows[0]
    assert row.idea_id == idea.id
    assert row.status == "CANCELLED"
    assert "прошла стоп" in row.reason
    assert row.comment == "цена вошла в зону и затем прошла стоп"


def test_live_and_unpresented_ideas_do_not_pollute_no_trade_history(
    session, instrument, now
):
    _idea(session, instrument, now)
    hidden = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            was_presented=False,
            status="WATCH",
        )
    )
    session.add(hidden)
    session.flush()
    record_initial(session, hidden)
    transition(
        session,
        hidden,
        TransitionRequest(
            new_status=IdeaStatus.CANCELLED,
            reason_code="invalidated_before_entry",
            reason_detail="скрытая идея",
        ),
    )

    assert misses(limit=100, db=session) == []


def test_idea_with_paper_trade_is_not_duplicated_in_no_trade_history(
    session, instrument, now
):
    idea = _idea(
        session,
        instrument,
        now,
        status="TRIGGERED",
        quality_status="ACTIVE",
    )
    trade, created = approve_for(session, idea, now=now)
    assert created is True
    assert trade.idea_id == idea.id

    transition(
        session,
        idea,
        TransitionRequest(
            new_status=IdeaStatus.CANCELLED,
            reason_code="invalidated_before_entry",
            reason_detail="paper row already owns this lifecycle",
        ),
    )

    assert misses(limit=100, db=session) == []
