"""Daily equity radar for Portfolio → Signals.

This is deliberately separate from short-term trading ideas. It ranks every
tracked MOEX equity on the latest *closed D1* information so the owner sees a
full ordered watch universe instead of an empty list when no research
hypothesis crossed a promotion threshold.

The score is descriptive, not an order instruction. It combines the data we
actually have today: research/economic evidence, daily technical context and
liquidity. Missing research does not remove a company; it lowers confidence
and is stated explicitly on the card.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from statistics import median

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .models import Bar, DataQualityEvent, Instrument, ResearchHypothesis
from .models.enums import AssetClass, HypothesisState, Timeframe

SNAPSHOT_SOURCE = "equity_radar"
SNAPSHOT_FLAG = "DAILY_SNAPSHOT"
_LIVE_RESEARCH = {
    HypothesisState.OBSERVATION.value,
    HypothesisState.EARLY_CANDIDATE.value,
    HypothesisState.CONFIRMED.value,
    HypothesisState.DILIGENCE_READY.value,
}


def _f(value: object | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _pct(current: float, previous: float) -> float | None:
    if previous <= 0:
        return None
    return (current / previous - 1.0) * 100.0


def _technical(closes: list[float]) -> tuple[float, float | None, float | None, list[str]]:
    """Transparent D1 timing score; no short-term strategy code is reused."""
    if not closes:
        return 20.0, None, None, ["нет закрытых дневных свечей"]

    last = closes[-1]
    ret20 = _pct(last, closes[-21]) if len(closes) >= 21 else None
    ret60 = _pct(last, closes[-61]) if len(closes) >= 61 else None
    sma20 = sum(closes[-20:]) / min(20, len(closes))
    sma60 = sum(closes[-60:]) / min(60, len(closes))
    high60 = max(closes[-60:])
    drawdown = (last / high60 - 1.0) * 100.0 if high60 > 0 else 0.0

    score = 50.0
    reasons: list[str] = []

    if last >= sma20:
        score += 12
        reasons.append("цена выше средней 20д")
    else:
        score -= 12
        reasons.append("цена ниже средней 20д")

    if len(closes) >= 60:
        if sma20 >= sma60:
            score += 10
            reasons.append("средняя 20д выше 60д")
        else:
            score -= 10
            reasons.append("средняя 20д ниже 60д")

    if ret20 is not None:
        score += max(-12.0, min(12.0, ret20 * 0.8))
    if ret60 is not None:
        score += max(-10.0, min(10.0, ret60 * 0.35))

    # Being far below the recent high is useful information for a daily
    # investment radar, but it is not treated as a trade trigger.
    score += max(-12.0, drawdown * 0.6)
    return _clamp(score), ret20, ret60, reasons[:3]


def _liquidity(notional: list[float]) -> tuple[float, float | None]:
    values = [v for v in notional[-20:] if v > 0]
    if not values:
        return 10.0, None
    med = float(median(values))
    # 1m RUB ≈ 0, 10m ≈ 20, 100m ≈ 40, 1bn ≈ 60, 10bn ≈ 80.
    score = 20.0 * math.log10(max(med, 1_000_000.0) / 1_000_000.0)
    return _clamp(score), med


def _research(h: ResearchHypothesis | None) -> tuple[float, str, list[str], bool]:
    if h is None:
        # Conservative ranking baseline, not fabricated fundamental data. The
        # card explicitly says that the issuer research contour has no thesis.
        return 35.0, "фундаментальный ранний сигнал пока не сформирован", [
            "фундаментальный контур пока не дал отдельной гипотезы"
        ], False

    evidence = _clamp(_f(h.evidence_score) * 100.0)
    economic = _clamp(_f(h.economic_score) * 100.0)
    context = _clamp(_f(h.market_context_score, 0.5) * 100.0)
    raw = 0.45 * economic + 0.35 * evidence + 0.20 * context
    direction = str(h.direction).lower()
    if "negative" in direction:
        score = _clamp(35.0 - raw * 0.55)
        direction_label = "экономическая гипотеза негативная"
    elif "positive" in direction:
        score = _clamp(raw)
        direction_label = "есть позитивная экономическая гипотеза"
    else:
        score = _clamp(raw * 0.65)
        direction_label = "экономическая гипотеза без явного направления"

    reasons = [direction_label]
    if economic >= 65:
        reasons.append("экономический эффект выглядит существенным")
    if evidence < 50:
        reasons.append("доказательность гипотезы пока слабая")
    return score, h.title or direction_label, reasons[:3], True


def _label(score: float) -> str:
    if score >= 75:
        return "стоит смотреть"
    if score >= 60:
        return "в фокус"
    if score >= 45:
        return "нейтрально"
    if score >= 30:
        return "слабая"
    return "сейчас мимо"


def _latest_hypothesis(session: Session, instrument_id: str) -> ResearchHypothesis | None:
    return session.execute(
        select(ResearchHypothesis)
        .where(
            ResearchHypothesis.instrument_id == instrument_id,
            ResearchHypothesis.state.in_(_LIVE_RESEARCH),
        )
        .order_by(desc(ResearchHypothesis.as_of), desc(ResearchHypothesis.research_priority))
        .limit(1)
    ).scalar_one_or_none()


def _tracked_equities_query():
    """Current investment watch universe, not stale historical instruments."""
    return select(Instrument).where(
        Instrument.asset_class == AssetClass.EQUITY,
        Instrument.in_universe.is_(True),
        Instrument.is_tradable.is_(True),
    )


def build(session: Session) -> dict:
    """Build one deterministic snapshot from latest closed daily data."""
    instruments = list(
        session.execute(_tracked_equities_query().order_by(Instrument.symbol)).scalars()
    )

    cards: list[dict] = []
    newest: datetime | None = None
    for instrument in instruments:
        bars = list(
            session.execute(
                select(Bar)
                .where(
                    Bar.instrument_id == instrument.instrument_id,
                    Bar.timeframe == Timeframe.D1,
                    Bar.is_closed.is_(True),
                )
                .order_by(desc(Bar.open_time))
                .limit(70)
            ).scalars()
        )
        bars.reverse()
        closes = [_f(b.close) for b in bars if _f(b.close) > 0]
        notionals = [_f(b.volume_notional) for b in bars]
        if bars and (newest is None or bars[-1].open_time > newest):
            newest = bars[-1].open_time

        technical, ret20, ret60, tech_reasons = _technical(closes)
        liquidity, median_notional = _liquidity(notionals)
        hypothesis = _latest_hypothesis(session, instrument.instrument_id)
        research, thesis, research_reasons, has_research = _research(hypothesis)

        # Research/fundamental context intentionally has the largest weight;
        # technicals decide timing, not the business thesis. Liquidity keeps
        # tiny/unusable names from floating to the top on one strong factor.
        total = _clamp(0.45 * research + 0.40 * technical + 0.15 * liquidity)
        warnings: list[str] = []
        if len(closes) < 21:
            warnings.append(f"дневной истории мало: {len(closes)} баров")
        if median_notional is None:
            warnings.append("оборот не измерен")
        if not has_research:
            warnings.append("фундаментальный скоринг ограничен: issuer-гипотезы нет")

        reasons = [*research_reasons, *tech_reasons]
        cards.append(
            {
                "instrument_id": instrument.instrument_id,
                "symbol": instrument.symbol,
                "title": instrument.title or instrument.symbol,
                "score": round(total, 1),
                "label": _label(total),
                "latest_price": closes[-1] if closes else None,
                "return_20d_percent": None if ret20 is None else round(ret20, 2),
                "return_60d_percent": None if ret60 is None else round(ret60, 2),
                "research_score": round(research, 1),
                "technical_score": round(technical, 1),
                "liquidity_score": round(liquidity, 1),
                "median_daily_notional_rub": None if median_notional is None else round(median_notional, 0),
                "has_research_hypothesis": has_research,
                "thesis": thesis,
                "reasons": reasons[:4],
                "warnings": warnings,
            }
        )

    cards.sort(key=lambda item: (-item["score"], item["symbol"]))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "as_of": newest.isoformat() if newest else None,
        "cadence": "daily_after_closed_d1",
        "method": "research 45% · D1 technical 40% · liquidity 15%",
        "cards": cards,
    }


def _latest_tracked_equity_d1(session: Session) -> datetime | None:
    """Newest closed D1 only for the current MOEX equity watch universe."""
    return session.execute(
        select(Bar.open_time)
        .join(Instrument, Instrument.instrument_id == Bar.instrument_id)
        .where(
            Instrument.asset_class == AssetClass.EQUITY,
            Instrument.in_universe.is_(True),
            Instrument.is_tradable.is_(True),
            Bar.timeframe == Timeframe.D1,
            Bar.is_closed.is_(True),
        )
        .order_by(desc(Bar.open_time))
        .limit(1)
    ).scalar_one_or_none()


def refresh_daily(session: Session) -> str:
    """Persist at most one snapshot per newest closed tracked-equity D1 bar.

    DataQualityEvent is already the scheduler's durable operational journal and
    has an unrestricted JSON payload. Reusing it avoids introducing a table
    whose only purpose would be a single latest cache row.
    """
    newest = _latest_tracked_equity_d1(session)
    if newest is None:
        return "закрытых D1 по отслеживаемым акциям нет — рейтинг не пересчитан"

    previous = session.execute(
        select(DataQualityEvent)
        .where(
            DataQualityEvent.source == SNAPSHOT_SOURCE,
            DataQualityEvent.flag == SNAPSHOT_FLAG,
        )
        .order_by(desc(DataQualityEvent.occurred_at))
        .limit(1)
    ).scalar_one_or_none()
    previous_as_of = (previous.payload_json or {}).get("as_of") if previous else None
    if previous_as_of == newest.isoformat():
        return f"рейтинг уже рассчитан по D1 {newest.date().isoformat()}"

    snapshot = build(session)
    session.add(
        DataQualityEvent(
            source=SNAPSHOT_SOURCE,
            flag=SNAPSHOT_FLAG,
            detail=f"daily equity radar: {len(snapshot['cards'])} карточек; D1 {newest.date().isoformat()}",
            payload_json=snapshot,
        )
    )
    return f"пересчитано карточек {len(snapshot['cards'])} по D1 {newest.date().isoformat()}"


def latest(session: Session) -> dict:
    row = session.execute(
        select(DataQualityEvent)
        .where(
            DataQualityEvent.source == SNAPSHOT_SOURCE,
            DataQualityEvent.flag == SNAPSHOT_FLAG,
        )
        .order_by(desc(DataQualityEvent.occurred_at))
        .limit(1)
    ).scalar_one_or_none()
    if row is not None and row.payload_json:
        return dict(row.payload_json)

    # First deployment should not leave the screen empty until the scheduler's
    # next tick. Build once, persist through the request transaction, and all
    # later reads use the daily snapshot.
    snapshot = build(session)
    session.add(
        DataQualityEvent(
            source=SNAPSHOT_SOURCE,
            flag=SNAPSHOT_FLAG,
            detail=f"initial daily equity radar: {len(snapshot['cards'])} карточек",
            payload_json=snapshot,
        )
    )
    return snapshot
