"""Риск-бюджет и размер позиции (engine-ТЗ §17, §17.1; UX-ТЗ §20.1).

Здесь считаются деньги, поэтому здесь нет ни одного ``float``. Разница цен
делится на шаг цены, умножается на стоимость шага и округляется вниз к лоту —
каждое из этих действий на ``float`` даёт хвост, а хвост в делении
превращается в лишний контракт.

Бюджет — **минимум** из всех ограничений (§20.1). Не «риск по баллу, если
лимиты позволяют», а именно минимум: связывающее ограничение всегда одно, и
оно обязано быть названо, иначе урезанный объём выглядит как ошибка.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Пороги §17 и §27."""

    base_risk_per_trade: Decimal = Decimal("0.005")
    max_risk_per_trade: Decimal = Decimal("0.0075")
    max_total_open_risk: Decimal = Decimal("0.02")
    max_cluster_risk: Decimal = Decimal("0.01")
    daily_loss_limit: Decimal = Decimal("0.015")
    weekly_loss_limit: Decimal = Decimal("0.035")
    monthly_loss_limit: Decimal = Decimal("0.06")
    min_liquidation_distance_ratio: Decimal = Decimal("2.5")
    max_leverage: Decimal = Decimal("3.0")


DRAWDOWN_LADDER: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0.04"), Decimal("1.00")),
    (Decimal("0.07"), Decimal("0.75")),
    (Decimal("0.10"), Decimal("0.50")),
    (Decimal("1.00"), Decimal("0.00")),
)


@dataclass(frozen=True, slots=True)
class RiskState:
    """Что уже израсходовано. Убытки приходят отрицательными числами."""

    risk_equity: Decimal
    day_pnl_pct: Decimal = Decimal(0)
    week_pnl_pct: Decimal = Decimal(0)
    month_pnl_pct: Decimal = Decimal(0)
    open_risk_pct: Decimal = Decimal(0)
    cluster_risk_pct: Decimal = Decimal(0)
    current_drawdown: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    """Спецификация, без которой размер не посчитать."""

    tick_size: Decimal
    tick_value: Decimal
    quantity_step: Decimal = Decimal(1)
    min_quantity: Decimal = Decimal(1)
    min_notional: Decimal | None = None
    contract_multiplier: Decimal = Decimal(1)
    is_linear: bool = False  # крипта: объём считается прямо из разницы цен


@dataclass(frozen=True, slots=True)
class BudgetLine:
    name: str
    label: str
    value: Decimal
    detail: str


@dataclass(frozen=True, slots=True)
class RiskBudget:
    """Бюджет риска и то, чем он ограничен."""

    amount: Decimal
    percent: Decimal
    binding: str
    binding_label: str
    drawdown_multiplier: Decimal
    lines: tuple[BudgetLine, ...]
    halted: bool = False

    @property
    def blocked(self) -> bool:
        return self.amount <= 0


@dataclass(frozen=True, slots=True)
class Sizing:
    """Итог расчёта объёма."""

    quantity: Decimal
    risk_per_unit: Decimal
    risk_amount: Decimal
    notional: Decimal
    tradable: bool
    reason: str = ""
    warnings: tuple[str, ...] = ()


def drawdown_multiplier(drawdown: Decimal) -> Decimal:
    """Множитель по глубине просадки (§17).

    Просадка передаётся положительным числом. Последняя ступень возвращает
    ноль — это остановка, а не «очень маленький размер».
    """
    depth = abs(drawdown)
    for bound, multiplier in DRAWDOWN_LADDER:
        if depth <= bound:
            return multiplier
    return Decimal(0)


def score_risk_percent(
    score: Decimal, limits: RiskLimits, *, a_grade_score: Decimal = Decimal(80)
) -> tuple[Decimal, str]:
    """Риск на сделку по баллу (§17).

    Engine-ТЗ знает две ступени: базовые 0,5% и 0,75% для A-grade. Ступени
    1% из UX-ТЗ здесь нет — см. docs/TZ_PRIORITY.md.
    """
    if score >= a_grade_score:
        return limits.max_risk_per_trade, f"балл {score} — A-grade"
    return limits.base_risk_per_trade, f"балл {score} — базовая ступень"


def compute_budget(
    *,
    score: Decimal,
    state: RiskState,
    limits: RiskLimits | None = None,
    strategy_multiplier: Decimal = Decimal(1),
) -> RiskBudget:
    """Бюджет риска — минимум из всех ограничений (§20.1).

    ``strategy_multiplier`` — множитель стратегии (§12.4 даёт 0,75 для
    разворота Вайкоффа): редкий контртрендовый сетап рискует меньше.
    """
    lim = limits or RiskLimits()
    multiplier = drawdown_multiplier(state.current_drawdown)

    by_score, score_detail = score_risk_percent(score, lim)
    lines: list[BudgetLine] = [
        BudgetLine(
            "score", "Риск по баллу",
            by_score * multiplier * strategy_multiplier,
            f"{score_detail}; множитель просадки {multiplier}"
            + (f", множитель стратегии {strategy_multiplier}"
               if strategy_multiplier != 1 else ""),
        ),
        BudgetLine(
            "daily", "Остаток дневного лимита",
            max(Decimal(0), lim.daily_loss_limit + min(Decimal(0), state.day_pnl_pct)),
            f"израсходовано {-min(Decimal(0), state.day_pnl_pct)} из {lim.daily_loss_limit}",
        ),
        BudgetLine(
            "weekly", "Остаток недельного лимита",
            max(Decimal(0), lim.weekly_loss_limit + min(Decimal(0), state.week_pnl_pct)),
            f"израсходовано {-min(Decimal(0), state.week_pnl_pct)} из {lim.weekly_loss_limit}",
        ),
        BudgetLine(
            "monthly", "Остаток месячного лимита",
            max(Decimal(0), lim.monthly_loss_limit + min(Decimal(0), state.month_pnl_pct)),
            f"израсходовано {-min(Decimal(0), state.month_pnl_pct)} из {lim.monthly_loss_limit}",
        ),
        BudgetLine(
            "open", "Остаток открытого риска",
            max(Decimal(0), lim.max_total_open_risk - state.open_risk_pct),
            f"открыто {state.open_risk_pct} из {lim.max_total_open_risk}",
        ),
        BudgetLine(
            "cluster", "Остаток риска кластера",
            max(Decimal(0), lim.max_cluster_risk - state.cluster_risk_pct),
            f"в кластере {state.cluster_risk_pct} из {lim.max_cluster_risk}",
        ),
    ]

    binding = min(lines, key=lambda line: line.value)
    percent = binding.value
    halted = multiplier == 0

    return RiskBudget(
        amount=(state.risk_equity * percent).quantize(
            Decimal("0.00000001"), rounding=ROUND_DOWN
        ),
        percent=percent,
        binding=binding.name,
        binding_label=binding.label,
        drawdown_multiplier=multiplier,
        lines=tuple(lines),
        halted=halted,
    )


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise ValueError("шаг объёма должен быть положительным")
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def risk_per_unit(
    *, entry: Decimal, stop: Decimal, spec: InstrumentSpec,
    slippage: Decimal = Decimal(0), fees: Decimal = Decimal(0),
) -> Decimal:
    """Риск на единицу позиции (§17.1).

    Для фьючерса разница цен переводится в деньги через шаг цены и стоимость
    шага — «сколько рублей стоит один пункт» у контракта своё. Для линейного
    инструмента (крипта) разница цен и есть деньги на единицу.

    Проскальзывание и комиссии входят в риск на единицу: без них расчётный
    убыток по стопу систематически меньше фактического, и позиция выходит
    крупнее допустимой.
    """
    distance = abs(entry - stop)
    if distance <= 0:
        raise ValueError("стоп совпадает с входом: риск на единицу равен нулю")
    if spec.is_linear:
        base = distance * spec.contract_multiplier
    else:
        if spec.tick_size <= 0 or spec.tick_value <= 0:
            raise ValueError("для нелинейного инструмента нужны шаг цены и его стоимость")
        base = distance / spec.tick_size * spec.tick_value
    return base + abs(slippage) + abs(fees)


def size_position(
    *,
    budget: RiskBudget,
    entry: Decimal,
    stop: Decimal,
    spec: InstrumentSpec,
    slippage: Decimal = Decimal(0),
    fees: Decimal = Decimal(0),
    leverage: Decimal | None = None,
    liquidation_price: Decimal | None = None,
    limits: RiskLimits | None = None,
) -> Sizing:
    """Объём позиции (§17.1, §20.1).

    Округление всегда вниз: округление вверх превышает бюджет риска, который
    только что был вычислен как минимум из всех ограничений.

    Если после округления объём меньше минимального лота — идея остаётся
    информационной и не подтверждается (§20.1 дословно). Не «округляем до
    одного контракта»: один контракт может стоить втрое дороже допустимого
    риска.
    """
    lim = limits or RiskLimits()
    warnings: list[str] = []

    if budget.halted:
        return Sizing(
            Decimal(0), Decimal(0), Decimal(0), Decimal(0), False,
            "торговля остановлена: просадка превысила последнюю ступень §17",
        )
    if budget.blocked:
        return Sizing(
            Decimal(0), Decimal(0), Decimal(0), Decimal(0), False,
            f"бюджет риска исчерпан: {budget.binding_label.lower()}",
        )

    per_unit = risk_per_unit(
        entry=entry, stop=stop, spec=spec, slippage=slippage, fees=fees
    )
    raw = budget.amount / per_unit
    quantity = _floor_to_step(raw, spec.quantity_step)

    if quantity < spec.min_quantity:
        return Sizing(
            Decimal(0), per_unit, Decimal(0), Decimal(0), False,
            f"объём {raw.quantize(Decimal('0.00000001'))} меньше минимального "
            f"{spec.min_quantity}: идея информационная и не подтверждается (§20.1)",
        )

    notional = quantity * entry * spec.contract_multiplier
    if spec.min_notional is not None and notional < spec.min_notional:
        return Sizing(
            Decimal(0), per_unit, Decimal(0), Decimal(0), False,
            f"номинал {notional} ниже минимального {spec.min_notional}",
        )

    if leverage is not None and leverage > lim.max_leverage:
        return Sizing(
            Decimal(0), per_unit, Decimal(0), Decimal(0), False,
            f"плечо {leverage} выше допустимого {lim.max_leverage}",
        )

    if liquidation_price is not None:
        stop_distance = abs(entry - stop)
        liq_distance = abs(entry - liquidation_price)
        if liq_distance < stop_distance * lim.min_liquidation_distance_ratio:
            return Sizing(
                Decimal(0), per_unit, Decimal(0), Decimal(0), False,
                f"ликвидация в {liq_distance} от входа при стопе {stop_distance}: "
                f"нужен запас не меньше {lim.min_liquidation_distance_ratio}× "
                "(§13) — иначе биржа закроет позицию раньше стопа",
            )

    risk_amount = (quantity * per_unit).quantize(
        Decimal("0.00000001"), rounding=ROUND_HALF_UP
    )
    if risk_amount > budget.amount:
        warnings.append(
            f"фактический риск {risk_amount} превышает бюджет {budget.amount} "
            "после округления — проверьте шаг объёма"
        )

    return Sizing(
        quantity=quantity,
        risk_per_unit=per_unit,
        risk_amount=risk_amount,
        notional=notional,
        tradable=True,
        warnings=tuple(warnings),
    )


def option_contracts(
    *, budget: RiskBudget, max_loss_per_contract: Decimal
) -> Sizing:
    """Объём опционной конструкции (§17.1).

    Максимальный убыток обязан быть известен: §1.2 и §14 запрещают
    конструкции с неограниченным риском, и здесь это выражено отказом, а не
    предупреждением.
    """
    if max_loss_per_contract <= 0:
        raise ValueError(
            "максимальный убыток на контракт неизвестен или неположителен: "
            "конструкция без известного max loss отклоняется (§14)"
        )
    if budget.halted or budget.blocked:
        return Sizing(
            Decimal(0), max_loss_per_contract, Decimal(0), Decimal(0), False,
            "бюджет риска исчерпан или торговля остановлена",
        )
    quantity = _floor_to_step(budget.amount / max_loss_per_contract, Decimal(1))
    if quantity < 1:
        return Sizing(
            Decimal(0), max_loss_per_contract, Decimal(0), Decimal(0), False,
            "бюджета не хватает даже на один контракт: идея информационная",
        )
    return Sizing(
        quantity=quantity,
        risk_per_unit=max_loss_per_contract,
        risk_amount=quantity * max_loss_per_contract,
        notional=quantity * max_loss_per_contract,
        tradable=True,
    )
