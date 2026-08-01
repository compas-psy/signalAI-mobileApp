"""От наблюдений к гипотезам (§12–§14).

Последнее недостающее звено. Наблюдения собираются, движки написаны,
объединение работает — но между ними ничего не было, и таблица гипотез
оставалась пустой при исправном сборе.

Сегодня здесь один движок: конечный спрос. Не потому, что остальные хуже,
а потому, что он единственный, у которого покрытие полное — мониторинг
предприятий Банка России закрывает его целиком. Писать переходники для
восьми движков, которым нечего есть, значит получить восемь мест, где
ошибка не проявится до подключения источника.

Про отраслевое наблюдение и эмитента. Спрос измерен по разделу ОКВЭД, а
гипотеза адресуется бумаге. Связь берётся из реестра эмитентов, и её
уверенность идёт в шлюз пригодности как есть: у эмитента без проверенного
ИНН она ниже, и это должно быть видно в гипотезе, а не сглажено.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ResearchObservation
from ..models.enums import ResearchDirection
from .engines import demand
from .fusion import Falsifier, SignalInput
from .issuers import Issuer, automatic, in_section
from .pipeline import run as run_pipeline

#: Сколько периодов нужно движку спроса, чтобы было с чем сравнивать.
MIN_PERIODS = 5

#: Наблюдения мониторинга предприятий. Префикс, а не точное имя: у ЦБ в
#: одном наборе несколько показателей, и все они про спрос.
DEMAND_PREFIX = "cbr:enterprise_monitoring:"


@dataclass
class EngineReport:
    """Что вышло из прохода по движкам."""

    observations: int = 0
    sections: int = 0
    signals: int = 0
    hypotheses: int = 0
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"наблюдений {self.observations}",
            f"разделов {self.sections}",
            f"сигналов {self.signals}",
            f"гипотез {self.hypotheses}",
        ]
        if self.skipped:
            parts.append(f"пропущено {len(self.skipped)}: {self.skipped[0]}")
        return ", ".join(parts)


def demand_periods(rows: list[ResearchObservation]) -> list[demand.DemandPeriod]:
    """Свести наблюдения раздела в ряд периодов.

    Порядок хронологический: движок сравнивает последний период с тем же
    периодом годом ранее, и перепутанный порядок даст рост вместо падения
    без единой ошибки в расчёте.
    """
    by_period: dict[str, Decimal] = {}
    for row in sorted(rows, key=lambda r: (r.period_end or r.first_seen_at.date())):
        if row.value_numeric is None or row.period_end is None:
            continue
        by_period[row.period_end.isoformat()] = row.value_numeric
    return [
        demand.DemandPeriod(period=period, nominal_spend=value)
        for period, value in by_period.items()
    ]


def _signal(issuer: Issuer, result: demand.DemandResult) -> SignalInput:
    """Отраслевой результат — сигнал по конкретной бумаге."""
    direction = (
        ResearchDirection.POSITIVE
        if result.direction == "positive"
        else ResearchDirection.NEGATIVE
        if result.direction == "negative"
        else ResearchDirection.NEUTRAL
    )
    return SignalInput(
        strategy_key=demand.STRATEGY_KEY,
        entity_id=issuer.secid,
        instrument_id=f"MOEX:EQ:{issuer.secid}",
        direction=direction,
        strength=result.strength,
        target_kpi_family="revenue",
        causal_driver="final_demand",
        # Спрос проявляется в выручке не мгновенно: квартал на признание,
        # год на полный эффект. Числа из §13.2, а не подобранные.
        window_from_days=90,
        window_to_days=365,
        reason_codes=tuple(result.reason_codes),
        detail=(
            f"{issuer.name}, {issuer.sector_name}: {result.detail}"
            if result.detail
            else f"{issuer.name}, {issuer.sector_name}"
        ),
    )


def _resolve(bucket: list[SignalInput]) -> dict:
    """Что движки не знают: уверенность в сущности и чем это опровергнуть."""
    from .issuers import of

    issuer = of(bucket[0].entity_id)
    confidence = issuer.confidence if issuer else Decimal(0)
    return {
        "confirmations": 1,
        "entity_confidence": confidence,
        "effect_size": float(max(abs(s.strength) for s in bucket)),
        # Отраслевое наблюдение относится к эмитенту тем хуже, чем меньше
        # он похож на среднюю компанию раздела. Ставить сюда единицу
        # значило бы обещать, что спрос по разделу — это спрос эмитента.
        "exposure_confidence": float(confidence) * 0.6,
        "falsifiers": [
            Falsifier(
                description=(
                    "спрос в разделе снижается два квартала подряд — "
                    "ожидание роста выручки не подтверждается"
                ),
                metric_or_event="cbr_enterprise_demand",
                operator="<",
                threshold=0.0,
                check_frequency="P1M",
            )
        ],
    }


def run_demand(session: Session, *, now: datetime | None = None) -> EngineReport:
    """Прогнать движок спроса по собранным наблюдениям."""
    moment = now or datetime.now(UTC)
    report = EngineReport()

    rows = list(
        session.execute(
            select(ResearchObservation).where(
                ResearchObservation.observation_type.like(f"{DEMAND_PREFIX}%")
            )
        ).scalars()
    )
    report.observations = len(rows)
    if not rows:
        report.skipped.append("наблюдений мониторинга предприятий нет")
        return report

    by_section: dict[str, list[ResearchObservation]] = {}
    for row in rows:
        by_section.setdefault(row.entity_id, []).append(row)
    report.sections = len(by_section)

    signals: list[SignalInput] = []
    for section, bucket in by_section.items():
        periods = demand_periods(bucket)
        if len(periods) < MIN_PERIODS:
            report.skipped.append(
                f"раздел {section}: периодов {len(periods)} из {MIN_PERIODS}"
            )
            continue
        result = demand.evaluate(periods)
        if not result.applicable:
            report.skipped.append(f"раздел {section}: {result.detail or 'неприменим'}")
            continue
        issuers = in_section(section)
        if not issuers:
            # Раздел без бумаг — не ошибка: ЦБ измеряет всю экономику, а
            # торгуется её часть. Но и гипотезы из него не выйдет.
            report.skipped.append(f"раздел {section}: эмитентов в реестре нет")
            continue
        for issuer in issuers:
            if not automatic(issuer):
                report.skipped.append(
                    f"{issuer.secid}: уверенность привязки ниже порога"
                )
                continue
            signals.append(_signal(issuer, result))

    report.signals = len(signals)
    if not signals:
        return report

    outcome = run_pipeline(session, signals, resolve=_resolve, now=moment)
    report.hypotheses = outcome.created + outcome.updated
    return report


__all__ = ["DEMAND_PREFIX", "EngineReport", "demand_periods", "run_demand"]
