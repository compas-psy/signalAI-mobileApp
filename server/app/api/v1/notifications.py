"""Low-latency server → Android notification stream.

SSE is used intentionally: notifications are one-way, so a WebSocket adds a
second proxy protocol without adding product value.  SSE stays ordinary HTTPS,
passes the existing nginx route, reconnects naturally after Wi-Fi/VPN changes,
and uses the durable outbox cursor to replay anything missed while offline.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...db import get_db, session_scope
from ...notification_outbox import emit, list_after, materialize
from ...schemas.common import ApiModel
from ...telegram_notifications import configured as telegram_configured
from ...telegram_notifications import set_enabled as set_telegram_enabled
from ...telegram_notifications import status as telegram_status

router = APIRouter(prefix="/notifications", tags=["notifications"])


class TelegramSettingIn(ApiModel):
    enabled: bool


def _batch(after: int) -> list[dict]:
    with session_scope() as session:
        materialize(session)
        return [event.to_json() for event in list_after(session, after, limit=100)]


@router.get("")
def notifications(
    after: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """Durable fallback/readback for diagnostics and reconnect tests."""
    materialize(db)
    events = [event.to_json() for event in list_after(db, after, limit=100)]
    return {
        "events": events,
        "cursor": events[-1]["id"] if events else after,
    }


@router.get("/telegram")
def get_telegram_setting(db: Session = Depends(get_db)) -> dict:
    return telegram_status(db)


@router.put("/telegram")
def update_telegram_setting(
    value: TelegramSettingIn,
    db: Session = Depends(get_db),
) -> dict:
    if value.enabled and not telegram_configured():
        raise HTTPException(409, "Telegram не настроен на сервере")
    return set_telegram_enabled(db, value.enabled)


@router.post("/test")
def test_notification(db: Session = Depends(get_db)) -> dict:
    """Create a real server-side test event; the client does not fabricate it."""
    event_id = emit(
        db,
        key=f"system:manual-test:{uuid4()}",
        kind="SYSTEM",
        title="SignalAI · тест с сервера",
        body="Это событие создано на VPS и отправлено по server push-каналу.",
    )
    if event_id is None:
        return {"created": False}
    event = list_after(db, event_id - 1, limit=1)[0]
    return {"created": True, "event": event.to_json()}


@router.get("/stream")
async def notification_stream(after: int = Query(0, ge=0)) -> StreamingResponse:
    """Persistent SSE connection.  Heartbeat stays below nginx's 30s timeout."""

    async def events():
        cursor = after
        yield ": signalai-connected\n\n"
        while True:
            batch = await asyncio.to_thread(_batch, cursor)
            if batch:
                for event in batch:
                    cursor = max(cursor, int(event["id"]))
                    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    yield f"id: {cursor}\nevent: signalai\ndata: {data}\n\n"
            else:
                # Existing nginx has proxy_read_timeout=30s. Ten seconds is
                # intentionally well inside it and is cheap for one device.
                yield ": heartbeat\n\n"
            await asyncio.sleep(10)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # nginx honours this response header and streams chunks instead of
            # buffering the SSE body until a proxy buffer is full.
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]
