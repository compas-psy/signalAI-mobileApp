from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import ResearchHypothesis
from app.models.enums import HypothesisState, ResearchDirection
from app.portfolio.research_evidence import MAX_RESEARCH_ADJUSTMENT, evidence_for

D = Decimal
NOW = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)
INSTRUMENT = "MOEX:EQ:TEST"


def _hypothesis(
    session,
    *,
    fingerprint: str,
    version: int = 1,
    state: HypothesisState = HypothesisState.CONFIRMED,
    direction: ResearchDirection = ResearchDirection.POSITIVE,
    evidence: str = "0.80",
    economic: str = "0.80",
    as_of: datetime = NOW - timedelta(days=1),
    expires_at: datetime | None = NOW + timedelta(days=30),
    instrument_id: str = INSTRUMENT,
) -> ResearchHypothesis:
    row = ResearchHypothesis(
        version=version,
        fingerprint=fingerprint,
        market="MOEX",
        entity_id=f"issuer-{instrument_id}",
        instrument_id=instrument_id,
        title=f"{fingerprint} v{version}",
        direction=direction,
        state=state,
        as_of=as_of,
        evidence_score=D(evidence),
        economic_score=D(economic),
        research_priority=D("0.5"),
        expires_at=expires_at,
        config_hash="a" * 64,
        engine_version="test",
    )
    session.add(row)
    session.flush()
    return row


def test_no_research_keeps_fundamental_score_unchanged(session):
    evidence = evidence_for(session, [INSTRUMENT], as_of=NOW)[INSTRUMENT]

    assert evidence.signed_conviction == 0
    assert evidence.research_adjustment == 0
    assert evidence.adjust(0.61) == 0.61
    assert evidence.hypotheses == []


def test_only_mature_states_can_change_automatic_portfolio_score(session):
    _hypothesis(session, fingerprint="confirmed", state=HypothesisState.CONFIRMED)
    _hypothesis(
        session,
        fingerprint="diligence",
        state=HypothesisState.DILIGENCE_READY,
        evidence="0.60",
        economic="0.60",
    )
    _hypothesis(
        session,
        fingerprint="early",
        state=HypothesisState.EARLY_CANDIDATE,
        evidence="1.00",
        economic="1.00",
    )

    evidence = evidence_for(session, [INSTRUMENT], as_of=NOW)[INSTRUMENT]

    assert {item["fingerprint"] for item in evidence.hypotheses} == {
        "confirmed",
        "diligence",
    }
    # confirmed: .8 * .75 = .6; diligence: .6 * 1 = .6; average = .6
    assert evidence.signed_conviction == D("0.600000")
    assert evidence.research_adjustment == D("0.120000")
    assert evidence.adjust(0.50) == 0.62


def test_future_expired_and_terminal_hypotheses_are_ignored(session):
    _hypothesis(
        session,
        fingerprint="future",
        as_of=NOW + timedelta(seconds=1),
    )
    _hypothesis(
        session,
        fingerprint="expired",
        expires_at=NOW,
    )
    _hypothesis(
        session,
        fingerprint="invalid",
        state=HypothesisState.INVALIDATED,
    )
    _hypothesis(
        session,
        fingerprint="rejected",
        state=HypothesisState.REJECTED,
    )

    evidence = evidence_for(session, [INSTRUMENT], as_of=NOW)[INSTRUMENT]

    assert evidence.signed_conviction == 0
    assert evidence.hypotheses == []


def test_latest_usable_version_per_fingerprint_is_used_once(session):
    old = _hypothesis(
        session,
        fingerprint="same-thesis",
        version=1,
        direction=ResearchDirection.POSITIVE,
        evidence="1.00",
        economic="1.00",
    )
    latest = _hypothesis(
        session,
        fingerprint="same-thesis",
        version=2,
        direction=ResearchDirection.NEGATIVE,
        evidence="0.80",
        economic="0.80",
    )

    evidence = evidence_for(session, [INSTRUMENT], as_of=NOW)[INSTRUMENT]

    assert len(evidence.hypotheses) == 1
    item = evidence.hypotheses[0]
    assert item["hypothesis_id"] == str(latest.id)
    assert item["hypothesis_id"] != str(old.id)
    assert item["version"] == 2
    assert evidence.signed_conviction == D("-0.600000")
    assert evidence.research_adjustment == D("-0.120000")


def test_research_adjustment_is_bounded_even_with_many_strong_hypotheses(session):
    for index in range(8):
        _hypothesis(
            session,
            fingerprint=f"strong-{index}",
            state=HypothesisState.DILIGENCE_READY,
            evidence="1.00",
            economic="1.00",
        )

    evidence = evidence_for(session, [INSTRUMENT], as_of=NOW)[INSTRUMENT]

    assert evidence.signed_conviction == D("1.000000")
    assert evidence.research_adjustment == MAX_RESEARCH_ADJUSTMENT
    assert evidence.adjust(0.95) == 1.0


def test_direction_can_penalise_but_never_bypass_score_bounds(session):
    _hypothesis(
        session,
        fingerprint="negative",
        state=HypothesisState.DILIGENCE_READY,
        direction=ResearchDirection.NEGATIVE,
        evidence="1.00",
        economic="1.00",
    )

    evidence = evidence_for(session, [INSTRUMENT], as_of=NOW)[INSTRUMENT]

    assert evidence.research_adjustment == -MAX_RESEARCH_ADJUSTMENT
    assert evidence.adjust(0.10) == 0.0
