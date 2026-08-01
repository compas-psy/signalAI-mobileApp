"""Движок CRYPTO_SUPPLY: способность рынка поглотить эмиссию (ТЗ §13.9).

Токен разбавляется двумя способами: по расписанию (разблокировки) и по
факту (рост обращающегося предложения). Их постоянно смешивают, а это
разные наблюдения: **плановая разблокировка и фактическая эмиссия — не
одно и то же**, и до исполнения план не меняет обращающееся предложение
вообще.

Второе, что смешивают: перевод на биржу и продажу. Токены на бирже — это
возможность продать, а не продажа. §13.9 требует называть это индикатором
давления, и формулировки здесь подобраны так, чтобы не утверждать большего.

Ликвидностное покрытие — тоже контекст, а не доказательство спроса.
Объём в десять раз больше разблокировки означает, что рынок **способен**
её переварить, а не что переварит.

Порядок источников истины из §13.9 соблюдается буквально: контракт
сильнее документации, документация сильнее агрегатора. Бесплатный план
DefiLlama разблокировки не отдаёт вовсе, поэтому расписание считается из
документации проекта и vesting-контрактов — и это не обход ограничения,
а единственный доступный законный путь.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .base import EPS, Component, Evaluation, clamp, q

STRATEGY_KEY = "CRYPTO_SUPPLY"
STRATEGY_VERSION = "1.0.0"

WEIGHTS = {
    "scheduled_unlock": 0.30,
    "realized_growth": 0.25,
    "exchange_inflow": 0.20,
    "depth_coverage": 0.15,
    "stable_liquidity": 0.10,
}

# Порядок источников истины (§13.9). Индекс — приоритет.
SOURCE_PRIORITY = (
    "vesting_contract",
    "executed_governance",
    "official_docs",
    "wallet_label",
    "aggregator",
)

# Потолок уверенности при неизвестном свободном предложении.
UNKNOWN_FLOAT_CAP = Decimal("0.50")


@dataclass(frozen=True, slots=True)
class UnlockEvent:
    """Одна разблокировка.

    ``executed_amount`` отделён от ``planned_amount`` намеренно: план и
    факт — разные наблюдения, и до исполнения обращающееся предложение не
    меняется.
    """

    planned_day: int
    planned_amount: Decimal
    beneficiary: str
    source: str
    executed_amount: Decimal | None = None
    executed_day: int | None = None
    cancelled: bool = False

    @property
    def executed(self) -> bool:
        return self.executed_amount is not None and not self.cancelled

    @property
    def confidence(self) -> Decimal:
        """Насколько надёжен источник расписания."""
        try:
            rank = SOURCE_PRIORITY.index(self.source)
        except ValueError:
            return Decimal("0.3")
        return q(Decimal(1) - Decimal(rank) * Decimal("0.15"))


@dataclass(frozen=True, slots=True)
class SupplySnapshot:
    """Состояние предложения на момент оценки."""

    circulating_now: Decimal
    circulating_90d_ago: Decimal | None = None
    free_float: Decimal | None = None
    price: Decimal | None = None
    spot_volume_30d: Decimal | None = None
    depth_2pct: Decimal | None = None
    stable_liquidity_growth: Decimal | None = None
    # Приток на помеченные биржевые адреса из отслеживаемых когорт.
    tracked_exchange_inflow: Decimal | None = None
    # Переводы между кошельками казначейства — не приток на биржу.
    internal_rotation: Decimal = Decimal(0)


@dataclass
class CryptoSupplyResult(Evaluation):
    net_supply_growth_90d: Decimal | None = None
    scheduled_unlock_share: Decimal | None = None
    unlock_usd: Decimal | None = None
    volume_coverage: Decimal | None = None
    float_known: bool = True


def circulating_after(events: list[UnlockEvent], base: Decimal) -> Decimal:
    """Обращающееся предложение с учётом только **исполненных** событий.

    Плановая разблокировка ничего не меняет до исполнения — это главный
    приёмочный критерий движка.
    """
    return base + sum(
        (e.executed_amount or Decimal(0) for e in events if e.executed), Decimal(0)
    )


def scheduled_within(events: list[UnlockEvent], *, day: int, horizon: int) -> Decimal:
    """Сумма запланированных разблокировок в окне."""
    return sum(
        (
            e.planned_amount * e.confidence
            for e in events
            if not e.cancelled
            and not e.executed
            and day <= e.planned_day <= day + horizon
        ),
        Decimal(0),
    )


def evaluate(
    snapshot: SupplySnapshot,
    events: list[UnlockEvent],
    *,
    day: int = 0,
    horizon: int = 90,
) -> CryptoSupplyResult:
    """Оценить давление предложения."""
    result = CryptoSupplyResult(
        strategy_key=STRATEGY_KEY, strategy_version=STRATEGY_VERSION
    )

    if snapshot.circulating_90d_ago and snapshot.circulating_90d_ago > 0:
        result.net_supply_growth_90d = q(
            (snapshot.circulating_now - snapshot.circulating_90d_ago)
            / snapshot.circulating_90d_ago
        )

    scheduled = scheduled_within(events, day=day, horizon=horizon)
    result.float_known = snapshot.free_float is not None and snapshot.free_float > 0
    if result.float_known:
        result.scheduled_unlock_share = q(scheduled / snapshot.free_float)
    else:
        result.reason_codes.append("crypto_supply_float_unknown")

    if snapshot.price is not None:
        result.unlock_usd = q(scheduled * snapshot.price, "0.01")
        if snapshot.spot_volume_30d and result.unlock_usd > 0:
            # Ликвидностный контекст, а не доказательство спроса: рынок
            # способен переварить объём, но переварит ли — другой вопрос.
            result.volume_coverage = q(
                snapshot.spot_volume_30d / result.unlock_usd
            )

    inflow = snapshot.tracked_exchange_inflow
    if inflow is not None:
        # Внутренняя ротация кошельков казначейства не является притоком на
        # биржу и вычитается явно.
        inflow = max(Decimal(0), inflow - snapshot.internal_rotation)

    result.components = [
        Component(
            "scheduled_unlock",
            WEIGHTS["scheduled_unlock"],
            None
            if result.scheduled_unlock_share is None
            else q(Decimal(str(clamp(float(result.scheduled_unlock_share) / 0.3)))),
            "разблокировки велики относительно свободного предложения",
        ),
        Component(
            "realized_growth",
            WEIGHTS["realized_growth"],
            None
            if result.net_supply_growth_90d is None
            else q(Decimal(str(clamp(float(result.net_supply_growth_90d) / 0.2)))),
            "обращающееся предложение выросло",
        ),
        Component(
            "exchange_inflow",
            WEIGHTS["exchange_inflow"],
            None
            if inflow is None or not result.float_known
            else q(Decimal(str(clamp(float(inflow / snapshot.free_float) / 0.05)))),
            "отслеживаемые когорты переводят токены на биржи",
        ),
        Component(
            "depth_coverage",
            WEIGHTS["depth_coverage"],
            None
            if snapshot.depth_2pct is None or not result.unlock_usd
            else q(
                Decimal(str(clamp(1 - float(snapshot.depth_2pct / result.unlock_usd))))
            ),
            "глубина стакана мала относительно разблокировки",
        ),
        Component(
            "stable_liquidity",
            WEIGHTS["stable_liquidity"],
            None
            if snapshot.stable_liquidity_growth is None
            else q(Decimal(str(clamp(-float(snapshot.stable_liquidity_growth) / 0.2)))),
            "стейблкоин-ликвидность не растёт",
        ),
    ]
    result.score()

    if not result.float_known:
        result.cap_confidence(UNKNOWN_FLOAT_CAP, "crypto_supply_float_unknown")

    if inflow is not None and snapshot.internal_rotation > 0:
        result.reason_codes.append("crypto_supply_internal_rotation_excluded")

    # Направление здесь обратное привычному: рост давления предложения —
    # это ухудшение для держателя.
    if result.strength >= Decimal("0.5"):
        result.direction = "negative"
        result.detail = (
            "предложение растёт быстрее, чем рынок его поглощает"
            + (
                f"; разблокировки за {horizon} дней — "
                f"{result.scheduled_unlock_share:.0%} свободного предложения"
                if result.scheduled_unlock_share is not None
                else ""
            )
        )
    else:
        result.direction = "neutral"
        result.detail = "давление предложения умеренное"

    if result.volume_coverage is not None and result.volume_coverage < 1:
        result.reason_codes.append("crypto_supply_thin_coverage")
    return result


__all__ = [
    "CryptoSupplyResult",
    "SOURCE_PRIORITY",
    "STRATEGY_KEY",
    "STRATEGY_VERSION",
    "SupplySnapshot",
    "UNKNOWN_FLOAT_CAP",
    "UnlockEvent",
    "WEIGHTS",
    "circulating_after",
    "evaluate",
    "scheduled_within",
]
