"""Схемы портфельного контура (§6, UX-ТЗ §7.1).

Доли уходят строками, как и цены: 0.075 в JSON-числе на другом конце легко
становится 0.07499999999999999, а из доли считается сумма покупки.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import ApiModel, Money


class PositionOut(ApiModel):
    """Позиция пакета вместе с тем, зачем она там и когда её выкидывать."""

    instrument_id: str
    symbol: str
    title: str
    asset_class: str
    target_weight: Money
    role: str
    thesis: str
    kill_conditions: str
    score: Money | None = None
    expected_return: Money | None = None


class ClassSliceOut(ApiModel):
    """Доля класса активов — то, что рисуется полосой, а не читается."""

    asset_class: str
    label: str
    weight: Money


class PackageOut(ApiModel):
    id: str
    profile: str
    package: str
    horizon_years: int
    expected_return_low: Money
    expected_return_high: Money
    target_volatility: Money
    drawdown_limit: Money
    cvar_95: Money | None = None
    rationale: str
    # Состав годен, но рискованнее, чем обещает профиль. Экран показывает это
    # отметкой рядом с числами риска, а не прячет состав целиком.
    meets_target: bool = True
    warnings: list[str] = Field(default_factory=list)
    stress: dict[str, str] = Field(default_factory=dict)
    generated_at: datetime
    valid_until: datetime
    mix: list[ClassSliceOut] = Field(default_factory=list)
    positions: list[PositionOut] = Field(default_factory=list)


class StageOut(ApiModel):
    """Шаг конвейера: сделан или нет, и чем измеряется.

    Прогресс считается по факту, а не по флагу в базе: «есть» означает, что
    в базе лежит результат шага, и число рядом — это он и есть.
    """

    key: str
    name: str
    done: bool
    detail: str = ""


class UniverseSliceOut(ApiModel):
    """Сколько бумаг каждого класса во вселенной и сколько с историей.

    Без этого «в отборе только акции и ОФЗ» остаётся загадкой: то ли фонды
    не синхронизировались с биржи, то ли не прошли срез, то ли их выкинуло
    окно истории. Три разные поломки с одним и тем же экраном.
    """

    asset_class: str
    label: str
    total: int
    with_history: int


class PortfolioStatusOut(ApiModel):
    stages: list[StageOut]
    universe_mix: list[UniverseSliceOut] = Field(default_factory=list)
    # Почему доска биржи не дала бумаг. Целый класс активов не может
    # пропадать беззвучно: «фондов нет» и «доска не ответила» требуют
    # разных действий.
    universe_notes: list[str] = Field(default_factory=list)
    packages_ready: int
    universe: int
    generated_at: datetime | None = None
    reason: str = ""


class PortfolioResponse(ApiModel):
    status: PortfolioStatusOut
    packages: list[PackageOut] = Field(default_factory=list)
