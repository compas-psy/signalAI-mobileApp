import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from app.market.economic_events import EconomicEventCalendar, load_owned_calendar


NOW = datetime(2026, 8, 25, 0, tzinfo=UTC)


def test_fresh_explicit_empty_coverage_is_clear_not_source_failure():
    calendar = EconomicEventCalendar(
        (),
        source_available=True,
        fetched_at=NOW - timedelta(minutes=2),
        coverage_until=NOW + timedelta(days=2),
    )

    result = calendar.assess("CRYPTO:PERP:BTCUSDT", as_of=NOW)

    assert result.status == "CLEAR"
    assert result.reason_code == "NO_BLOCKING_EVENT"


def test_xoomar_payload_maps_high_impact_us_macro_to_crypto_and_usd():
    from app.market.economic_event_source import build_owned_snapshot

    payload = {
        "data": [
            {
                "source": "bls",
                "eventName": "CPI (Consumer Price Index)",
                "importance": "high",
                "scheduledAt": "2026-08-26T12:30:00Z",
                "periodLabel": "July 2026",
            }
        ],
        "updatedAt": "2026-08-24T22:00:00Z",
        "source": "xoomar.com",
    }

    snapshot = build_owned_snapshot(
        payload,
        fetched_at=NOW,
        coverage_until=NOW + timedelta(days=7),
    )

    assert snapshot["source"] == "xoomar-official-us-macro"
    assert snapshot["fetched_at"] == NOW.isoformat()
    assert snapshot["coverage_until"] == (NOW + timedelta(days=7)).isoformat()
    assert len(snapshot["events"]) == 1
    event = snapshot["events"][0]
    assert event["impact"] == "HIGH"
    assert set(event["instrument_tags"]) == {"CRYPTO", "USD"}
    assert event["observed_at"] == NOW.isoformat()
    assert event["tradable_at"] == NOW.isoformat()
    assert event["provider_event_id"].startswith("xoomar:")


def test_refresh_writes_loadable_snapshot_atomically(tmp_path):
    from app.market.economic_event_source import refresh_owned_snapshot

    target = tmp_path / "calendar" / "events.json"
    payload = {
        "data": [
            {
                "source": "fed",
                "eventName": "FOMC Rate Decision",
                "importance": "high",
                "scheduledAt": "2026-08-25T00:20:00Z",
                "periodLabel": "August 2026",
            }
        ],
        "updatedAt": NOW.isoformat(),
        "source": "xoomar.com",
    }

    refreshed = refresh_owned_snapshot(
        target,
        now=NOW,
        fetch_payload=lambda _start, _end: payload,
    )

    assert refreshed is True
    assert target.is_file()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["events"][0]["title"] == "FOMC Rate Decision"
    assessment = load_owned_calendar(now=NOW, path=str(target)).assess(
        "CRYPTO:PERP:BTCUSDT", as_of=NOW
    )
    assert assessment.status == "BLOCKED"
    assert assessment.reason_code == "HIGH_IMPACT_EVENT_WINDOW"
    assert list(target.parent.glob(".*.tmp")) == []


def test_refresh_failure_preserves_last_good_snapshot(tmp_path):
    from app.market.economic_event_source import refresh_owned_snapshot

    target = tmp_path / "events.json"
    original = json.dumps(
        {
            "source": "previous-good",
            "fetched_at": NOW.isoformat(),
            "coverage_until": (NOW + timedelta(days=1)).isoformat(),
            "events": [],
        },
        sort_keys=True,
    )
    target.write_text(original, encoding="utf-8")

    def fail(_start, _end):
        raise RuntimeError("provider down")

    refreshed = refresh_owned_snapshot(target, now=NOW, fetch_payload=fail)

    assert refreshed is False
    assert target.read_text(encoding="utf-8") == original


def test_compose_shares_owned_calendar_snapshot_with_api_and_market_scheduler():
    compose_path = Path(__file__).parents[1] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]
    source = services["event-calendar"]
    expected_path = "/var/lib/signalai-calendar/events.json"

    assert source["environment"]["SIGNALAI_EVENT_CALENDAR_PATH"] == expected_path
    assert "calendar-data:/var/lib/signalai-calendar" in source["volumes"]
    assert source["command"] == ["python", "-m", "app.market.economic_event_source"]

    for service_name in ("api", "scheduler"):
        service = services[service_name]
        assert service["environment"]["SIGNALAI_EVENT_CALENDAR_PATH"] == expected_path
        assert "calendar-data:/var/lib/signalai-calendar:ro" in service["volumes"]

    assert "calendar-data" in compose["volumes"]
