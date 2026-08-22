from decimal import Decimal

from app.api.v1.ideas import get_idea
from app.models import TradeIdea
from app.models.enums import IdeaStatus, QualityStatus
from tests.conftest import idea_kwargs


def test_actionable_idea_detail_accepts_persisted_evidence_shape(session, instrument, now):
    """Regression for the production BTC/DOGE/GKU6 detail failure.

    ``pipeline.presentation.build_evidence`` persists kind/title/detail based
    evidence. The detail API must serialize that exact persisted contract;
    otherwise the phone is left with the summary from /ideas/today and loses
    Entry/SL/TP plus the owner-confirm action.
    """
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            status=IdeaStatus.TRIGGERED.value,
            quality_status=QualityStatus.ACTIVE.value,
            quantity=Decimal("2"),
        )
    )
    idea.evidence_json = [
        {
            "id": "smc",
            "kind": "smc_context",
            "title": "Структура и Smart Money",
            "detail": "BOS по направлению сделки",
            "summary": "bullish structure",
            "confidence": 0.74,
            "detector_version": "smc-1.0.0",
            "measured": True,
            "missing_terms": [],
            "measures": [
                {
                    "name": "structure",
                    "label": "Структура",
                    "value": 1.0,
                    "status": "measured",
                    "note": "BOS",
                }
            ],
            "conflicts_with": [],
        }
    ]
    idea.annotations_json = [
        {
            "id": "smc-0",
            "type": "smcBos",
            "timeframe": "1h",
            "start_time": now.isoformat(),
            "end_time": idea.expires_at.isoformat(),
            "price_low": None,
            "price_high": None,
            "confidence": 0.74,
            "evidence_id": "smc",
            "detector_version": "smc-1.0.0",
            "label": "BOS",
            "display_priority": 80,
        }
    ]
    idea.explanation_json = {
        "event_calendar": {
            "status": "UNAVAILABLE",
            "reason_code": "EVENT_SOURCE_UNAVAILABLE",
            "detail": "календарь событий недоступен",
            "blocks_admission": True,
            "event": None,
        }
    }
    session.add(idea)
    session.flush()

    detail = get_idea(idea.id, session)

    assert detail.status is IdeaStatus.TRIGGERED
    assert detail.quality_status is QualityStatus.ACTIVE
    assert detail.plan.entry_reference == Decimal("90100")
    assert detail.plan.stop == Decimal("89400")
    assert [detail.plan.tp1, detail.plan.tp2, detail.plan.tp3] == [
        Decimal("91000"),
        Decimal("92000"),
        Decimal("93000"),
    ]
    assert detail.sizing.tradable is True
    assert detail.evidence[0].id == "smc"
    assert detail.evidence[0].kind == "smc_context"
    assert detail.evidence[0].detector_version == "smc-1.0.0"
    assert detail.evidence[0].measures[0].status == "measured"
    assert detail.event_calendar.reason_code == "EVENT_SOURCE_UNAVAILABLE"
    assert detail.event_calendar.blocks_admission is True
