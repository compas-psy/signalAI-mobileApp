"""Revision semantics for persisted research observations.

A re-fetch of the same fact must not create another confirmation. If the
source later publishes a different value for the same series/entity/period,
however, the contradiction is evidence and must remain auditable instead of
being silently discarded.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import ResearchObservation
from app.research import collector
from app.research.provenance import cbr as cbr_provenance
from app.research.sources import sync_registry

NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
PERIOD = date(2026, 6, 30)


def _write(session, *, value: Decimal, seen_at: datetime, raw_sha256: str) -> bool:
    return collector._write(
        session,
        source_id="cbr_data",
        observation_type="cbr:enterprise_monitoring:demand",
        entity_id="RU",
        value=value,
        unit="balance",
        period_start=date(2026, 6, 1),
        period_end=PERIOD,
        published_at=None,
        availability=None,
        first_seen_at=seen_at,
        locator={"url": "https://example.invalid/cbr"},
        raw_sha256=raw_sha256,
        provenance=cbr_provenance("enterprise_monitoring"),
    )


def test_identical_refetch_is_still_a_duplicate(session):
    sync_registry(session, now=NOW)

    assert _write(session, value=Decimal("3.4"), seen_at=NOW, raw_sha256="a" * 64)
    session.flush()
    assert not _write(
        session,
        value=Decimal("3.4"),
        seen_at=NOW + timedelta(days=1),
        raw_sha256="b" * 64,
    )
    session.flush()

    rows = session.execute(select(ResearchObservation)).scalars().all()
    assert len(rows) == 1
    assert rows[0].revision_number == 0
    assert rows[0].supersedes_id is None


def test_conflicting_refetch_is_preserved_as_revision(session):
    sync_registry(session, now=NOW)

    assert _write(session, value=Decimal("3.4"), seen_at=NOW, raw_sha256="a" * 64)
    session.flush()
    original = session.execute(select(ResearchObservation)).scalars().one()

    revised_at = NOW + timedelta(days=10)
    assert _write(
        session,
        value=Decimal("2.1"),
        seen_at=revised_at,
        raw_sha256="c" * 64,
    )
    session.flush()

    rows = session.execute(
        select(ResearchObservation).order_by(ResearchObservation.revision_number.asc())
    ).scalars().all()
    assert len(rows) == 2
    assert [Decimal(row.value_numeric) for row in rows] == [Decimal("3.4"), Decimal("2.1")]
    assert [row.revision_number for row in rows] == [0, 1]
    assert rows[1].supersedes_id == original.id
    # A correction becomes usable only when we actually saw that correction;
    # it must never rewrite history as if the revised value had been known on
    # the original collection date.
    assert rows[1].tradable_at >= revised_at
