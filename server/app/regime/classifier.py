"""Классификатор рыночного режима (engine-ТЗ §9).

Четыре измерения считаются независимо и **не схлопываются в одно число**.
«Тренд вверх при экстремальной волатильности и тонкой ликвидности» — это не
«хороший рынок с оговорками», а запрет на вход: стоп в таком рынке выбьет
раньше, чем сработает идея, а выйти будет некуда.

Все пять признаков тренда §9.1 реализованы буквально и по отдельности,
чтобы в разборе идеи было видно, какой именно из них не выполнен.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from ..features.indicators import (
    adx,
    atr,
    ema,
    percentile_rank,
    structure_direction,
    swings,
)
from ..market.candles import Candle
from ..models.enums import (
    DerivativesFlow,
    LiquidityRegime,
    TrendRegime,
    VolatilityRegime,
)


@dataclass(frozen=True, slots=True)
class TrendSignal:
    """Один признак тренда: как называется, что показал, куда голосует."""

    name: str
    label: str
    vote: int  # +1 за рост, −1 за падение, 0 — не измерен или нейтрален
    detail: str


@dataclass(frozen=True, slots=True)
class RegimeResult:
    trend: TrendRegime
    trend_score: int
    volatility: VolatilityRegime
    liquidity: LiquidityRegime
    derivatives_flow: DerivativesFlow
    signals: list[TrendSignal] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    @property
    def tradable(self) -> bool:
        """Можно ли вообще торговать в таком рынке.

        Непроходимая ликвидность запрещает вход независимо от красоты
        сетапа: цена входа и цена выхода в таком стакане — разные вселенные.
        """
        return self.liquidity is not LiquidityRegime.UNTRADEABLE


def _slope_positive(
    values: Sequence[Decimal | None], lookback: int, deadband: Decimal = Decimal(0)
) -> int:
    """Наклон средней за ``lookback`` баров: +1 / −1 / 0.

    Мёртвая зона та же, что у прочих сравнений: средняя, сдвинувшаяся на
    тысячную, не «растёт».
    """
    known = [v for v in values if v is not None]
    if len(known) <= lookback:
        return 0
    now, before = known[-1], known[-1 - lookback]
    diff = now - before
    if abs(diff) <= deadband:
        return 0
    return 1 if diff > 0 else -1


def classify_trend(
    candles: Sequence[Candle],
    *,
    ema_fast: int = 50,
    ema_slow: int = 200,
    adx_period: int = 14,
    adx_trend_min: float = 18.0,
    adx_range_max: float = 20.0,
    uptrend_min: int = 3,
    downtrend_max: int = -3,
    range_abs_max: int = 1,
    slope_lookback: int = 5,
    confirm_bars: int = 2,
    deadband_atr: float = 0.10,
) -> tuple[TrendRegime, int, list[TrendSignal], dict]:
    """Пять признаков §9.1 → ``trend_score`` от −5 до +5.

    Признак, который посчитать не из чего (мало истории), голосует нулём и
    честно говорит об этом. Подставлять «нейтрально» вместо «не измерено»
    нельзя: в первом случае рынок спокоен, во втором мы просто не знаем.

    **Мёртвая зона.** Сравнения «цена выше средней» и «быстрая выше медленной»
    засчитываются, только если разница превышает ``deadband_atr`` × ATR. Без
    неё в мёртвом флэте EMA50 обгоняет EMA200 на тысячную доли процента, три
    признака голосуют «вверх», и движок объявляет тренд там, где его нет, —
    то есть выдаёт трендовый сетап в диапазоне, где он гарантированно
    убыточен.
    """
    closes = [c.close for c in candles]
    fast = ema(closes, ema_fast)
    slow = ema(closes, ema_slow)
    adx_values = adx(candles, adx_period)
    swing_points = swings(candles, confirm_bars=confirm_bars)
    structure = structure_direction(swing_points)

    signals: list[TrendSignal] = []

    last_close = closes[-1] if closes else None
    last_fast = fast[-1] if fast else None
    last_slow = slow[-1] if slow else None
    last_adx = next((v for v in reversed(adx_values) if v is not None), None)

    last_atr = next((v for v in reversed(atr(candles)) if v is not None), None)
    deadband = (
        last_atr * Decimal(str(deadband_atr)) if last_atr is not None else Decimal(0)
    )

    def compare(left: Decimal, right: Decimal) -> int:
        """Сравнение с мёртвой зоной: разница меньше доли ATR — не сигнал."""
        diff = left - right
        if abs(diff) <= deadband:
            return 0
        return 1 if diff > 0 else -1

    if last_close is not None and last_fast is not None:
        vote = compare(last_close, last_fast)
        signals.append(
            TrendSignal(
                "close_vs_ema_fast",
                f"Цена против EMA{ema_fast}",
                vote,
                f"{last_close} против {last_fast:.2f}"
                + ("" if vote else f" — разница внутри мёртвой зоны {deadband:.4f}"),
            )
        )
    else:
        signals.append(
            TrendSignal(
                "close_vs_ema_fast",
                f"Цена против EMA{ema_fast}",
                0,
                f"не измерено: нужно {ema_fast} закрытых баров",
            )
        )

    if last_fast is not None and last_slow is not None:
        vote = compare(last_fast, last_slow)
        signals.append(
            TrendSignal(
                "ema_order",
                f"EMA{ema_fast} против EMA{ema_slow}",
                vote,
                f"{last_fast:.2f} против {last_slow:.2f}"
                + ("" if vote else f" — разница внутри мёртвой зоны {deadband:.4f}"),
            )
        )
    else:
        signals.append(
            TrendSignal(
                "ema_order",
                f"EMA{ema_fast} против EMA{ema_slow}",
                0,
                f"не измерено: нужно {ema_slow} закрытых баров",
            )
        )

    slope = _slope_positive(fast, slope_lookback, deadband)
    signals.append(
        TrendSignal(
            "ema_slope",
            f"Наклон EMA{ema_fast}",
            slope,
            "растёт" if slope > 0 else ("падает" if slope < 0 else "плоская"),
        )
    )

    # ADX не имеет направления: он усиливает уже сложившееся мнение, а не
    # голосует сам. Если остальные признаки в нуле, усиливать нечего.
    if last_adx is None:
        signals.append(
            TrendSignal("adx", f"ADX{adx_period}", 0, "не измерено: мало истории")
        )
        adx_vote = 0
    else:
        direction = sum(s.vote for s in signals)
        adx_vote = 0
        if last_adx >= adx_trend_min and direction != 0:
            adx_vote = 1 if direction > 0 else -1
        signals.append(
            TrendSignal(
                "adx",
                f"ADX{adx_period}",
                adx_vote,
                f"{last_adx:.1f}"
                + ("" if last_adx >= adx_trend_min else f" — ниже порога {adx_trend_min}"),
            )
        )

    structure_vote = {"up": 1, "down": -1, "flat": 0}[structure]
    signals.append(
        TrendSignal(
            "structure",
            "Структура HH/HL",
            structure_vote,
            {"up": "выше максимумы и минимумы", "down": "ниже максимумы и минимумы",
             "flat": "структура не сложилась"}[structure],
        )
    )

    score = sum(s.vote for s in signals)
    score = max(-5, min(5, score))

    if score >= uptrend_min:
        regime = TrendRegime.UPTREND
    elif score <= downtrend_max:
        regime = TrendRegime.DOWNTREND
    elif abs(score) <= range_abs_max and (
        last_adx is not None and last_adx < adx_range_max
    ):
        regime = TrendRegime.RANGE
    else:
        regime = TrendRegime.TRANSITION

    detail = {
        "adx": last_adx,
        "structure": structure,
        "ema_fast": float(last_fast) if last_fast is not None else None,
        "ema_slow": float(last_slow) if last_slow is not None else None,
    }
    return regime, score, signals, detail


def classify_volatility(
    candles: Sequence[Candle],
    *,
    atr_period: int = 14,
    window: int = 252,
    low: float = 30.0,
    normal: float = 75.0,
    high: float = 90.0,
) -> tuple[VolatilityRegime, float | None]:
    """Перцентиль ATR/цена за ``window`` периодов (§9.2).

    Нормируем ATR на цену: без этого «высокая волатильность» означала бы
    просто «дорогой инструмент», и золото всегда было бы спокойнее биткоина.
    """
    values = atr(candles, atr_period)
    ratios: list[float] = []
    for candle, value in zip(candles, values, strict=False):
        if value is None or candle.close == 0:
            continue
        ratios.append(float(value / candle.close))
    if not ratios:
        return VolatilityRegime.NORMAL, None

    sample = ratios[-window:]
    rank = percentile_rank(sample, sample[-1])
    if rank < low:
        return VolatilityRegime.LOW, rank
    if rank < normal:
        return VolatilityRegime.NORMAL, rank
    if rank < high:
        return VolatilityRegime.HIGH, rank
    return VolatilityRegime.EXTREME, rank


def classify_liquidity(
    *,
    relative_spread: float | None,
    median_notional: Decimal | None,
    min_notional: Decimal,
    max_spread: float,
    gaps: int = 0,
    session_open: bool = True,
) -> tuple[LiquidityRegime, list[str]]:
    """Ликвидность по спреду, обороту, дырам и сессии (§9.3).

    Возвращает ещё и список причин: «тонко» без объяснения не позволяет ни
    поспорить, ни починить.
    """
    reasons: list[str] = []

    if not session_open:
        return LiquidityRegime.UNTRADEABLE, ["сессия закрыта"]
    if relative_spread is None or median_notional is None:
        return LiquidityRegime.UNTRADEABLE, [
            "нет данных о спреде или обороте — торговать вслепую нельзя"
        ]

    if relative_spread > max_spread * 3:
        return LiquidityRegime.UNTRADEABLE, [
            f"спред {relative_spread:.4%} втрое выше допустимого {max_spread:.4%}"
        ]
    if median_notional < min_notional / 10:
        return LiquidityRegime.UNTRADEABLE, [
            f"оборот {median_notional:.0f} на порядок ниже порога {min_notional:.0f}"
        ]

    score = 0
    if relative_spread <= max_spread / 2:
        score += 1
    elif relative_spread > max_spread:
        score -= 1
        reasons.append(f"спред {relative_spread:.4%} выше порога {max_spread:.4%}")

    if median_notional >= min_notional * 2:
        score += 1
    elif median_notional < min_notional:
        score -= 1
        reasons.append(f"оборот {median_notional:.0f} ниже порога {min_notional:.0f}")

    if gaps > 0:
        score -= 1
        reasons.append(f"дыр в ряду: {gaps}")

    if score >= 2:
        return LiquidityRegime.GOOD, reasons
    if score >= 0:
        return LiquidityRegime.NORMAL, reasons
    return LiquidityRegime.THIN, reasons


def classify_derivatives_flow(
    *,
    price_change_z: float | None,
    oi_change_z: float | None,
    threshold: float = 0.5,
) -> DerivativesFlow:
    """Квадранты цена/OI (§9.4).

    Без открытого интереса ответ ``NEUTRAL`` — это «не измерено», и фактор
    производных в оценке обязан быть помечен как неизмеренный, а не
    посчитан как нейтральный.
    """
    if price_change_z is None or oi_change_z is None:
        return DerivativesFlow.NEUTRAL
    if abs(price_change_z) < threshold or abs(oi_change_z) < threshold:
        return DerivativesFlow.NEUTRAL
    if price_change_z > 0 and oi_change_z > 0:
        return DerivativesFlow.LONG_BUILDUP
    if price_change_z < 0 and oi_change_z > 0:
        return DerivativesFlow.SHORT_BUILDUP
    if price_change_z > 0 and oi_change_z < 0:
        return DerivativesFlow.SHORT_COVERING
    return DerivativesFlow.LONG_LIQUIDATION
