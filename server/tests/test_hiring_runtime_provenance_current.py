"""HIRING availability must reuse durable revisions and preserve source provenance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import ResearchObservation, ResearchSource
from app.models.enums import LicenseStatus
from app.research.adapters import trudvsem
from app.research.hiring_runtime import (
    _persist_vacancy_observation,
    _runtime_vacancy,
    _vacancy_lineage_root,
    _vacancy_observation_type,
)
from app.research.issuers import REGISTRY


def _source(session) -> None:
    if session.get(ResearchSource, trudvsem.SOURCE_ID) is not None:
        return
    session.add(
        ResearchSource(
            source_id=trudvsem.SOURCE_ID,
            name="Работа России",
            owner="Роструд",
            base_url=trudvsem.BASE_URL,
            access_method="api",
            license_status=LicenseStatus.APPROVED,
            enabled=True,
        )
    )
    session.flush()


def _issuer():
    return next(issuer for issuer in REGISTRY if issuer.secid == "SBER")


def _row(*, modified_at: datetime) -> trudvsem.VacancyDatum:
    issuer = _issuer()
    return trudvsem.VacancyDatum(
        vacancy_id="vacancy-provenance-current-1",
        employer_code="sber",
        employer_inn=issuer.inn,
        employer_ogrn="",
        employer_name=issuer.name,
        title="Инженер по данным",
        region_code="77",
        region_name="Москва",
        published_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        modified_at=modified_at,
        source_url="https://trudvsem.ru/vacancy-provenance-current-1",
    )


def _revision(session, row: trudvsem.VacancyDatum) -> ResearchObservation:
    return session.execute(
        select(ResearchObservation).where(
            ResearchObservation.source_id == trudvsem.SOURCE_ID,
            ResearchObservation.entity_id == _issuer().secid,
            ResearchObservation.observation_type == _vacancy_observation_type(row),
        )
    ).scalar_one()


def test_same_revision_reuses_original_availability_and_persists_source_clocks(session) -> None:
    _source(session)
    issuer = _issuer()
    row = _row(modified_at=datetime(2026, 1, 10, 8, 0, tzinfo=UTC))
    first_seen = datetime(2026, 1, 13, 10, 0, tzinfo=UTC)

    assert _persist_vacancy_observation(
        session,
        row=row,
        issuer=issuer,
        first_seen_at=first_seen,
        raw_sha256="a" * 64,
    ) is True
    session.flush()
    original = _revision(session, row)

    assert original.first_seen_at == first_seen
    assert original.event_time == row.published_at
    assert original.published_at == row.modified_at
    assert original.source_locator["source_created_at"] == row.published_at.isoformat()
    assert original.source_locator["source_modified_at"] == row.modified_at.isoformat()
    assert _runtime_vacancy(
        session,
        row=row,
        issuer=issuer,
        as_of=first_seen,
    ) is None

    mature_at = original.tradable_at + timedelta(seconds=1)
    assert _persist_vacancy_observation(
        session,
        row=row,
        issuer=issuer,
        first_seen_at=mature_at,
        raw_sha256="b" * 64,
    ) is False
    session.flush()
    repeated = _revision(session, row)

    assert repeated.id == original.id
    assert repeated.first_seen_at == first_seen
    assert repeated.tradable_at == original.tradable_at
    assert _runtime_vacancy(
        session,
        row=row,
        issuer=issuer,
        as_of=mature_at,
    ) is not None


def test_modified_source_revision_resets_first_seen_and_availability_lag(session) -> None:
    _source(session)
    issuer = _issuer()
    first = _row(modified_at=datetime(2026, 1, 10, 8, 0, tzinfo=UTC))
    first_seen = datetime(2026, 1, 13, 10, 0, tzinfo=UTC)
    assert _persist_vacancy_observation(
        session,
        row=first,
        issuer=issuer,
        first_seen_at=first_seen,
        raw_sha256="c" * 64,
    ) is True
    session.flush()
    original = _revision(session, first)

    revised = _row(modified_at=datetime(2026, 1, 15, 8, 0, tzinfo=UTC))
    revision_seen = datetime(2026, 1, 15, 11, 0, tzinfo=UTC)
    assert _persist_vacancy_observation(
        session,
        row=revised,
        issuer=issuer,
        first_seen_at=revision_seen,
        raw_sha256="d" * 64,
    ) is True
    session.flush()
    revision = _revision(session, revised)

    assert revision.id != original.id
    assert revision.lineage_root_id == _vacancy_lineage_root(revised)
    assert revision.revision_number == original.revision_number + 1
    assert revision.supersedes_id == original.id
    assert revision.first_seen_at == revision_seen
    assert revision.tradable_at > revision_seen
    assert revision.source_locator["source_created_at"] == revised.published_at.isoformat()
    assert revision.source_locator["source_modified_at"] == revised.modified_at.isoformat()
    assert _runtime_vacancy(
        session,
        row=revised,
        issuer=issuer,
        as_of=revision_seen,
    ) is None
