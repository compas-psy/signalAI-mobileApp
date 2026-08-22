import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from inspect import getsource

import pytest
from fastapi import HTTPException

from app.api.v1.ideas import _validate_approval
from app.models import TradeIdea
from app.models.enums import IdeaStatus, QualityStatus
from app.pipeline import scan as scan_module
from app.pipeline.trigger import mark_confirmed
from tests.conftest import idea_kwargs


def test_active_market_signal_is_not_demoted_by_zero_sizing():
    """Sizing may block execution, but it must not rewrite signal discovery.

    FORTS has integer contracts. A perfectly valid ACTIVE signal can therefore
    have quantity=0 when the configured risk budget is below one contract's
    stop risk. WATCH means "market trigger is still missing", not "account is
    too small for this instrument".
    """
    source = getsource(scan_module.scan_instrument)
    assert "verdict.status is QualityStatus.ACTIVE and sizing.tradable" not in source
    assert "if verdict.status is QualityStatus.ACTIVE" in source


def test_delayed_trigger_promotes_quality_and_clears_trigger_gate(
    session, instrument, now
):
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            status=IdeaStatus.WATCH.value,
            quality_status=QualityStatus.WATCH.value,
        )
    )
    idea.explanation_json = {
        "admission": "не хватает: триггер (триггера нет)",
        "admission_failed": ["trigger"],
        "timeframes": [
            {"tf": "1D", "role": "context", "summary": "UPTREND"},
            {"tf": "1H", "role": "trigger", "summary": "триггера нет"},
        ],
    }
    session.add(idea)
    session.flush()

    mark_confirmed(idea, "Ложный пробой зоны entry")

    assert idea.quality_status == QualityStatus.ACTIVE
    assert idea.explanation_json["admission_failed"] == []
    assert "все условия" in idea.explanation_json["admission"]
    trigger = next(
        row for row in idea.explanation_json["timeframes"] if row["role"] == "trigger"
    )
    assert trigger["summary"] == "Ложный пробой зоны entry"


def test_zero_quantity_still_blocks_user_approval(session, instrument, now):
    """Decoupling lifecycle from sizing must never bypass execution risk."""
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            status=IdeaStatus.TRIGGERED.value,
            quality_status=QualityStatus.ACTIVE.value,
            quantity=Decimal("0"),
        )
    )
    session.add(idea)
    session.flush()

    with pytest.raises(HTTPException, match="размер позиции равен нулю"):
        _validate_approval(idea, now=now)


def test_approval_revalidates_current_owner_calendar_over_saved_clear(
    tmp_path, monkeypatch
):
    """A calendar event added after scan must still block owner approval."""
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    snapshot = tmp_path / "events.json"
    snapshot.write_text(
        json.dumps(
            {
                "source": "owner-feed-v1",
                "fetched_at": (now - timedelta(minutes=5)).isoformat(),
                "coverage_until": (now + timedelta(hours=2)).isoformat(),
                "events": [
                    {
                        "provider_event_id": "rate-1",
                        "title": "Rate decision",
                        "scheduled_at": (now + timedelta(minutes=20)).isoformat(),
                        "observed_at": (now - timedelta(minutes=10)).isoformat(),
                        "tradable_at": (now - timedelta(minutes=5)).isoformat(),
                        "instrument_tags": ["USD"],
                        "impact": "HIGH",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SIGNALAI_EVENT_CALENDAR_PATH", str(snapshot))
    idea = TradeIdea(
        **idea_kwargs(
            "MOEX:FUT:SIU6",
            now,
            status=IdeaStatus.TRIGGERED.value,
            quality_status=QualityStatus.ACTIVE.value,
            explanation_json={"event_calendar": {"blocks_admission": False}},
        )
    )

    with pytest.raises(HTTPException, match="HIGH_IMPACT_EVENT_WINDOW"):
        _validate_approval(idea, now=now)
