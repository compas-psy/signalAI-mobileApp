"""Owner-facing selection of three investment portfolio profiles.

The optimiser may keep several internal package variants per risk profile.  The
owner should not have to choose between algorithm implementation variants,
though: this module deterministically selects one current model for each of
the three risk profiles at one explicit horizon.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Iterable

from ..models.enums import PackageSize, RiskProfile


class HeadlineStatus(StrEnum):
    READY = "ready"
    RISKIER_THAN_TARGET = "riskier_than_target"
    MISSING = "missing"


PROFILE_LABELS: dict[RiskProfile, str] = {
    RiskProfile.CONSERVATIVE: "Консервативный",
    RiskProfile.OPTIMAL: "Сбалансированный",
    RiskProfile.AGGRESSIVE: "Доходный",
}

_PROFILE_ORDER = (
    RiskProfile.CONSERVATIVE,
    RiskProfile.OPTIMAL,
    RiskProfile.AGGRESSIVE,
)
_PACKAGE_ORDER = {
    PackageSize.SIMPLE: 0,
    PackageSize.BALANCED: 1,
    PackageSize.MAX_POTENTIAL: 2,
}


@dataclass(frozen=True, slots=True)
class HeadlinePortfolio:
    profile: RiskProfile
    label: str
    status: HeadlineStatus
    reason: str
    model: Any | None


def _number(value: Any, *, missing: float) -> float:
    if value is None:
        return missing
    return float(value)


def _latest_variants(models: Iterable[Any]) -> list[Any]:
    """Collapse repeated persisted versions before choosing a headline."""
    latest: dict[tuple[RiskProfile, PackageSize, int], Any] = {}
    for model in models:
        key = (model.profile, model.package, model.horizon_years)
        incumbent = latest.get(key)
        if incumbent is None or model.generated_at > incumbent.generated_at:
            latest[key] = model
    return list(latest.values())


def _compliant_choice(models: list[Any]) -> Any | None:
    compliant = [model for model in models if bool(model.meets_target)]
    if not compliant:
        return None

    balanced = [model for model in compliant if model.package is PackageSize.BALANCED]
    if balanced:
        return max(balanced, key=lambda model: model.generated_at)

    return min(
        compliant,
        key=lambda model: (
            -_number(model.expected_return_low, missing=float("-inf")),
            _number(model.cvar_95, missing=float("inf")),
            _PACKAGE_ORDER.get(model.package, 99),
            -model.generated_at.timestamp(),
        ),
    )


def _risk_fallback(models: list[Any]) -> Any | None:
    if not models:
        return None
    return min(
        models,
        key=lambda model: (
            _number(model.model_drawdown_limit, missing=float("inf")),
            _number(model.cvar_95, missing=float("inf")),
            0 if model.package is PackageSize.BALANCED else 1,
            _PACKAGE_ORDER.get(model.package, 99),
            -model.generated_at.timestamp(),
        ),
    )


def select_headlines(
    models: Iterable[Any],
    *,
    horizon_years: int,
    as_of: datetime,
) -> list[HeadlinePortfolio]:
    """Return exactly one owner-facing slot for every risk profile.

    Only unexpired models at the requested horizon participate.  A computed
    portfolio that misses its profile risk target is still shown as an
    explicit warning state rather than disappearing and looking like a data
    outage.
    """
    current = [
        model
        for model in _latest_variants(models)
        if model.horizon_years == horizon_years and model.valid_until > as_of
    ]

    result: list[HeadlinePortfolio] = []
    for profile in _PROFILE_ORDER:
        candidates = [model for model in current if model.profile is profile]
        chosen = _compliant_choice(candidates)
        if chosen is not None:
            result.append(
                HeadlinePortfolio(
                    profile=profile,
                    label=PROFILE_LABELS[profile],
                    status=HeadlineStatus.READY,
                    reason="",
                    model=chosen,
                )
            )
            continue

        fallback = _risk_fallback(candidates)
        if fallback is not None:
            result.append(
                HeadlinePortfolio(
                    profile=profile,
                    label=PROFILE_LABELS[profile],
                    status=HeadlineStatus.RISKIER_THAN_TARGET,
                    reason=(
                        "Состав рассчитан, но риск выше целевого для этого профиля; "
                        "показан наиболее осторожный из доступных вариантов."
                    ),
                    model=fallback,
                )
            )
            continue

        result.append(
            HeadlinePortfolio(
                profile=profile,
                label=PROFILE_LABELS[profile],
                status=HeadlineStatus.MISSING,
                reason=(
                    f"Нет актуального состава на горизонт {horizon_years} г.: "
                    "нужен новый расчёт или достаточно данных."
                ),
                model=None,
            )
        )
    return result


__all__ = [
    "HeadlinePortfolio",
    "HeadlineStatus",
    "PROFILE_LABELS",
    "select_headlines",
]
