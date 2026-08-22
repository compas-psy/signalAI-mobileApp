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

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import get_config
from ...db import get_db
from ...market.investments import INVESTMENT_CLASSES, investment_universe
from ...portfolio import holdings as holdings_store
from ...portfolio.lifecycle import current_models
from ...portfolio.rebalance import (
    EconomicsStatus,
    economics_policy_from_config,
    latest_holdings,
    plan,
)
from ...schemas.common import ApiModel, Money
from pydantic import Field
from uuid import UUID
from decimal import Decimal
from ...models import (
    Account,
    Bar,
    DataQualityEvent,
    Instrument,
    PortfolioModel,
    PortfolioRun,
    TradeIdea,
)
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


def _universe_notes(session: Session, limit: int = 3) -> list[str]:
    """Свежие жалобы на доски биржи — по одной на доску, самые новые."""
    rows = session.execute(
        select(DataQualityEvent.detail)
        .where(DataQualityEvent.flag == "BOARD_EMPTY")
        .order_by(DataQualityEvent.occurred_at.desc())
        .limit(30)
    ).scalars()
    seen: dict[str, str] = {}
    for detail in rows:
        board = str(detail).split(":", 1)[0]
        seen.setdefault(board, str(detail))
    return list(seen.values())[:limit]


def _jobs(session: Session, limit: int = 5) -> list[str]:
    """Итоги последних задач планировщика — по одной на задачу, свежие."""
    rows = session.execute(
        select(DataQualityEvent.detail, DataQualityEvent.occurred_at)
        .where(DataQualityEvent.source == "scheduler")
        .order_by(DataQualityEvent.occurred_at.desc())
        .limit(40)
    ).all()
    seen: dict[str, str] = {}
    for detail, at in rows:
        name = str(detail).split(":", 1)[0]
        seen.setdefault(name, f"{at:%H:%M} {detail}")
    return list(seen.values())[:limit]


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
    """Только последние неистёкшие поколения пакетов и состояние конвейера."""
    everything = current_models(session)
    models = [
        model
        for model in everything
        if (profile is None or model.profile == profile)
        and (horizon_years is None or model.horizon_years == horizon_years)
    ]

    order = {PackageSize.SIMPLE: 0, PackageSize.BALANCED: 1, PackageSize.MAX_POTENTIAL: 2}
    models.sort(key=lambda m: (m.horizon_years, order.get(m.package, 9)))

    run = _last_run(session)
    stages = _stages(session, everything, run)
    generated = max((m.generated_at for m in everything), default=None)
    reason = ""
    if not models:
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
            universe_notes=_universe_notes(session),
            jobs=_jobs(session),
            packages_ready=len(everything),
            universe=len(investment_universe(session)),
            generated_at=generated,
            reason=reason,
        ),
        packages=[_package_out(session, m) for m in models],
    )


class RebalanceActionOut(ApiModel):
    instrument_id: str
    symbol: str = ""
    side: str
    target_weight: Money
    actual_weight: Money
    amount_rub: Money
    reason: str
    economics_status: EconomicsStatus
    actionable: bool
    order_quantity: Money | None = None
    order_notional_rub: Money | None = None
    estimated_costs_rub: Money | None = None
    estimated_tax_rub: Money | None = None
    broker_final_costs_rub: Money | None = None
    broker_final_tax_rub: Money | None = None
    economics_provenance: dict[str, str] = Field(default_factory=dict)
    economics_blockers: list[str] = Field(default_factory=list)


class RebalanceOut(ApiModel):
    """Черновик ребаланса — предложение, а не заявка.

    Приложение по нему ничего не отправляет: за инвестиционным счётом стоит
    основной капитал владельца, токен на него выдан только на чтение, и
    сделки владелец совершает сам. Поле ``executable`` отсутствует
    намеренно — его нечему было бы включать.
    """

    model_id: str = ""
    needed: bool = False
    urgent: bool = False
    reason: str = ""
    max_drift: Money = Decimal(0)
    total_value: Money = Decimal(0)
    actionable: bool = False
    economics_status: EconomicsStatus = EconomicsStatus.UNKNOWN
    estimated_costs_rub: Money | None = None
    estimated_tax_rub: Money | None = None
    broker_final_costs_rub: Money | None = None
    broker_final_tax_rub: Money | None = None
    actions: list[RebalanceActionOut] = Field(default_factory=list)


@router.get("/rebalance", response_model=RebalanceOut)
def rebalance(
    session: Session = Depends(get_db),
    account_id: UUID | None = Query(default=None),
    model_id: UUID | None = Query(default=None),
) -> RebalanceOut:
    """Что стоило бы поправить в фактическом составе."""
    account = session.execute(
        select(Account).where(Account.circuit == "investment")
        if account_id is None
        else select(Account).where(Account.id == account_id)
    ).scalars().first()
    if account is None:
        return RebalanceOut(reason="инвестиционный счёт не подключён")

    holdings = latest_holdings(session, account.id)
    if model_id is not None:
        model = session.execute(
            select(PortfolioModel).where(PortfolioModel.id == model_id)
        ).scalar_one_or_none()
        if model is None:
            return RebalanceOut(
                reason="пакет не найден — обновите список и выберите пакет снова"
            )
    else:
        models = sorted(current_models(session), key=lambda m: m.generated_at, reverse=True)
        if not models:
            return RebalanceOut(
                reason="пакет ещё не посчитан — сравнивать не с чем"
            )
        if len(models) > 1:
            return RebalanceOut(
                reason=(
                    "выберите пакет: для ребаланса нужен model_id "
                    "выбранного состава"
                )
            )
        model = models[0]

    lot_sizes = {
        instrument_id: lot_size
        for instrument_id, lot_size in session.execute(
            select(Instrument.instrument_id, Instrument.lot_size).where(
                Instrument.instrument_id.in_(
                    [action.instrument_id for action in model.weights]
                )
            )
        ).all()
    }
    draft = plan(
        model,
        holdings,
        economics_policy=economics_policy_from_config(get_config()),
        lot_sizes=lot_sizes,
    )
    return RebalanceOut(
        model_id=str(model.id),
        needed=draft.needed,
        urgent=draft.urgent,
        reason=draft.reason,
        max_drift=draft.max_drift,
        total_value=draft.total_value,
        actionable=draft.actionable,
        economics_status=draft.economics_status,
        estimated_costs_rub=draft.estimated_costs_rub,
        estimated_tax_rub=draft.estimated_tax_rub,
        broker_final_costs_rub=draft.broker_final_costs_rub,
        broker_final_tax_rub=draft.broker_final_tax_rub,
        actions=[
            RebalanceActionOut(
                instrument_id=a.instrument_id,
                symbol=_symbol(session, a.instrument_id)[0],
                side=a.side,
                target_weight=a.target_weight,
                actual_weight=a.actual_weight,
                amount_rub=a.amount_rub,
                reason=a.reason,
                economics_status=a.economics.status,
                actionable=a.economics.actionable,
                order_quantity=a.economics.order_quantity,
                order_notional_rub=a.economics.order_notional_rub,
                estimated_costs_rub=a.economics.estimated_costs_rub,
                estimated_tax_rub=a.economics.estimated_tax_rub,
                broker_final_costs_rub=a.economics.broker_final_costs_rub,
                broker_final_tax_rub=a.economics.broker_final_tax_rub,
                economics_provenance=a.economics.provenance,
                economics_blockers=list(a.economics.blockers),
            )
            for a in draft.actions
        ],
    )


class HoldingIn(ApiModel):
    """Одна позиция, как её прочитало устройство."""

    symbol: str
    quantity: Decimal
    market_value: Money
    average_price: Money | None = None
    market_price: Money | None = None
    instrument_id: str = ""


class HoldingsIn(ApiModel):
    """Снимок инвестиционного счёта целиком.

    Приезжает с устройства, а не читается сервером: токен Т-Инвестиций на
    чтение лежит в защищённом хранилище телефона и на VPS не передаётся.
    Токен Invest API привязан к пользователю, а не к счёту, и видит все счета
    владельца — хранить такой на сервере значило бы сложить весь капитал в
    одну точку отказа ради удобства синхронизации.
    """

    broker: str = "tinvest"
    account_id: str
    title: str = ""
    equity: Money | None = None
    as_of: date | None = None
    positions: list[HoldingIn] = Field(default_factory=list)


class HoldingsOut(ApiModel):
    """Что записалось и чего сервер не узнал."""

    account_id: str = ""
    as_of: date | None = None
    stored: int = 0
    total_value: Money = Decimal(0)
    unknown: list[str] = Field(default_factory=list)
    note: str = ""


@router.post("/holdings", response_model=HoldingsOut)
def put_holdings(
    payload: HoldingsIn,
    session: Session = Depends(get_db),
) -> HoldingsOut:
    """Принять снимок фактических позиций."""
    account = holdings_store.account_for(
        session,
        broker=payload.broker,
        external_id=payload.account_id,
        title=payload.title,
        equity=payload.equity,
    )
    snapshot = holdings_store.store(
        session,
        account,
        [
            holdings_store.Position(
                symbol=p.symbol,
                quantity=p.quantity,
                market_value=p.market_value,
                average_price=p.average_price,
                market_price=p.market_price,
                instrument_id=p.instrument_id,
            )
            for p in payload.positions
        ],
        as_of=payload.as_of,
    )
    session.commit()
    return HoldingsOut(
        account_id=str(snapshot.account_id),
        as_of=snapshot.as_of,
        stored=snapshot.stored,
        total_value=snapshot.total_value,
        unknown=snapshot.unknown,
        note=snapshot.note,
    )


@router.post("/rebuild", response_model=PortfolioResponse)
def rebuild(
    session: Session = Depends(get_db),
    draws: int = Query(default=120, ge=20, le=400),
) -> PortfolioResponse:
    """Пересобрать пакеты немедленно, добавив новое поколение моделей."""
    now = datetime.now(UTC)
    previous = _last_rebuild.get("at")
    if previous is not None and now - previous < REBUILD_COOLDOWN:
        return packages(session=session)
    _last_rebuild["at"] = now
    build_all(session, draws=draws)
    session.commit()
    return packages(session=session)


__all__ = ["router", "INVESTMENT_CLASSES"]
