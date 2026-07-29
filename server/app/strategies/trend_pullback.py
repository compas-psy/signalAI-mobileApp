"""S1 — TREND_PULLBACK (engine-ТЗ §10).

Основная стратегия, 50–60% потока. Условия ТЗ реализованы по пунктам, и
каждый пункт — отдельная проверка с именем, чтобы отбраковка называла
причину.

§10.1 допуск: контекст UPTREND, trend score ≥3, ADX ≥18 или устойчивая
структура, волатильность не EXTREME, есть пространство до встречного уровня.

§10.2 сетап: коррекция входит **минимум в две зоны** и не ломает защищённый
свинг, не уходит глубже 0.786.

§10.3 триггер: **минимум два** из перечисленных подтверждений.

§10.4 план: приоритет лимитному входу на ретесте, стоп за снятием ликвидности
плюс max(0.1 ATR, 2 тика), TP1 на ликвидности или 1.0–1.3R, TP2 не ближе 2.0R.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..detectors.price_action import PriceZone
from ..models.enums import Direction, OrderIntent, Strategy, TrendRegime, VolatilityRegime
from .base import Candidate, Check, Outcome, SetupContext, Target, reject, round_to_tick

STRATEGY = Strategy.TREND_PULLBACK


@dataclass(frozen=True, slots=True)
class TrendPullbackParams:
    min_trend_score: int = 3
    min_adx: float = 18.0
    fib_zone: tuple[Decimal, Decimal] = (Decimal("0.382"), Decimal("0.618"))
    max_retracement: Decimal = Decimal("0.786")
    stop_atr_buffer: Decimal = Decimal("0.10")
    min_stop_ticks: int = 2
    min_zones: int = 2
    min_triggers: int = 2
    tp1_r_min: Decimal = Decimal("1.0")
    tp1_r_max: Decimal = Decimal("1.3")
    tp2_r_min: Decimal = Decimal("2.0")
    tp3_r_min: Decimal = Decimal("3.0")


def _zones(ctx: SetupContext, direction: Direction) -> list[PriceZone]:
    """Собрать контекстные зоны §10.2 из наблюдений детекторов.

    Чистое число Фибоначчи зоной не считается: §10.2 перечисляет объекты
    (EMA, VWAP, FVG, order block, старый уровень пробоя), а уровень
    коррекции лишь один из них. Одно совпадение — не конфлюэнс.
    """
    zones: list[PriceZone] = []
    if ctx.smc is None:
        return zones
    payload = ctx.smc.payload
    want_bullish = direction is Direction.LONG

    for block in payload.get("order_blocks", []):
        if block.is_bullish == want_bullish and block.reacted:
            zones.append(PriceZone(block.low, block.high, "order_block"))
    for gap in payload.get("fvg", []):
        if gap.is_bullish == want_bullish:
            zones.append(PriceZone(gap.low, gap.high, "fvg"))
    for pool in payload.get("pools", []):
        if pool.is_high != want_bullish:
            tolerance = (ctx.atr_setup or Decimal(0)) * Decimal("0.15")
            zones.append(
                PriceZone(pool.level - tolerance, pool.level + tolerance, "liquidity_pool")
            )
    return zones


def _last_impulse(ctx: SetupContext, direction: Direction):
    """Последний импульс — база для уровней коррекции."""
    if ctx.smc is None:
        return None
    swing_points = ctx.smc.payload.get("swings", [])
    highs = [s for s in swing_points if s.is_high]
    lows = [s for s in swing_points if not s.is_high]
    if not highs or not lows:
        return None
    if direction is Direction.LONG:
        high = highs[-1]
        prior = [low for low in lows if low.index < high.index]
        if not prior:
            return None
        return prior[-1].price, high.price
    low = lows[-1]
    prior = [h for h in highs if h.index < low.index]
    if not prior:
        return None
    return prior[-1].price, low.price


def build(ctx: SetupContext, params: TrendPullbackParams | None = None) -> Outcome:
    p = params or TrendPullbackParams()
    checks: list[Check] = []

    up = ctx.regime.trend is TrendRegime.UPTREND
    down = ctx.regime.trend is TrendRegime.DOWNTREND
    direction = Direction.LONG if up else Direction.SHORT

    checks.append(
        Check(
            "context_trend", "Контекстный тренд", up or down,
            f"режим {ctx.regime.trend.value}",
        )
    )
    checks.append(
        Check(
            "trend_score", "Сила тренда",
            abs(ctx.regime.trend_score) >= p.min_trend_score,
            f"trend score {ctx.regime.trend_score} против порога {p.min_trend_score}",
        )
    )
    adx = ctx.regime.detail.get("adx")
    structure_ok = ctx.regime.detail.get("structure") in ("up", "down")
    checks.append(
        Check(
            "adx_or_structure", "ADX или структура",
            (adx is not None and adx >= p.min_adx) or structure_ok,
            f"ADX {adx if adx is None else round(adx, 1)}, структура "
            f"{ctx.regime.detail.get('structure')}",
        )
    )
    checks.append(
        Check(
            "volatility", "Волатильность",
            ctx.regime.volatility is not VolatilityRegime.EXTREME,
            f"режим волатильности {ctx.regime.volatility.value}",
        )
    )
    checks.append(
        Check("tradable", "Ликвидность", ctx.regime.tradable,
              f"режим ликвидности {ctx.regime.liquidity.value}")
    )

    if any(not c.passed for c in checks):
        return reject(STRATEGY, checks)

    impulse = _last_impulse(ctx, direction)
    checks.append(
        Check("impulse", "Импульс", impulse is not None,
              "последний импульс найден" if impulse else "структуры импульса нет")
    )
    if impulse is None:
        return reject(STRATEGY, checks)

    origin, extreme = impulse
    span = abs(extreme - origin)
    price = ctx.trigger_bars[-1].close
    retracement = (
        (extreme - price) / span if direction is Direction.LONG else (price - extreme) / span
    ) if span > 0 else Decimal(0)

    checks.append(
        Check(
            "retracement_depth", "Глубина коррекции",
            Decimal(0) < retracement <= p.max_retracement,
            f"откат {retracement:.3f} при потолке {p.max_retracement}",
        )
    )

    zones = _zones(ctx, direction)
    hit = [z for z in zones if z.contains(price)]
    in_fib = p.fib_zone[0] <= retracement <= p.fib_zone[1]
    confluence = len(hit) + (1 if in_fib else 0)
    checks.append(
        Check(
            "zone_confluence", "Схождение зон", confluence >= p.min_zones,
            f"совпадений {confluence} из требуемых {p.min_zones}"
            f" (зоны: {[z.kind for z in hit]}"
            f"{', фибо' if in_fib else ''})",
        )
    )

    triggers: list[str] = []
    if ctx.smc is not None:
        payload = ctx.smc.payload
        want_high_sweep = direction is Direction.SHORT
        if any(s.is_high == want_high_sweep for s in payload.get("sweeps", [])):
            triggers.append("снятие ликвидности против тренда")
        wanted = "up" if direction is Direction.LONG else "down"
        if any(e.direction == wanted for e in payload.get("events", [])[-2:]):
            triggers.append("слом структуры по тренду")
    if ctx.price_action is not None and ctx.price_action.payload.get("direction") == (
        "long" if direction is Direction.LONG else "short"
    ):
        triggers.append(ctx.price_action.summary)
    if ctx.volume_reading is not None:
        triggers.append("объём подтверждает")
    if ctx.oi_reading is not None:
        triggers.append("поток производных подтверждает")

    checks.append(
        Check(
            "triggers", "Триггеры", len(triggers) >= p.min_triggers,
            f"подтверждений {len(triggers)} из требуемых {p.min_triggers}"
            + (f": {', '.join(triggers)}" if triggers else ""),
        )
    )

    if any(not c.passed for c in checks):
        return reject(STRATEGY, checks)

    atr_trigger = ctx.atr_trigger or Decimal(0)
    buffer = max(atr_trigger * p.stop_atr_buffer, ctx.tick_size * p.min_stop_ticks)

    zone_low = min((z.low for z in hit), default=price)
    zone_high = max((z.high for z in hit), default=price)
    if direction is Direction.LONG:
        entry_low, entry_high = zone_low, zone_high
        stop = round_to_tick(zone_low - buffer, ctx.tick_size, up=False)
    else:
        entry_low, entry_high = zone_low, zone_high
        stop = round_to_tick(zone_high + buffer, ctx.tick_size, up=True)

    entry_reference = (entry_low + entry_high) / 2
    risk = abs(entry_reference - stop)
    checks.append(
        Check("stop_distance", "Расстояние до стопа", risk > 0,
              f"риск на единицу {risk}")
    )
    if risk <= 0:
        return reject(STRATEGY, checks)

    sign = Decimal(1) if direction is Direction.LONG else Decimal(-1)
    tp1 = entry_reference + sign * risk * p.tp1_r_min
    tp2 = entry_reference + sign * risk * p.tp2_r_min
    tp3 = entry_reference + sign * risk * p.tp3_r_min

    targets = (
        Target(round_to_tick(tp1, ctx.tick_size, up=direction is Direction.SHORT),
               Decimal("0.5"), f"{p.tp1_r_min}R или ближайшая ликвидность"),
        Target(round_to_tick(tp2, ctx.tick_size, up=direction is Direction.SHORT),
               Decimal("0.3"), f"не ближе {p.tp2_r_min}R"),
        Target(round_to_tick(tp3, ctx.tick_size, up=direction is Direction.SHORT),
               Decimal("0.2"), f"проекция импульса, {p.tp3_r_min}R"),
    )

    used = tuple(
        r for r in (ctx.smc, ctx.price_action, ctx.volume_reading, ctx.oi_reading)
        if r is not None
    )
    counter = [
        s.detail for s in ctx.regime.signals
        if (s.vote < 0 and direction is Direction.LONG)
        or (s.vote > 0 and direction is Direction.SHORT)
    ]

    return Candidate(
        strategy=STRATEGY,
        direction=direction,
        order_intent=OrderIntent.LIMIT_RETEST,
        entry_low=entry_low,
        entry_high=entry_high,
        entry_reference=entry_reference,
        stop=stop,
        targets=targets,
        checks=tuple(checks),
        used=used,
        zones=tuple(hit),
        invalidation=(
            f"закрытие {ctx.trigger_tf} за {stop}; слом структуры против "
            f"направления на {ctx.setup_tf}; смена режима рынка"
        ),
        thesis=(
            f"{'Лонг' if direction is Direction.LONG else 'Шорт'} после отката "
            f"в {'восходящем' if direction is Direction.LONG else 'нисходящем'} "
            f"тренде: откат {retracement:.0%} последнего импульса в зону "
            f"({', '.join(z.kind for z in hit) or 'фибо'}), "
            f"подтверждений {len(triggers)}"
        ),
        counter_factors=tuple(counter),
    )
