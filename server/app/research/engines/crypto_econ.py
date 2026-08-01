"""Движок CRYPTO_ECON: экономика протокола против токена (ТЗ §13.8).

Два вопроса, которые постоянно путают: зарабатывает ли протокол — и
получает ли из этого что-нибудь токен. Ответы бывают любой комбинацией, и
самая опасная — «протокол зарабатывает, токен не получает ничего». Она
выглядит как инвестиционная возможность и ею не является.

Поэтому §13.8 требует двухуровневой проверки, и здесь она встроена в
структуру результата: сначала экономика, потом актив. Протокол без
механизма передачи стоимости держателю получает положительный сигнал о
**продукте** и метку ``no_value_capture`` — торговать по ней нечего.

Три нормализации, без которых числа врут в разы.

**Собственный токен в TVL.** Протокол, у которого половина «заблокированной
стоимости» — его же токен, показывает рост TVL при росте цены токена. Это
рефлексия, а не приток. Считается отдельно.

**Двойной счёт через мосты и вложенные протоколы.** Одни и те же деньги,
посчитанные дважды, дают удвоение из ничего.

**Комиссии и выручка — разные вещи.** Fees платят пользователи, revenue
остаётся протоколу. Смешение завышает экономику на величину, отданную
поставщикам ликвидности.

Про органичность объёма: эвристики дают **флаги**, а не обвинение. §13.8
прямо запрещает утверждать манипуляцию без доказательств, и формулировки
здесь подобраны так, чтобы этого не делать.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .base import EPS, Component, Evaluation, clamp, q, robust_z, sigmoid

STRATEGY_KEY = "CRYPTO_ECON"
STRATEGY_VERSION = "1.0.0"

WEIGHTS = {
    "fee_growth": 0.25,
    "stable_liquidity": 0.20,
    "repeat_use": 0.20,
    "organic_volume": 0.15,
    "value_capture": 0.20,
}


@dataclass(frozen=True, slots=True)
class ProtocolPeriod:
    """Экономика протокола за период."""

    period: str
    fees: Decimal | None = None
    revenue: Decimal | None = None
    holder_revenue: Decimal | None = None
    tvl_total: Decimal | None = None
    tvl_own_token: Decimal | None = None
    stablecoin_liquidity: Decimal | None = None
    volume: Decimal | None = None
    incentives_usd: Decimal | None = None
    active_users: int | None = None
    repeat_users: int | None = None
    # Двойной счёт через мосты и вложенные протоколы уже исключён.
    double_count_removed: bool = True


@dataclass
class CryptoEconResult(Evaluation):
    tvl_excluding_own: Decimal | None = None
    value_capture_ratio: Decimal | None = None
    incentive_efficiency: Decimal | None = None
    no_value_capture: bool = False
    product_signal: bool = False


def tvl_excluding_own(period: ProtocolPeriod) -> Decimal | None:
    """TVL без собственного токена.

    Протокол, половина «заблокированной стоимости» которого — его же токен,
    показывает рост при росте цены токена. Это рефлексия, а не приток
    капитала.
    """
    if period.tvl_total is None:
        return None
    own = period.tvl_own_token or Decimal(0)
    return max(Decimal(0), period.tvl_total - own)


def _growth(now: Decimal | None, past: Decimal | None) -> Decimal | None:
    if now is None or past is None or past <= 0:
        return None
    return (now - past) / past


def evaluate(periods: list[ProtocolPeriod]) -> CryptoEconResult:
    """Оценить рост реального использования протокола."""
    result = CryptoEconResult(
        strategy_key=STRATEGY_KEY, strategy_version=STRATEGY_VERSION
    )

    if len(periods) < 2:
        result.applicable = False
        result.reason_codes.append("crypto_econ_insufficient_history")
        return result

    now, past = periods[-1], periods[-2]

    if not now.double_count_removed:
        # Одни и те же деньги, посчитанные дважды, дают удвоение из ничего.
        result.applicable = False
        result.reason_codes.append("crypto_econ_double_count")
        result.detail = "двойной счёт через мосты не исключён: числа несопоставимы"
        return result

    result.tvl_excluding_own = tvl_excluding_own(now)
    own_share = (
        (now.tvl_own_token or Decimal(0)) / now.tvl_total
        if now.tvl_total and now.tvl_total > 0
        else None
    )
    if own_share is not None and own_share > Decimal("0.5"):
        result.reason_codes.append("crypto_econ_reflexive_tvl")

    fee_series = [p.fees for p in periods if p.fees is not None]
    fee_growth = _growth(now.fees, past.fees)
    fee_value = None
    if fee_growth is not None:
        z = robust_z(fee_series, now.fees) if len(fee_series) >= 4 else None
        fee_value = sigmoid(z if z is not None else fee_growth)

    stable_growth = _growth(now.stablecoin_liquidity, past.stablecoin_liquidity)
    repeat_share = (
        Decimal(now.repeat_users) / Decimal(now.active_users)
        if now.active_users and now.repeat_users is not None and now.active_users > 0
        else None
    )

    # Эффективность стимулов: сколько органического роста на доллар выплат.
    organic_growth = _growth(result.tvl_excluding_own, tvl_excluding_own(past))
    if organic_growth is not None and now.incentives_usd:
        result.incentive_efficiency = q(
            organic_growth / max(now.incentives_usd, EPS) * Decimal(1000)
        )

    organic_value = None
    if now.volume is not None and now.incentives_usd is not None:
        # Доля объёма, не покрытая прямыми выплатами. Это флаг качества, а
        # не суждение о добросовестности участников.
        subsidised = min(Decimal(1), now.incentives_usd / max(now.volume, EPS))
        organic_value = q(Decimal(1) - subsidised)
        if subsidised > Decimal("0.5"):
            result.reason_codes.append("crypto_econ_incentive_driven")

    if now.revenue is not None and now.revenue > 0:
        result.value_capture_ratio = q(
            (now.holder_revenue or Decimal(0)) / now.revenue
        )
    capture_value = (
        None
        if result.value_capture_ratio is None
        else q(Decimal(str(clamp(float(result.value_capture_ratio)))))
    )

    result.components = [
        Component("fee_growth", WEIGHTS["fee_growth"], fee_value,
                  "комиссии протокола растут"),
        Component(
            "stable_liquidity",
            WEIGHTS["stable_liquidity"],
            None if stable_growth is None else sigmoid(stable_growth),
            "стейблкоин-ликвидность растёт",
        ),
        Component("repeat_use", WEIGHTS["repeat_use"], repeat_share,
                  "пользователи возвращаются"),
        Component("organic_volume", WEIGHTS["organic_volume"], organic_value,
                  "объём не оплачен стимулами"),
        Component("value_capture", WEIGHTS["value_capture"], capture_value,
                  "токен получает часть выручки"),
    ]
    result.score()

    # Двухуровневая проверка §13.8: сначала экономика, потом актив.
    economics_up = fee_growth is not None and fee_growth > 0
    result.product_signal = economics_up

    if result.value_capture_ratio is None:
        result.reason_codes.append("crypto_econ_value_capture_unknown")
    elif result.value_capture_ratio <= 0:
        result.no_value_capture = True
        result.reason_codes.append("crypto_econ_no_value_capture")
        result.direction = "neutral"
        result.detail = (
            "протокол зарабатывает, но токен из этого не получает ничего: "
            "это гипотеза о продукте, а не об активе"
        )
        return result

    if not economics_up:
        result.direction = "negative" if fee_growth is not None else "neutral"
        result.detail = "экономическая активность протокола не растёт"
        return result

    result.direction = "positive"
    # Доля токена называется только когда она измерена. «Токену достаётся
    # None% выручки» — это не осторожная формулировка, а сломанная строка.
    share = (
        f"; токену достаётся {result.value_capture_ratio:.0%} выручки"
        if result.value_capture_ratio is not None
        else "; сколько из этого достаётся токену — неизвестно"
    )
    result.detail = f"комиссии растут на {fee_growth:.1%}{share}"
    return result


__all__ = [
    "CryptoEconResult",
    "ProtocolPeriod",
    "STRATEGY_KEY",
    "STRATEGY_VERSION",
    "WEIGHTS",
    "evaluate",
    "tvl_excluding_own",
]
