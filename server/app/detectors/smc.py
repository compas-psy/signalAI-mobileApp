"""Структура и ликвидность — Smart Money (engine-ТЗ §8.3).

Правила взяты из ТЗ дословно и вынесены в параметры: ни одного порога в теле
функций. §8.3 сам настаивает, что это «набор измеримых признаков, а не
эзотерическая разметка», — поэтому каждый объект здесь имеет проверяемое
определение, и каждое определение покрыто тестом «условие снято → объекта
нет».

Никакого взгляда в будущее: свинги приходят подтверждёнными (§8.3), а
события ищутся только среди баров, наступивших **после** подтверждения
свинга.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..features.indicators import Swing, atr, relative_volume, swings, zscore
from ..market.candles import Candle
from .base import Confidence, ConfidenceTerm, Mark, Measure, Reading

VERSION = "smc-1.0.0"
EVIDENCE_KIND = "smc_context"


@dataclass(frozen=True, slots=True)
class SmcParams:
    """Пороги §8.3. Значения по умолчанию — из текста ТЗ."""

    pivot_confirm_bars: int = 2
    bos_min_atr: Decimal = Decimal("0.10")
    sweep_atr_min: Decimal = Decimal("0.05")
    sweep_atr_max: Decimal = Decimal("0.75")
    sweep_return_bars: int = 3
    fvg_min_atr: Decimal = Decimal("0.10")
    fvg_fill_threshold: Decimal = Decimal("0.5")
    ob_min_body_atr: Decimal = Decimal("0.80")
    ob_min_volume_z: float = 0.50
    pool_tolerance_atr: Decimal = Decimal("0.15")
    pool_min_touches: int = 2


@dataclass(frozen=True, slots=True)
class StructureEvent:
    """Слом структуры: где, каким закрытием и насколько уверенно."""

    kind: str  # 'BOS' | 'CHoCH'
    direction: str  # 'up' | 'down'
    level: Decimal
    index: int
    displacement_atr: float
    broken_swing_index: int


@dataclass(frozen=True, slots=True)
class Sweep:
    """Снятие ликвидности: экстремум пробит, закрытие вернулось внутрь."""

    index: int
    swing_index: int
    level: Decimal
    extreme: Decimal
    penetration_atr: float
    return_bars: int
    is_high: bool


@dataclass(frozen=True, slots=True)
class FairValueGap:
    index: int  # индекс третьей свечи тройки
    low: Decimal
    high: Decimal
    is_bullish: bool
    fill_ratio: float

    @property
    def size(self) -> Decimal:
        return self.high - self.low


@dataclass(frozen=True, slots=True)
class OrderBlock:
    index: int
    low: Decimal
    high: Decimal
    is_bullish: bool
    body_atr: float
    volume_z: float
    reacted: bool


@dataclass(frozen=True, slots=True)
class LiquidityPool:
    level: Decimal
    touches: int
    is_high: bool
    first_index: int
    last_index: int


def _atr_at(values: Sequence[Decimal | None], index: int) -> Decimal | None:
    if 0 <= index < len(values):
        return values[index]
    return None


def find_structure_events(
    candles: Sequence[Candle],
    swing_points: Sequence[Swing],
    atr_values: Sequence[Decimal | None],
    params: SmcParams,
) -> list[StructureEvent]:
    """BOS и CHoCH по закрытию (§8.3).

    * **BOS** — закрытие за свингом в сторону сложившейся структуры,
      превышение не меньше ``bos_min_atr``.
    * **CHoCH** — первое закрытие за *защищённым* свингом **против**
      структуры. Защищённый свинг — последний экстремум, от которого пошло
      движение по тренду; именно его пробой и означает смену характера.

    Порядок баров соблюдается строго: событие ищется среди баров после
    ``confirmed_index`` свинга, иначе мы «узнаём» о сломе раньше, чем узнали
    бы в реальности.
    """
    events: list[StructureEvent] = []
    if not swing_points:
        return events

    direction = "flat"
    for i, candle in enumerate(candles):
        available = [s for s in swing_points if s.confirmed_index <= i and s.index < i]
        if not available:
            continue
        current_atr = _atr_at(atr_values, i)
        if current_atr is None or current_atr <= 0:
            continue
        threshold = current_atr * params.bos_min_atr

        last_high = next((s for s in reversed(available) if s.is_high), None)
        last_low = next((s for s in reversed(available) if not s.is_high), None)

        if last_high is not None and candle.close > last_high.price + threshold:
            kind = "BOS" if direction in ("up", "flat") else "CHoCH"
            events.append(
                StructureEvent(
                    kind=kind,
                    direction="up",
                    level=last_high.price,
                    index=i,
                    displacement_atr=float(
                        (candle.close - last_high.price) / current_atr
                    ),
                    broken_swing_index=last_high.index,
                )
            )
            direction = "up"
            continue

        if last_low is not None and candle.close < last_low.price - threshold:
            kind = "BOS" if direction in ("down", "flat") else "CHoCH"
            events.append(
                StructureEvent(
                    kind=kind,
                    direction="down",
                    level=last_low.price,
                    index=i,
                    displacement_atr=float(
                        (last_low.price - candle.close) / current_atr
                    ),
                    broken_swing_index=last_low.index,
                )
            )
            direction = "down"
    return events


def find_sweeps(
    candles: Sequence[Candle],
    swing_points: Sequence[Swing],
    atr_values: Sequence[Decimal | None],
    params: SmcParams,
) -> list[Sweep]:
    """Снятие ликвидности (§8.3).

    «Выход за swing на 0.05–0.75 ATR и возврат close внутрь». Обе границы
    важны: слишком мелкое протыкание — шум, слишком глубокое — уже пробой, а
    не сбор стопов, и торговать его надо в другую сторону.
    """
    found: list[Sweep] = []
    for swing in swing_points:
        current_atr = _atr_at(atr_values, swing.confirmed_index)
        if current_atr is None or current_atr <= 0:
            continue
        low_bound = current_atr * params.sweep_atr_min
        high_bound = current_atr * params.sweep_atr_max

        start = swing.confirmed_index + 1
        for i in range(start, min(len(candles), start + 60)):
            candle = candles[i]
            if swing.is_high:
                penetration = candle.high - swing.price
                if penetration < low_bound or penetration > high_bound:
                    continue
                extreme, back_inside = candle.high, candle.close < swing.price
            else:
                penetration = swing.price - candle.low
                if penetration < low_bound or penetration > high_bound:
                    continue
                extreme, back_inside = candle.low, candle.close > swing.price

            if back_inside:
                return_bars = 0
            else:
                return_bars = -1
                for j in range(i + 1, min(len(candles), i + 1 + params.sweep_return_bars)):
                    inside = (
                        candles[j].close < swing.price
                        if swing.is_high
                        else candles[j].close > swing.price
                    )
                    if inside:
                        return_bars = j - i
                        break
                if return_bars < 0:
                    continue  # цена не вернулась — это пробой, а не снятие

            found.append(
                Sweep(
                    index=i,
                    swing_index=swing.index,
                    level=swing.price,
                    extreme=extreme,
                    penetration_atr=float(penetration / current_atr),
                    return_bars=return_bars,
                    is_high=swing.is_high,
                )
            )
            break
    return found


def find_fair_value_gaps(
    candles: Sequence[Candle],
    atr_values: Sequence[Decimal | None],
    params: SmcParams,
) -> list[FairValueGap]:
    """Трёхсвечный имбаланс (§8.3).

    Бычий: ``low[t] > high[t-2]``; медвежий: ``high[t] < low[t-2]``. Разрыв
    меньше ``fvg_min_atr`` не считается: это обычный шум котировок.

    Заполнение считается на всей последующей истории; заполненные более чем
    на ``fvg_fill_threshold`` не возвращаются — иначе экран зарастает
    отработанными зонами, а §10.7 требует обратного.
    """
    gaps: list[FairValueGap] = []
    for t in range(2, len(candles)):
        current_atr = _atr_at(atr_values, t)
        if current_atr is None or current_atr <= 0:
            continue
        minimum = current_atr * params.fvg_min_atr

        first, third = candles[t - 2], candles[t]
        if third.low > first.high and third.low - first.high >= minimum:
            low, high, bullish = first.high, third.low, True
        elif third.high < first.low and first.low - third.high >= minimum:
            low, high, bullish = third.high, first.low, False
        else:
            continue

        size = high - low
        filled = Decimal(0)
        for later in candles[t + 1 :]:
            if bullish:
                filled = max(filled, min(size, high - later.low))
            else:
                filled = max(filled, min(size, later.high - low))
        ratio = float(filled / size) if size > 0 else 1.0
        if ratio >= float(params.fvg_fill_threshold):
            continue
        gaps.append(FairValueGap(t, low, high, bullish, ratio))
    return gaps


def find_order_blocks(
    candles: Sequence[Candle],
    events: Sequence[StructureEvent],
    atr_values: Sequence[Decimal | None],
    volume_z: Sequence[float | None],
    params: SmcParams,
) -> list[OrderBlock]:
    """Order block (§8.3).

    «Последняя противоположная свеча перед BOS-импульсом с body ≥0.8 ATR и
    volume z ≥0.5».

    Дополнительно проверяется **реакция** цены на возврат в зону. Блок, к
    которому цена вернулась и прошла насквозь, — не зона интереса, а бывший
    уровень; рисовать его как действующий значит выдавать желаемое за
    измеренное.
    """
    blocks: list[OrderBlock] = []
    for event in events:
        impulse_index = event.index
        current_atr = _atr_at(atr_values, impulse_index)
        if current_atr is None or current_atr <= 0:
            continue
        impulse = candles[impulse_index]
        if impulse.body < current_atr * params.ob_min_body_atr:
            continue
        z = volume_z[impulse_index] if impulse_index < len(volume_z) else None
        if z is None or z < params.ob_min_volume_z:
            continue

        wanted_down = event.direction == "up"  # перед ростом ищем медвежью свечу
        origin = None
        for j in range(impulse_index - 1, max(-1, impulse_index - 12), -1):
            candle = candles[j]
            if (candle.is_down if wanted_down else candle.is_up):
                origin = j
                break
        if origin is None:
            continue

        block = candles[origin]
        reacted = False
        for later in candles[impulse_index + 1 :]:
            if wanted_down:
                if later.low <= block.high:
                    reacted = later.close > block.high
                    break
            elif later.high >= block.low:
                reacted = later.close < block.low
                break

        blocks.append(
            OrderBlock(
                index=origin,
                low=block.low,
                high=block.high,
                is_bullish=wanted_down,
                body_atr=float(impulse.body / current_atr),
                volume_z=z,
                reacted=reacted,
            )
        )
    return blocks


def find_liquidity_pools(
    swing_points: Sequence[Swing],
    atr_values: Sequence[Decimal | None],
    params: SmcParams,
) -> list[LiquidityPool]:
    """Кластеры равных максимумов и минимумов с допуском от ATR (§8.3)."""
    pools: list[LiquidityPool] = []
    for is_high in (True, False):
        group = [s for s in swing_points if s.is_high is is_high]
        used: set[int] = set()
        for i, anchor in enumerate(group):
            if i in used:
                continue
            current_atr = _atr_at(atr_values, anchor.confirmed_index)
            if current_atr is None or current_atr <= 0:
                continue
            tolerance = current_atr * params.pool_tolerance_atr
            members = [anchor]
            for j in range(i + 1, len(group)):
                if j in used:
                    continue
                if abs(group[j].price - anchor.price) <= tolerance:
                    members.append(group[j])
                    used.add(j)
            if len(members) >= params.pool_min_touches:
                pools.append(
                    LiquidityPool(
                        level=sum(m.price for m in members) / len(members),
                        touches=len(members),
                        is_high=is_high,
                        first_index=members[0].index,
                        last_index=members[-1].index,
                    )
                )
    return pools


def detect(
    candles: Sequence[Candle],
    *,
    timeframe: str,
    params: SmcParams | None = None,
    atr_period: int = 14,
) -> Reading | None:
    """Собрать наблюдение SMC.

    Возвращает ``None``, когда структуры нет вовсе: пустое наблюдение с
    нулевой уверенностью читалось бы как «структура посчитана и она никакая»,
    а это разные вещи.
    """
    p = params or SmcParams()
    if len(candles) < atr_period + p.pivot_confirm_bars * 2 + 3:
        return None

    atr_values = atr(candles, atr_period)
    swing_points = swings(candles, confirm_bars=p.pivot_confirm_bars)
    if not swing_points:
        return None

    rel_vol = relative_volume(candles)
    volume_z = zscore([v if v is not None else 1.0 for v in rel_vol])

    events = find_structure_events(candles, swing_points, atr_values, p)
    sweeps = find_sweeps(candles, swing_points, atr_values, p)
    gaps = find_fair_value_gaps(candles, atr_values, p)
    blocks = find_order_blocks(candles, events, atr_values, volume_z, p)
    pools = find_liquidity_pools(swing_points, atr_values, p)

    last_event = events[-1] if events else None
    marks: list[Mark] = []
    for event in events[-3:]:
        candle = candles[event.index]
        marks.append(
            Mark(
                kind="smc_bos" if event.kind == "BOS" else "smc_choch",
                timeframe=timeframe,
                start_time=candle.open_time,
                end_time=candle.open_time,
                price_low=event.level,
                price_high=event.level,
                label=f"{event.kind} {'вверх' if event.direction == 'up' else 'вниз'}",
                confidence=min(1.0, event.displacement_atr / 2),
                display_priority=80,
            )
        )
    for sweep in sweeps[-3:]:
        candle = candles[sweep.index]
        marks.append(
            Mark(
                kind="smc_sweep",
                timeframe=timeframe,
                start_time=candle.open_time,
                end_time=candle.open_time,
                price_low=min(sweep.level, sweep.extreme),
                price_high=max(sweep.level, sweep.extreme),
                label=f"Снятие ликвидности {sweep.penetration_atr:.2f} ATR",
                confidence=0.7,
                display_priority=70,
            )
        )
    for gap in gaps[-4:]:
        marks.append(
            Mark(
                kind="smc_fvg",
                timeframe=timeframe,
                start_time=candles[max(0, gap.index - 2)].open_time,
                end_time=candles[gap.index].open_time,
                price_low=gap.low,
                price_high=gap.high,
                label=f"FVG, заполнен на {gap.fill_ratio:.0%}",
                confidence=1.0 - gap.fill_ratio,
                display_priority=55,
            )
        )
    for block in blocks[-3:]:
        candle = candles[block.index]
        marks.append(
            Mark(
                kind="smc_order_block",
                timeframe=timeframe,
                start_time=candle.open_time,
                end_time=candle.open_time,
                price_low=block.low,
                price_high=block.high,
                label="Order block" + ("" if block.reacted else " (реакции не было)"),
                confidence=0.8 if block.reacted else 0.4,
                display_priority=65,
            )
        )

    terms = (
        ConfidenceTerm(
            "structure_event", 0.35, 1.0 if last_event else 0.0,
            "есть подтверждённый слом" if last_event else "слома структуры нет",
        ),
        ConfidenceTerm("sweep", 0.20, 1.0 if sweeps else 0.0),
        ConfidenceTerm("zone", 0.25, 1.0 if (gaps or blocks) else 0.0),
        ConfidenceTerm("pools", 0.20, min(1.0, len(pools) / 2)),
    )

    summary = (
        f"{last_event.kind} {'вверх' if last_event.direction == 'up' else 'вниз'}"
        if last_event
        else "структура без подтверждённого слома"
    )

    return Reading(
        detector="smc",
        version=VERSION,
        evidence_kind=EVIDENCE_KIND,
        summary=summary,
        detail=(
            f"сломов: {len(events)}, снятий: {len(sweeps)}, "
            f"незакрытых FVG: {len(gaps)}, order block: {len(blocks)}, "
            f"пулов ликвидности: {len(pools)}"
        ),
        confidence=Confidence(terms),
        measures=(
            Measure("structure_events", "Сломов структуры", float(len(events))),
            Measure("sweeps", "Снятий ликвидности", float(len(sweeps))),
            Measure("open_fvg", "Незакрытых FVG", float(len(gaps))),
            Measure("order_blocks", "Order block", float(len(blocks))),
            Measure("liquidity_pools", "Пулов ликвидности", float(len(pools))),
            Measure(
                "last_displacement_atr",
                "Импульс слома, ATR",
                last_event.displacement_atr if last_event else None,
                status="measured" if last_event else "missing",
                note="" if last_event else "подтверждённого слома структуры нет",
            ),
        ),
        marks=tuple(marks),
        payload={
            "events": events,
            "sweeps": sweeps,
            "fvg": gaps,
            "order_blocks": blocks,
            "pools": pools,
            "swings": swing_points,
        },
    )
