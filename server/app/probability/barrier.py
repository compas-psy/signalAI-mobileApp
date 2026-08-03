"""Тройной барьер и вероятность (engine-ТЗ §15.2–§15.6).

Вероятность здесь означает ровно одно и всегда одно и то же:

    P(TP1 достигнут раньше SL в пределах горизонта)

Это определение едет вместе с числом в каждую карточку (§32). Без него 0,61
читается как «шанс заработать», а это другая величина.

Уверенность считается отдельно и не смешивается с вероятностью (§15.4):
вероятность говорит о рынке, уверенность — о том, насколько нам вообще есть
на что опереться.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from ..market.candles import Candle
from ..models.enums import BarrierOutcome, Direction


@dataclass(frozen=True, slots=True)
class BarrierResult:
    outcome: BarrierOutcome
    bars_to_resolution: int | None
    resolved_index: int | None
    detail: str
    # Неоднозначный бар (TP и SL внутри одной свечи) нельзя использовать как
    # чистую метку для обучения (§15.2). Отчёт его показывает, модель — нет.
    label_usable: bool = True


def label_triple_barrier(
    candles: Sequence[Candle],
    *,
    direction: Direction,
    entry_index: int,
    take_profit: Decimal,
    stop_loss: Decimal,
    horizon_bars: int,
    lower_tf: Sequence[Candle] | None = None,
) -> BarrierResult:
    """Разметить исход по трём барьерам (§15.2).

    ``WIN`` — TP раньше SL, ``LOSS`` — SL раньше TP, ``TIMEOUT`` — ни один за
    горизонт, ``AMBIGUOUS`` — оба внутри одной свечи.

    Неоднозначность разрешается младшим таймфреймом, если он передан. Если
    нет — исход остаётся ``AMBIGUOUS``, а для отчёта берётся консервативно
    худший вариант; меткой для обучения такой случай не становится (§15.2).
    Подставлять «наверное, сначала TP» здесь значит систематически завышать
    вероятность на самых волатильных барах, то есть ровно там, где ошибка
    дороже всего.
    """
    long = direction is Direction.LONG
    end = min(len(candles) - 1, entry_index + horizon_bars)

    for i in range(entry_index + 1, end + 1):
        candle = candles[i]
        hit_tp = candle.high >= take_profit if long else candle.low <= take_profit
        hit_sl = candle.low <= stop_loss if long else candle.high >= stop_loss

        if hit_tp and hit_sl:
            resolved = _resolve_ambiguous(
                candle, lower_tf, direction, take_profit, stop_loss
            )
            if resolved is not None:
                return BarrierResult(
                    outcome=resolved,
                    bars_to_resolution=i - entry_index,
                    resolved_index=i,
                    detail="оба барьера в одном баре, разрешено младшим таймфреймом",
                )
            return BarrierResult(
                outcome=BarrierOutcome.AMBIGUOUS,
                bars_to_resolution=i - entry_index,
                resolved_index=i,
                detail=(
                    "TP и SL внутри одной свечи, младшего таймфрейма нет; "
                    "для отчёта считается худший исход, меткой не используется"
                ),
                label_usable=False,
            )
        if hit_tp:
            return BarrierResult(
                BarrierOutcome.WIN, i - entry_index, i, "TP достигнут раньше SL"
            )
        if hit_sl:
            return BarrierResult(
                BarrierOutcome.LOSS, i - entry_index, i, "SL достигнут раньше TP"
            )

    return BarrierResult(
        BarrierOutcome.TIMEOUT, None, None,
        f"за {horizon_bars} баров не сработал ни один барьер",
    )


def _resolve_ambiguous(
    bar: Candle,
    lower_tf: Sequence[Candle] | None,
    direction: Direction,
    take_profit: Decimal,
    stop_loss: Decimal,
) -> BarrierOutcome | None:
    """Разобрать неоднозначный бар по младшему таймфрейму."""
    if not lower_tf:
        return None
    long = direction is Direction.LONG
    inside = [
        c for c in lower_tf if bar.open_time <= c.open_time < bar.open_time + _span(bar, lower_tf)
    ]
    for c in inside:
        hit_tp = c.high >= take_profit if long else c.low <= take_profit
        hit_sl = c.low <= stop_loss if long else c.high >= stop_loss
        if hit_tp and hit_sl:
            return None  # неоднозначность повторилась — глубже не идём
        if hit_tp:
            return BarrierOutcome.WIN
        if hit_sl:
            return BarrierOutcome.LOSS
    return None


def _span(bar: Candle, lower_tf: Sequence[Candle]):
    """Длительность старшего бара, оценённая по шагу младшего ряда."""
    from datetime import timedelta

    if len(lower_tf) < 2:
        return timedelta(hours=1)
    step = lower_tf[1].open_time - lower_tf[0].open_time
    return step * 60 if step.total_seconds() < 60 else step * 4


@dataclass(frozen=True, slots=True)
class ProbabilityEstimate:
    """Оценка вероятности вместе со всем, без чего её нельзя читать."""

    p_tp1_before_sl: Decimal
    source: str
    sample_size: int
    capped: bool
    cap_reason: str
    definition: str = (
        "P(TP1 достигнут раньше SL в пределах горизонта, "
        "с учётом модели исполнения)"
    )


def rule_prior(
    score: Decimal,
    *,
    cap: Decimal = Decimal("0.68"),
    floor: Decimal = Decimal("0.35"),
) -> ProbabilityEstimate:
    """Априорная вероятность по баллу — до накопления статистики (§15.3).

    Линейное отображение балла в коридор, ограниченный сверху ``cap``. Потолок
    здесь — не осторожность, а честность: пока нет ста релевантных
    OOS-сделок, различить 0,68 и 0,80 не на чем, и показывать вторую цифру
    значит выдумывать точность.

    Балл **не превращается** в вероятность напрямую: это приближение до
    появления данных, и оно так и называется в поле ``source``.
    """
    clamped = max(Decimal(0), min(Decimal(100), score))
    value = floor + (cap - floor) * clamped / Decimal(100)
    return ProbabilityEstimate(
        p_tp1_before_sl=value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
        source="rule_prior",
        sample_size=0,
        capped=True,
        cap_reason=(
            f"статистики недостаточно: вероятность ограничена сверху {cap} "
            "до накопления 100 релевантных OOS-сделок (§15.3)"
        ),
    )


def empirical(
    wins: int,
    total: int,
    *,
    prior: Decimal,
    shrinkage_k: int = 20,
    source: str = "instrument_setup_regime",
    min_sample: int = 100,
    cap: Decimal = Decimal("0.68"),
) -> ProbabilityEstimate:
    """Эмпирическая частота, стянутая к априорной (§15.3).

    Стягивание обязательно: три победы из четырёх — это не 75%, это «мы почти
    ничего не знаем». Формула ``(wins + k·prior) / (total + k)`` при малой
    выборке отдаёт почти prior, при большой — почти частоту.

    Пока выборка меньше ``min_sample``, потолок §15.3 продолжает действовать.
    """
    if total < 0 or wins < 0 or wins > total:
        raise ValueError("некорректная выборка исходов")
    k = Decimal(shrinkage_k)
    value = (Decimal(wins) + k * prior) / (Decimal(total) + k)

    capped = total < min_sample
    if capped:
        value = min(value, cap)
    return ProbabilityEstimate(
        p_tp1_before_sl=value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
        source=source,
        sample_size=total,
        capped=capped,
        cap_reason=(
            f"выборка {total} меньше {min_sample}: потолок {cap} сохраняется (§15.3)"
            if capped else ""
        ),
    )


#: Чему равен неизмеренный компонент.
#:
#: Половине — то есть незнанию. Это не мягкость, а единственное честное
#: значение: «статистики закрытых сделок ещё нет» и «калибровка плохая» —
#: разные утверждения, и подставлять вместо первого ноль значит выносить
#: приговор вместо признания незнания.
#:
#: Цена прежней подстановки нуля была видна на экране. Два компонента из
#: пяти зануляли 0,55 веса, потолок достижимого падал до 0,325 при пороге
#: 0,50 — ворота не мог пройти никто и никогда. Каскадом это выключало
#: `quality_status = ACTIVE`, перевод в `TRIGGERED` и перепроверку
#: триггера, а на устройстве вкладка «Решения» оставалась пустой
#: структурно, сколько бы хороших сетапов ни нашёл движок.
UNKNOWN = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class ConfidenceInput:
    """Компоненты уверенности. ``None`` — «не измерено», а не «плохо»."""

    sample_adequacy: Decimal | None
    calibration: Decimal | None
    data_completeness: Decimal | None
    regime_similarity: Decimal | None
    stability: Decimal | None


def confidence(
    inputs: ConfidenceInput,
) -> tuple[Decimal, str, tuple[tuple[str, Decimal, Decimal | None], ...]]:
    """Уверенность §15.4 — не вероятность.

    Веса из ТЗ: адекватность выборки 0,30; калибровка 0,25; полнота данных
    0,20; похожесть режима 0,15; устойчивость 0,10. Неизмеренный компонент
    считается за ``UNKNOWN``.

    Полосу «высокая» неизмеренное закрывает: заявлять высокую уверенность,
    не зная калибровки, нельзя — именно калибровка отвечает на вопрос,
    совпадали ли прошлые оценки с исходом.
    """
    weights = (
        ("sample_adequacy", Decimal("0.30"), inputs.sample_adequacy),
        ("calibration", Decimal("0.25"), inputs.calibration),
        ("data_completeness", Decimal("0.20"), inputs.data_completeness),
        ("regime_similarity", Decimal("0.15"), inputs.regime_similarity),
        ("stability", Decimal("0.10"), inputs.stability),
    )
    for name, _, value in weights:
        if value is not None and not (Decimal(0) <= value <= Decimal(1)):
            raise ValueError(f"{name}: {value} вне 0..1")
    total = sum(w * (UNKNOWN if v is None else v) for _, w, v in weights)
    value = total.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    unmeasured = any(v is None for _, _, v in weights)
    if value < Decimal("0.45"):
        band = "LOW"
    elif value > Decimal("0.70") and not unmeasured:
        band = "HIGH"
    else:
        band = "MEDIUM"
    return value, band, weights


def unmeasured_components(
    weights: tuple[tuple[str, Decimal, Decimal | None], ...],
) -> list[str]:
    """Что осталось неизмеренным. Причина обязана быть видна в карточке."""
    return [name for name, _, value in weights if value is None]


def expected_value(
    *,
    p_win: Decimal,
    avg_win_r: Decimal,
    avg_loss_r: Decimal,
    p_timeout: Decimal = Decimal(0),
    timeout_cost_r: Decimal = Decimal(0),
    costs_r: Decimal = Decimal(0),
) -> Decimal:
    """Ожидание в R (§15.5).

    Издержки вычитаются всегда. Ожидание «до комиссий» — это не ожидание, а
    приглашение торговать в минус: на горизонте 1–14 дней комиссии и спред
    съедают заметную часть R.
    """
    p_loss = Decimal(1) - p_win - p_timeout
    if p_loss < 0:
        raise ValueError("сумма вероятностей больше единицы")
    value = (
        p_win * avg_win_r
        - p_loss * abs(avg_loss_r)
        - p_timeout * abs(timeout_cost_r)
        - abs(costs_r)
    )
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
