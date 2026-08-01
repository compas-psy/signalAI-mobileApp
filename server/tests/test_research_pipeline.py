"""Запись гипотез и версии (§14.5).

Главное, что здесь проверяется: пересчёт не плодит копии. Две записи об
одном и том же выглядят как два независимых подтверждения — ровно тот
способ обмануться, от которого защищает правило 3–2–1.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HypothesisEvidence, ResearchHypothesis
from app.models.enums import EvidenceRole, HypothesisState, ResearchDirection
from app.research.fusion import Falsifier, SignalInput
from app.research.investability import EquityFacts, Valuation, check_equity
from app.research.pipeline import expire, latest_version, run, store
from app.research.scoring import EvidenceItem

D = Decimal
NOW = datetime(2026, 8, 1, tzinfo=UTC)

FALSIFIER = Falsifier(
    description="Исполнение контрактов снизится два месяца подряд",
    metric_or_event="executed_contract_value_30d",
    operator="declines_consecutively",
    threshold=2,
)

GOOD = EquityFacts(
    exposure_proven=True,
    median_turnover_rub=50_000_000,
    net_debt_to_ebitda=1.5,
    disclosure_regular=True,
    valuation_state=Valuation.REASONABLE,
)


def ev(root: str, kind: str, role=EvidenceRole.SUPPORT) -> EvidenceItem:
    return EvidenceItem(
        lineage_root_id=root,
        data_type=kind,
        role=role,
        source_quality=0.9,
        freshness=0.9,
        data_quality=0.9,
    )


def sig(key: str, evidence: tuple[EvidenceItem, ...]) -> SignalInput:
    return SignalInput(
        strategy_key=key,
        entity_id="issuer-1",
        instrument_id="MOEX:EQ:TEST",
        direction=ResearchDirection.POSITIVE,
        strength=D("0.7"),
        target_kpi_family="segment_revenue",
        causal_driver="исполнение контрактов",
        window_from_days=90,
        window_to_days=540,
        evidence=evidence,
        detail=f"{key}: наблюдение",
    )


def resolver(**overrides):
    def resolve(bucket):
        base = dict(
            confirmations=2,
            entity_confidence=D("0.95"),
            effect_size=0.8,
            exposure_confidence=0.8,
            falsifiers=[FALSIFIER],
            market_context=D("0.5"),
            investability=check_equity(GOOD),
        )
        base.update(overrides)
        return base

    return resolve


THREE = [
    sig("BUDGET", (ev("eis", "state"),)),
    sig("SUPPLIER", (ev("fns", "filing"),)),
    sig("HIRING", (ev("trudvsem", "operational"),)),
]


def test_прогон_записывает_гипотезу(session: Session):
    report = run(session, THREE, resolve=resolver(), now=NOW)

    assert report.signals == 3
    assert report.groups == 1
    assert report.created == 1
    rows = list(session.execute(select(ResearchHypothesis)).scalars())
    assert len(rows) == 1
    assert rows[0].version == 1
    assert rows[0].state == HypothesisState.DILIGENCE_READY


def test_повторный_прогон_без_изменений_не_плодит_версий(session: Session):
    """Две записи об одном выглядят как два подтверждения.

    Это ровно тот способ обмануться, от которого защищает 3–2–1.
    """
    run(session, THREE, resolve=resolver(), now=NOW)
    report = run(session, THREE, resolve=resolver(), now=NOW + timedelta(days=1))

    assert report.unchanged == 1
    assert report.created == 0
    assert len(list(session.execute(select(ResearchHypothesis)).scalars())) == 1


def test_изменение_состояния_поднимает_версию_и_сохраняет_прежнюю(
    session: Session,
):
    """На вопрос «почему вы так решили в марте» должно быть чем ответить."""
    run(session, THREE, resolve=resolver(), now=NOW)
    # Рынок узнал новость — контекст пропал, состояние понижается.
    run(
        session,
        THREE,
        resolve=resolver(market_context=None),
        now=NOW + timedelta(days=7),
    )

    rows = sorted(
        session.execute(select(ResearchHypothesis)).scalars(),
        key=lambda r: r.version,
    )
    assert [r.version for r in rows] == [1, 2]
    assert rows[0].state == HypothesisState.DILIGENCE_READY
    assert rows[1].state == HypothesisState.CONFIRMED
    # Отпечаток один: это одна и та же гипотеза, а не две.
    assert rows[0].fingerprint == rows[1].fingerprint


def test_новый_независимый_источник_поднимает_версию(session: Session):
    run(session, THREE[:2], resolve=resolver(), now=NOW)
    report = run(session, THREE, resolve=resolver(), now=NOW + timedelta(days=3))

    assert report.updated == 1
    row = latest_version(session, THREE[0].strategy_key and
                         list(session.execute(select(ResearchHypothesis))
                              .scalars())[0].fingerprint)
    assert row.version == 2


def test_опровергающие_доказательства_пишутся_наравне(session: Session):
    """Гипотеза, из которой вычистили неудобные факты, непроверяема."""
    mixed = [
        sig("BUDGET", (ev("eis", "state"),)),
        sig("SUPPLIER", (ev("fns", "filing"),)),
        sig("HIRING", (ev("ir", "operational", role=EvidenceRole.CONTRADICT),)),
    ]
    run(session, mixed, resolve=resolver(), now=NOW)

    rows = list(session.execute(select(HypothesisEvidence)).scalars())
    assert any(r.role is EvidenceRole.CONTRADICT for r in rows)


def test_разные_истории_дают_разные_гипотезы(session: Session):
    other = SignalInput(
        strategy_key="SPREAD",
        entity_id="issuer-1",
        instrument_id="MOEX:EQ:TEST",
        direction=ResearchDirection.POSITIVE,
        strength=D("0.7"),
        target_kpi_family="gross_margin",
        causal_driver="сырьевой спред",
        window_from_days=30,
        window_to_days=180,
        evidence=(ev("cbr", "state"),),
    )
    report = run(session, [*THREE, other], resolve=resolver(), now=NOW)

    assert report.groups == 2
    assert report.created == 2


def test_пустой_вход_не_ошибка_а_результат(session: Session):
    report = run(session, [], resolve=resolver(), now=NOW)
    assert report.stored == 0
    assert report.notes


def test_протухшая_гипотеза_меняет_состояние_а_не_исчезает(session: Session):
    """Статистика «сколько наших гипотез не подтвердилось» должна остаться."""
    run(session, THREE, resolve=resolver(), now=NOW)
    touched = expire(session, now=NOW + timedelta(days=1000))

    assert touched == 1
    row = list(session.execute(select(ResearchHypothesis)).scalars())[0]
    assert row.state == HypothesisState.EXPIRED
    assert "без подтверждения" in row.state_reason


def test_живая_гипотеза_не_протухает_раньше_срока(session: Session):
    run(session, THREE, resolve=resolver(), now=NOW)
    assert expire(session, now=NOW + timedelta(days=10)) == 0
