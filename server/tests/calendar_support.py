"""Deterministic owned-calendar fixtures for tests that require an actionable idea.

Production intentionally fails closed when the event calendar is missing. A
successful approval test therefore provides both the persisted CLEAR assessment
captured with the idea and a fresh owned snapshot for approval-time revalidation.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path


CLEAR_EVENT_CALENDAR_BLOCK = {
    "status": "CLEAR",
    "reason_code": "NO_BLOCKING_EVENT",
    "detail": "проверка календаря завершена",
    "blocks_admission": False,
    "event": None,
}


def configure_clear_event_calendar(monkeypatch, tmp_path: Path) -> Path:
    now = datetime.now(UTC)
    observed = now - timedelta(seconds=5)
    snapshot = tmp_path / "signalai-test-event-calendar.json"
    snapshot.write_text(
        json.dumps(
            {
                "source": "server-test-calendar",
                "fetched_at": observed.isoformat(),
                "coverage_until": (now + timedelta(hours=3)).isoformat(),
                "events": [
                    {
                        "provider_event_id": "clear-coverage-marker",
                        "title": "Low impact coverage marker",
                        "scheduled_at": (now + timedelta(hours=2)).isoformat(),
                        "impact": "LOW",
                        "instrument_tags": ["USD", "RUB"],
                        "observed_at": observed.isoformat(),
                        "tradable_at": observed.isoformat(),
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SIGNALAI_EVENT_CALENDAR_PATH", str(snapshot.resolve()))
    return snapshot
