from __future__ import annotations

from datetime import UTC, datetime

from app.backtest.venue_readiness import assess_history_readiness


def test_venue_history_requires_full_calendar_36_months() -> None:
    start = datetime(2023, 8, 29, 12, tzinfo=UTC)

    blocked = assess_history_readiness(
        [
            start,
            datetime(2026, 7, 29, 12, tzinfo=UTC),
        ],
        min_history_months=36,
    )
    ready = assess_history_readiness(
        [
            start,
            datetime(2026, 8, 29, 12, tzinfo=UTC),
        ],
        min_history_months=36,
    )

    assert blocked.ready is False
    assert blocked.status == "BLOCKED_INSUFFICIENT_HISTORY"
    assert blocked.required_months == 36
    assert blocked.available_months == 35
    assert blocked.period_from == start
    assert blocked.period_to == datetime(2026, 7, 29, 12, tzinfo=UTC)

    assert ready.ready is True
    assert ready.status == "DATA_READY"
    assert ready.available_months == 36


def test_history_readiness_fails_closed_on_empty_naive_unsorted_or_duplicate_times() -> None:
    aware = datetime(2026, 8, 29, tzinfo=UTC)
    naive = datetime(2026, 8, 29)

    invalid = (
        [],
        [naive],
        [aware, datetime(2026, 7, 29, tzinfo=UTC)],
        [aware, aware],
    )
    for values in invalid:
        try:
            assess_history_readiness(values, min_history_months=36)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid history input must fail closed")
