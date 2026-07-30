"""S3 — WYCKOFF_REVERSAL (engine-ТЗ §12).

Единственный разрешённый контртрендовый сетап (§12), и потому самый строгий:
все шесть условий §12.3 обязательны. Без любого из них идея остаётся
наблюдением — «только WATCH», как прямо сказано в ТЗ.

Множитель риска 0,75 (§12.4): редкий разворотный сетап рискует меньше
обычного. Он передаётся наружу и применяется в расчёте бюджета, а не
зашивается в размер.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..detectors.price_action import PriceZone
from ..models.enums import Direction, OrderIntent, Strategy
from .base import (
    Candidate,
    Check,
    Outcome,
    SetupContext,
    Target,
    price_text,
    reject,
    round_to_tick,
)

STRATEGY = Strategy.WYCKOFF_REVERSAL


@dataclass(frozen=True, slots=True)
class WyckoffReversalParams:
    min_prior_move_atr: Decimal = Decimal("3.0")
    stop_atr_buffer: Decimal = Decimal("0.10")
    min_stop_ticks: int = 2
    tp2_rr_min: Decimal = Decimal("2.2")
    risk_multiplier: Decimal = Decimal("0.75")


def build(ctx: SetupContext, params: WyckoffReversalParams | None = None) -> Outcome:
    p = params or WyckoffReversalParams()
    checks: list[Check] = []

    has_range = ctx.wyckoff is not None and ctx.wyckoff.payload.get("range") is not None
    checks.append(
        Check("range", "Диапазон", has_range,
              "диапазон найден" if has_range else "диапазона нет")
    )
    if not has_range:
        return reject(STRATEGY, checks)

    trading_range = ctx.wyckoff.payload["range"]
    events = ctx.wyckoff.payload.get("events", [])
    atr_setup = ctx.atr_setup or Decimal(0)

    # 1. Климакс (§12.2 «желательно», §12.3 требует объёмного подтверждения).
    climax = next((e for e in events if e.kind == "climax"), None)
    checks.append(
        Check("climax", "Климакс объёма", climax is not None,
              climax.detail if climax else "климакса объёма не было")
    )

    # 2. Spring или Upthrust.
    spring = next((e for e in events if e.kind == "spring"), None)
    upthrust = next((e for e in events if e.kind == "upthrust"), None)
    sweep = spring or upthrust
    checks.append(
        Check("spring", "Spring/Upthrust", sweep is not None,
              sweep.detail if sweep else "прокола границы с возвратом не было")
    )
    if sweep is None:
        return reject(STRATEGY, checks)

    direction = Direction.LONG if sweep.kind == "spring" else Direction.SHORT

    # 3. Предшествующее движение ≥3 ATR (§12.1).
    bars = ctx.setup_bars
    window = bars[max(0, trading_range.start_index - 20) : trading_range.start_index + 1]
    prior_move = (
        max(c.high for c in window) - min(c.low for c in window) if window else Decimal(0)
    )
    prior_atr = prior_move / atr_setup if atr_setup > 0 else Decimal(0)
    checks.append(
        Check(
            "prior_move", "Предшествующее движение",
            prior_atr >= p.min_prior_move_atr,
            f"{prior_atr:.2f} ATR против порога {p.min_prior_move_atr}",
        )
    )

    # 4. CHoCH после Spring — порядок проверяется по времени, а не по факту.
    choch = None
    if ctx.smc is not None:
        wanted = "up" if direction is Direction.LONG else "down"
        choch = next(
            (
                e for e in ctx.smc.payload.get("events", [])
                if e.kind == "CHoCH" and e.direction == wanted and e.index > sweep.index
            ),
            None,
        )
    checks.append(
        Check(
            "choch", "Смена характера", choch is not None,
            f"CHoCH на баре {choch.index} после Spring на {sweep.index}"
            if choch else "CHoCH в сторону разворота после Spring не было",
        )
    )

    # 5. Тест LPS/LPSY.
    test = next((e for e in events if e.kind in ("lps", "lpsy")), None)
    checks.append(
        Check("test", "Повторный тест", test is not None,
              test.detail if test else "повторного теста не было")
    )

    # 6. Пространство до цели.
    if direction is Direction.LONG:
        entry_zone_low = trading_range.support
        entry_zone_high = trading_range.support + atr_setup * Decimal("0.5")
        stop_base = sweep.price
    else:
        entry_zone_low = trading_range.resistance - atr_setup * Decimal("0.5")
        entry_zone_high = trading_range.resistance
        stop_base = sweep.price

    buffer = max(atr_setup * p.stop_atr_buffer, ctx.tick_size * p.min_stop_ticks)
    stop = round_to_tick(
        stop_base - buffer if direction is Direction.LONG else stop_base + buffer,
        ctx.tick_size,
        up=direction is Direction.SHORT,
    )
    entry_reference = (entry_zone_low + entry_zone_high) / 2
    risk = abs(entry_reference - stop)

    tp1 = trading_range.midpoint
    tp2 = (
        trading_range.resistance if direction is Direction.LONG else trading_range.support
    )
    sign = Decimal(1) if direction is Direction.LONG else Decimal(-1)
    tp3 = tp2 + sign * trading_range.height

    rr_tp2 = abs(tp2 - entry_reference) / risk if risk > 0 else Decimal(0)
    checks.append(
        Check("room", "Пространство до цели", rr_tp2 >= p.tp2_rr_min,
              f"R/R до противоположной границы {rr_tp2:.2f} против порога {p.tp2_rr_min}")
    )

    if any(not c.passed for c in checks):
        # §12.3: без полного подтверждения — только наблюдение.
        return reject(STRATEGY, checks)

    used = tuple(
        r for r in (ctx.wyckoff, ctx.smc, ctx.volume_reading, ctx.price_action)
        if r is not None
    )
    return Candidate(
        strategy=STRATEGY,
        direction=direction,
        order_intent=OrderIntent.LIMIT_RETEST,
        entry_low=entry_zone_low,
        entry_high=entry_zone_high,
        entry_reference=entry_reference,
        stop=stop,
        targets=(
            Target(round_to_tick(tp1, ctx.tick_size, up=direction is Direction.SHORT),
                   Decimal("0.5"), "середина диапазона"),
            Target(round_to_tick(tp2, ctx.tick_size, up=direction is Direction.SHORT),
                   Decimal("0.3"), "противоположная граница"),
            Target(round_to_tick(tp3, ctx.tick_size, up=direction is Direction.SHORT),
                   Decimal("0.2"), "проекция высоты диапазона"),
        ),
        checks=tuple(checks),
        used=used,
        zones=(PriceZone(entry_zone_low, entry_zone_high, "range_boundary"),),
        risk_multiplier=p.risk_multiplier,
        invalidation=(
            f"закрытие за экстремумом {sweep.kind} ({price_text(sweep.price)}); "
            "возврат в фазу B диапазона"
        ),
        thesis=(
            f"Разворот по Вайкоффу: {sweep.kind} на границе {price_text(sweep.price)}, "
            f"CHoCH после него, тест удержан. "
            f"Цель — противоположная граница {price_text(tp2)}"
        ),
    )
