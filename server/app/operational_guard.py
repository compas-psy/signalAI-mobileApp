"""Fail-safe reconciliation for the owner-facing trading lifecycle.

The scheduler remains the primary tracker.  This guard exists for one class of
failure that is unacceptable in a personal trading product: the database can
contain a paper trade while the idea shown to the owner still says
``TRIGGERED / можно действовать``.  It also kills a pending entry when the
market has already completed the thesis without ever giving the promised
entry.

The guard is deliberately conservative.  It never creates a trade and never
moves an entry/stop/target.  It can only repair state to match an existing
paper trade or terminate a stale plan.
"""

from __future__ import annotations

import hmac
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from .db import session_scope
from .journal.lifecycle import ALLOWED, TransitionRequest, transition
from .models import Bar, IdeaEvent, PaperTrade, TradeIdea
from .models.enums import Direction, IdeaStatus, PaperStatus, Timeframe


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_long(value) -> bool:
    return str(value) == str(Direction.LONG)


def _path(start: IdeaStatus, goal: IdeaStatus) -> list[IdeaStatus]:
    if start is goal:
        return []
    seen = {start}
    queue: list[tuple[IdeaStatus, list[IdeaStatus]]] = [(start, [])]
    while queue:
        node, path = queue.pop(0)
        for nxt in ALLOWED.get(node, frozenset()):
            if nxt in seen:
                continue
            candidate = [*path, nxt]
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
    start = IdeaStatus(idea.status)
    if start is goal or start.is_terminal:
        return False
    path = _path(start, goal)
    if not path:
        return False
    changed = False
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
        changed = True
    return changed


def _decision_ttl(idea: TradeIdea) -> timedelta:
    """How long a trigger is still a decision, not historical information."""
    tf = str(idea.trigger_timeframe or "").strip().lower()
    if tf in {"1m", "3m", "5m", "15m", "30m", "m1", "m3", "m5", "m15", "m30"}:
        return timedelta(hours=2)
    if tf in {"4h", "h4"}:
        return timedelta(hours=24)
    if tf in {"1d", "d1", "day"}:
        return timedelta(hours=72)
    # H1 and unknown intraday triggers: one trading session at most.
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


def _close_pending_without_entry(
    session: Session,
    trade: PaperTrade,
    idea: TradeIdea,
    *,
    now: datetime,
) -> bool:
    """Cancel a limit entry if the thesis already happened before the entry.

    We replay closed H1 bars from approval time in chronological order.  The
    first touch of the entry wins: after that the normal paper tracker owns the
    trade.  If TP1 or the invalidation side happens first, returning to the old
    entry later must *not* resurrect the trade.
    """
    if PaperStatus(trade.status) is not PaperStatus.PENDING:
        return False

    activated = _utc(trade.opened_at)
    bars = list(
        session.execute(
            select(Bar)
            .where(
                Bar.instrument_id == trade.instrument_id,
                Bar.timeframe == Timeframe.H1,
                Bar.is_closed.is_(True),
                Bar.open_time > activated,
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
        if bar.low <= trade.entry <= bar.high:
            # There was a valid entry before any stale condition.  Normal
            # tracker will fill/manage it; do not reinterpret history here.
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
            goal = IdeaStatus.MISSED if current in {
                IdeaStatus.DISCOVERED,
                IdeaStatus.WATCH,
                IdeaStatus.TRIGGERED,
            } else IdeaStatus.CANCELLED
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
        current = IdeaStatus(idea.status)
        if trade.outcome == "MISS" and current in {
            IdeaStatus.DISCOVERED,
            IdeaStatus.WATCH,
            IdeaStatus.TRIGGERED,
        }:
            return IdeaStatus.MISSED
        return IdeaStatus.TIMED_OUT if current is not IdeaStatus.ACTIVE else IdeaStatus.CANCELLED
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

    # First invalidate stale pending entries.  This must happen before a
    # legacy TRIGGERED+PENDING row is repaired to ACTIVE, otherwise a clean
    # TRIGGERED -> MISSED transition would become impossible.
    for trade in trades:
        if PaperStatus(trade.status) is not PaperStatus.PENDING:
            continue
        idea = session.get(TradeIdea, trade.idea_id)
        if idea is None:
            continue
        if _close_pending_without_entry(session, trade, idea, now=moment):
            changed += 1
            continue
        if moment >= _utc(trade.expires_at):
            trade.status = PaperStatus.CANCELLED
            trade.closed_at = moment
            trade.outcome = "TIMEOUT"
            trade.close_reason = "цена до заявки не дошла, срок вышел"
            if _move(
                session,
                idea,
                IdeaStatus.TIMED_OUT,
                code="paper_entry_timeout_guard",
                detail=trade.close_reason,
                snapshot={"paper_trade_id": str(trade.id)},
            ):
                changed += 1

    # Existing paper trade is stronger evidence than a stale idea status.
    for trade in trades:
        idea = session.get(TradeIdea, trade.idea_id)
        if idea is None or IdeaStatus(idea.status).is_terminal:
            continue
        goal = _target_for_trade(trade, idea)
        if goal is None:
            continue
        if _move(
            session,
            idea,
            goal,
            code="paper_lifecycle_repair",
            detail=f"статус идеи восстановлен из paper-сделки {trade.id}",
            snapshot={
                "paper_trade_id": str(trade.id),
                "paper_status": str(trade.status),
                "tps_taken": trade.tps_taken,
            },
        ):
            changed += 1

    # A trigger without a trade is a request for a decision, not a standing
    # recommendation forever.  Expire it by the trigger timeframe even if an
    # old idea-level horizon was much longer.
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
    expected = os.environ.get("SIGNALAI_DEVICE_TOKEN", "").strip()
    if not expected:
        return False
    authorization = request.headers.get("authorization", "")
    scheme, _, supplied = authorization.partition(" ")
    return (
        scheme.lower() == "bearer"
        and bool(supplied)
        and hmac.compare_digest(supplied.strip(), expected)
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
        return await call_next(request)


__all__ = ["OperationalLifecycleMiddleware", "reconcile_operational_lifecycle"]
