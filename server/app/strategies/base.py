"""Общий контракт стратегий (engine-ТЗ §10–§12).

Каждая стратегия — набор **проверок**, а не набор условий в `if`. Различие
практическое: проверка помнит, как называется, прошла ли она и почему. Из
этого получается отбраковка с причиной для каждого шага — то, без чего
владелец не может понять, почему сегодня нет сделок, и не может отличить
«рынок не дал» от «движок сломался».

Кандидат несёт список **использованных** наблюдений. Разметка графика
собирается только из него, поэтому нарисовать слой, не участвовавший в
решении, невозможно физически (UX-ТЗ §9.1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from ..detectors.base import Reading
from ..detectors.price_action import PriceZone
from ..market.candles import Candle
from ..models.enums import Direction, OrderIntent, Strategy
from ..regime.classifier import RegimeResult


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    label: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class SetupContext:
    """Всё, что нужно стратегии, и ничего сверх того.

    Три ряда свечей соответствуют ролям §7: контекст, сетап, триггер. Их
    нельзя перепутать местами — роль задаётся именем поля, а не порядком
    аргументов.
    """

    instrument_id: str
    context_bars: Sequence[Candle]
    setup_bars: Sequence[Candle]
    trigger_bars: Sequence[Candle]
    context_tf: str
    setup_tf: str
    trigger_tf: str
    regime: RegimeResult
    horizon_days: int
    tick_size: Decimal
    smc: Reading | None = None
    wyckoff: Reading | None = None
    price_action: Reading | None = None
    volume_reading: Reading | None = None
    oi_reading: Reading | None = None
    atr_setup: Decimal | None = None
    atr_trigger: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Target:
    price: Decimal
    fraction: Decimal
    rationale: str


@dataclass(frozen=True, slots=True)
class Candidate:
    """Готовый план до расчёта размера позиции."""

    strategy: Strategy
    direction: Direction
    order_intent: OrderIntent
    entry_low: Decimal
    entry_high: Decimal
    entry_reference: Decimal
    stop: Decimal
    targets: tuple[Target, ...]
    checks: tuple[Check, ...]
    used: tuple[Reading, ...]
    zones: tuple[PriceZone, ...]
    invalidation: str
    thesis: str
    risk_multiplier: Decimal = Decimal(1)
    counter_factors: tuple[str, ...] = ()

    @property
    def risk_per_unit(self) -> Decimal:
        return abs(self.entry_reference - self.stop)

    def rr(self, index: int) -> Decimal:
        """Отношение до цели номер ``index`` (с нуля)."""
        risk = self.risk_per_unit
        if risk <= 0 or index >= len(self.targets):
            return Decimal(0)
        reward = abs(self.targets[index].price - self.entry_reference)
        return (reward / risk).quantize(Decimal("0.0001"))


@dataclass(frozen=True, slots=True)
class Rejection:
    """Отказ с причиной. Молчаливая потеря кандидата недопустима."""

    strategy: Strategy
    checks: tuple[Check, ...]
    reason: str

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.passed)


Outcome = Candidate | Rejection


def price_text(price: Decimal) -> str:
    """Цена для человеческого текста: без хвоста нулей.

    ``Decimal`` носит масштаб инструмента, и в f-строку он попадает целиком:
    «1849.100000000000». В тезисе идеи такое число нечитаемо — двенадцать
    нулей вместо цены. Формат нужен только для текста; в API цены уходят
    строкой как есть, чтобы не потерять ни знака.
    """
    normalized = price.normalize()
    # Экспонента появляется у больших нормализованных чисел (1E+3) — она
    # здесь недопустима: цену читает человек, а не парсер.
    if normalized == normalized.to_integral_value():
        return f"{normalized:.0f}"
    return format(normalized, "f")


def first_failure(checks: Sequence[Check]) -> Check | None:
    return next((c for c in checks if not c.passed), None)


def reject(strategy: Strategy, checks: Sequence[Check]) -> Rejection:
    failed = [c for c in checks if not c.passed]
    return Rejection(
        strategy=strategy,
        checks=tuple(checks),
        reason="; ".join(f"{c.label}: {c.detail}" for c in failed),
    )


def snap_entry(
    low: Decimal, high: Decimal, reference: Decimal, tick: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    """Границы зоны входа на сетке цен инструмента.

    Стоп и цели округлялись к шагу всегда, а зона входа — нет: она приходила
    как есть из ATR, то есть числом вроде 1874.023606. Биржа такую заявку не
    примет, и это первая причина. Вторая видна на экране: приложение
    подбирает число знаков по самим ценам, и один неокруглённый край зоны
    заставлял показывать ВСЕ цены идеи с шестью знаками — «1 858,120000»
    вместо «1 858,12».

    Края округляются наружу (зона не сужается), опорная цена — внутрь, и она
    остаётся между краями: из неё считается риск на единицу, и вылет за
    границу зоны сделал бы R/R неверным.
    """
    lo = round_to_tick(min(low, high), tick, up=False)
    hi = round_to_tick(max(low, high), tick, up=True)
    ref = round_to_tick(reference, tick, up=False)
    return lo, hi, min(max(ref, lo), hi)


def round_to_tick(price: Decimal, tick: Decimal, *, up: bool) -> Decimal:
    """Округлить цену к шагу инструмента.

    Направление задаётся вызывающим: стоп округляется дальше от входа, цель —
    ближе. Округление «как получится» сдвигает и риск, и вознаграждение.
    """
    if tick <= 0:
        return price
    steps = price / tick
    whole = steps.to_integral_value(rounding="ROUND_CEILING" if up else "ROUND_FLOOR")
    return whole * tick
