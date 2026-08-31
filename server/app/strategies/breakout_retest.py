"""S2 — BREAKOUT_RETEST (engine-ТЗ §11).

Диапазон берётся у детектора Вайкоффа, а не строится заново. Два независимых
определения диапазона неизбежно разъедутся, и тогда одна и та же цена
окажется одновременно внутри и снаружи — а от этого зависит, где стоп.

§11.2 пробой: закрытие за границей ≥0.15 ATR, тело ≥0.7 ATR, объём z ≥1.0,
OI не падает экстремально, нет близкого встречного уровня.
§11.3 ретест: возврат к границе в 1–6 баров, закрытие не глубже 0.35 ATR
внутрь, реакция по направлению.
§11.4 план: стоп внутри диапазона за экстремум ретеста, TP2 — measured move,
R/R до TP2 ≥ 2.0. Ложный пробой отменяет идею, автопереворот запрещён.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..detectors.price_action import PriceZone
from ..detectors.wyckoff import TradingRange, WyckoffParams, find_range
from ..features.indicators import atr, swings
from ..market.candles import Candle
from ..models.enums import Direction, OrderIntent, Strategy
from .base import (
    Candidate,
    Check,
    Outcome,
    SetupContext,
    Target,
    reject,
    round_to_tick,
    snap_entry,
)

STRATEGY = Strategy.BREAKOUT_RETEST


@dataclass(frozen=True, slots=True)
class BreakoutParams:
    breakout_atr: Decimal = Decimal("0.15")
    breakout_body_atr: Decimal = Decimal("0.70")
    breakout_volume_z: float = 1.0
    max_retest_bars: int = 6
    retest_max_depth_atr: Decimal = Decimal("0.35")
    stop_atr_buffer: Decimal = Decimal("0.10")
    min_stop_ticks: int = 2
    tp1_r_min: Decimal = Decimal("1.0")
    tp2_rr_min: Decimal = Decimal("2.0")


def detect_range(candles: Sequence[Candle]) -> TradingRange | None:
    """Return the canonical raw Wyckoff trading range without phase confidence.

    A trading range is a lower-level price fact shared by Wyckoff reversal and
    breakout-retest.  Wyckoff's phase reading is intentionally confidence-
    gated by Spring/climax/LPS evidence; breakout must not disappear merely
    because those reversal-specific confirmations are absent.
    """

    params = WyckoffParams()
    atr_period = 14
    if len(candles) < max(params.min_range_bars, atr_period) + 5:
        return None
    atr_values = atr(candles, atr_period)
    return find_range(
        candles,
        atr_values,
        swings(candles, confirm_bars=2),
        params,
    )


def build(ctx: SetupContext, params: BreakoutParams | None = None) -> Outcome:
    p = params or BreakoutParams()
    checks: list[Check] = []

    trading_range = detect_range(ctx.setup_bars)
    has_range = trading_range is not None
    checks.append(
        Check("range", "Диапазон", has_range,
              "диапазон найден" if has_range else "диапазона нет — пробивать нечего")
    )
    if trading_range is None:
        return reject(STRATEGY, checks)

    atr_setup = ctx.atr_setup or Decimal(0)
    checks.append(
        Check("atr", "ATR", atr_setup > 0, f"ATR сетапа {atr_setup}")
    )
    if atr_setup <= 0:
        return reject(STRATEGY, checks)

    bars = ctx.setup_bars
    breakout_index = None
    direction = None
    # Окно берётся с запасом на ретест, а проход — **вперёд**: пробой это
    # первый выход за границу, а не последний. Поиск от конца назад объявлял
    # бы пробоем сам бар ретеста, и ретеста после него уже не находилось.
    window_start = max(0, len(bars) - 1 - (p.max_retest_bars + 3))
    for i in range(window_start, len(bars)):
        candle = bars[i]
        if candle.close > trading_range.resistance + atr_setup * p.breakout_atr:
            breakout_index, direction = i, Direction.LONG
            break
        if candle.close < trading_range.support - atr_setup * p.breakout_atr:
            breakout_index, direction = i, Direction.SHORT
            break

    checks.append(
        Check(
            "breakout", "Пробой", breakout_index is not None,
            f"закрытие за границей на ≥{p.breakout_atr} ATR"
            if breakout_index is not None
            else "закрытия за границей с нужным запасом не было",
        )
    )
    if breakout_index is None:
        return reject(STRATEGY, checks)

    impulse = bars[breakout_index]
    checks.append(
        Check(
            "breakout_body", "Тело пробоя",
            impulse.body >= atr_setup * p.breakout_body_atr,
            f"тело {impulse.body} против порога {atr_setup * p.breakout_body_atr}",
        )
    )

    volume_ok = ctx.volume_reading is not None
    checks.append(
        Check("breakout_volume", "Объём пробоя", volume_ok,
              "объём подтверждает" if volume_ok else "объёмного подтверждения нет")
    )

    oi_falling = (
        ctx.oi_reading is not None
        and ctx.oi_reading.payload.get("quadrant") in ("SHORT_COVERING", "LONG_LIQUIDATION")
    )
    checks.append(
        Check("open_interest", "Открытый интерес", not oi_falling,
              "OI падает — движение на закрытии позиций" if oi_falling
              else "OI не противоречит")
    )

    boundary = (
        trading_range.resistance if direction is Direction.LONG else trading_range.support
    )
    retest_index = None
    for j in range(breakout_index + 1, min(len(bars), breakout_index + 1 + p.max_retest_bars)):
        candle = bars[j]
        touched = (
            candle.low <= boundary if direction is Direction.LONG
            else candle.high >= boundary
        )
        if not touched:
            continue
        depth = (
            boundary - candle.close if direction is Direction.LONG
            else candle.close - boundary
        )
        if depth > atr_setup * p.retest_max_depth_atr:
            checks.append(
                Check("retest_held", "Удержание ретеста", False,
                      f"закрытие ушло внутрь на {depth}, порог "
                      f"{atr_setup * p.retest_max_depth_atr} — ложный пробой")
            )
            return reject(STRATEGY, checks)
        retest_index = j
        break

    checks.append(
        Check("retest", "Ретест", retest_index is not None,
              f"возврат к границе в пределах {p.max_retest_bars} баров"
              if retest_index is not None else "ретеста ещё не было")
    )
    if any(not c.passed for c in checks):
        return reject(STRATEGY, checks)

    retest = bars[retest_index]
    buffer = max(atr_setup * p.stop_atr_buffer, ctx.tick_size * p.min_stop_ticks)
    if direction is Direction.LONG:
        entry_low, entry_high = boundary - atr_setup * Decimal("0.25"), boundary + atr_setup * Decimal("0.25")
        stop = round_to_tick(min(retest.low, boundary) - buffer, ctx.tick_size, up=False)
    else:
        entry_low, entry_high = boundary - atr_setup * Decimal("0.25"), boundary + atr_setup * Decimal("0.25")
        stop = round_to_tick(max(retest.high, boundary) + buffer, ctx.tick_size, up=True)

    entry_low, entry_high, entry_reference = snap_entry(
        entry_low, entry_high, boundary, ctx.tick_size
    )
    risk = abs(entry_reference - stop)
    if risk <= 0:
        checks.append(Check("stop_distance", "Расстояние до стопа", False, "нулевой риск"))
        return reject(STRATEGY, checks)

    sign = Decimal(1) if direction is Direction.LONG else Decimal(-1)
    measured = trading_range.height
    tp1 = entry_reference + sign * risk * p.tp1_r_min
    tp2 = entry_reference + sign * measured
    tp3 = entry_reference + sign * measured * Decimal("1.618")

    rr_tp2 = abs(tp2 - entry_reference) / risk
    checks.append(
        Check("rr", "Соотношение риска", rr_tp2 >= p.tp2_rr_min,
              f"R/R до TP2 {rr_tp2:.2f} против порога {p.tp2_rr_min}")
    )
    if rr_tp2 < p.tp2_rr_min:
        return reject(STRATEGY, checks)

    used = tuple(
        r for r in (ctx.wyckoff, ctx.smc, ctx.volume_reading, ctx.oi_reading, ctx.price_action)
        if r is not None
    )
    return Candidate(
        strategy=STRATEGY,
        direction=direction,
        order_intent=OrderIntent.LIMIT_RETEST,
        entry_low=entry_low,
        entry_high=entry_high,
        entry_reference=entry_reference,
        stop=stop,
        targets=(
            Target(round_to_tick(tp1, ctx.tick_size, up=direction is Direction.SHORT),
                   Decimal("0.5"), f"{p.tp1_r_min}R"),
            Target(round_to_tick(tp2, ctx.tick_size, up=direction is Direction.SHORT),
                   Decimal("0.3"), "measured move — высота диапазона"),
            Target(round_to_tick(tp3, ctx.tick_size, up=direction is Direction.SHORT),
                   Decimal("0.2"), "1,618 высоты диапазона"),
        ),
        checks=tuple(checks),
        used=used,
        zones=(PriceZone(entry_low, entry_high, "range_boundary"),),
        invalidation=(
            f"закрытие {ctx.setup_tf} обратно внутрь диапазона "
            f"[{trading_range.support}, {trading_range.resistance}] — ложный пробой; "
            "переворот в обратную сторону запрещён (§11.4)"
        ),
        thesis=(
            f"Пробой {'сопротивления' if direction is Direction.LONG else 'поддержки'} "
            f"{boundary} после диапазона в {trading_range.bars} баров, "
            f"удержанный ретест. Цель — measured move {measured}"
        ),
    )