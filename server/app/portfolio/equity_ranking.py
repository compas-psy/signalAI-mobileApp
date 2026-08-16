"""Daily ranked watchlist for Portfolio → Signals.

This is deliberately separate from the short-term trading scanner. It does not
create an entry, stop, side or order. Its question is: which Russian listed
companies deserve attention *before* an obvious move is already mature?

v2 keeps fundamental quality and slow D1 context, but adds an explicit early
layer built only from closed bars available at the snapshot time: volatility
compression, 5d-vs-20d acceleration, proximity to a 63-session breakout,
turnover expansion and up-day accumulation. A separate anti-chase penalty
prevents a vertical five-day spike or a large MA20 extension from being
mistaken for an early opportunity.

Every usable equity is kept. Weak/rejected names stay visible at the bottom
with a reason instead of disappearing. Research hypotheses remain an overlay,
never an admission prerequisite. No forward data is queried or reconstructed.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from statistics import fmean, median, pstdev
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..market.investments import investment_universe
from ..models import Bar, EquityRankingSnapshot, ResearchHypothesis
from ..models.enums import (
    AssetClass,
    HypothesisState,
    ResearchDirection,
    Timeframe,
)
from . import fundamentals as fund

Moscow = ZoneInfo("Europe/Moscow")
METHODOLOGY = "equity_rank_v2_early"
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


def _turnovers(session: Session, instrument_id: str, *, limit: int = 280) -> list[float | None]:
    rows = list(
        session.execute(
            select(Bar.volume_notional)
            .where(
                Bar.instrument_id == instrument_id,
                Bar.timeframe == Timeframe.D1,
                Bar.is_closed.is_(True),
            )
            .order_by(Bar.open_time.desc())
            .limit(limit)
        ).scalars()
    )
    result: list[float | None] = []
    for value in reversed(rows):
        if value is None or value <= 0:
            result.append(None)
        else:
            result.append(float(value))
    return result


def _return(closes: list[float], sessions: int) -> float | None:
    if len(closes) <= sessions or closes[-1 - sessions] <= 0:
        return None
    return closes[-1] / closes[-1 - sessions] - 1.0


def _realized_vol(closes: list[float]) -> float | None:
    if len(closes) < 3:
        return None
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    if len(returns) < 2:
        return None
    return pstdev(returns) * math.sqrt(252)


def _early_features(closes: list[float], turnovers: list[float | None] | None) -> dict:
    """As-of-only pre-move features and anti-chase diagnostics."""
    price = closes[-1]
    r5 = _return(closes, 5)
    r20 = _return(closes, 20)

    acceleration = None
    acceleration_score = 0.5
    if r5 is not None and r20 is not None:
        acceleration = r5 - r20 / 4.0
        acceleration_score = _ramp(acceleration, -0.035, 0.045)

    recent_vol = _realized_vol(closes[-11:])
    baseline_vol = _realized_vol(closes[-64:-10]) if len(closes) >= 64 else _realized_vol(closes[:-10])
    compression_ratio = None
    compression_score = 0.5
    if recent_vol is not None and baseline_vol is not None and baseline_vol > 1e-9:
        compression_ratio = recent_vol / baseline_vol
        # 0.45x = strong coil, 1.10x+ = no compression benefit.
        compression_score = 1.0 - _ramp(compression_ratio, 0.45, 1.10)

    prior_window = closes[-64:-1] if len(closes) >= 64 else closes[:-1]
    prior_high = max(prior_window) if prior_window else price
    breakout_distance = price / prior_high - 1.0 if prior_high > 0 else 0.0
    if -0.04 <= breakout_distance <= 0.015:
        proximity_score = 1.0 - min(abs(breakout_distance), 0.04) / 0.04
    elif breakout_distance < -0.04:
        proximity_score = _ramp(breakout_distance, -0.20, -0.04) * 0.45
    else:
        proximity_score = max(0.0, 1.0 - _ramp(breakout_distance, 0.015, 0.12))

    turnover_ratio = None
    turnover_score = 0.5
    accumulation_score = 0.5
    usable_turnover = turnovers or []
    if len(usable_turnover) >= len(closes):
        usable_turnover = usable_turnover[-len(closes):]
    if len(usable_turnover) >= 25:
        recent = [v for v in usable_turnover[-5:] if v is not None and v > 0]
        prior = [v for v in usable_turnover[-25:-5] if v is not None and v > 0]
        if recent and prior:
            turnover_ratio = median(recent) / median(prior)
            turnover_score = _ramp(turnover_ratio, 0.85, 1.80)

        up_volume = 0.0
        down_volume = 0.0
        start = max(1, len(closes) - 20)
        offset = len(usable_turnover) - len(closes)
        for index in range(start, len(closes)):
            volume_index = index + offset
            if volume_index < 0 or volume_index >= len(usable_turnover):
                continue
            value = usable_turnover[volume_index]
            if value is None or value <= 0:
                continue
            if closes[index] >= closes[index - 1]:
                up_volume += value
            else:
                down_volume += value
        if up_volume + down_volume > 0:
            accumulation_score = up_volume / (up_volume + down_volume)

    ma20 = fmean(closes[-20:])
    price_vs_20 = price / ma20 - 1.0 if ma20 > 0 else 0.0
    five_day_spike = max(0.0, r5 or 0.0)
    chase_from_return = _ramp(five_day_spike, 0.08, 0.18)
    chase_from_extension = _ramp(price_vs_20, 0.08, 0.18)
    chase_penalty = max(chase_from_return, chase_from_extension)

    raw_early = _clamp(
        0.25 * compression_score
        + 0.20 * acceleration_score
        + 0.25 * proximity_score
        + 0.15 * turnover_score
        + 0.15 * accumulation_score
    )
    early_score = _clamp(raw_early - 0.55 * chase_penalty)

    why: list[str] = []
    if compression_ratio is not None and compression_ratio <= 0.75:
        why.append(f"волатильность сжалась до {compression_ratio:.2f}× базовой")
    if acceleration is not None and acceleration >= 0.01:
        why.append(f"5-дневный импульс ускоряется ({acceleration:+.1%} сверх темпа 20д)")
    if -0.04 <= breakout_distance <= 0.015:
        why.append(f"цена в {abs(breakout_distance):.1%} от 63-дневного максимума")
    if turnover_ratio is not None and turnover_ratio >= 1.20:
        why.append(f"оборот последних 5 сессий {turnover_ratio:.1f}× к предыдущим")
    if accumulation_score >= 0.62:
        why.append(f"{accumulation_score:.0%} оборота 20д пришлось на растущие дни")
    if chase_penalty >= 0.35:
        why.append("anti-chase: движение уже заметно растянуто — балл снижен")
    if not why:
        why.append("раннего технического преимущества пока не измерено")

    if chase_penalty >= 0.55:
        early_state = "поздно / не догонять"
    elif early_score >= 0.72:
        early_state = "ранняя подготовка"
    elif early_score >= 0.58:
        early_state = "формируется"
    elif early_score >= 0.45:
        early_state = "наблюдать"
    else:
        early_state = "раннего преимущества нет"

    confirm = (
        "подтверждение: закрытие выше 63-дневного максимума без резкого "
        "расширения цены от MA20"
    )
    invalidation = "слом ранней гипотезы: потеря MA20 и исчезновение ускорения/оборота"

    return {
        "score": early_score,
        "state": early_state,
        "why_now": why,
        "confirmation": confirm,
        "invalidation": invalidation,
        "momentum_5d": r5,
        "momentum_20d": r20,
        "acceleration": acceleration,
        "compression_ratio": compression_ratio,
        "breakout_distance_63d": breakout_distance,
        "turnover_ratio_5v20": turnover_ratio,
        "accumulation_share": accumulation_score,
        "chase_penalty": chase_penalty,
    }


def _technical(closes: list[float], turnovers: list[float | None] | None = None) -> dict:
    """Transparent D1 context plus an explicit pre-move layer."""
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
            "early": {
                "score": 0.0,
                "state": "истории мало",
                "why_now": ["недостаточно D1 для раннего радара"],
                "confirmation": "",
                "invalidation": "",
                "momentum_5d": None,
                "momentum_20d": None,
                "acceleration": None,
                "compression_ratio": None,
                "breakout_distance_63d": None,
                "turnover_ratio_5v20": None,
                "accumulation_share": None,
                "chase_penalty": 0.0,
            },
            "ready": False,
        }

    price = closes[-1]
    ma20 = fmean(closes[-20:])
    ma50 = fmean(closes[-50:])
    ma200 = fmean(closes[-200:]) if len(closes) >= 200 else None
    m3 = _return(closes, 63)
    m6 = _return(closes, 126)

    price_vs_20 = price / ma20 - 1.0 if ma20 else 0.0
    trend_fast = _ramp(price / ma20 - 1.0, -0.08, 0.08) if ma20 else 0.5
    trend_mid = _ramp(ma20 / ma50 - 1.0, -0.08, 0.08) if ma50 else 0.5
    trend_slow = (
        _ramp(ma50 / ma200 - 1.0, -0.12, 0.12)
        if ma200 and ma200 > 0
        else 0.5
    )
    trend = 0.35 * trend_fast + 0.40 * trend_mid + 0.25 * trend_slow

    momentum_parts = []
    if m3 is not None:
        momentum_parts.append(_ramp(m3, -0.25, 0.35))
    if m6 is not None:
        momentum_parts.append(_ramp(m6, -0.35, 0.55))
    momentum = fmean(momentum_parts) if momentum_parts else 0.5

    window = closes[-126:] if len(closes) >= 126 else closes
    peak = max(window)
    drawdown = price / peak - 1.0 if peak > 0 else 0.0
    drawdown_quality = _ramp(drawdown, -0.35, -0.03)
    extension_penalty = _ramp(price_vs_20, 0.10, 0.25)
    timing = _clamp(drawdown_quality - 0.55 * extension_penalty)

    daily = [
        math.log(closes[i] / closes[i - 1])
        for i in range(max(1, len(closes) - 63), len(closes))
    ]
    volatility = pstdev(daily) * math.sqrt(252) if len(daily) >= 2 else None
    early = _early_features(closes, turnovers)

    score = _clamp(
        0.32 * trend
        + 0.18 * momentum
        + 0.30 * float(early["score"])
        + 0.20 * timing
    )
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
        *early["why_now"][:3],
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
        "early": early,
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


def _previous_snapshot(session: Session, day) -> EquityRankingSnapshot | None:
    return session.execute(
        select(EquityRankingSnapshot)
        .where(EquityRankingSnapshot.market_day < day)
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

    previous = _previous_snapshot(session, day)
    previous_ranks = {
        str(item.get("symbol")): int(item.get("rank", 0))
        for item in (previous.items_json or [])
        if item.get("symbol") and item.get("rank")
    } if previous is not None else {}

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
        closes = _closes(session, card.instrument_id)
        technical = _technical(closes, _turnovers(session, card.instrument_id))
        fundamental_score = _clamp(float(card.score))
        technical_score = _clamp(float(technical["score"]))
        early = technical["early"]
        early_score = _clamp(float(early["score"]))
        adjustment, hypothesis = _hypothesis_overlay(hypotheses.get(card.instrument_id))

        eligible = not card.rejected and bool(technical["ready"])
        # Early evidence has its own weight instead of hiding inside a slow
        # momentum average. Fundamental quality remains the largest component.
        overall = 100.0 * (
            0.50 * fundamental_score
            + 0.35 * technical_score
            + 0.15 * early_score
        )
        overall = _clamp((overall + adjustment) / 100.0) * 100.0
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
        if float(early.get("chase_penalty") or 0.0) >= 0.55:
            warnings.append("движение уже растянуто: ранний балл снижен anti-chase")

        items.append(
            {
                "rank": 0,
                "rank_change": None,
                "instrument_id": card.instrument_id,
                "symbol": instrument.symbol,
                "title": instrument.title or instrument.symbol,
                "score": round(overall, 1),
                "tier": _tier(overall, eligible),
                "eligible": eligible,
                "fundamental_score": round(fundamental_score * 100.0, 1),
                "technical_score": round(technical_score * 100.0, 1),
                "early_score": round(early_score * 100.0, 1),
                "early_state": early["state"],
                "why_now": early["why_now"],
                "confirmation": early["confirmation"],
                "invalidation": early["invalidation"],
                "momentum_5d": early["momentum_5d"],
                "momentum_20d": early["momentum_20d"],
                "acceleration": early["acceleration"],
                "compression_ratio": early["compression_ratio"],
                "breakout_distance_63d": early["breakout_distance_63d"],
                "turnover_ratio_5v20": early["turnover_ratio_5v20"],
                "accumulation_share": early["accumulation_share"],
                "chase_penalty": early["chase_penalty"],
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
        previous_rank = previous_ranks.get(str(item["symbol"]))
        if previous_rank is not None:
            item["rank_change"] = previous_rank - rank

    snapshot = existing or EquityRankingSnapshot(market_day=day)
    snapshot.generated_at = moment
    snapshot.data_as_of = _latest_data_time(session, [i.instrument_id for i in universe])
    snapshot.universe_count = len(universe)
    snapshot.scored_count = len(items)
    snapshot.methodology = METHODOLOGY
    snapshot.items_json = items
    if existing is None:
        session.add(snapshot)

    cutoff = day - timedelta(days=14)
    session.execute(
        delete(EquityRankingSnapshot).where(EquityRankingSnapshot.market_day < cutoff)
    )
    session.flush()
    return snapshot


def summary(snapshot: EquityRankingSnapshot) -> str:
    top = (snapshot.items_json or [])[:3]
    leaders = ", ".join(
        f"{item['symbol']} {item['score']:.0f}/early {item.get('early_score', 0):.0f}"
        for item in top
    )
    suffix = f"; лидеры: {leaders}" if leaders else ""
    return (
        f"ранний рейтинг компаний {snapshot.market_day.isoformat()}: "
        f"{snapshot.scored_count} из {snapshot.universe_count}{suffix}"
    )


__all__ = [
    "METHODOLOGY",
    "build_daily_ranking",
    "latest_snapshot",
    "summary",
]
