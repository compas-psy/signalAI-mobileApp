"""Криптодвижки CRYPTO_ECON и CRYPTO_SUPPLY (§13.8, §13.9).

Два вопроса, которые постоянно смешивают и которые здесь разделены явно:
зарабатывает ли протокол — и получает ли из этого что-нибудь токен;
запланирована ли разблокировка — и случилась ли она.
"""

from __future__ import annotations

from decimal import Decimal

from app.research.engines import crypto_econ, crypto_supply
from app.research.engines.crypto_econ import ProtocolPeriod, tvl_excluding_own
from app.research.engines.crypto_supply import (
    SupplySnapshot,
    UnlockEvent,
    circulating_after,
    scheduled_within,
)

D = Decimal


# ── CRYPTO_ECON §13.8 ───────────────────────────────────────────────────────


def protocol(**overrides) -> list[ProtocolPeriod]:
    base = [
        ProtocolPeriod(
            period=f"m{i}",
            fees=D(100) + D(20) * i,
            revenue=D(60) + D(12) * i,
            holder_revenue=D(30) + D(6) * i,
            tvl_total=D(10_000) + D(500) * i,
            tvl_own_token=D(1_000),
            stablecoin_liquidity=D(5_000) + D(300) * i,
            volume=D(50_000),
            incentives_usd=D(1_000),
            active_users=1000 + 50 * i,
            repeat_users=600 + 30 * i,
        )
        for i in range(6)
    ]
    for key, value in overrides.items():
        index, field = key.split("__")
        row = base[int(index)]
        values = {n: getattr(row, n) for n in ProtocolPeriod.__dataclass_fields__}
        values[field] = value
        base[int(index)] = ProtocolPeriod(**values)
    return base


def test_веса_crypto_econ_складываются_в_единицу():
    assert abs(sum(crypto_econ.WEIGHTS.values()) - 1.0) < 1e-9


def test_растущая_экономика_с_передачей_стоимости_даёт_сигнал():
    verdict = crypto_econ.evaluate(protocol())
    assert verdict.direction == "positive"
    assert verdict.value_capture_ratio > 0


def test_протокол_без_передачи_стоимости_это_гипотеза_о_продукте():
    """Приёмочный критерий §13.8 и главная ловушка крипты.

    Протокол зарабатывает, токен не получает ничего. Выглядит как
    инвестиционная возможность и ею не является.
    """
    verdict = crypto_econ.evaluate(protocol(**{f"{i}__holder_revenue": D(0) for i in range(6)}))
    assert verdict.no_value_capture
    assert verdict.direction == "neutral"
    assert verdict.product_signal
    assert "гипотеза о продукте" in verdict.detail


def test_собственный_токен_исключается_из_tvl():
    """Рост TVL от роста цены своего же токена — рефлексия, а не приток."""
    period = ProtocolPeriod(period="m", tvl_total=D(1000), tvl_own_token=D(700))
    assert tvl_excluding_own(period) == D(300)


def test_рефлексивный_tvl_помечается():
    verdict = crypto_econ.evaluate(
        protocol(**{f"{i}__tvl_own_token": D(9_000) for i in range(6)})
    )
    assert "crypto_econ_reflexive_tvl" in verdict.reason_codes


def test_объём_оплаченный_стимулами_помечается_но_не_обвиняет():
    """§13.8 запрещает утверждать манипуляцию без доказательств.

    Эвристики дают флаг качества, а не суждение об участниках.
    """
    verdict = crypto_econ.evaluate(
        protocol(**{f"{i}__incentives_usd": D(40_000) for i in range(6)})
    )
    assert "crypto_econ_incentive_driven" in verdict.reason_codes
    assert "манипул" not in verdict.detail


def test_двойной_счёт_блокирует_расчёт():
    verdict = crypto_econ.evaluate(
        protocol(**{"5__double_count_removed": False})
    )
    assert not verdict.applicable
    assert "crypto_econ_double_count" in verdict.reason_codes


def test_неизвестная_передача_стоимости_отличается_от_нулевой():
    verdict = crypto_econ.evaluate(
        protocol(**{f"{i}__revenue": None for i in range(6)})
    )
    assert "crypto_econ_value_capture_unknown" in verdict.reason_codes
    assert not verdict.no_value_capture


# ── CRYPTO_SUPPLY §13.9 ─────────────────────────────────────────────────────


def test_веса_crypto_supply_складываются_в_единицу():
    assert abs(sum(crypto_supply.WEIGHTS.values()) - 1.0) < 1e-9


def test_плановая_разблокировка_не_меняет_обращающееся_предложение():
    """Главный приёмочный критерий §13.9.

    План и факт — разные наблюдения. До исполнения предложение не растёт.
    """
    planned = UnlockEvent(
        planned_day=30, planned_amount=D(1_000_000), beneficiary="team",
        source="vesting_contract",
    )
    assert circulating_after([planned], D(10_000_000)) == D(10_000_000)

    executed = UnlockEvent(
        planned_day=30,
        planned_amount=D(1_000_000),
        beneficiary="team",
        source="vesting_contract",
        executed_amount=D(1_000_000),
        executed_day=31,
    )
    assert circulating_after([executed], D(10_000_000)) == D(11_000_000)


def test_источник_расписания_влияет_на_доверие():
    """Контракт сильнее документации, документация сильнее агрегатора."""
    contract = UnlockEvent(30, D(100), "team", "vesting_contract")
    aggregator = UnlockEvent(30, D(100), "team", "aggregator")
    assert contract.confidence > aggregator.confidence


def test_отменённая_разблокировка_не_учитывается():
    cancelled = UnlockEvent(30, D(100), "team", "vesting_contract", cancelled=True)
    assert scheduled_within([cancelled], day=0, horizon=90) == D(0)


def test_внутренняя_ротация_не_считается_притоком_на_биржу():
    """Переводы между кошельками казначейства — не продажа (§13.9)."""
    with_rotation = crypto_supply.evaluate(
        SupplySnapshot(
            circulating_now=D(10_000_000),
            circulating_90d_ago=D(10_000_000),
            free_float=D(5_000_000),
            price=D(1),
            spot_volume_30d=D(50_000_000),
            tracked_exchange_inflow=D(500_000),
            internal_rotation=D(500_000),
        ),
        [],
    )
    inflow = next(
        c for c in with_rotation.components if c.key == "exchange_inflow"
    )
    assert inflow.value == D(0)
    assert "crypto_supply_internal_rotation_excluded" in with_rotation.reason_codes


def test_неизвестное_свободное_предложение_ограничивает_уверенность():
    verdict = crypto_supply.evaluate(
        SupplySnapshot(circulating_now=D(10_000_000), free_float=None), []
    )
    assert not verdict.float_known
    assert verdict.confidence <= crypto_supply.UNKNOWN_FLOAT_CAP
    assert "crypto_supply_float_unknown" in verdict.reason_codes


def test_крупная_разблокировка_даёт_отрицательное_направление():
    """Рост давления предложения — ухудшение для держателя."""
    verdict = crypto_supply.evaluate(
        SupplySnapshot(
            circulating_now=D(12_000_000),
            circulating_90d_ago=D(10_000_000),
            free_float=D(5_000_000),
            price=D(1),
            spot_volume_30d=D(500_000),
            depth_2pct=D(10_000),
            stable_liquidity_growth=D("-0.2"),
            tracked_exchange_inflow=D(400_000),
        ),
        [UnlockEvent(10, D(2_000_000), "team", "vesting_contract")],
    )
    assert verdict.direction == "negative"
    assert verdict.scheduled_unlock_share > D("0.3")


def test_покрытие_объёмом_это_контекст_а_не_доказательство_спроса():
    verdict = crypto_supply.evaluate(
        SupplySnapshot(
            circulating_now=D(10_000_000),
            circulating_90d_ago=D(10_000_000),
            free_float=D(5_000_000),
            price=D(1),
            spot_volume_30d=D(100_000),
        ),
        [UnlockEvent(10, D(1_000_000), "team", "vesting_contract")],
    )
    assert verdict.volume_coverage < 1
    assert "crypto_supply_thin_coverage" in verdict.reason_codes
