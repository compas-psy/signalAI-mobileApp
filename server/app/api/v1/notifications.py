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

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from ...db import session_scope
from ...notification_outbox import emit, list_after, materialize

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _batch(after: int) -> list[dict]:
    with session_scope() as session:
        materialize(session)
        return [event.to_json() for event in list_after(session, after, limit=100)]


@router.get("")
def notifications(after: int = Query(0, ge=0)) -> dict:
    """Durable fallback/readback for diagnostics and reconnect tests."""
    events = _batch(after)
    return {
        "events": events,
        "cursor": events[-1]["id"] if events else after,
    }


@router.post("/test")
def test_notification() -> dict:
    """Create a real server-side test event; the client does not fabricate it."""
    with session_scope() as session:
        event_id = emit(
            session,
            key=f"system:manual-test:{uuid4()}",
            kind="SYSTEM",
            title="SignalAI · тест с сервера",
            body="Это событие создано на VPS и отправлено по server push-каналу.",
        )
        if event_id is None:
            return {"created": False}
        event = list_after(session, event_id - 1, limit=1)[0]
        return {"created": True, "event": event.to_json()}


@router.get("/stream")
async def notification_stream(after: int = Query(0, ge=0)) -> StreamingResponse:
    """Persistent SSE connection.  Heartbeat stays below nginx's 30s timeout."""

    async def events():
        cursor = after
        # Tell intermediaries/client that the connection is alive before the
        # first materialization round finishes.
        yield ": signalai-connected\n\n"
        while True:
            batch = await asyncio.to_thread(_batch, cursor)
            if batch:
                for event in batch:
                    cursor = max(cursor, int(event["id"]))
                    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    yield f"id: {cursor}\nevent: signalai\ndata: {data}\n\n"
            else:
                # Existing nginx has proxy_read_timeout=30s.  Ten seconds is
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
