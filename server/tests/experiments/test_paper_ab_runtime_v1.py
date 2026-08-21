from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import DBAPIError

from app.experiments.paper_ab_runtime_v1 import (
    PaperAbControlDecision,
    build_persisted_rolling_report,
    resolve_paper_ab,
    seed_paper_ab,
)
from app.experiments.paper_ab_v1 import PaperAbEvidenceStatus
from app.models import (
    Bar,
    ExecutionIntent,
    Instrument,
    NotificationOutbox,
    PaperAbDecision,
    PaperAbOutcome,
    PaperTrade,
    ShadowObservation,
    TradeIdea,
)
from app.models.enums import AssetClass, Direction, Timeframe, Venue


AT = datetime(2026, 8, 21, 4, 0, tzinfo=UTC)
MARKET = "a" * 64
COST = "b" * 64


def _instrument(session, *, with_cost: bool = True) -> Instrument:
    metadata = {}
    if with_cost:
        metadata = {
            "shadow_cost_model": {
                "cost_model_hash": COST,
                "round_trip_cost_bps": "10",
            }
        }
    row = Instrument(
        instrument_id="CRYPTO:PERP:BTCUSDT",
        symbol="BTCUSDT",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        currency="USDT",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("5"),
        contract_multiplier=Decimal("1"),
        is_tradable=True,
        in_universe=True,
        metadata_json=metadata,
    )
    session.add(row)
    session.flush()
    return row


def _shadow(
    session,
    *,
    emitted: bool,
    version: str = "momentum_v2",
    evidence_status: str = "EVALUATED",
    reason: str | None = None,
) -> ShadowObservation:
    row = ShadowObservation(
        observation_key=("c" if version == "momentum_v2" else "d") * 64,
        opportunity_key="e" * 64,
        stage="SHADOW",
        instrument_id="CRYPTO:PERP:BTCUSDT",
        venue="CRYPTO",
        strategy_family="MOMENTUM" if version == "momentum_v2" else "CRYPTO_CARRY",
        strategy_version=version,
        evidence_status=evidence_status,
        reason_code=reason,
        signal_emitted=emitted,
        direction=Direction.LONG.value if emitted else None,
        raw_edge_score=Decimal("0.7") if emitted else None,
        entry_reference=Decimal("100") if emitted else None,
        data_quality_state="GOOD" if emitted else None,
        evaluated_at=AT,
        market_snapshot_hash=MARKET,
        cost_model_hash=COST,
    )
    session.add(row)
    session.flush()
    return row


def _control_none(_session, _instrument, _at) -> PaperAbControlDecision:
    return PaperAbControlDecision(signal_emitted=False)


def _control_long(_session, _instrument, _at) -> PaperAbControlDecision:
    return PaperAbControlDecision(
        signal_emitted=True,
        direction=Direction.LONG,
        entry_reference=Decimal("100"),
        confidence=Decimal("0.6"),
    )


def _risk_unit(_session, _instrument_id: str, _at: datetime) -> Decimal:
    return Decimal("2")


def _regime(_session, _instrument, _at) -> str:
    return "TREND|NORMAL|GOOD"


def _exit_bar(session, at: datetime, close: str) -> None:
    price = Decimal(close)
    session.add(
        Bar(
            instrument_id="CRYPTO:PERP:BTCUSDT",
            timeframe=Timeframe.H1,
            open_time=at,
            open=price,
            high=price + Decimal("1"),
            low=price - Decimal("1"),
            close=price,
            volume_units=Decimal("1000"),
            volume_notional=Decimal("100000"),
            open_interest=Decimal("10000"),
            is_closed=True,
            source="fixture",
            quality_flags=[],
        )
    )
    session.flush()


def test_seed_paper_ab_creates_two_isolated_no_signal_decisions_and_is_idempotent(session) -> None:
    _instrument(session)
    _shadow(session, emitted=False)
    before = {
        "ideas": session.query(TradeIdea).count(),
        "owner_paper": session.query(PaperTrade).count(),
        "notifications": session.query(NotificationOutbox).count(),
        "execution": session.query(ExecutionIntent).count(),
    }

    first = seed_paper_ab(
        session,
        evaluated_at=AT,
        control_provider=_control_none,
        risk_unit_provider=_risk_unit,
        regime_provider=_regime,
    )
    session.flush()
    second = seed_paper_ab(
        session,
        evaluated_at=AT,
        control_provider=_control_none,
        risk_unit_provider=_risk_unit,
        regime_provider=_regime,
    )
    session.flush()

    assert first.seeded_pairs == 1
    assert second.seeded_pairs == 0
    assert session.query(PaperAbDecision).count() == 2
    assert session.query(PaperAbOutcome).count() == 2
    assert {row.arm_role for row in session.query(PaperAbDecision).all()} == {
        "CONTROL",
        "CANDIDATE",
    }
    assert all(
        row.evidence_status == PaperAbEvidenceStatus.EVALUATED.value
        for row in session.query(PaperAbOutcome).all()
    )
    after = {
        "ideas": session.query(TradeIdea).count(),
        "owner_paper": session.query(PaperTrade).count(),
        "notifications": session.query(NotificationOutbox).count(),
        "execution": session.query(ExecutionIntent).count(),
    }
    assert after == before


def test_shadow_input_unavailable_is_preserved_not_converted_to_zero_return(session) -> None:
    _instrument(session)
    _shadow(
        session,
        emitted=False,
        evidence_status="INPUT_UNAVAILABLE",
        reason="FUNDING_FACTS_UNAVAILABLE",
    )

    seed_paper_ab(
        session,
        evaluated_at=AT,
        control_provider=_control_none,
        risk_unit_provider=_risk_unit,
        regime_provider=_regime,
    )
    session.flush()

    candidate = session.query(PaperAbDecision).filter_by(arm_role="CANDIDATE").one()
    outcome = session.query(PaperAbOutcome).filter_by(decision_id=candidate.id).one()
    assert outcome.evidence_status == PaperAbEvidenceStatus.INPUT_UNAVAILABLE.value
    assert outcome.reason_code == "FUNDING_FACTS_UNAVAILABLE"
    assert outcome.net_r is None


def test_emitted_arms_stay_pending_until_horizon_then_resolve_net_of_costs(session) -> None:
    _instrument(session)
    _shadow(session, emitted=True)

    seed_paper_ab(
        session,
        evaluated_at=AT,
        control_provider=_control_long,
        risk_unit_provider=_risk_unit,
        regime_provider=_regime,
    )
    session.flush()
    assert session.query(PaperAbDecision).count() == 2
    assert session.query(PaperAbOutcome).count() == 0

    before = resolve_paper_ab(session, as_of=AT + timedelta(hours=23))
    session.flush()
    assert before.resolved == 0
    assert before.pending == 2

    maturity = AT + timedelta(hours=24)
    _exit_bar(session, maturity, "104")
    after = resolve_paper_ab(session, as_of=maturity)
    session.flush()
    assert after.resolved == 2
    assert after.pending == 0

    outcomes = session.query(PaperAbOutcome).all()
    assert len(outcomes) == 2
    # gross R = (104-100)/2 = 2.0; cost R = (100*10bps)/2 = 0.05.
    assert {row.net_r for row in outcomes} == {Decimal("1.95")}
    again = resolve_paper_ab(session, as_of=maturity + timedelta(hours=1))
    session.flush()
    assert again.resolved == 0
    assert session.query(PaperAbOutcome).count() == 2


def test_missing_cost_fact_for_emitted_arm_fails_closed_without_fake_pnl(session) -> None:
    _instrument(session, with_cost=False)
    _shadow(session, emitted=True)

    seed_paper_ab(
        session,
        evaluated_at=AT,
        control_provider=_control_long,
        risk_unit_provider=_risk_unit,
        regime_provider=_regime,
    )
    session.flush()

    outcomes = session.query(PaperAbOutcome).all()
    assert len(outcomes) == 2
    assert all(
        row.evidence_status == PaperAbEvidenceStatus.INPUT_UNAVAILABLE.value
        for row in outcomes
    )
    assert all(row.reason_code == "ROUND_TRIP_COST_UNAVAILABLE" for row in outcomes)
    assert all(row.net_r is None for row in outcomes)


def test_emitted_crypto_carry_never_uses_price_only_pnl_as_realized_carry(session) -> None:
    _instrument(session)
    _shadow(session, emitted=True, version="crypto_carry_v1")

    seed_paper_ab(
        session,
        evaluated_at=AT,
        control_provider=_control_none,
        risk_unit_provider=_risk_unit,
        regime_provider=_regime,
    )
    session.flush()

    candidate = session.query(PaperAbDecision).filter_by(arm_role="CANDIDATE").one()
    outcome = session.query(PaperAbOutcome).filter_by(decision_id=candidate.id).one()
    assert outcome.evidence_status == PaperAbEvidenceStatus.INPUT_UNAVAILABLE.value
    assert outcome.reason_code == "CARRY_REALIZED_FUNDING_PATH_UNAVAILABLE"
    assert outcome.net_r is None


def test_persisted_rolling_report_uses_outcomes_and_keeps_measure_only(session) -> None:
    _instrument(session)
    _shadow(session, emitted=False)
    seed_paper_ab(
        session,
        evaluated_at=AT,
        control_provider=_control_none,
        risk_unit_provider=_risk_unit,
        regime_provider=_regime,
    )
    session.flush()

    report = build_persisted_rolling_report(
        session,
        candidate_version="momentum_v2",
        as_of=AT + timedelta(hours=1),
        window=timedelta(days=7),
        min_sample=1,
    )

    assert report.total_pairs == 1
    assert report.total_usable_pairs == 1
    assert report.recommendation == "MEASURE_ONLY"
    assert report.segments[0].evaluation is not None


def test_paper_ab_decisions_and_outcomes_are_append_only_at_database_layer(session) -> None:
    _instrument(session)
    _shadow(session, emitted=False)
    seed_paper_ab(
        session,
        evaluated_at=AT,
        control_provider=_control_none,
        risk_unit_provider=_risk_unit,
        regime_provider=_regime,
    )
    session.flush()
    decision = session.query(PaperAbDecision).first()
    outcome = session.query(PaperAbOutcome).first()

    with pytest.raises(DBAPIError):
        with session.begin_nested():
            decision.strategy_version = "tampered_v9"
            session.flush()
    session.refresh(decision)

    with pytest.raises(DBAPIError):
        with session.begin_nested():
            outcome.reason_code = "tampered"
            session.flush()
    session.refresh(outcome)

    with pytest.raises(DBAPIError):
        with session.begin_nested():
            session.delete(decision)
            session.flush()


def test_paper_ab_tables_have_no_owner_trade_or_execution_foreign_keys() -> None:
    decision_targets = {
        fk.target_fullname for fk in PaperAbDecision.__table__.foreign_keys
    }
    outcome_targets = {
        fk.target_fullname for fk in PaperAbOutcome.__table__.foreign_keys
    }
    assert not any("trade_ideas" in target for target in decision_targets | outcome_targets)
    assert not any("paper_trades" in target for target in decision_targets | outcome_targets)
    assert not any("execution" in target for target in decision_targets | outcome_targets)
    assert outcome_targets == {"paper_ab_decisions.id"}


def test_default_control_replay_fails_closed_if_newer_closed_bar_already_exists(session) -> None:
    _instrument(session)
    _shadow(session, emitted=False)
    _exit_bar(session, AT + timedelta(hours=1), "999")

    seed_paper_ab(session, evaluated_at=AT)
    session.flush()

    control = session.query(PaperAbDecision).filter_by(arm_role="CONTROL").one()
    outcome = session.query(PaperAbOutcome).filter_by(decision_id=control.id).one()
    assert outcome.evidence_status == PaperAbEvidenceStatus.INPUT_UNAVAILABLE.value
    assert outcome.reason_code == "CONTROL_REPLAY_NOT_POINT_IN_TIME"
    assert outcome.net_r is None
