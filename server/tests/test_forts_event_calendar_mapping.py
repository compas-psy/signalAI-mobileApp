from datetime import UTC, datetime, timedelta

from app.market.economic_events import EconomicEventCalendar, parse_provider_rows


NOW = datetime(2026, 8, 21, 9, tzinfo=UTC)


def _calendar(*, event_minutes: int | None) -> EconomicEventCalendar:
    rows = []
    if event_minutes is not None:
        rows = parse_provider_rows(
            [
                {
                    "provider_event_id": "us-macro-1",
                    "title": "US high-impact macro",
                    "scheduled_at": (NOW + timedelta(minutes=event_minutes)).isoformat(),
                    "observed_at": (NOW - timedelta(hours=1)).isoformat(),
                    "tradable_at": (NOW - timedelta(hours=1)).isoformat(),
                    "instrument_tags": ["USD"],
                    "impact": "HIGH",
                }
            ],
            source="fixture-us-macro",
        )
    return EconomicEventCalendar(
        rows,
        source_available=True,
        fetched_at=NOW - timedelta(minutes=5),
        coverage_until=NOW + timedelta(hours=4),
    )


def test_generic_forts_is_clear_when_fresh_calendar_has_no_blocking_event():
    result = _calendar(event_minutes=None).assess("MOEX:FUT:YDU6", as_of=NOW)

    assert (result.status, result.reason_code) == ("CLEAR", "NO_BLOCKING_EVENT")


def test_generic_forts_still_blocks_in_high_impact_us_macro_window():
    result = _calendar(event_minutes=20).assess("MOEX:FUT:YDU6", as_of=NOW)

    assert (result.status, result.reason_code) == (
        "BLOCKED",
        "HIGH_IMPACT_EVENT_WINDOW",
    )


def test_unknown_non_moex_instrument_remains_fail_closed():
    result = _calendar(event_minutes=None).assess("OTHER:FUT:XYZ", as_of=NOW)

    assert (result.status, result.reason_code) == (
        "AMBIGUOUS",
        "EVENT_INSTRUMENT_MAPPING_AMBIGUOUS",
    )
