"""Read-only forensic execution timeline API (SAI-051)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db import get_db
from ...execution.timeline import ExecutionTimelineNotFound, read_execution_timeline
from ...schemas.common import ApiModel

router = APIRouter(tags=["execution"])


class ExecutionTimelineEventOut(ApiModel):
    source: str
    kind: str
    occurred_at: datetime
    facts: dict[str, Any]


class ExecutionTimelineOut(ApiModel):
    idea_id: uuid.UUID
    intent_ids: list[uuid.UUID]
    events: list[ExecutionTimelineEventOut]


@router.get(
    "/execution/ideas/{idea_id}/timeline",
    response_model=ExecutionTimelineOut,
)
def get_execution_timeline(
    idea_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> ExecutionTimelineOut:
    try:
        timeline = read_execution_timeline(db, idea_id=idea_id)
    except ExecutionTimelineNotFound as exc:
        raise HTTPException(status_code=404, detail="execution timeline not found") from exc

    return ExecutionTimelineOut(
        idea_id=timeline.idea_id,
        intent_ids=list(timeline.intent_ids),
        events=[
            ExecutionTimelineEventOut(
                source=event.source,
                kind=event.kind,
                occurred_at=event.occurred_at,
                facts=event.facts,
            )
            for event in timeline.events
        ],
    )


__all__ = [
    "ExecutionTimelineEventOut",
    "ExecutionTimelineOut",
    "get_execution_timeline",
    "router",
]
