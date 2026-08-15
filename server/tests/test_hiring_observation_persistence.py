from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import ResearchObservation
from app.research import hiring_runtime
from app.research.adapters.trudvsem import VacancyDatum
from app.research.sources import sync_registry


def datum(*, modified: datetime) -> VacancyDatum:
    return VacancyDatum(
        vacancy_id="vacancy-42",
        employer_code="employer-42",
        employer_inn="7736050003",
        employer_ogrn="",
        employer_name="Газпром",
        title="Инженер",
        region_code="77",
        region_name="Москва",
        published_at=datetime(2026, 8, 10, 9, tzinfo=UTC),
        modified_at=modified,
    )


def test_same_revision_keeps_original_first_seen_and_deduplicates(session):
    sync_registry(session)
    row = datum(modified=datetime(2026, 8, 12, 10, tzinfo=UTC))
    issuer = hiring_runtime._issuer(row)
    assert issuer is not None
    first_seen = datetime(2026, 8, 13, 10, tzinfo=UTC)

    assert hiring_runtime._persist_vacancy_observation(
        session, row=row, issuer=issuer, first_seen_at=first_seen, raw_sha256="a" * 64
    )
    session.flush()
    assert not hiring_runtime._persist_vacancy_observation(
        session,
        row=row,
        issuer=issuer,
        first_seen_at=first_seen + timedelta(days=2),
        raw_sha256="b" * 64,
    )
    session.flush()

    observations = list(session.execute(select(ResearchObservation)).scalars())
    assert len(observations) == 1
    assert observations[0].first_seen_at == first_seen
    assert observations[0].tradable_at > first_seen
    assert observations[0].source_id == "trudvsem"
    assert observations[0].entity_id == "GAZP"


def test_new_information_time_creates_a_new_revision(session):
    sync_registry(session)
    first_seen = datetime(2026, 8, 13, 10, tzinfo=UTC)
    first = datum(modified=datetime(2026, 8, 12, 10, tzinfo=UTC))
    second = datum(modified=datetime(2026, 8, 13, 11, tzinfo=UTC))
    issuer = hiring_runtime._issuer(first)
    assert issuer is not None

    assert hiring_runtime._persist_vacancy_observation(
        session, row=first, issuer=issuer, first_seen_at=first_seen, raw_sha256="a" * 64
    )
    assert hiring_runtime._persist_vacancy_observation(
        session,
        row=second,
        issuer=issuer,
        first_seen_at=first_seen + timedelta(days=1),
        raw_sha256="b" * 64,
    )
    session.flush()

    observations = list(session.execute(select(ResearchObservation)).scalars())
    assert len(observations) == 2
    assert len({row.observation_type for row in observations}) == 2
    assert len({row.lineage_root_id for row in observations}) == 1


def test_runtime_uses_persisted_tradable_at_before_evaluating_vacancy(session):
    sync_registry(session)
    row = datum(modified=datetime(2026, 8, 12, 10, tzinfo=UTC))
    issuer = hiring_runtime._issuer(row)
    assert issuer is not None
    first_seen = datetime(2026, 8, 13, 10, tzinfo=UTC)

    assert hiring_runtime._persist_vacancy_observation(
        session,
        row=row,
        issuer=issuer,
        first_seen_at=first_seen,
        raw_sha256="a" * 64,
    )
    session.flush()
    observation = session.execute(select(ResearchObservation)).scalar_one()

    assert observation.tradable_at > first_seen
    assert hiring_runtime._runtime_vacancy(
        session,
        row=row,
        issuer=issuer,
        as_of=first_seen,
    ) is None

    usable = hiring_runtime._runtime_vacancy(
        session,
        row=row,
        issuer=issuer,
        as_of=observation.tradable_at + timedelta(seconds=1),
    )
    assert usable is not None
    assert usable.employer_entity_id == "GAZP"


def test_runtime_reuses_original_availability_after_repeat_collection(session):
    sync_registry(session)
    row = datum(modified=datetime(2026, 8, 12, 10, tzinfo=UTC))
    issuer = hiring_runtime._issuer(row)
    assert issuer is not None
    first_seen = datetime(2026, 8, 13, 10, tzinfo=UTC)

    assert hiring_runtime._persist_vacancy_observation(
        session,
        row=row,
        issuer=issuer,
        first_seen_at=first_seen,
        raw_sha256="a" * 64,
    )
    session.flush()
    original = session.execute(select(ResearchObservation)).scalar_one()
    original_tradable_at = original.tradable_at

    assert not hiring_runtime._persist_vacancy_observation(
        session,
        row=row,
        issuer=issuer,
        first_seen_at=first_seen + timedelta(days=7),
        raw_sha256="b" * 64,
    )
    session.flush()

    repeated = session.execute(select(ResearchObservation)).scalar_one()
    assert repeated.first_seen_at == first_seen
    assert repeated.tradable_at == original_tradable_at
    assert hiring_runtime._runtime_vacancy(
        session,
        row=row,
        issuer=issuer,
        as_of=original_tradable_at + timedelta(seconds=1),
    ) is not None
