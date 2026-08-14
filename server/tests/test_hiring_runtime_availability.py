"""HIRING must not consume a vacancy before its persisted tradable_at."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models import ResearchObservation, ResearchSource
from app.models.enums import LicenseStatus
from app.research.adapters import trudvsem
from app.research.hiring_runtime import _persist_vacancy_observation, _vacancy
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
        vacancy_id="vacancy-availability-1",
        employer_code="sber",
        employer_inn=issuer.inn,
        employer_ogrn="",
        employer_name=issuer.name,
        title="Инженер по данным",
        region_code="77",
        region_name="Москва",
        published_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        modified_at=modified_at,
        source_url="https://trudvsem.ru/vacancy-availability-1",
    )


def test_first_seen_revision_is_persisted_but_not_usable_before_tradable_at(session):
    _source(session)
    issuer = _issuer()
    row = _row(modified_at=datetime(2026, 1, 10, 8, 0, tzinfo=UTC))
    first_seen = datetime(2026, 1, 13, 10, 0, tzinfo=UTC)

    observation, created = _persist_vacancy_observation(
        session,
        row=row,
        issuer=issuer,
        first_seen_at=first_seen,
        raw_sha256="a" * 64,
    )
    session.flush()

    assert created is True
    assert observation.first_seen_at == first_seen
    assert observation.event_time == row.published_at
    assert observation.published_at == row.modified_at
    assert observation.tradable_at > first_seen
    assert observation.source_locator["source_created_at"] == row.published_at.isoformat()
    assert observation.source_locator["source_modified_at"] == row.modified_at.isoformat()

    assert (
        _vacancy(
            row,
            issuer,
            first_seen,
            tradable_at=observation.tradable_at,
        )
        is None
    )
    assert _vacancy(
        row,
        issuer,
        observation.tradable_at,
        tradable_at=observation.tradable_at,
    ) is not None


def test_same_revision_reuses_original_first_seen_and_matures_without_new_fact(session):
    _source(session)
    issuer = _issuer()
    row = _row(modified_at=datetime(2026, 1, 10, 8, 0, tzinfo=UTC))
    first_seen = datetime(2026, 1, 13, 10, 0, tzinfo=UTC)

    original, created = _persist_vacancy_observation(
        session,
        row=row,
        issuer=issuer,
        first_seen_at=first_seen,
        raw_sha256="b" * 64,
    )
    session.flush()
    assert created is True

    later = original.tradable_at.replace(hour=min(23, original.tradable_at.hour + 1))
    repeated, repeated_created = _persist_vacancy_observation(
        session,
        row=row,
        issuer=issuer,
        first_seen_at=later,
        raw_sha256="c" * 64,
    )
    session.flush()

    assert repeated_created is False
    assert repeated.id == original.id
    assert repeated.first_seen_at == first_seen
    assert repeated.tradable_at == original.tradable_at
    assert session.query(ResearchObservation).filter_by(
        source_id=trudvsem.SOURCE_ID,
        entity_id=issuer.secid,
    ).count() == 1
    assert _vacancy(
        row,
        issuer,
        later,
        tradable_at=repeated.tradable_at,
    ) is not None


def test_modified_revision_gets_new_first_seen_and_new_availability_lag(session):
    _source(session)
    issuer = _issuer()
    first = _row(modified_at=datetime(2026, 1, 10, 8, 0, tzinfo=UTC))
    first_seen = datetime(2026, 1, 13, 10, 0, tzinfo=UTC)
    original, _ = _persist_vacancy_observation(
        session,
        row=first,
        issuer=issuer,
        first_seen_at=first_seen,
        raw_sha256="d" * 64,
    )
    session.flush()

    revised = _row(modified_at=datetime(2026, 1, 15, 8, 0, tzinfo=UTC))
    revision_seen = datetime(2026, 1, 15, 11, 0, tzinfo=UTC)
    revision, created = _persist_vacancy_observation(
        session,
        row=revised,
        issuer=issuer,
        first_seen_at=revision_seen,
        raw_sha256="e" * 64,
    )
    session.flush()

    assert created is True
    assert revision.id != original.id
    assert revision.revision_number == original.revision_number + 1
    assert revision.supersedes_id == original.id
    assert revision.first_seen_at == revision_seen
    assert revision.tradable_at > revision_seen
    assert _vacancy(
        revised,
        issuer,
        revision_seen,
        tradable_at=revision.tradable_at,
    ) is None
