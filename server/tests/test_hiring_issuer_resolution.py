from __future__ import annotations

from app.research import hiring_runtime
from app.research.adapters.trudvsem import VacancyDatum


def vacancy(*, inn: str = "", name: str) -> VacancyDatum:
    return VacancyDatum(
        vacancy_id="vacancy-1",
        employer_code="employer-1",
        employer_inn=inn,
        employer_ogrn="",
        employer_name=name,
        title="Инженер",
        region_code="77",
        region_name="Москва",
        published_at=None,
        modified_at=None,
    )


def test_known_inn_wins_over_employer_name():
    issuer = hiring_runtime._issuer(
        vacancy(inn="7736050003", name="Другое отображаемое имя")
    )

    assert issuer is not None
    assert issuer.secid == "GAZP"


def test_unknown_inn_does_not_fall_back_to_brand_name():
    issuer = hiring_runtime._issuer(
        vacancy(inn="0000000000", name="Газпром")
    )

    assert issuer is None


def test_brand_fallback_requires_exact_normalized_alias():
    assert hiring_runtime._issuer(vacancy(name="ПАО Газпром")).secid == "GAZP"
    assert hiring_runtime._issuer(vacancy(name="Газпром нефть")) is None
