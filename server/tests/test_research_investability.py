"""Investability Gate §13.10.

Шлюз отвечает на вопрос, отличный от «доказано ли изменение»: годится ли
инструмент. Смешивать их запрещено (§2.6), и приёмочные тесты ТЗ проверяют
именно неслипание этих двух вопросов.
"""

from __future__ import annotations

from app.research.investability import (
    Disclosure,
    EquityFacts,
    GateStatus,
    Liquidity,
    TokenFacts,
    Valuation,
    check_equity,
    check_token,
)

GOOD = EquityFacts(
    exposure_proven=True,
    median_turnover_rub=50_000_000,
    free_float=0.35,
    net_debt_to_ebitda=1.5,
    interest_coverage=6.0,
    disclosure_regular=True,
    valuation_state=Valuation.REASONABLE,
)

GOOD_TOKEN = TokenFacts(
    value_capture=True,
    median_spot_volume_usd=25_000_000,
    depth_2pct_usd=400_000,
    free_float_confidence=0.9,
    unlock_pressure_90d=0.03,
    holder_concentration=0.2,
    treasury_runway_months=36,
    fdv_to_holder_revenue=18,
)


def replace(base, **changes):
    values = {name: getattr(base, name) for name in type(base).__dataclass_fields__}
    values.update(changes)
    return type(base)(**values)


# ── Российская бумага ───────────────────────────────────────────────────────


def test_чистая_бумага_проходит():
    result = check_equity(GOOD)
    assert result.status is GateStatus.PASS
    assert result.allows_diligence
    assert result.liquidity_state is Liquidity.SUFFICIENT


def test_недоказанная_связь_с_драйвером_блокирует():
    """Сигнал об отрасли не является сигналом об этой компании."""
    result = check_equity(replace(GOOD, exposure_proven=False))
    assert result.status is GateStatus.BLOCKED_BY_RISK
    assert any("материальная связь" in b for b in result.hard_blocks)


def test_неликвидная_бумага_блокируется_но_причина_названа():
    result = check_equity(replace(GOOD, median_turnover_rub=1_000_000))
    assert result.status is GateStatus.BLOCKED_BY_RISK
    assert result.liquidity_state is Liquidity.INSUFFICIENT


def test_пограничная_ликвидность_это_риск_а_не_запрет():
    result = check_equity(replace(GOOD, median_turnover_rub=15_000_000))
    assert result.status is GateStatus.RESEARCH_ONLY
    assert result.liquidity_state is Liquidity.MARGINAL


def test_отсутствие_данных_отличается_от_риска():
    """Первое чинится подключением источника, второе не чинится вовсе.

    Свести их в одно «не проходит» значит лишить владельца понимания, что
    делать дальше.
    """
    result = check_equity(replace(GOOD, median_turnover_rub=None))
    assert result.status is GateStatus.BLOCKED_BY_DATA
    assert result.hard_blocks == []
    assert result.missing_required_data


def test_к_банкам_долговые_метрики_не_применяются():
    """§13.10: отсутствие net debt/EBITDA у банка — не пробел в данных.

    Ложный жёсткий блок по неприменимой метрике — отдельный приёмочный
    критерий ТЗ.
    """
    bank = replace(GOOD, sector="banks", net_debt_to_ebitda=None,
                   interest_coverage=None)
    result = check_equity(bank)
    assert result.status is GateStatus.PASS
    assert not any("долг" in b for b in result.hard_blocks)
    assert not any("долгов" in m for m in result.missing_required_data)


def test_перегруженный_долг_блокирует():
    result = check_equity(replace(GOOD, net_debt_to_ebitda=6.0))
    assert result.status is GateStatus.BLOCKED_BY_RISK


def test_недостаточное_раскрытие_блокирует():
    """Целевой показатель нечем проверить — гипотеза непроверяема."""
    result = check_equity(replace(GOOD, disclosure_regular=False))
    assert result.status is GateStatus.BLOCKED_BY_RISK
    assert result.disclosure_state is Disclosure.INSUFFICIENT


def test_неизмеренная_оценка_не_подменяется_нейтральной():
    """§13.10: missing valuation не заменяется средним значением."""
    result = check_equity(replace(GOOD, valuation_state=Valuation.NOT_ASSESSED))
    assert result.valuation_state is Valuation.NOT_ASSESSED
    assert result.status is GateStatus.RESEARCH_ONLY
    assert any("оценка не измерена" in r for r in result.soft_risks)


def test_нет_законного_маршрута_к_ценам_это_блок():
    """Исполнимость нечем оценить — значит нечего и оценивать."""
    result = check_equity(replace(GOOD, market_data_route_legal=False))
    assert result.status is GateStatus.BLOCKED_BY_RISK
    assert any("рыночным данным" in b for b in result.hard_blocks)


# ── Криптоактив ─────────────────────────────────────────────────────────────


def test_чистый_токен_проходит():
    assert check_token(GOOD_TOKEN).status is GateStatus.PASS


def test_протокол_без_value_capture_остаётся_гипотезой_о_продукте():
    """Главный приёмочный тест крипты (§13.10).

    Экономика протокола может расти, а токен не получать из неё ничего.
    Тогда торговать по этой находке нечем, и она обязана это сказать.
    """
    result = check_token(replace(GOOD_TOKEN, value_capture=False))
    assert result.status is GateStatus.BLOCKED_BY_RISK
    assert any("гипотеза о продукте" in b for b in result.hard_blocks)


def test_неустановленный_value_capture_это_нехватка_данных():
    result = check_token(replace(GOOD_TOKEN, value_capture=None))
    assert result.status is GateStatus.BLOCKED_BY_DATA


def test_критический_инцидент_безопасности_блокирует_разбор():
    """Экономический сигнал не удаляется, но диligence закрыт."""
    result = check_token(replace(GOOD_TOKEN, critical_exploit=True))
    assert result.status is GateStatus.BLOCKED_BY_RISK


def test_давление_разблокировок_это_риск():
    result = check_token(replace(GOOD_TOKEN, unlock_pressure_90d=0.4))
    assert result.status is GateStatus.RESEARCH_ONLY
    assert any("разблокировки" in r for r in result.soft_risks)


def test_неточное_оборотное_предложение_снижает_доверие():
    result = check_token(replace(GOOD_TOKEN, free_float_confidence=0.3))
    assert result.status is GateStatus.RESEARCH_ONLY


def test_дорогая_оценка_помечается_а_не_блокируется():
    result = check_token(replace(GOOD_TOKEN, fdv_to_holder_revenue=200))
    assert result.valuation_state is Valuation.EXPECTATIONS_HEAVY
    assert result.status is not GateStatus.BLOCKED_BY_RISK
