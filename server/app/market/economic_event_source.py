"""Refresh the owned economic-event snapshot from a keyless official-source feed.

The scanner and admission code never perform network I/O.  This module runs as
an isolated sidecar, writes one provider-neutral snapshot atomically, and keeps
the last good snapshot when the provider is unavailable.  The reader's normal
freshness/coverage checks therefore remain the fail-closed safety boundary.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time as wall_time
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


XOOMAR_CALENDAR_URL = "https://xoomar.com/api/markets/calendar"
DEFAULT_SNAPSHOT_PATH = "/var/lib/signalai-calendar/events.json"
DEFAULT_REFRESH_SECONDS = 30 * 60
SOURCE_IDENTITY = "xoomar-official-us-macro"

FetchPayload = Callable[[datetime, datetime], dict[str, object]]


def _aware_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _provider_event_id(raw: dict[str, object]) -> str:
    stable = "|".join(
        (
            str(raw.get("source") or "unknown").strip().lower(),
            str(raw.get("eventName") or "").strip(),
            str(raw.get("scheduledAt") or "").strip(),
            str(raw.get("periodLabel") or "").strip(),
        )
    )
    return "xoomar:" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]


def build_owned_snapshot(
    payload: dict[str, object],
    *,
    fetched_at: datetime,
    coverage_until: datetime,
) -> dict[str, object]:
    """Convert Xoomar's envelope into SignalAI's provider-neutral snapshot."""
    fetched = _aware_utc(fetched_at, "fetched_at")
    coverage = _aware_utc(coverage_until, "coverage_until")
    if coverage <= fetched:
        raise ValueError("coverage_until must be after fetched_at")

    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("calendar payload must contain data[]")

    events: list[dict[str, object]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("calendar event must be an object")
        importance = str(raw.get("importance") or "").strip().lower()
        if importance != "high":
            continue
        title = str(raw.get("eventName") or "").strip()
        scheduled_at = str(raw.get("scheduledAt") or "").strip()
        if not title or not scheduled_at:
            raise ValueError("calendar event requires eventName and scheduledAt")
        # Validate timezone now rather than letting a malformed provider row
        # poison the owned file consumed by admission later.
        parsed = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("scheduledAt must be timezone-aware")

        events.append(
            {
                "provider_event_id": _provider_event_id(raw),
                "title": title,
                "scheduled_at": parsed.astimezone(UTC).isoformat(),
                "observed_at": fetched.isoformat(),
                "tradable_at": fetched.isoformat(),
                "instrument_tags": ["CRYPTO", "USD"],
                "impact": "HIGH",
            }
        )

    events.sort(key=lambda item: (str(item["scheduled_at"]), str(item["provider_event_id"])))
    return {
        "source": SOURCE_IDENTITY,
        "fetched_at": fetched.isoformat(),
        "coverage_until": coverage.isoformat(),
        "events": events,
    }


def fetch_xoomar_calendar(start: datetime, end: datetime) -> dict[str, object]:
    """Fetch high-impact US macro schedule without API credentials."""
    start_utc = _aware_utc(start, "start")
    end_utc = _aware_utc(end, "end")
    params = urlencode(
        {
            "from": start_utc.date().isoformat(),
            "to": end_utc.date().isoformat(),
            "importance": "high",
        }
    )
    request = Request(
        f"{XOOMAR_CALENDAR_URL}?{params}",
        headers={"Accept": "application/json", "User-Agent": "SignalAI-calendar/1.0"},
        method="GET",
    )
    with urlopen(request, timeout=15) as response:  # nosec B310 - fixed HTTPS host
        if getattr(response, "status", 200) != 200:
            raise OSError(f"calendar provider HTTP {response.status}")
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("calendar response exceeds 2 MB")
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("calendar response must be an object")
    return decoded


def refresh_owned_snapshot(
    path: str | Path,
    *,
    now: datetime,
    fetch_payload: FetchPayload = fetch_xoomar_calendar,
) -> bool:
    """Refresh snapshot atomically; preserve the last good file on failure."""
    moment = _aware_utc(now, "now")
    target = Path(path)
    if not target.is_absolute():
        raise ValueError("calendar snapshot path must be absolute")

    query_start = moment - timedelta(days=1)
    query_end = moment + timedelta(days=7)
    # The provider's `to` date is an inclusive schedule window.  The owned
    # metadata records that explicit coverage even when `data` is empty.
    coverage_until = datetime.combine(query_end.date(), time.max, tzinfo=UTC)

    temp_name: str | None = None
    try:
        payload = fetch_payload(query_start, query_end)
        snapshot = build_owned_snapshot(
            payload,
            fetched_at=moment,
            coverage_until=coverage_until,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_name = stream.name
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, target)
        temp_name = None
        return True
    except Exception as exc:
        print(
            f"economic calendar refresh failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return False
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def run_forever() -> None:
    path = os.environ.get("SIGNALAI_EVENT_CALENDAR_PATH", DEFAULT_SNAPSHOT_PATH)
    raw_interval = os.environ.get(
        "SIGNALAI_EVENT_CALENDAR_REFRESH_SECONDS", str(DEFAULT_REFRESH_SECONDS)
    )
    try:
        interval = max(300, int(raw_interval))
    except ValueError:
        interval = DEFAULT_REFRESH_SECONDS

    while True:
        now = datetime.now(UTC)
        ok = refresh_owned_snapshot(path, now=now)
        print(
            f"economic calendar refresh {'ok' if ok else 'failed'} at {now.isoformat()}",
            flush=True,
        )
        wall_time.sleep(interval)


if __name__ == "__main__":
    run_forever()
