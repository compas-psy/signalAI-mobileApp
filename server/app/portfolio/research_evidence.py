"""Bounded, as-of-safe research overlay for investment screening.

Research hypotheses are not orders and never receive portfolio weights directly.
This adapter only answers a narrower question for the existing portfolio
screen: does mature long-horizon research strengthen or weaken the case for an
equity that already passed the investment universe/fundamental gates?

The overlay is deliberately bounded. Portfolio class caps, optimiser
constraints and walk-forward admission remain authoritative downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import ResearchHypothesis
from ..models.enums import HypothesisState, ResearchDirection

D = Decimal
MAX_RESEARCH_ADJUSTMENT = D("0.200000")
_MATURE_STATES = (HypothesisState.CONFIRMED, HypothesisState.DILIGENCE_READY)
_STATE_MULTIPLIER = {
    HypothesisState.CONFIRMED: D("0.75"),
    HypothesisState.DILIGENCE_READY: D("1.00"),
}
_DIRECTION_SIGN = {
    ResearchDirection.POSITIVE: D("1"),
    ResearchDirection.NEGATIVE: D("-1"),
    ResearchDirection.NEUTRAL: D("0"),
}
_QUANT = D("0.000001")


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _q(value: Decimal) -> Decimal:
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)


@dataclass(slots=True)
class PortfolioResearchEvidence:
    instrument_id: str
    signed_conviction: Decimal = D("0.000000")
    research_adjustment: Decimal = D("0.000000")
    hypotheses: list[dict] = field(default_factory=list)

    def adjust(self, fundamental_score: float) -> float:
        combined = D(str(fundamental_score)) + self.research_adjustment
        return float(_clamp(combined, D("0"), D("1")))

    def as_json(self, *, fundamental_score: float) -> dict:
        combined = self.adjust(fundamental_score)
        return {
            "fundamental_score": round(float(fundamental_score), 6),
            "signed_conviction": float(self.signed_conviction),
            "research_adjustment": float(self.research_adjustment),
            "combined_score": round(combined, 6),
            "hypotheses": list(self.hypotheses),
        }


def _contribution(row: ResearchHypothesis) -> Decimal:
    evidence = D(row.evidence_score or 0)
    economic = D(row.economic_score or 0)
    base = (evidence + economic) / D("2")
    multiplier = _STATE_MULTIPLIER[row.state]
    sign = _DIRECTION_SIGN[row.direction]
    return _clamp(base, D("0"), D("1")) * multiplier * sign


def evidence_for(
    session: Session,
    instrument_ids: list[str] | tuple[str, ...] | set[str],
    *,
    as_of: datetime,
) -> dict[str, PortfolioResearchEvidence]:
    """Return latest mature, decision-available research per instrument.

    Versions are collapsed by fingerprint before conviction is aggregated so a
    revised thesis never looks like multiple independent portfolio evidence.
    """
    requested = [item for item in dict.fromkeys(instrument_ids) if item]
    result = {item: PortfolioResearchEvidence(instrument_id=item) for item in requested}
    if not requested:
        return result

    rows = list(
        session.execute(
            select(ResearchHypothesis)
            .where(
                ResearchHypothesis.instrument_id.in_(requested),
                ResearchHypothesis.state.in_(_MATURE_STATES),
                ResearchHypothesis.as_of <= as_of,
                or_(
                    ResearchHypothesis.expires_at.is_(None),
                    ResearchHypothesis.expires_at > as_of,
                ),
            )
            .order_by(
                ResearchHypothesis.fingerprint.asc(),
                ResearchHypothesis.version.desc(),
                ResearchHypothesis.created_at.desc(),
            )
        ).scalars()
    )

    latest: dict[tuple[str, str], ResearchHypothesis] = {}
    for row in rows:
        latest.setdefault((row.instrument_id, row.fingerprint), row)

    grouped: dict[str, list[tuple[ResearchHypothesis, Decimal]]] = {}
    for (instrument_id, _), row in latest.items():
        grouped.setdefault(instrument_id, []).append((row, _contribution(row)))

    for instrument_id, items in grouped.items():
        contributions = [value for _, value in items]
        signed = _clamp(
            sum(contributions, start=D("0")) / D(len(contributions)),
            D("-1"),
            D("1"),
        )
        signed = _q(signed)
        adjustment = _q(signed * MAX_RESEARCH_ADJUSTMENT)
        payload = []
        for row, contribution in sorted(items, key=lambda item: item[0].fingerprint):
            payload.append(
                {
                    "hypothesis_id": str(row.id),
                    "fingerprint": row.fingerprint,
                    "version": row.version,
                    "state": str(row.state),
                    "direction": str(row.direction),
                    "as_of": row.as_of.isoformat(),
                    "evidence_score": float(row.evidence_score),
                    "economic_score": float(row.economic_score),
                    "contribution": float(_q(contribution)),
                    "title": row.title,
                }
            )
        result[instrument_id] = PortfolioResearchEvidence(
            instrument_id=instrument_id,
            signed_conviction=signed,
            research_adjustment=adjustment,
            hypotheses=payload,
        )
    return result


__all__ = [
    "MAX_RESEARCH_ADJUSTMENT",
    "PortfolioResearchEvidence",
    "evidence_for",
]
