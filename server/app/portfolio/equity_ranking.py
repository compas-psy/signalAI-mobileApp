"""Daily ranked watchlist for Portfolio → Signals.

This is deliberately separate from the short-term trading scanner.  It does
not create an entry, stop, side or order.  Its question is slower and simpler:
which Russian listed companies deserve attention *today* when fundamental
quality and D1 market context are considered together?

Every usable equity is kept.  High scores float to the top; weak/rejected
names stay visible at the bottom with a reason instead of disappearing.  A
rare early-signal hypothesis is an overlay (positive or negative), never a
prerequisite for a company to appear.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from statistics import fmean, pstdev
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..market.investments import investment_universe
from ..models import (
    Bar,
    EquityRankingSnapshot,
    ResearchHypothesis,
)
from ..models.enums import (
    AssetClass,
    HypothesisState,
    ResearchDirection,
    Timeframe,
)
from . import fundamentals as fund

Moscow = ZoneInfo("Europe/Moscow")
METHODOLOGY = "equity_rank_v1"
LIVE_HYPOTHESES = frozenset(
    {
        HypothesisState.OBSERVATION,
        HypothesisState.EARLY_CANDIDATE,
        HypothesisState.CONFIRMED,
        HypothesisState.DILIGENCE_READY,
    }
)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _ramp(value: float, bad: float, good: float) -> float:
    if good == bad:
        return 0.5
    return _clamp((value - bad) / (good - bad))


def _closes(session: Session, instrument_id: str, *, limit: int = 280) -> list[float]:
    rows = list(
        session.execute(
            select(Bar.close)
            .where(
                Bar.instrument_id == instrument_id,
                Bar.timeframe == Timeframe.D1,
                Bar.is_closed.is_(True),
            )
            .order_by(Bar.open_time.desc())
            .limit(limit)
        ).scalars()
    )
    return [float(value) for value in reversed(rows) if value is not None and value > 0]


def _return(closes: list[float], sessions: int) -> float | None:
    if len(closes) <= sessions or closes[-1 - sessions] <= 0:
        return None
    return closes[-1] / closes[-1 - sessions] - 1.0


def _technical(closes: list[float]) -> dict:
    """Transparent D1 overlay, not a trading setup detector.

    The components intentionally use only slow daily context.  Nothing here
    is shared with H1/H4 signal discovery, so changing this ranking cannot
    change which short-term setups are found.
    """
    if len(closes) < 50:
        return {
            "score": 0.0,
            "state": "истории мало",
            "facts": [f"дневных закрытий {len(closes)} — нужно хотя бы 50"],
            "price": closes[-1] if closes else None,
            "momentum_3m": None,
            "momentum_6m": None,
            "drawdown_6m": None,
            "volatility_3m": None,
            "ready": False,
        }

    price = closes[-1]
    ma20 = fmean(closes[-20:])
    ma50 = fmean(closes[-50:])
    ma200 = fmean(closes[-200:]) if len(closes) >= 200 else None
    m3 = _return(closes, 63)
    m6 = _return(closes, 126)

    # Trend: current price, medium trend and (when available) long trend.
    price_vs_20 = price / ma20 - 1.0 if ma20 else 0.0
    trend_fast = _ramp(price / ma20 - 1.0, -0.08, 0.08) if ma20 else 0.5
    trend_mid = _ramp(ma20 / ma50 - 1.0, -0.08, 0.08) if ma50 else 0.5
    trend_slow = (
        _ramp(ma50 / ma200 - 1.0, -0.12, 0.12)
        if ma200 and ma200 > 0
        else 0.5
    )
    trend = 0.35 * trend_fast + 0.40 * trend_mid + 0.25 * trend_slow

    # Momentum: medium-term only.  One-month noise must not reorder a
    # long-horizon investment watchlist every morning.
    momentum_parts = []
    if m3 is not None:
        momentum_parts.append(_ramp(m3, -0.25, 0.35))
    if m6 is not None:
        momentum_parts.append(_ramp(m6, -0.35, 0.55))
    momentum = fmean(momentum_parts) if momentum_parts else 0.5

    window = closes[-126:] if len(closes) >= 126 else closes
    peak = max(window)
    drawdown = price / peak - 1.0 if peak > 0 else 0.0

    # Timing rewards strength without rewarding a vertical spike.  Being
    # 15–20% above MA20 is not an invitation to chase the move.
    drawdown_quality = _ramp(drawdown, -0.35, -0.03)
    extension_penalty = _ramp(price_vs_20, 0.10, 0.25)
    timing = _clamp(drawdown_quality - 0.55 * extension_penalty)

    daily = [math.log(closes[i] / closes[i - 1]) for i in range(max(1, len(closes) - 63), len(closes))]
    volatility = pstdev(daily) * math.sqrt(252) if len(daily) >= 2 else None

    score = _clamp(0.45 * trend + 0.35 * momentum + 0.20 * timing)
    if price > ma50 and (ma200 is None or ma50 > ma200):
        state = "восходящий D1"
    elif price < ma50 and (ma200 is not None and ma50 < ma200):
        state = "нисходящий D1"
    else:
        state = "смешанный D1"

    facts = [
        f"цена {'выше' if price >= ma50 else 'ниже'} MA50",
        f"3 мес. {m3:+.1%}" if m3 is not None else "3 мес. истории мало",
        f"6 мес. {m6:+.1%}" if m6 is not None else "6 мес. истории мало",
        f"от 6-месячного максимума {drawdown:.1%}",
    ]
    if abs(price_vs_20) >= 0.10:
        facts.append(f"отклонение от MA20 {price_vs_20:+.1%}")

    return {
        "score": score,
        "state": state,
        "facts": facts,
        "price": price,
        "momentum_3m": m3,
        "momentum_6m": m6,
        "drawdown_6m": drawdown,
        "volatility_3m": volatility,
        "ready": True,
    }


def _latest_hypotheses(session: Session) -> dict[str, ResearchHypothesis]:
    rows = list(
        session.execute(
            select(ResearchHypothesis)
            .where(
                ResearchHypothesis.instrument_id != "",
                ResearchHypothesis.state.in_(LIVE_HYPOTHESES),
            )
            .order_by(
                ResearchHypothesis.instrument_id,
                ResearchHypothesis.as_of.desc(),
                ResearchHypothesis.version.desc(),
            )
        ).scalars()
    )
    result: dict[str, ResearchHypothesis] = {}
    for row in rows:
        result.setdefault(row.instrument_id, row)
    return result


def _hypothesis_overlay(row: ResearchHypothesis | None) -> tuple[float, dict | None]:
    if row is None:
        return 0.0, None

    strength = _clamp((float(row.evidence_score) + float(row.economic_score)) / 2.0)
    state_weight = {
        HypothesisState.OBSERVATION: 0.25,
        HypothesisState.EARLY_CANDIDATE: 0.55,
        HypothesisState.CONFIRMED: 0.85,
        HypothesisState.DILIGENCE_READY: 1.0,
    }.get(row.state, 0.0)
    magnitude = 10.0 * strength * state_weight
    adjustment = 0.0
    if row.direction is ResearchDirection.POSITIVE:
        adjustment = magnitude
    elif row.direction is ResearchDirection.NEGATIVE:
        adjustment = -magnitude

    return adjustment, {
        "title": row.title,
        "direction": str(row.direction),
        "state": str(row.state),
        "evidence_score": float(row.evidence_score),
        "economic_score": float(row.economic_score),
        "priority": float(row.research_priority),
        "as_of": row.as_of.isoformat(),
    }


def _tier(score: float, eligible: bool) -> str:
    if not eligible:
        return "вне отбора"
    if score >= 75:
        return "стоит смотреть"
    if score >= 60:
        return "наблюдать"
    if score >= 45:
        return "средне"
    return "слабо сейчас"


def _latest_data_time(session: Session, ids: list[str]) -> datetime | None:
    if not ids:
        return None
    return session.execute(
        select(func.max(Bar.open_time)).where(
            Bar.instrument_id.in_(ids),
            Bar.timeframe == Timeframe.D1,
            Bar.is_closed.is_(True),
        )
    ).scalar_one_or_none()


def latest_snapshot(session: Session) -> EquityRankingSnapshot | None:
    return session.execute(
        select(EquityRankingSnapshot)
        .order_by(EquityRankingSnapshot.market_day.desc())
        .limit(1)
    ).scalar_one_or_none()


def build_daily_ranking(
    session: Session,
    *,
    now: datetime | None = None,
    fetch=None,
    force: bool = False,
) -> EquityRankingSnapshot:
    """Build at most one snapshot for the current Moscow calendar day."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    day = moment.astimezone(Moscow).date()

    existing = session.execute(
        select(EquityRankingSnapshot).where(EquityRankingSnapshot.market_day == day)
    ).scalar_one_or_none()
    if existing is not None and not force:
        return existing

    universe = [
        instrument
        for instrument in investment_universe(session)
        if instrument.asset_class is AssetClass.EQUITY
        and instrument.instrument_id.startswith("MOEX:EQ:")
    ]
    cards = fund.screen(session, universe, fetch=fetch) if universe else []
    hypotheses = _latest_hypotheses(session)

    items: list[dict] = []
    by_instrument = {instrument.instrument_id: instrument for instrument in universe}
    for card in cards:
        instrument = by_instrument[card.instrument_id]
        technical = _technical(_closes(session, card.instrument_id))
        fundamental_score = _clamp(float(card.score))
        technical_score = _clamp(float(technical["score"]))
        adjustment, hypothesis = _hypothesis_overlay(hypotheses.get(card.instrument_id))

        eligible = not card.rejected and bool(technical["ready"])
        overall = 100.0 * (0.55 * fundamental_score + 0.45 * technical_score)
        overall = _clamp((overall + adjustment) / 100.0) * 100.0
        # Insufficient history/liquidity stays visible, but can never outrank a
        # genuinely usable name only because one measured metric looks good.
        if not eligible:
            overall = min(overall, 39.0)

        fundamental_facts = [
            metric.text
            for metric in card.metrics
            if metric.counts and metric.text
        ][:4]
        warnings = []
        if card.rejected:
            warnings.append(card.rejected)
        if not technical["ready"]:
            warnings.extend(technical["facts"][:1])

        items.append(
            {
                "rank": 0,
                "instrument_id": card.instrument_id,
                "symbol": instrument.symbol,
                "title": instrument.title or instrument.symbol,
                "score": round(overall, 1),
                "tier": _tier(overall, eligible),
                "eligible": eligible,
                "fundamental_score": round(fundamental_score * 100.0, 1),
                "technical_score": round(technical_score * 100.0, 1),
                "catalyst_adjustment": round(adjustment, 1),
                "technical_state": technical["state"],
                "price": technical["price"],
                "momentum_3m": technical["momentum_3m"],
                "momentum_6m": technical["momentum_6m"],
                "drawdown_6m": technical["drawdown_6m"],
                "volatility_3m": technical["volatility_3m"],
                "fundamental_facts": fundamental_facts,
                "technical_facts": technical["facts"],
                "warnings": warnings,
                "hypothesis": hypothesis,
            }
        )

    items.sort(key=lambda item: (-float(item["score"]), item["symbol"]))
    for rank, item in enumerate(items, start=1):
        item["rank"] = rank

    snapshot = existing or EquityRankingSnapshot(market_day=day)
    snapshot.generated_at = moment
    snapshot.data_as_of = _latest_data_time(session, [i.instrument_id for i in universe])
    snapshot.universe_count = len(universe)
    snapshot.scored_count = len(items)
    snapshot.methodology = METHODOLOGY
    snapshot.items_json = items
    if existing is None:
        session.add(snapshot)

    # Keep enough history to diagnose ranking changes, not an unbounded log.
    cutoff = day - timedelta(days=14)
    session.execute(
        delete(EquityRankingSnapshot).where(EquityRankingSnapshot.market_day < cutoff)
    )
    session.flush()
    return snapshot


def summary(snapshot: EquityRankingSnapshot) -> str:
    top = (snapshot.items_json or [])[:3]
    leaders = ", ".join(f"{item['symbol']} {item['score']:.0f}" for item in top)
    suffix = f"; лидеры: {leaders}" if leaders else ""
    return (
        f"рейтинг компаний {snapshot.market_day.isoformat()}: "
        f"{snapshot.scored_count} из {snapshot.universe_count}{suffix}"
    )


__all__ = [
    "METHODOLOGY",
    "build_daily_ranking",
    "latest_snapshot",
    "summary",
]
