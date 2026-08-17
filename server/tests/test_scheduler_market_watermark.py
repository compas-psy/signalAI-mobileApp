from datetime import UTC, datetime

from app.scheduler.market_watermark import changed_lanes


def test_crypto_catchup_at_same_timestamp_still_triggers_scan():
    at = datetime(2026, 8, 17, 10, tzinfo=UTC)
    before = {"forts": (at, 120), "crypto": (at, 80)}
    after = {"forts": (at, 120), "crypto": (at, 81)}

    assert changed_lanes(before, after) == ("crypto",)


def test_forts_and_crypto_are_independent_lanes():
    at = datetime(2026, 8, 17, 10, tzinfo=UTC)
    later = datetime(2026, 8, 17, 11, tzinfo=UTC)
    before = {"forts": (at, 120), "crypto": (at, 80)}

    assert changed_lanes(before, {**before, "forts": (later, 121)}) == ("forts",)
    assert changed_lanes(before, {**before, "crypto": (later, 81)}) == ("crypto",)
    assert changed_lanes(before, before) == ()
