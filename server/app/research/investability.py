"""Investability Gate — второй фильтр, а не десятый сигнал (ТЗ §13.10).

Экономический сигнал и инвестиционная пригодность — разные вещи, и смешивать
их запрещено (§2.6). Компания может действительно расти, а бумага при этом
быть неторгуемой, перегруженной долгом или непрозрачной. Протокол может
зарабатывать, а токен не получать из этого ничего.

Шлюз не отменяет и не ослабляет экономический вывод. Он отвечает на другой
вопрос: можно ли по этой гипотезе вообще что-то делать, или её место —
наблюдение. Поэтому сильный сигнал у неликвидной бумаги не исчезает: он
сохраняется со статусом ``blocked_by_risk``, и владелец видит и находку, и
причину, по которой она не годится.

Отдельно про отсутствие данных. ``blocked_by_data`` — не то же самое, что
``blocked_by_risk``: первое чинится подключением источника, второе не
чинится вовсе. Сводить их в одно «не проходит» значит лишить владельца
понимания, что делать дальше.

Снятие блокировки создаёт новую версию гипотезы, а не переписывает прошлую
(§13.10, acceptance): история решений — часть доказательной базы.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class GateStatus(StrEnum):
    PASS = "pass"
    RESEARCH_ONLY = "research_only"
    BLOCKED_BY_RISK = "blocked_by_risk"
    BLOCKED_BY_DATA = "blocked_by_data"


class Liquidity(StrEnum):
    SUFFICIENT = "sufficient"
    MARGINAL = "marginal"
    INSUFFICIENT = "insufficient"
    UNKNOWN = "unknown"


class Valuation(StrEnum):
    NOT_ASSESSED = "not_assessed"
    REASONABLE = "reasonable_candidate"
    EXPECTATIONS_HEAVY = "expectations_heavy"
    INCOMPARABLE = "incomparable"


class Disclosure(StrEnum):
    SUFFICIENT = "sufficient"
    DEGRADED = "degraded"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True, slots=True)
class EquityFacts:
    """Что известно о российской бумаге.

    ``None`` везде означает «не измерено», и это не ноль. Ноль долга и
    неизвестный долг — противоположные новости.
    """

    entity_resolved: bool = True
    median_turnover_rub: float | None = None
    free_float: float | None = None
    net_debt_to_ebitda: float | None = None
    interest_coverage: float | None = None
    short_term_debt_share: float | None = None
    exposure_proven: bool = False
    disclosure_regular: bool | None = None
    covenant_breach: bool = False
    suspended: bool = False
    # Банки и страховщики: к ним долговые метрики неприменимы, и отсутствие
    # net debt/EBITDA у банка не является пробелом в данных.
    sector: str = ""
    valuation_state: Valuation = Valuation.NOT_ASSESSED
    market_data_route_legal: bool = True


@dataclass(frozen=True, slots=True)
class TokenFacts:
    """Что известно о криптоактиве."""

    mapping_resolved: bool = True
    value_capture: bool | None = None
    median_spot_volume_usd: float | None = None
    depth_2pct_usd: float | None = None
    free_float_confidence: float | None = None
    unlock_pressure_90d: float | None = None
    holder_concentration: float | None = None
    critical_exploit: bool = False
    admin_key_risk: bool = False
    treasury_runway_months: float | None = None
    fdv_to_holder_revenue: float | None = None


# Пороги. Живут в коде рядом с объяснением, а не в магических числах по
# месту использования; в конфигурацию выносятся, когда появится вторая
# площадка с другими нормами.
MIN_TURNOVER_RUB = 20_000_000.0
MIN_FREE_FLOAT = 0.10
MAX_NET_DEBT_EBITDA = 4.0
MIN_INTEREST_COVERAGE = 2.0
MIN_SPOT_VOLUME_USD = 1_000_000.0
MIN_DEPTH_USD = 50_000.0
MIN_FLOAT_CONFIDENCE = 0.5

# Отрасли, к которым долговые метрики не применяются.
FINANCIAL_SECTORS = frozenset({"banks", "insurance", "financials"})


@dataclass
class GateResult:
    status: GateStatus = GateStatus.PASS
    hard_blocks: list[str] = field(default_factory=list)
    soft_risks: list[str] = field(default_factory=list)
    missing_required_data: list[str] = field(default_factory=list)
    liquidity_state: Liquidity = Liquidity.UNKNOWN
    valuation_state: Valuation = Valuation.NOT_ASSESSED
    disclosure_state: Disclosure = Disclosure.SUFFICIENT

    @property
    def allows_diligence(self) -> bool:
        return self.status is GateStatus.PASS

    def as_dict(self) -> dict:
        return {
            "status": str(self.status),
            "hard_blocks": list(self.hard_blocks),
            "soft_risks": list(self.soft_risks),
            "missing_required_data": list(self.missing_required_data),
            "liquidity_state": str(self.liquidity_state),
            "valuation_state": str(self.valuation_state),
            "disclosure_state": str(self.disclosure_state),
        }


def _finish(result: GateResult) -> GateResult:
    """Свести проверки в статус.

    Порядок приоритета: жёсткая блокировка сильнее нехватки данных, потому
    что подключение источника её не снимет. Нехватка данных сильнее мягкого
    риска: неизвестное нельзя взвесить.
    """
    if result.hard_blocks:
        result.status = GateStatus.BLOCKED_BY_RISK
    elif result.missing_required_data:
        result.status = GateStatus.BLOCKED_BY_DATA
    elif result.soft_risks:
        result.status = GateStatus.RESEARCH_ONLY
    else:
        result.status = GateStatus.PASS
    return result


def check_equity(facts: EquityFacts) -> GateResult:
    """Пригодность российской бумаги к углублённому анализу."""
    result = GateResult()

    if not facts.entity_resolved:
        result.hard_blocks.append("сущность не сопоставлена однозначно")
    if not facts.exposure_proven:
        result.hard_blocks.append("материальная связь эмитента с драйвером не доказана")
    if facts.suspended:
        result.hard_blocks.append("торги приостановлены")
    if not facts.market_data_route_legal:
        # §13.10: нет законного маршрута к рыночным данным — исполнимость
        # оценить нечем, и это именно жёсткая блокировка.
        result.hard_blocks.append(
            "нет законного маршрута к рыночным данным: исполнимость не оценить"
        )

    # ── Ликвидность ──────────────────────────────────────────────────────
    if facts.median_turnover_rub is None:
        result.liquidity_state = Liquidity.UNKNOWN
        result.missing_required_data.append("медианный оборот не измерен")
    elif facts.median_turnover_rub < MIN_TURNOVER_RUB * 0.5:
        result.liquidity_state = Liquidity.INSUFFICIENT
        result.hard_blocks.append(
            f"оборот {facts.median_turnover_rub:,.0f} ₽ ниже порога исполнимости"
        )
    elif facts.median_turnover_rub < MIN_TURNOVER_RUB:
        result.liquidity_state = Liquidity.MARGINAL
        result.soft_risks.append("оборот на границе исполнимости")
    else:
        result.liquidity_state = Liquidity.SUFFICIENT

    if facts.free_float is not None and facts.free_float < MIN_FREE_FLOAT:
        result.soft_risks.append(f"free float {facts.free_float:.0%}")

    # ── Долговая устойчивость ────────────────────────────────────────────
    #
    # К банкам и страховщикам не применяется: EBITDA у них не тот
    # показатель, и «нет net debt/EBITDA» у банка — не пробел в данных, а
    # свойство отрасли. §13.10 требует отдельного адаптера, а не ложного
    # блока.
    financial = facts.sector.lower() in FINANCIAL_SECTORS
    if not financial:
        if facts.net_debt_to_ebitda is None:
            result.missing_required_data.append("долговая нагрузка не измерена")
        elif facts.net_debt_to_ebitda > MAX_NET_DEBT_EBITDA:
            result.hard_blocks.append(
                f"чистый долг/EBITDA {facts.net_debt_to_ebitda:.1f}"
            )
        if (
            facts.interest_coverage is not None
            and facts.interest_coverage < MIN_INTEREST_COVERAGE
        ):
            result.soft_risks.append(
                f"покрытие процентов {facts.interest_coverage:.1f}"
            )
    if facts.covenant_breach:
        result.hard_blocks.append("нарушение ковенант или реструктуризация")
    if (
        facts.short_term_debt_share is not None
        and facts.short_term_debt_share > 0.5
    ):
        result.soft_risks.append("больше половины долга короткая")

    # ── Раскрытие ────────────────────────────────────────────────────────
    if facts.disclosure_regular is None:
        result.disclosure_state = Disclosure.DEGRADED
        result.missing_required_data.append("регулярность раскрытия не проверена")
    elif not facts.disclosure_regular:
        result.disclosure_state = Disclosure.INSUFFICIENT
        result.hard_blocks.append(
            "раскрытия недостаточно для проверки целевого показателя"
        )
    else:
        result.disclosure_state = Disclosure.SUFFICIENT

    result.valuation_state = facts.valuation_state
    if facts.valuation_state is Valuation.EXPECTATIONS_HEAVY:
        result.soft_risks.append("оценка уже отражает ожидания")
    elif facts.valuation_state is Valuation.NOT_ASSESSED:
        # Не блок: отсутствие лицензированных данных об оценке — известное
        # ограничение, и превращать его в отказ значило бы закрыть контур.
        result.soft_risks.append("оценка не измерена: лицензированных данных нет")

    return _finish(result)


def check_token(facts: TokenFacts) -> GateResult:
    """Пригодность криптоактива к углублённому анализу."""
    result = GateResult()

    if not facts.mapping_resolved:
        result.hard_blocks.append("токен не сопоставлен с контрактом однозначно")
    if facts.critical_exploit:
        result.hard_blocks.append("незакрытый критический инцидент безопасности")
    if facts.admin_key_risk:
        result.soft_risks.append("административные ключи без таймлока")

    # Главная проверка крипты. Протокол может зарабатывать, а токен не
    # получать из этого ничего — тогда это гипотеза о продукте, а не об
    # активе, и торговать по ней нечего.
    if facts.value_capture is None:
        result.missing_required_data.append("механизм value capture не установлен")
    elif not facts.value_capture:
        result.hard_blocks.append(
            "токен не получает экономику протокола: это гипотеза о продукте, "
            "а не об активе"
        )

    if facts.median_spot_volume_usd is None:
        result.liquidity_state = Liquidity.UNKNOWN
        result.missing_required_data.append("объём торгов не измерен")
    elif facts.median_spot_volume_usd < MIN_SPOT_VOLUME_USD:
        result.liquidity_state = Liquidity.INSUFFICIENT
        result.hard_blocks.append("объём торгов ниже порога исполнимости")
    else:
        result.liquidity_state = Liquidity.SUFFICIENT

    if facts.depth_2pct_usd is not None and facts.depth_2pct_usd < MIN_DEPTH_USD:
        result.soft_risks.append("глубина стакана мала")

    if (
        facts.free_float_confidence is not None
        and facts.free_float_confidence < MIN_FLOAT_CONFIDENCE
    ):
        result.soft_risks.append("оборотное предложение известно неточно")

    if facts.unlock_pressure_90d is not None and facts.unlock_pressure_90d > 0.15:
        result.soft_risks.append(
            f"разблокировки за 90 дней — {facts.unlock_pressure_90d:.0%} свободного "
            "предложения"
        )
    if facts.holder_concentration is not None and facts.holder_concentration > 0.5:
        result.soft_risks.append("концентрация держателей высокая")
    if (
        facts.treasury_runway_months is not None
        and facts.treasury_runway_months < 12
    ):
        result.soft_risks.append("казначейства хватает меньше чем на год")

    result.valuation_state = (
        Valuation.NOT_ASSESSED
        if facts.fdv_to_holder_revenue is None
        else (
            Valuation.EXPECTATIONS_HEAVY
            if facts.fdv_to_holder_revenue > 50
            else Valuation.REASONABLE
        )
    )
    return _finish(result)


__all__ = [
    "Disclosure",
    "EquityFacts",
    "GateResult",
    "GateStatus",
    "Liquidity",
    "TokenFacts",
    "Valuation",
    "check_equity",
    "check_token",
]
