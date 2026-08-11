"""Action layer for Portfolio → Signals.

The daily equity ranking already combines measured fundamentals, D1 timing and
research catalysts. What it deliberately did *not* do was answer the product
question: which names deserve action now? This module adds that final layer
without inventing valuation targets or analyst estimates the data source does
not provide.

Actions are intentionally investment actions, not short-term orders:

* ACCUMULATE — strong measured fundamental + technical combination;
* EARLY — a positive research hypothesis materially improves a usable name;
* WATCH — usable company worth following but not strong enough to accumulate.

No entry/stop/position size is produced here. That keeps the investment contour
separate from the H1/H4 trading scanner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .equity_ranking import build_daily_ranking, latest_snapshot
from .equity_ranking_refresh import latest_equity_d1


@dataclass(frozen=True, slots=True)
class InvestmentSignal:
    rank: int
    instrument_id: str
    symbol: str
    title: str
    action: str
    action_label: str
    score: float
    price: float | None
    horizon: str
    fundamental_score: float
    technical_score: float
    technical_state: str
    catalyst_adjustment: float
    why: tuple[str, ...]
    risks: tuple[str, ...]
    catalyst: dict | None

    def as_dict(self) -> dict:
        return {
            "rank": self.rank,
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "title": self.title,
            "action": self.action,
            "action_label": self.action_label,
            "score": self.score,
            "price": self.price,
            "horizon": self.horizon,
            "fundamental_score": self.fundamental_score,
            "technical_score": self.technical_score,
            "technical_state": self.technical_state,
            "catalyst_adjustment": self.catalyst_adjustment,
            "why": list(self.why),
            "risks": list(self.risks),
            "catalyst": self.catalyst,
        }


def _positive_hypothesis(item: dict) -> bool:
    hypothesis = item.get("hypothesis")
    if not isinstance(hypothesis, dict):
        return False
    return str(hypothesis.get("direction", "")).upper().endswith("POSITIVE")


def _classify(item: dict) -> tuple[str, str] | None:
    if not bool(item.get("eligible")):
        return None

    score = float(item.get("score") or 0)
    fundamental = float(item.get("fundamental_score") or 0)
    technical = float(item.get("technical_score") or 0)
    catalyst = float(item.get("catalyst_adjustment") or 0)
    technical_state = str(item.get("technical_state") or "").lower()

    # A genuine research catalyst gets its own label. The underlying company
    # must still be investable and its combined score must be respectable: a
    # news-like catalyst is not permission to rescue a weak business.
    if _positive_hypothesis(item) and catalyst >= 3.0 and score >= 60:
        return "EARLY", "РАННИЙ СИГНАЛ"

    # Accumulation requires both halves to stand on their own. A 90 technical
    # score cannot compensate for a 25 fundamental score, and vice versa.
    if (
        score >= 72
        and fundamental >= 55
        and technical >= 55
        and "нисход" not in technical_state
        and catalyst > -3
    ):
        return "ACCUMULATE", "НАБИРАТЬ"

    # WATCH is still a signal: it tells the owner which usable names are next
    # in line, without pretending the timing is already good enough to buy.
    if score >= 50:
        return "WATCH", "НАБЛЮДАТЬ"
    return None


def _signal(item: dict) -> InvestmentSignal | None:
    classified = _classify(item)
    if classified is None:
        return None
    action, label = classified

    fundamental_facts = [
        str(value) for value in item.get("fundamental_facts", []) if value
    ]
    technical_facts = [
        str(value) for value in item.get("technical_facts", []) if value
    ]
    why: list[str] = []
    why.extend(fundamental_facts[:2])
    why.extend(technical_facts[:2])

    hypothesis = item.get("hypothesis") if isinstance(item.get("hypothesis"), dict) else None
    if action == "EARLY" and hypothesis:
        title = str(hypothesis.get("title") or "").strip()
        if title:
            why.insert(0, f"катализатор: {title}")

    risks = [str(value) for value in item.get("warnings", []) if value]
    state = str(item.get("technical_state") or "")
    if state and "нисход" in state.lower():
        risks.append(f"D1: {state}")
    if not fundamental_facts:
        risks.append("фундаментальные факты пока измерены неполно")

    return InvestmentSignal(
        rank=int(item.get("rank") or 0),
        instrument_id=str(item.get("instrument_id") or ""),
        symbol=str(item.get("symbol") or ""),
        title=str(item.get("title") or item.get("symbol") or ""),
        action=action,
        action_label=label,
        score=float(item.get("score") or 0),
        price=(float(item["price"]) if item.get("price") is not None else None),
        horizon="6–12 мес.",
        fundamental_score=float(item.get("fundamental_score") or 0),
        technical_score=float(item.get("technical_score") or 0),
        technical_state=state,
        catalyst_adjustment=float(item.get("catalyst_adjustment") or 0),
        why=tuple(why),
        risks=tuple(dict.fromkeys(risks)),
        catalyst=hypothesis,
    )


def ensure_current_ranking(session: Session):
    """Return a useful snapshot, repairing an empty/stale warm-up snapshot.

    The old endpoint rebuilt only when *no row* existed. A row created during
    startup before equities had D1 history could therefore remain empty for a
    whole day. Here an empty snapshot is not considered success, and a newer
    closed equity D1 forces a rebuild immediately.
    """
    snapshot = latest_snapshot(session)
    newest = latest_equity_d1(session)
    stale = (
        snapshot is not None
        and newest is not None
        and (snapshot.data_as_of is None or newest > snapshot.data_as_of)
    )
    empty = snapshot is not None and not (snapshot.items_json or [])
    if snapshot is None or stale or (empty and newest is not None):
        snapshot = build_daily_ranking(
            session,
            now=datetime.now(UTC),
            force=snapshot is not None,
        )
        session.flush()
    return snapshot


def build_investment_signals(
    session: Session,
    *,
    limit: int = 12,
) -> tuple[object | None, list[InvestmentSignal], str]:
    snapshot = ensure_current_ranking(session)
    if snapshot is None:
        return None, [], "инвестиционный рейтинг ещё не создан"

    items = list(snapshot.items_json or [])
    if not items:
        return (
            snapshot,
            [],
            "акции уже синхронизированы, но закрытых D1/фундаментальных данных "
            "пока недостаточно для инвестиционного сигнала",
        )

    signals = [signal for item in items if (signal := _signal(item)) is not None]
    priority = {"EARLY": 0, "ACCUMULATE": 1, "WATCH": 2}
    signals.sort(key=lambda s: (priority.get(s.action, 9), -s.score, s.rank))
    signals = signals[:limit]

    if signals:
        reason = ""
    else:
        eligible = sum(bool(item.get("eligible")) for item in items)
        reason = (
            f"пригодных компаний {eligible}, но сегодня ни одна не проходит "
            "порог даже для наблюдения; сигнал не создаётся ради заполнения экрана"
        )
    return snapshot, signals, reason


__all__ = [
    "InvestmentSignal",
    "build_investment_signals",
    "ensure_current_ranking",
]
