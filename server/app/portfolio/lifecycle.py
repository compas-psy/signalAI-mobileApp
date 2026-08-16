"""Lifecycle инвестиционных моделей: поколения, срок жизни и причины пересчёта.

Модель портфеля — версия рекомендации, а не mutable-состояние. Новый расчёт
создаёт новое поколение; старое остаётся аудит-трейлом и источником diff.
Текущей считается только последнее поколение каждого слота, пока оно не
истекло.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Bar, PortfolioModel
from ..models.enums import Timeframe


Slot = tuple[object, object, int]


@dataclass(frozen=True, slots=True)
class ModelDiff:
    """Изменения между двумя последовательными поколениями одного слота."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    weight_changed: tuple[str, ...] = ()

    @property
    def material(self) -> bool:
        return bool(self.added or self.removed or self.weight_changed)


@dataclass(frozen=True, slots=True)
class RebuildDecision:
    """Нужно ли планировщику пересобирать модели и почему."""

    due: bool
    reason: str


def _slot(model: PortfolioModel) -> Slot:
    return model.profile, model.package, int(model.horizon_years)


def latest_models(session: Session) -> list[PortfolioModel]:
    """Последнее поколение каждого profile/package/horizon, включая expired."""
    ordered = list(
        session.execute(
            select(PortfolioModel).order_by(PortfolioModel.generated_at.desc())
        ).scalars()
    )
    latest: dict[Slot, PortfolioModel] = {}
    for model in ordered:
        latest.setdefault(_slot(model), model)
    return list(latest.values())


def current_models(
    session: Session, *, as_of: datetime | None = None
) -> list[PortfolioModel]:
    """Только актуальные последние поколения; к старой версии не откатываемся."""
    now = as_of or datetime.now(UTC)
    return [model for model in latest_models(session) if model.valid_until > now]


def previous_generation(session: Session, model: PortfolioModel) -> PortfolioModel | None:
    """Предыдущее поколение того же слота, если оно существует."""
    return session.execute(
        select(PortfolioModel)
        .where(
            PortfolioModel.profile == model.profile,
            PortfolioModel.package == model.package,
            PortfolioModel.horizon_years == model.horizon_years,
            PortfolioModel.generated_at < model.generated_at,
        )
        .order_by(PortfolioModel.generated_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def model_diff(
    previous: PortfolioModel | None,
    current: PortfolioModel,
    *,
    weight_threshold: Decimal = Decimal("0.005"),
) -> ModelDiff:
    """Состав и целевые веса, изменившиеся минимум на 0.5 п.п."""
    if previous is None:
        added = tuple(sorted(weight.instrument_id for weight in current.weights))
        return ModelDiff(added=added)

    before = {weight.instrument_id: Decimal(weight.target_weight) for weight in previous.weights}
    after = {weight.instrument_id: Decimal(weight.target_weight) for weight in current.weights}
    before_ids = set(before)
    after_ids = set(after)
    common = before_ids & after_ids
    return ModelDiff(
        added=tuple(sorted(after_ids - before_ids)),
        removed=tuple(sorted(before_ids - after_ids)),
        weight_changed=tuple(
            sorted(
                instrument_id
                for instrument_id in common
                if abs(after[instrument_id] - before[instrument_id]) >= weight_threshold
            )
        ),
    )


def rebuild_due(
    session: Session,
    *,
    as_of: datetime | None = None,
    expiry_buffer: timedelta = timedelta(days=1),
) -> RebuildDecision:
    """Причина планового пересчёта без зависимости от клиентского приложения."""
    now = as_of or datetime.now(UTC)
    latest = latest_models(session)
    if not latest:
        return RebuildDecision(True, "no_model")

    if any(model.valid_until <= now for model in latest):
        return RebuildDecision(True, "model_expired")
    if any(model.valid_until <= now + expiry_buffer for model in latest):
        return RebuildDecision(True, "model_expiring")

    newest_generation = max(model.generated_at for model in latest)
    newest_d1 = session.execute(
        select(func.max(Bar.open_time)).where(
            Bar.timeframe == Timeframe.D1,
            Bar.is_closed.is_(True),
        )
    ).scalar_one_or_none()
    if newest_d1 is not None and newest_d1 > newest_generation:
        return RebuildDecision(True, "new_market_data")

    return RebuildDecision(False, "up_to_date")
