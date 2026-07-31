"""Портфель: пакеты капитала и прогресс их сборки (engine-ТЗ §23, §6).

Два эндпоинта и разное отношение к пустоте.

``/portfolio/packages`` возвращает пакеты **и** состояние конвейера. Пустой
список пакетов сам по себе неинформативен: он одинаково выглядит и когда
данных ещё нет, и когда ни один состав не прошёл проверку на истории.
Поэтому статус едет всегда, а в нём — на каком шаге стоит работа.

``/portfolio/rebuild`` пересобирает состав по требованию. Прогон тяжёлый
(сотня оптимизаций на пакет), поэтому чаще чем раз в десять минут он не
запускается — и отвечает не отказом, а тем, когда считал в прошлый раз.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import get_db
from ...market.investments import INVESTMENT_CLASSES, investment_universe
from ...models import Bar, Instrument, PortfolioModel, PortfolioRun, TradeIdea
from ...models.enums import PackageSize, RiskProfile, Timeframe
from ...portfolio.build import build_all
from ...schemas.portfolio import (
    ClassSliceOut,
    UniverseSliceOut,
    PackageOut,
    PortfolioResponse,
    PortfolioStatusOut,
    PositionOut,
    StageOut,
)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

CLASS_LABELS = {
    "MONEY_MARKET": "Денежный рынок",
    "BOND_FUND": "Облигационные фонды",
    "OFZ": "ОФЗ",
    "CORPORATE_BOND": "Корпоративные облигации",
    "EQUITY": "Акции",
    "GOLD": "Золото",
    "CRYPTO_SPOT": "Криптовалюты",
    "CRYPTO_PERPETUAL": "Криптовалюты",
}

# Последний прогон процесса. В памяти, а не в базе: это защита от повторного
# запуска тяжёлой сборки, а не факт, который надо переживать перезапуск.
_last_rebuild: dict[str, datetime] = {}
REBUILD_COOLDOWN = timedelta(minutes=10)


def _symbol(session: Session, instrument_id: str) -> tuple[str, str]:
    row = session.execute(
        select(Instrument.symbol, Instrument.title).where(
            Instrument.instrument_id == instrument_id
        )
    ).first()
    if row is None:
        return instrument_id.split(":")[-1], ""
    return row[0], row[1] or ""


def _universe_mix(session: Session) -> list[UniverseSliceOut]:
    """Состав вселенной по классам: сколько всего и у скольких есть история."""
    universe = investment_universe(session)
    if not universe:
        return []
    counts = session.execute(
        select(Bar.instrument_id, func.count())
        .where(
            Bar.instrument_id.in_([i.instrument_id for i in universe]),
            Bar.timeframe == Timeframe.D1,
            Bar.is_closed.is_(True),
        )
        .group_by(Bar.instrument_id)
    ).all()
    bars = {key: value for key, value in counts}

    total: dict[str, int] = {}
    ready: dict[str, int] = {}
    for item in universe:
        key = str(item.asset_class)
        total[key] = total.get(key, 0) + 1
        if bars.get(item.instrument_id, 0) >= 120:
            ready[key] = ready.get(key, 0) + 1
    return [
        UniverseSliceOut(
            asset_class=key,
            label=CLASS_LABELS.get(key, key),
            total=value,
            with_history=ready.get(key, 0),
        )
        for key, value in sorted(total.items(), key=lambda kv: -kv[1])
    ]


def _last_run(session: Session) -> PortfolioRun | None:
    return session.execute(
        select(PortfolioRun).order_by(PortfolioRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()


def _stages(
    session: Session, models: list[PortfolioModel], run: PortfolioRun | None
) -> list[StageOut]:
    universe = investment_universe(session)
    with_history = 0
    if universe:
        rows = session.execute(
            select(Bar.instrument_id, func.count())
            .where(
                Bar.instrument_id.in_([i.instrument_id for i in universe]),
                Bar.timeframe == Timeframe.D1,
                Bar.is_closed.is_(True),
            )
            .group_by(Bar.instrument_id)
        ).all()
        with_history = sum(1 for _, count in rows if count >= 120)

    ideas = session.execute(select(func.count()).select_from(TradeIdea)).scalar_one()
    validated = [m for m in models if m.weights]

    return [
        StageOut(
            key="universe",
            name="Вселенная инструментов",
            done=bool(universe),
            detail=f"{len(universe)} бумаг" if universe else "доски не загружены",
        ),
        StageOut(
            key="history",
            name="Рыночные данные и режим",
            done=with_history >= 3,
            detail=f"история есть у {with_history}",
        ),
        StageOut(
            key="ideas",
            name="Отбор сделок и риск",
            done=ideas > 0,
            detail=f"идей в журнале {ideas}",
        ),
        StageOut(
            key="fundamentals",
            name="Фундаментальный срез",
            done=with_history >= 3,
            detail="доходности, дюрации, дивиденды" if with_history >= 3 else "",
        ),
        StageOut(
            key="optimisation",
            name="Оптимизация состава",
            # Шаг считается сделанным, если составы посчитаны, — даже если
            # ни один не прошёл проверку. Иначе «посчитали и забраковали»
            # выглядит как «не считали», и владелец ждёт того, чего не будет.
            done=bool(models) or bool(run and run.built),
            detail=(
                f"составов {len(models)}"
                if models
                else (f"посчитано {run.built}" if run and run.built else "")
            ),
        ),
        StageOut(
            key="walkforward",
            name="Проверка на истории",
            done=bool(validated),
            detail=(
                f"прошли проверку {len(validated)}"
                if validated
                else (f"не прошёл ни один из {run.built}" if run and run.built else "")
            ),
        ),
    ]


def _package_out(session: Session, model: PortfolioModel) -> PackageOut:
    mix: dict[str, float] = {}
    positions: list[PositionOut] = []
    for weight in sorted(model.weights, key=lambda w: -w.target_weight):
        symbol, title = _symbol(session, weight.instrument_id)
        asset_class = str(weight.asset_class)
        mix[asset_class] = mix.get(asset_class, 0.0) + float(weight.target_weight)
        positions.append(
            PositionOut(
                instrument_id=weight.instrument_id,
                symbol=symbol,
                title=title or symbol,
                asset_class=asset_class,
                target_weight=weight.target_weight,
                role=weight.role,
                thesis=weight.thesis,
                kill_conditions=weight.kill_conditions,
                score=weight.score,
                expected_return=weight.expected_return,
            )
        )
    return PackageOut(
        id=str(model.id),
        profile=str(model.profile),
        package=str(model.package),
        horizon_years=model.horizon_years,
        expected_return_low=model.expected_return_low,
        expected_return_high=model.expected_return_high,
        target_volatility=model.target_volatility,
        drawdown_limit=model.model_drawdown_limit,
        cvar_95=model.cvar_95,
        rationale=model.rationale,
        meets_target=model.meets_target,
        warnings=[str(w) for w in (model.warnings_json or [])],
        stress={str(k): str(v) for k, v in (model.stress_json or {}).items()},
        generated_at=model.generated_at,
        valid_until=model.valid_until,
        mix=[
            ClassSliceOut(
                asset_class=key,
                label=CLASS_LABELS.get(key, key),
                weight=round(value, 6),
            )
            for key, value in sorted(mix.items(), key=lambda kv: -kv[1])
        ],
        positions=positions,
    )


@router.get("/packages", response_model=PortfolioResponse)
def packages(
    session: Session = Depends(get_db),
    profile: RiskProfile | None = Query(default=None),
    horizon_years: int | None = Query(default=None, ge=1, le=30),
) -> PortfolioResponse:
    """Пакеты капитала вместе с состоянием конвейера."""
    query = select(PortfolioModel)
    if profile is not None:
        query = query.where(PortfolioModel.profile == profile)
    if horizon_years is not None:
        query = query.where(PortfolioModel.horizon_years == horizon_years)
    models = list(session.execute(query).scalars())

    order = {PackageSize.SIMPLE: 0, PackageSize.BALANCED: 1, PackageSize.MAX_POTENTIAL: 2}
    models.sort(key=lambda m: (m.horizon_years, order.get(m.package, 9)))

    everything = list(session.execute(select(PortfolioModel)).scalars())
    run = _last_run(session)
    stages = _stages(session, everything, run)
    generated = max((m.generated_at for m in everything), default=None)
    reason = ""
    if not models:
        # Причина берётся с прогона, а не выводится из «первого невыполненного
        # шага»: шаг говорит, где остановились, а прогон — почему.
        if run is not None and run.note:
            reason = run.note
        else:
            pending = next((s for s in stages if not s.done), None)
            reason = (
                f"состав не посчитан: работа стоит на шаге «{pending.name}»"
                if pending
                else "состав не посчитан: ни один вариант не прошёл проверку"
            )

    return PortfolioResponse(
        status=PortfolioStatusOut(
            stages=stages,
            universe_mix=_universe_mix(session),
            packages_ready=len(everything),
            universe=len(investment_universe(session)),
            generated_at=generated,
            reason=reason,
        ),
        packages=[_package_out(session, m) for m in models],
    )


@router.post("/rebuild", response_model=PortfolioResponse)
def rebuild(
    session: Session = Depends(get_db),
    draws: int = Query(default=120, ge=20, le=400),
) -> PortfolioResponse:
    """Пересобрать пакеты немедленно.

    Ограничение по частоте — не вежливость к серверу, а защита данных:
    параллельные прогоны переписывали бы одни и те же модели.
    """
    now = datetime.now(UTC)
    previous = _last_rebuild.get("at")
    if previous is not None and now - previous < REBUILD_COOLDOWN:
        return packages(session=session)
    _last_rebuild["at"] = now
    build_all(session, draws=draws)
    session.commit()
    return packages(session=session)


__all__ = ["router", "INVESTMENT_CLASSES"]
