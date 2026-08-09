"""S1 — TREND_PULLBACK.

Recovery semantics:
* контекст и setup должны быть качественными;
* отсутствие финального trigger НЕ уничтожает setup — он возвращается как
  Candidate и admission переводит его в WATCH;
* actionable статус всё равно требует подтверждённый price-action trigger,
  RR, probability, confidence, liquidity и risk gates в pipeline.scan.

Это устраняет структурную тишину: раньше WATCH почти не мог родиться, потому
что strategy.build() отклонял идею раньше admission, если trigger ещё не был
сформирован. Проверять такой trigger через 15 минут было уже не у чего.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..detectors.price_action import PriceZone
from ..models.enums import Direction, OrderIntent, Strategy, TrendRegime, VolatilityRegime
from .base import (
    Candidate,
    Check,
    Outcome,
    SetupContext,
    Target,
    price_text,
    reject,
    round_to_tick,
    snap_entry,
)

STRATEGY = Strategy.TREND_PULLBACK


@dataclass(frozen=True, slots=True)
class TrendPullbackParams:
    min_trend_score: int = 3
    min_adx: float = 18.0
    fib_zone: tuple[Decimal, Decimal] = (Decimal("0.382"), Decimal("0.618"))
    max_retracement: Decimal = Decimal("0.786")
    stop_atr_buffer: Decimal = Decimal("0.10")
    min_stop_ticks: int = 2
    # Один подтверждённый setup-confluence достаточен, потому что финальный
    # admission отдельно требует price-action trigger и score gates.
    min_zones: int = 1
    # Внутренний trigger стратегии больше не дублирует admission trigger.
    min_triggers: int = 1
    tp1_r_min: Decimal = Decimal("1.0")
    tp1_r_max: Decimal = Decimal("1.3")
    tp2_r_min: Decimal = Decimal("2.0")
    tp3_r_min: Decimal = Decimal("3.0")


def _zones(ctx: SetupContext, direction: Direction) -> list[PriceZone]:
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

    # Без setup нет идеи вообще. Без trigger — есть WATCH-кандидат.
    if any(not c.passed for c in checks):
        return reject(STRATEGY, checks)

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
    # ВАЖНО: triggers — не structural rejection. Финальный admission ниже по
    # pipeline спросит настоящий price-action trigger и не сделает WATCH
    # actionable раньше времени.

    atr_trigger = ctx.atr_trigger or Decimal(0)
    stop_side_low = direction is Direction.LONG
    known_sweeps = ctx.smc.payload.get("sweeps", []) if ctx.smc is not None else []
    sweep_depth = max(
        (
            Decimal(str(s.penetration_atr))
            for s in known_sweeps
            if s.is_high != stop_side_low
        ),
        default=Decimal(0),
    )
    buffer = max(
        atr_trigger * (sweep_depth + p.stop_atr_buffer),
        atr_trigger * p.stop_atr_buffer,
        ctx.tick_size * p.min_stop_ticks,
    )

    # Если confluence только по fib и явной SMC-зоны под текущей ценой нет,
    # зона WATCH строится узко вокруг текущего уровня. Actionable она станет
    # только после отдельного price-action подтверждения в этой зоне.
    fib_half_width = max(atr_trigger * Decimal("0.10"), ctx.tick_size * 2)
    zone_low = min((z.low for z in hit), default=price - fib_half_width)
    zone_high = max((z.high for z in hit), default=price + fib_half_width)
    if direction is Direction.LONG:
        entry_low, entry_high = zone_low, zone_high
        stop = round_to_tick(zone_low - buffer, ctx.tick_size, up=False)
    else:
        entry_low, entry_high = zone_low, zone_high
        stop = round_to_tick(zone_high + buffer, ctx.tick_size, up=True)

    entry_low, entry_high, entry_reference = snap_entry(
        entry_low, entry_high, (entry_low + entry_high) / 2, ctx.tick_size
    )
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
            f"закрытие {ctx.trigger_tf} за {price_text(stop)}; слом структуры против "
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
