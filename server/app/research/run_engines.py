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
from .confirmations import Reading, count as count_confirmations, history_verdict
from .engines import demand
from .fusion import Falsifier, SignalInput
from .issuers import Issuer, automatic, in_section, of
from .market_context import MarketContext, for_hypothesis
from .pipeline import run as run_pipeline

#: Периодичность мониторинга предприятий. Общий порог в пять периодов был
#: одинаково неверен для всех рядов: месячному мало, годовому много.
#: Требование считается из периодичности — сезонный лаг плюс подтверждения.
DEMAND_FREQUENCY = "monthly"

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


def _resolve(bucket: list[SignalInput], readings: list[Reading] | None = None) -> dict:
    """Что движки не знают: уверенность в сущности и чем это опровергнуть.

    ``confirmations`` считается по различным экономическим периодам, а не
    по числу прогонов. Раньше здесь стояла единица, и любая её замена на
    «сколько раз мы это видели» превратила бы повторный сбор в
    подтверждение — система подтверждала бы гипотезы своей активностью.
    """
    issuer = of(bucket[0].entity_id)
    confidence = issuer.confidence if issuer else Decimal(0)
    return {
        "confirmations": count_confirmations(readings or []).periods,
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


def _critic(fused, bucket: list[SignalInput]):
    """Проверить гипотезу моделью перед подтверждением.

    Документ собирается из того, что гипотеза о себе утверждает: причинная
    цепочка, показатели, опровержения. Отправлять модели сырые наблюдения
    незачем — проверяется рассуждение, а не данные, и числа всё равно
    сверяются вычислительно после ответа.

    Ненастроенная модель — не ошибка конфигурации, а нормальное состояние
    до того, как владелец её включит. Отсутствие критика поднимается
    наверх исключением: молчание не должно выглядеть как одобрение.
    """
    from . import critic as critic_module

    строки = [f"Гипотеза: {fused.title}", f"Причинная цепочка: {fused.causal_path}"]
    строки += [f"Сигнал {s.strategy_key}: {s.detail}" for s in bucket]
    строки += [f"Опровержение: {f.get('description', '')}" for f in fused.falsifiers]
    claims = [
        critic_module.Claim(claim_id=s.strategy_key, text=s.detail)
        for s in bucket
        if s.detail
    ]

    verdict, _report, _exchange = critic_module.review(
        hypothesis=fused.title,
        document="\n".join(строки),
        claims=claims,
    )
    return verdict


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
    readings_by_section: dict[str, list[Reading]] = {}
    for section, bucket in by_section.items():
        periods = demand_periods(bucket)
        enough, why = history_verdict(len(periods), DEMAND_FREQUENCY)
        if not enough:
            report.skipped.append(f"раздел {section}: {why}")
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
        readings_by_section[section] = [
            Reading(
                fingerprint=f"demand:{section}",
                period_end=row.period_end,
                direction=result.direction,
                strength=result.strength,
                revision=row.revision_number,
            )
            for row in bucket
            if row.period_end is not None
        ]
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

    # Показания по периодам собираются один раз и раздаются по разделам:
    # подтверждение — это тот же сигнал в другом экономическом периоде.
    market_snapshots: dict[tuple[str, ResearchDirection], MarketContext] = {}

    def resolve(bucket: list[SignalInput]) -> dict:
        issuer_section = getattr(of(bucket[0].entity_id), "section", "")
        head = bucket[0]
        key = (head.instrument_id, head.direction)
        snapshot = market_snapshots.get(key)
        if snapshot is None:
            snapshot = for_hypothesis(
                session,
                instrument_id=head.instrument_id,
                direction=head.direction,
            )
            market_snapshots[key] = snapshot
        return {
            **_resolve(bucket, readings_by_section.get(issuer_section, [])),
            "market_context": snapshot.score,
            "market_context_state": snapshot.state,
            "market_context_detail": snapshot.detail,
        }

    outcome = run_pipeline(
        session, signals, resolve=resolve, critic=_critic, now=moment
    )
    report.hypotheses = outcome.created + outcome.updated
    return report


__all__ = [
    "DEMAND_FREQUENCY",
    "DEMAND_PREFIX",
    "EngineReport",
    "demand_periods",
    "run_demand",
]
