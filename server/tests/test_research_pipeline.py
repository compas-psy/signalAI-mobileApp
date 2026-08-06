"""Запись гипотез и версии (§14.5).

Главное, что здесь проверяется: пересчёт не плодит копии. Две записи об
одном и том же выглядят как два независимых подтверждения — ровно тот
способ обмануться, от которого защищает правило 3–2–1.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Bar, HypothesisEvidence, Instrument, ResearchHypothesis
from app.models.enums import (
    AssetClass,
    EvidenceRole,
    HypothesisState,
    ResearchDirection,
    Timeframe,
    Venue,
)
from app.research.fusion import Falsifier, SignalInput
from app.research.investability import EquityFacts, Valuation, check_equity
from app.research.market_context import for_hypothesis
from app.research.pipeline import _changed, expire, latest_version, run, store
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


def version_pair(score: str, state: str = "aligned_watch"):
    previous = SimpleNamespace(
        state=HypothesisState.EARLY_CANDIDATE,
        evidence_score=D("0.50"),
        economic_score=D("0.60"),
        three_two_one_json={"unique_lineage_roots": 1},
        market_context_score=D("0.50"),
        market_context_state="aligned_watch",
    )
    current = SimpleNamespace(
        state=HypothesisState.EARLY_CANDIDATE,
        evidence=SimpleNamespace(value=D("0.50"), unique_lineage_roots=1),
        economic=D("0.60"),
        market_context_score=D(score),
        market_context_state=state,
    )
    return previous, current


def test_market_context_version_step_filters_daily_score_noise():
    previous, noise = version_pair("0.54")
    _, material = version_pair("0.55")

    assert _changed(previous, noise) is False
    assert _changed(previous, material) is True


def test_market_context_state_change_is_always_material():
    previous, current = version_pair("0.50", state="extended")
    assert _changed(previous, current) is True


def test_прогон_записывает_гипотезу(session: Session):
    report = run(session, THREE, resolve=resolver(), now=NOW)

    assert report.signals == 3
    assert report.groups == 1
    assert report.created == 1
    rows = list(session.execute(select(ResearchHypothesis)).scalars())
    assert len(rows) == 1
    assert rows[0].version == 1
    assert rows[0].state == HypothesisState.DILIGENCE_READY
    assert rows[0].market_context_score == D("0.5")
    assert rows[0].market_context_state == "measured"


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
    # Поток D1-данных пропал — контекст отсутствует, состояние понижается.
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


def test_малый_шум_market_context_не_поднимает_версию(session: Session):
    run(
        session,
        THREE,
        resolve=resolver(
            market_context=D("0.50"),
            market_context_state="aligned_watch",
        ),
        now=NOW,
    )
    report = run(
        session,
        THREE,
        resolve=resolver(
            market_context=D("0.53"),
            market_context_state="aligned_watch",
        ),
        now=NOW + timedelta(days=1),
    )

    assert report.unchanged == 1
    assert len(list(session.execute(select(ResearchHypothesis)).scalars())) == 1


def test_материальный_market_context_поднимает_версию(session: Session):
    run(
        session,
        THREE,
        resolve=resolver(
            market_context=D("0.50"),
            market_context_state="aligned_watch",
        ),
        now=NOW,
    )
    report = run(
        session,
        THREE,
        resolve=resolver(
            market_context=D("0.55"),
            market_context_state="aligned_watch",
        ),
        now=NOW + timedelta(days=1),
    )

    assert report.updated == 1
    rows = list(session.execute(select(ResearchHypothesis)).scalars())
    assert sorted(row.version for row in rows) == [1, 2]


def test_closed_d1_overlay_reaches_pipeline_storage(session: Session):
    instrument = Instrument(
        instrument_id="MOEX:EQ:TEST",
        venue=Venue.MOEX,
        asset_class=AssetClass.EQUITY,
        symbol="TEST",
        title="Тестовая акция",
        tick_size=D("0.01"),
        tick_value=D("0.01"),
        quantity_step=D(1),
        min_quantity=D(1),
        in_universe=True,
    )
    session.add(instrument)
    # Bar ссылается на инструмент только строковым ключом. Без relationship
    # unit of work не обязан поставить INSERT родителя раньше ста свечей.
    session.flush()
    closes = [D(100) + D("0.1") * index for index in range(99)] + [D("110.4")]
    for index, close in enumerate(closes):
        previous = closes[max(0, index - 1)]
        session.add(
            Bar(
                instrument_id=instrument.instrument_id,
                timeframe=Timeframe.D1,
                open_time=NOW - timedelta(days=len(closes) - index),
                open=previous,
                high=max(previous, close) + D("0.4"),
                low=min(previous, close) - D("0.4"),
                close=close,
                volume_units=D(200) if index == len(closes) - 1 else D(100),
                is_closed=True,
                source="test",
            )
        )
    session.flush()

    def with_market(bucket):
        base = resolver()(bucket)
        snapshot = for_hypothesis(
            session,
            instrument_id=bucket[0].instrument_id,
            direction=bucket[0].direction,
        )
        return {
            **base,
            "market_context": snapshot.score,
            "market_context_state": snapshot.state,
            "market_context_detail": snapshot.detail,
        }

    run(session, THREE, resolve=with_market, now=NOW)
    row = session.execute(select(ResearchHypothesis)).scalars().one()

    assert row.market_context_score is not None
    assert row.market_context_state == "aligned_entry"
    assert row.fact_summary_json[0]["role"] == "research_timing_overlay"
    assert row.fact_summary_json[0]["not_a_trade_signal"] is True

    replay = run(
        session,
        THREE,
        resolve=with_market,
        now=NOW + timedelta(hours=1),
    )
    assert replay.unchanged == 1
    assert session.scalar(select(func.count()).select_from(ResearchHypothesis)) == 1


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
