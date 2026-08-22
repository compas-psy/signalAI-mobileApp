"""Fail-safe reconciliation for the owner-facing trading lifecycle.

The scheduler remains the primary tracker. This guard enforces a smaller set
of invariants that must never be violated in the owner UI:

* an idea with a live paper trade is not still an actionable TRIGGERED card;
* an intraday limit entry cannot remain alive for days;
* if target/invalidation happened before the promised entry, a later return to
  the old price does not resurrect the plan;
* a terminal paper plan cannot be approved again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .db import session_scope
from .journal.lifecycle import ALLOWED, TransitionRequest, transition
from .models import Bar, IdeaEvent, PaperTrade, TradeIdea
from .models.enums import Direction, IdeaStatus, PaperStatus, Timeframe


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_long(value) -> bool:
    return str(getattr(value, "value", value)) == Direction.LONG.value


def _path(start: IdeaStatus, goal: IdeaStatus) -> list[IdeaStatus]:
    """Shortest valid §18 path, used only to repair an existing fact."""
    if start is goal:
        return []
    seen = {start}
    queue: list[tuple[IdeaStatus, list[IdeaStatus]]] = [(start, [])]
    while queue:
        node, prefix = queue.pop(0)
        for nxt in ALLOWED.get(node, frozenset()):
            if nxt in seen:
                continue
            candidate = [*prefix, nxt]
            if nxt is goal:
                return candidate
            seen.add(nxt)
            queue.append((nxt, candidate))
    return []


def _move(
    session: Session,
    idea: TradeIdea,
    goal: IdeaStatus,
    *,
    code: str,
    detail: str,
    snapshot: dict | None = None,
) -> bool:
    current = IdeaStatus(idea.status)
    if current is goal or current.is_terminal:
        return False
    path = _path(current, goal)
    if not path:
        return False
    for step in path:
        transition(
            session,
            idea,
            TransitionRequest(
                new_status=step,
                reason_code=code,
                reason_detail=detail[:512],
                market_snapshot=snapshot or {},
            ),
        )
    return True


def _decision_ttl(idea: TradeIdea) -> timedelta:
    tf = str(idea.trigger_timeframe or "").strip().lower()
    if tf in {"1m", "3m", "5m", "15m", "30m", "m1", "m3", "m5", "m15", "m30"}:
        return timedelta(hours=2)
    if tf in {"4h", "h4"}:
        return timedelta(hours=24)
    if tf in {"1d", "d1", "day"}:
        return timedelta(hours=72)
    # H1 and unknown intraday trigger: one trading session.
    return timedelta(hours=6)


def _triggered_at(session: Session, idea: TradeIdea) -> datetime:
    value = session.execute(
        select(IdeaEvent.occurred_at)
        .where(
            IdeaEvent.idea_id == idea.id,
            IdeaEvent.new_status == IdeaStatus.TRIGGERED,
        )
        .order_by(IdeaEvent.sequence.desc())
        .limit(1)
    ).scalar_one_or_none()
    return _utc(value or idea.signal_time)


def _approved_terminal_goal(idea: TradeIdea) -> IdeaStatus:
    # ACTIVE is already post-owner-approval. §18 permits ACTIVE -> CANCELLED,
    # while TIMED_OUT is used for a pre-decision trigger/watch window.
    return (
        IdeaStatus.CANCELLED
        if IdeaStatus(idea.status) is IdeaStatus.ACTIVE
        else IdeaStatus.TIMED_OUT
    )


def _cancel_if_thesis_happened_before_entry(
    session: Session,
    trade: PaperTrade,
    idea: TradeIdea,
) -> bool:
    if PaperStatus(trade.status) is not PaperStatus.PENDING:
        return False

    bars = list(
        session.execute(
            select(Bar)
            .where(
                Bar.instrument_id == trade.instrument_id,
                Bar.timeframe == Timeframe.H1,
                Bar.is_closed.is_(True),
                Bar.open_time > _utc(trade.opened_at),
            )
            .order_by(Bar.open_time)
        ).scalars()
    )
    if not bars:
        return False

    long = _is_long(trade.direction)
    targets = [Decimal(str(raw)) for raw in (trade.tp_prices or [])]
    tp1 = targets[0] if targets else None

    for bar in bars:
        # Chronology matters: once an entry was really available, the ordinary
        # paper tracker owns the trade and this guard must not reinterpret it.
        if bar.low <= trade.entry <= bar.high:
            return False

        target_passed = tp1 is not None and (
            bar.high >= tp1 if long else bar.low <= tp1
        )
        invalidated = (
            bar.low <= trade.initial_stop if long else bar.high >= trade.initial_stop
        )
        if not target_passed and not invalidated:
            continue

        trade.status = PaperStatus.CANCELLED
        trade.closed_at = _utc(bar.open_time)
        trade.last_reconciled_at = _utc(bar.open_time)
        if target_passed:
            trade.outcome = "MISS"
            trade.close_reason = "рынок дошёл до цели без входа — план пропущен"
            current = IdeaStatus(idea.status)
            goal = (
                IdeaStatus.MISSED
                if current in {IdeaStatus.DISCOVERED, IdeaStatus.WATCH, IdeaStatus.TRIGGERED}
                else IdeaStatus.CANCELLED
            )
        else:
            trade.outcome = "INVALID"
            trade.close_reason = "план сломан до входа"
            goal = IdeaStatus.CANCELLED
        _move(
            session,
            idea,
            goal,
            code="paper_entry_never_filled",
            detail=trade.close_reason,
            snapshot={
                "paper_trade_id": str(trade.id),
                "bar_time": _utc(bar.open_time).isoformat(),
                "entry": str(trade.entry),
            },
        )
        return True
    return False


def _target_for_trade(trade: PaperTrade, idea: TradeIdea) -> IdeaStatus | None:
    status = PaperStatus(trade.status)
    if status is PaperStatus.PENDING:
        return IdeaStatus.ACTIVE
    if status is PaperStatus.OPEN:
        if trade.tps_taken <= 0:
            return IdeaStatus.FILLED
        if trade.tps_taken == 1:
            return IdeaStatus.TP1_HIT
        if trade.tps_taken == 2:
            return IdeaStatus.TP2_HIT
        return IdeaStatus.MANAGING
    if status is PaperStatus.CANCELLED:
        return _approved_terminal_goal(idea)
    if status is PaperStatus.CLOSED:
        return IdeaStatus.STOPPED if trade.outcome in {"SL", "BE"} else IdeaStatus.CLOSED
    return None


def reconcile_operational_lifecycle(
    session: Session,
    *,
    now: datetime | None = None,
) -> int:
    """Repair owner-visible lifecycle invariants; return number of changes."""
    moment = now or datetime.now(UTC)
    changed = 0
    trades = list(session.execute(select(PaperTrade)).scalars())
    trade_by_idea = {trade.idea_id: trade for trade in trades}

    # Resolve PENDING before repairing old TRIGGERED -> ACTIVE, so a thesis
    # that already ran without entry may still become MISSED rather than being
    # resurrected as an active approved plan.
    for trade in trades:
        if PaperStatus(trade.status) is not PaperStatus.PENDING:
            continue
        idea = session.get(TradeIdea, trade.idea_id)
        if idea is None:
            continue
        if _cancel_if_thesis_happened_before_entry(session, trade, idea):
            changed += 1
            continue

        entry_deadline = min(
            _utc(trade.expires_at),
            _utc(trade.opened_at) + _decision_ttl(idea),
        )
        if moment >= entry_deadline:
            trade.status = PaperStatus.CANCELLED
            trade.closed_at = moment
            trade.last_reconciled_at = moment
            trade.outcome = "TIMEOUT"
            trade.close_reason = "цена до входа не дошла, окно сделки истекло"
            goal = _approved_terminal_goal(idea)
            if _move(
                session,
                idea,
                goal,
                code="paper_entry_timeout_guard",
                detail=trade.close_reason,
                snapshot={
                    "paper_trade_id": str(trade.id),
                    "entry_deadline": entry_deadline.isoformat(),
                },
            ):
                changed += 1

    # Existing paper trade is stronger evidence than a stale idea card.
    for trade in trades:
        idea = session.get(TradeIdea, trade.idea_id)
        if idea is None or IdeaStatus(idea.status).is_terminal:
            continue
        goal = _target_for_trade(trade, idea)
        if goal is not None and _move(
            session,
            idea,
            goal,
            code="paper_lifecycle_repair",
            detail=f"статус идеи восстановлен из paper-сделки {trade.id}",
            snapshot={
                "paper_trade_id": str(trade.id),
                "paper_status": str(getattr(trade.status, "value", trade.status)),
                "tps_taken": trade.tps_taken,
            },
        ):
            changed += 1

    # Trigger without a trade is a short-lived decision request, not standing
    # advice for the full multi-day idea horizon.
    triggered = list(
        session.execute(
            select(TradeIdea).where(TradeIdea.status == IdeaStatus.TRIGGERED)
        ).scalars()
    )
    for idea in triggered:
        if idea.id in trade_by_idea:
            continue
        if moment - _triggered_at(session, idea) < _decision_ttl(idea):
            continue
        if _move(
            session,
            idea,
            IdeaStatus.TIMED_OUT,
            code="decision_window_expired",
            detail="окно решения по триггеру истекло; старый вход больше не предлагается",
        ):
            changed += 1

    if changed:
        session.flush()
    return changed


def _authorized(request: Request) -> bool:
    return bool(getattr(request.state, "device_authenticated", False))


def _terminal_approval_response(session: Session, request: Request) -> Response | None:
    if request.method != "POST" or not request.url.path.endswith("/approve-paper"):
        return None
    parts = request.url.path.strip("/").split("/")
    try:
        idea_id = UUID(parts[-2])
    except (IndexError, ValueError):
        return None
    trade = session.execute(
        select(PaperTrade)
        .where(PaperTrade.idea_id == idea_id)
        .order_by(PaperTrade.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if trade is None or PaperStatus(trade.status) in {PaperStatus.PENDING, PaperStatus.OPEN}:
        return None
    reason = trade.close_reason or trade.outcome or str(trade.status)
    return JSONResponse(
        status_code=409,
        content={
            "detail": "paper-план уже завершён и повторно не подтверждается: " + reason
        },
    )


class OperationalLifecycleMiddleware(BaseHTTPMiddleware):
    """Repair lifecycle before owner-facing reads without weakening auth."""

    _PREFIXES = (
        "/api/v1/ideas",
        "/api/v1/paper",
        "/api/v1/journal",
        "/api/v1/notifications",
    )

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.url.path.startswith(self._PREFIXES) and _authorized(request):
            with session_scope() as session:
                reconcile_operational_lifecycle(session)
                blocked = _terminal_approval_response(session, request)
                if blocked is not None:
                    return blocked
        return await call_next(request)


__all__ = ["OperationalLifecycleMiddleware", "reconcile_operational_lifecycle"]
