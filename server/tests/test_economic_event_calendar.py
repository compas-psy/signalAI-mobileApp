import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.market.economic_events import (
    EconomicEventCalendar,
    load_owned_calendar,
    parse_provider_rows,
)
from app.models.enums import LiquidityRegime, QualityStatus
from app.scoring.admission import admit
from decimal import Decimal


NOW = datetime(2026, 8, 21, 9, tzinfo=UTC)


def test_provider_fixture_is_deduplicated_visible_as_of_and_blocks_matching_window():
    """A future/duplicate provider row must not create a false-safe admission."""
    fixture = Path(__file__).with_name("fixtures") / "economic_events.json"
    rows = parse_provider_rows(json.loads(fixture.read_text()), source="fixture_calendar")
    calendar = EconomicEventCalendar(
        rows,
        source_available=True,
        fetched_at=NOW - timedelta(minutes=5),
        coverage_until=NOW + timedelta(hours=2),
    )

    result = calendar.assess("MOEX:FUT:SIU6", as_of=NOW)

    assert len(calendar.visible(as_of=NOW)) == 1
    assert result.status == "BLOCKED"
    assert result.reason_code == "HIGH_IMPACT_EVENT_WINDOW"
    assert result.event is not None
    assert result.event.provenance.source == "fixture_calendar"


def test_missing_source_and_ambiguous_mapping_are_explicit_never_safe():
    unavailable = EconomicEventCalendar((), source_available=False).assess(
        "CRYPTO:PERP:BTCUSDT", as_of=NOW
    )
    ambiguous = EconomicEventCalendar(
        parse_provider_rows(
            [
                {
                    "provider_event_id": "global",
                    "title": "Central bank decision",
                    "scheduled_at": (NOW + timedelta(minutes=20)).isoformat(),
                    "observed_at": (NOW - timedelta(hours=1)).isoformat(),
                    "tradable_at": (NOW - timedelta(hours=1)).isoformat(),
                    "instrument_tags": ["GLOBAL"],
                    "impact": "HIGH",
                }
            ],
            source="fixture_calendar",
        ),
        source_available=True,
        fetched_at=NOW - timedelta(minutes=5),
        coverage_until=NOW + timedelta(hours=2),
    ).assess("CRYPTO:PERP:BTCUSDT", as_of=NOW)

    assert (unavailable.status, unavailable.reason_code) == ("UNAVAILABLE", "EVENT_SOURCE_UNAVAILABLE")
    assert (ambiguous.status, ambiguous.reason_code) == ("AMBIGUOUS", "EVENT_INSTRUMENT_MAPPING_AMBIGUOUS")


def test_visible_revisions_are_as_of_deterministic_and_reject_naive_time():
    rows = parse_provider_rows(
        [
            {"provider_event_id": "r", "title": "later", "scheduled_at": "2026-08-21T10:00:00Z", "observed_at": "2026-08-20T12:00:00Z", "tradable_at": "2026-08-21T09:05:00Z", "instrument_tags": ["USD"], "impact": "HIGH"},
            {"provider_event_id": "r", "title": "earlier", "scheduled_at": "2026-08-21T10:00:00Z", "observed_at": "2026-08-20T12:00:00Z", "tradable_at": "2026-08-20T12:05:00Z", "instrument_tags": ["USD"], "impact": "HIGH"},
        ], source="fixture"
    )
    calendar = EconomicEventCalendar(tuple(reversed(rows)), source_available=True)

    assert calendar.visible(as_of=NOW)[0].title == "earlier"
    try:
        calendar.visible(as_of=datetime(2026, 8, 21, 9))
    except ValueError as exc:
        assert "timezone" in str(exc)
    else:
        raise AssertionError("naive as_of must fail closed")


def test_admission_uses_existing_blocking_policy_for_calendar_unavailability():
    assessment = EconomicEventCalendar((), source_available=False).assess(
        "MOEX:FUT:SIU6", as_of=NOW
    )
    decision = admit(
        probability=Decimal("0.70"), expected_r=Decimal("0.90"), rr_tp2=Decimal("3"),
        confidence=Decimal("0.80"), liquidity=LiquidityRegime.GOOD, has_trigger=True,
        risk_blocked=False, event_assessment=assessment,
    )

    assert decision.status is QualityStatus.REJECTED
    assert decision.failed[0].name == "economic_event"


def test_owned_file_source_requires_fresh_metadata_and_coverage(tmp_path):
    source = tmp_path / "events.json"
    source.write_text(
        json.dumps(
            {
                "source": "owner-feed-v1",
                "fetched_at": (NOW - timedelta(minutes=5)).isoformat(),
                "coverage_until": (NOW + timedelta(hours=2)).isoformat(),
                "events": [
                    {
                        "provider_event_id": "rate-1",
                        "title": "Rate decision",
                        "scheduled_at": (NOW + timedelta(minutes=20)).isoformat(),
                        "observed_at": (NOW - timedelta(minutes=10)).isoformat(),
                        "tradable_at": (NOW - timedelta(minutes=5)).isoformat(),
                        "instrument_tags": ["USD"],
                        "impact": "HIGH",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    fresh = load_owned_calendar(now=NOW, path=str(source)).assess(
        "MOEX:FUT:SIU6", as_of=NOW
    )
    stale = load_owned_calendar(
        now=NOW + timedelta(hours=7), path=str(source)
    ).assess("MOEX:FUT:SIU6", as_of=NOW + timedelta(hours=7))

    assert fresh.status == "BLOCKED"
    assert stale.reason_code in {"EVENT_SOURCE_STALE", "EVENT_SOURCE_COVERAGE_GAP"}


def test_owned_file_source_rejects_symlink(tmp_path):
    source = tmp_path / "events.json"
    source.write_text("{}", encoding="utf-8")
    link = tmp_path / "events-link.json"
    link.symlink_to(source)

    result = load_owned_calendar(now=NOW, path=str(link)).assess(
        "MOEX:FUT:SIU6", as_of=NOW
    )

    assert result.reason_code == "EVENT_SOURCE_UNAVAILABLE"


def test_owned_file_source_cannot_be_swapped_to_false_clear_after_metadata_check(
    tmp_path, monkeypatch
):
    source = tmp_path / "events.json"
    replacement = tmp_path / "replacement.json"

    def envelope(*, impact: str) -> str:
        return json.dumps(
            {
                "source": "owner-feed-v1",
                "fetched_at": (NOW - timedelta(minutes=5)).isoformat(),
                "coverage_until": (NOW + timedelta(hours=2)).isoformat(),
                "events": [
                    {
                        "provider_event_id": "rate-1",
                        "title": "Rate decision",
                        "scheduled_at": (NOW + timedelta(minutes=20)).isoformat(),
                        "observed_at": (NOW - timedelta(minutes=10)).isoformat(),
                        "tradable_at": (NOW - timedelta(minutes=5)).isoformat(),
                        "instrument_tags": ["USD"],
                        "impact": impact,
                    }
                ],
            }
        )

    source.write_text(envelope(impact="HIGH"), encoding="utf-8")
    replacement.write_text(envelope(impact="LOW"), encoding="utf-8")
    original_stat = Path.stat
    swapped = False

    def swap_after_stat(path: Path, *args, **kwargs):
        nonlocal swapped
        result = original_stat(path, *args, **kwargs)
        if path == source and not swapped:
            swapped = True
            source.unlink()
            source.symlink_to(replacement)
        return result

    monkeypatch.setattr(Path, "stat", swap_after_stat)

    result = load_owned_calendar(now=NOW, path=str(source)).assess(
        "MOEX:FUT:SIU6", as_of=NOW
    )

    assert result.blocks_admission
    assert result.status != "CLEAR"
