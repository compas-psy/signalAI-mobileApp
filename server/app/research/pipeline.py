"""Прогон исследовательского контура: сигналы → гипотезы в базе (§14.5, §17).

Здесь замыкается цепочка. Движки посчитали, объединение собрало гипотезу —
осталось записать её так, чтобы история не потерялась.

Главное правило записи: **новое доказательство поднимает версию, а не
создаёт вторую гипотезу**. Отпечаток замысла считается из сущности,
направления, семейства показателя, драйвера и корзины лага; всё, что
совпало по отпечатку, — это та же гипотеза, просто узнавшая о себе больше.
Две записи об одном и том же выглядят как два независимых подтверждения,
и это ровно тот способ обмануться, от которого защищает правило 3–2–1.

Второе правило: версия поднимается только при изменении по существу.
Пересчёт, давший то же состояние, тот же приоритет и тот же состав
источников, — не событие. История, забитая одинаковыми версиями,
перестаёт быть читаемой, а значит перестаёт работать как доказательство.

Прежняя версия не переписывается никогда. Гипотеза, у которой сняли
блокировку или изменили состояние, обязана сохранить и то, как выглядела
раньше: иначе на вопрос «почему вы так решили в марте» ответить нечем.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import HypothesisEvidence, ResearchHypothesis, ResearchSignal
from ..models.enums import EvidenceRole, HypothesisState, SignalStatus
from ..version import ENGINE_VERSION
from .fusion import Fused, SignalInput, fuse, group


@dataclass
class RunReport:
    """Итог прогона: что посчитали и что из этого записали."""

    signals: int = 0
    groups: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    states: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def stored(self) -> int:
        return self.created + self.updated


def latest_version(
    session: Session, fingerprint: str
) -> ResearchHypothesis | None:
    """Последняя версия гипотезы с этим отпечатком."""
    return session.execute(
        select(ResearchHypothesis)
        .where(ResearchHypothesis.fingerprint == fingerprint)
        .order_by(ResearchHypothesis.version.desc())
        .limit(1)
    ).scalars().first()


def _changed(previous: ResearchHypothesis, current: Fused) -> bool:
    """Изменилось ли что-то по существу.

    Состояние, приоритет и число независимых источников. Всё остальное —
    оттенки формулировки, и поднимать из-за них версию значит превратить
    историю в шум.
    """
    was_roots = (previous.three_two_one_json or {}).get("unique_lineage_roots")
    return (
        str(previous.state) != str(current.state)
        or previous.research_priority != current.priority
        or was_roots != current.evidence.unique_lineage_roots
    )


def _write_evidence(
    session: Session, row: ResearchHypothesis, signals: list[SignalInput]
) -> None:
    """Записать доказательства версии.

    Опровергающие пишутся наравне с подтверждающими: гипотеза, из которой
    вычистили неудобные факты, перестаёт быть проверяемой.
    """
    for signal in signals:
        for item in signal.evidence:
            session.add(
                HypothesisEvidence(
                    hypothesis_id=row.id,
                    role=item.role,
                    lineage_root_id=item.lineage_root_id,
                    independence_group=item.data_type,
                    claim_text=signal.detail[:2000],
                    source_locator={"strategy": signal.strategy_key},
                    confidence=item.source_quality,
                    weight=item.weight,
                )
            )


def store(
    session: Session,
    fused: Fused,
    signals: list[SignalInput],
    *,
    market: str = "russia_equity",
    now: datetime | None = None,
) -> tuple[ResearchHypothesis | None, str]:
    """Записать гипотезу. Возвращает (строку, что произошло).

    Второй элемент — ``created``, ``updated`` или ``unchanged``. Отдельно
    от строки, потому что «ничего не изменилось» это тоже результат
    прогона, а не отсутствие результата.
    """
    moment = now or datetime.now(UTC)
    previous = latest_version(session, fused.fingerprint)

    if previous is not None and not _changed(previous, fused):
        return previous, "unchanged"

    row = ResearchHypothesis(
        version=1 if previous is None else previous.version + 1,
        fingerprint=fused.fingerprint,
        market=market,
        entity_id=fused.entity_id,
        instrument_id=fused.instrument_id,
        title=fused.title,
        direction=fused.direction,
        state=fused.state,
        as_of=moment,
        causal_path_json=fused.causal_path,
        target_kpis_json=fused.target_kpis,
        expected_lag=fused.expected_lag,
        evidence_score=fused.evidence.value,
        economic_score=fused.economic,
        market_context_score=None if fused.market_context_missing else None,
        market_context_state=(
            "unknown" if fused.market_context_missing else "measured"
        ),
        research_priority=fused.priority,
        three_two_one_json=fused.gate.facts,
        investability_json=(
            fused.investability.as_dict() if fused.investability else {}
        ),
        falsifiers_json=fused.falsifiers,
        missing_evidence_json=fused.missing_evidence,
        risk_flags=list(
            fused.investability.soft_risks if fused.investability else []
        ),
        state_reason=fused.state_reason,
        expires_at=fused.expires_at,
        engine_version=ENGINE_VERSION,
    )
    session.add(row)
    session.flush()
    _write_evidence(session, row, signals)
    return row, "created" if previous is None else "updated"


def run(
    session: Session,
    signals: list[SignalInput],
    *,
    resolve,
    critic=None,
    market: str = "russia_equity",
    now: datetime | None = None,
) -> RunReport:
    """Собрать гипотезы из сигналов и записать.

    ``resolve`` — функция, отдающая по группе сигналов всё, чего движки не
    знают: уверенность в сущности, размер эффекта, опровержения и результат
    шлюза пригодности. Она вынесена наружу намеренно: движки не должны
    знать ни о базе, ни о правовом режиме источников.
    """
    report = RunReport(signals=len(signals))
    if not signals:
        report.notes.append("сигналов нет: движкам нечего объединять")
        return report

    groups = group(signals)
    report.groups = len(groups)
    for bucket in groups:
        context = resolve(bucket)
        fused = fuse(bucket, now=now, **context)
        if critic is not None:
            _apply_critic(critic, fused, bucket, report)
        row, outcome = store(session, fused, bucket, market=market, now=now)
        setattr(report, outcome, getattr(report, outcome) + 1)
        key = str(fused.state)
        report.states[key] = report.states.get(key, 0) + 1
    return report


def _apply_critic(critic, fused, bucket, report: RunReport) -> None:
    """Спросить критика перед подтверждением — и только перед ним.

    Ниже подтверждённой гипотеза ничего не обещает, и тратить на неё
    вызов модели незачем. Выше — обещает достаточно, чтобы проверка
    окупалась.

    Критик не меняет состояние сам: §15.1 это прямо запрещает, и здесь
    решение принимает объединение. Он называет причину, а вниз гипотезу
    опускает эта функция — и обязательно с текстом причины, иначе на
    экране появится необъяснимое понижение.
    """
    if fused.state not in (HypothesisState.CONFIRMED, HypothesisState.DILIGENCE_READY):
        return
    try:
        verdict = critic(fused, bucket)
    except Exception as error:  # noqa: BLE001 — недоступная модель не роняет прогон
        # Молчание модели не должно подтверждать гипотезу: недоступный
        # критик — это отсутствие проверки, а не её успешное прохождение.
        fused.state = HypothesisState.EARLY_CANDIDATE
        fused.state_reason = (
            f"критик недоступен ({type(error).__name__}) — подтверждение "
            "отложено до проверки"
        )
        report.notes.append(f"критик недоступен: {error}")
        return
    if verdict is None or not verdict.blocks_confirmation:
        return
    fused.state = HypothesisState.EARLY_CANDIDATE
    fused.state_reason = verdict.summary or "критик не подтвердил гипотезу"
    report.notes.append(f"критик отклонил подтверждение: {fused.state_reason}")


def expire(
    session: Session, *, now: datetime | None = None
) -> int:
    """Пометить протухшие гипотезы.

    Гипотеза без срока живёт вечно и превращается в кладбище решений.
    Истечение — не удаление: запись остаётся, меняется состояние, и
    статистика «сколько наших гипотез не подтвердилось» продолжает
    существовать.
    """
    moment = now or datetime.now(UTC)
    live = [
        HypothesisState.OBSERVATION.value,
        HypothesisState.EARLY_CANDIDATE.value,
        HypothesisState.CONFIRMED.value,
        HypothesisState.DILIGENCE_READY.value,
    ]
    rows = list(
        session.execute(
            select(ResearchHypothesis).where(
                ResearchHypothesis.state.in_(live),
                ResearchHypothesis.expires_at.is_not(None),
            )
        ).scalars()
    )
    touched = 0
    for row in rows:
        deadline = row.expires_at
        if deadline is None:
            continue
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        if moment < deadline:
            continue
        row.state = HypothesisState.EXPIRED
        row.state_reason = (
            f"окно эффекта закончилось {deadline:%d.%m.%Y} без подтверждения"
        )
        touched += 1
    session.flush()
    return touched


__all__ = ["RunReport", "expire", "latest_version", "run", "store"]
