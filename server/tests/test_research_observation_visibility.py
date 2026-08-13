from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import ResearchObservation
from app.research.observation_read import visible_observations
from app.research.sources import sync_registry


def _row(*, entity_id: str, tradable_at: datetime, observation_type: str) -> ResearchObservation:
    return ResearchObservation(
        observation_type=observation_type,
        entity_id=entity_id,
        source_id="trudvsem",
        period_start=None,
        period_end=tradable_at.date(),
        published_at=tradable_at - timedelta(days=1),
        first_seen_at=tradable_at - timedelta(days=1),
        tradable_at=tradable_at,
        publication_time_uncertain=False,
        lineage_root_id=f"trudvsem:issuer:{entity_id}",
        source_locator={},
        raw_sha256="",
        value_numeric=None,
        value_text="",
        unit="",
    )


def test_visible_observations_excludes_future_rows_and_other_entities(session):
    sync_registry(session)
    as_of = datetime(2026, 8, 13, 12, tzinfo=UTC)
    session.add_all(
        [
            _row(
                entity_id="GAZP",
                tradable_at=as_of - timedelta(minutes=1),
                observation_type="trudvsem:vacancy:visible",
            ),
            _row(
                entity_id="GAZP",
                tradable_at=as_of + timedelta(minutes=1),
                observation_type="trudvsem:vacancy:future",
            ),
            _row(
                entity_id="SBER",
                tradable_at=as_of - timedelta(minutes=1),
                observation_type="trudvsem:vacancy:other",
            ),
        ]
    )
    session.flush()

    rows = visible_observations(
        session,
        source_id="trudvsem",
        entity_id="GAZP",
        as_of=as_of,
        observation_prefix="trudvsem:vacancy:",
    )

    assert [row.observation_type for row in rows] == ["trudvsem:vacancy:visible"]
