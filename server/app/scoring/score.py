"""Оценка идеи (engine-ТЗ §15.1).

Одиннадцать слагаемых с весами из ТЗ, умноженные на ``data_quality``.
Множитель, а не двенадцатое слагаемое: плохие данные портят **всю** оценку, а
не отнимают у неё четыре процента. Идея, собранная на дырявом ряду, не может
быть «почти такой же хорошей».

Ключевое различие, без которого оценка врёт:

* **не применимо** — открытого интереса у акции не бывает. Вес компонента
  перераспределяется между остальными, и оценка не проседает за то, чего в
  природе нет.
* **не измерено** — открытый интерес у фьючерса бывает, но запрос упал.
  Компонент получает ноль и тянет вниз ``data_quality``.

Смешение этих двух состояний превращает отказ источника в подтверждение —
ровно та ошибка, из-за которой в прежней версии приложения фактор
ликвидности всегда был нулём, а событийный риск всегда единицей.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

ComponentStatus = Literal["measured", "not_applicable", "missing"]

# Порядок как в §15.1.
WEIGHTS: dict[str, Decimal] = {
    "regime_fit": Decimal("0.16"),
    "market_structure": Decimal("0.14"),
    "setup_quality": Decimal("0.14"),
    "trigger_quality": Decimal("0.12"),
    "derivatives_confirmation": Decimal("0.10"),
    "volume_confirmation": Decimal("0.08"),
    "liquidity_quality": Decimal("0.07"),
    "volatility_fit": Decimal("0.06"),
    "level_confluence": Decimal("0.06"),
    "risk_reward_quality": Decimal("0.04"),
    "execution_quality": Decimal("0.03"),
}

LABELS: dict[str, str] = {
    "regime_fit": "Соответствие режиму рынка",
    "market_structure": "Структура рынка",
    "setup_quality": "Качество сетапа",
    "trigger_quality": "Качество триггера",
    "derivatives_confirmation": "Подтверждение производными",
    "volume_confirmation": "Подтверждение объёмом",
    "liquidity_quality": "Ликвидность",
    "volatility_fit": "Соответствие волатильности",
    "level_confluence": "Схождение уровней",
    "risk_reward_quality": "Качество соотношения риска",
    "execution_quality": "Качество исполнения",
}


@dataclass(frozen=True, slots=True)
class Component:
    """Один компонент оценки: 0–100, вес и происхождение значения."""

    name: str
    value: Decimal  # 0..100
    status: ComponentStatus = "measured"
    note: str = ""
    evidence_id: str | None = None

    @property
    def label(self) -> str:
        return LABELS.get(self.name, self.name)

    @property
    def weight(self) -> Decimal:
        return WEIGHTS[self.name]


@dataclass(frozen=True, slots=True)
class ScoredComponent(Component):
    """Компонент с фактическим весом после перенормировки."""

    effective_weight: Decimal = Decimal(0)
    points: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class Score:
    total: Decimal  # 0..100 после умножения на data_quality
    raw: Decimal  # до умножения
    data_quality: Decimal
    components: tuple[ScoredComponent, ...]
    notes: tuple[str, ...] = ()

    def component(self, name: str) -> ScoredComponent | None:
        return next((c for c in self.components if c.name == name), None)

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.components if c.status == "missing")

    @property
    def not_applicable(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.components if c.status == "not_applicable")


def _q(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def compute(
    components: list[Component],
    *,
    data_quality_floor: Decimal = Decimal("0.5"),
    data_quality_ceiling: Decimal = Decimal("1.0"),
    missing_penalty: Decimal = Decimal("0.15"),
) -> Score:
    """Собрать оценку §15.1.

    ``data_quality`` начинается с потолка и снижается за каждый неизмеренный
    компонент пропорционально его весу — компонент весом 0,16 портит доверие
    сильнее, чем весом 0,03. Ниже пола не опускается: §15.1 задаёт коридор
    0,5–1,0.

    Отсутствие компонента в списке — ошибка вызывающего, а не повод
    подставить ноль: молчаливый пропуск читался бы как «измерено и плохо».
    """
    provided = {c.name for c in components}
    unknown = provided - set(WEIGHTS)
    if unknown:
        raise ValueError(f"неизвестные компоненты оценки: {sorted(unknown)}")
    absent = set(WEIGHTS) - provided
    if absent:
        raise ValueError(
            "не переданы компоненты оценки: "
            f"{sorted(absent)} — подставлять за них ноль нельзя, "
            "это читалось бы как «измерено и плохо»"
        )

    for c in components:
        if not (Decimal(0) <= c.value <= Decimal(100)):
            raise ValueError(f"компонент {c.name}: значение {c.value} вне 0..100")

    applicable = [c for c in components if c.status != "not_applicable"]
    applicable_weight = sum((c.weight for c in applicable), Decimal(0))
    if applicable_weight <= 0:
        raise ValueError("все компоненты оценки помечены неприменимыми")

    scored: list[ScoredComponent] = []
    raw = Decimal(0)
    quality = data_quality_ceiling
    notes: list[str] = []

    for c in components:
        if c.status == "not_applicable":
            scored.append(
                ScoredComponent(
                    name=c.name, value=Decimal(0), status=c.status,
                    note=c.note or "у этого инструмента такого не бывает",
                    evidence_id=c.evidence_id,
                    effective_weight=Decimal(0), points=Decimal(0),
                )
            )
            notes.append(
                f"{LABELS[c.name]}: не применимо, вес перераспределён "
                f"({c.note})" if c.note else
                f"{LABELS[c.name]}: не применимо, вес перераспределён"
            )
            continue

        effective = c.weight / applicable_weight
        value = c.value if c.status == "measured" else Decimal(0)
        points = effective * value
        raw += points
        if c.status == "missing":
            quality -= c.weight * missing_penalty / Decimal("0.16")
            notes.append(f"{LABELS[c.name]}: не измерено — {c.note or 'нет данных'}")
        scored.append(
            ScoredComponent(
                name=c.name, value=value, status=c.status, note=c.note,
                evidence_id=c.evidence_id,
                effective_weight=_q(effective, "0.0001"), points=_q(points),
            )
        )

    quality = max(data_quality_floor, min(data_quality_ceiling, quality))
    total = raw * quality
    return Score(
        total=_q(total),
        raw=_q(raw),
        data_quality=_q(quality, "0.001"),
        components=tuple(scored),
        notes=tuple(notes),
    )
