"""Near-real-time paper execution for crypto.

Signal discovery stays on audited H1/H4/D1 closed bars. Execution tracking is
a different problem: once the owner has approved a paper plan, waiting for the
next H1 close can miss an entry, target or stop by almost an hour. This module
uses Bybit's public 1-minute candles only as an execution sensor. They are not
persisted into the canonical feature store and never influence signal scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..market import crypto
from ..models import Instrument, PaperTrade, TradeIdea
from ..models.enums import PaperStatus, Venue
from .tracker import _expire, _sync_idea, advance


@dataclass(slots=True)
class LivePaperReport:
    checked: int = 0
    reconciled: int = 0
    filled: int = 0
    tp_hits: int = 0
    closed: int = 0
    awaiting_minute: int = 0
    failures: int = 0
    details: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"crypto live: проверено {self.checked}"]
        if self.reconciled:
            parts.append(f"сверено {self.reconciled}")
        if self.filled:
            parts.append(f"вход {self.filled}")
        if self.tp_hits:
            parts.append(f"целей {self.tp_hits}")
        if self.closed:
            parts.append(f"закрыто {self.closed}")
        if self.awaiting_minute:
            parts.append(f"ждут минуты {self.awaiting_minute}")
        if self.failures:
            parts.append(f"ошибок источника {self.failures}")
        if self.details:
            parts.append("; ".join(self.details[:2]))
        return ", ".join(parts)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def track_crypto_live(
    session: Session,
    *,
    now: datetime | None = None,
    fetch=None,
) -> LivePaperReport:
    """Advance approved crypto paper trades from closed one-minute candles.

    A closed 1m candle caps the detection lag at roughly one minute while
    preserving deterministic chronology. A forming candle is deliberately not
    replayed: after TP1 moves the stop to breakeven, reprocessing the same
    changing minute could fabricate a stop that happened before the move.

    Up to three hours are replayed on each call. This matters immediately after
    a deploy/restart: a DOGE entry crossed shortly before the new server came up
    must still be reconciled instead of being forgotten because the live loop
    was temporarily absent.
    """
    moment = now or datetime.now(UTC)
    report = LivePaperReport()

    rows = session.execute(
        select(PaperTrade, Instrument)
        .join(Instrument, Instrument.instrument_id == PaperTrade.instrument_id)
        .where(
            Instrument.venue == Venue.CRYPTO,
            PaperTrade.status.in_([PaperStatus.PENDING, PaperStatus.OPEN]),
        )
    ).all()

    for trade, instrument in rows:
        report.checked += 1
        try:
            kwargs = {"fetch": fetch} if fetch is not None else {}
            candles, _ = crypto.minute_klines(
                instrument.symbol,
                limit=180,
                now=moment,
                **kwargs,
            )
        except Exception as exc:
            report.failures += 1
            report.details.append(
                f"{instrument.symbol}: minute feed {type(exc).__name__}: {exc}"
            )
            continue

        since = _utc(trade.last_reconciled_at or trade.opened_at)
        bars = [
            candle
            for candle in candles
            if candle.is_closed and _utc(candle.open_time) > since
        ]

        if not bars:
            report.awaiting_minute += 1
        else:
            was_pending = PaperStatus(trade.status) is PaperStatus.PENDING
            before_tp = int(trade.tps_taken)
            events = advance(trade, bars, now=moment)
            trade.last_reconciled_at = _utc(bars[-1].open_time)
            report.reconciled += 1
            if was_pending and PaperStatus(trade.status) is not PaperStatus.PENDING:
                report.filled += 1
            report.tp_hits += max(0, int(trade.tps_taken) - before_tp)
            if events:
                report.details.append(f"{instrument.symbol}: {events[-1]}")

        expired = _expire(trade, moment)
        if expired:
            report.details.append(f"{instrument.symbol}: {expired}")

        if PaperStatus(trade.status) in {PaperStatus.CLOSED, PaperStatus.CANCELLED}:
            report.closed += 1

        idea = session.get(TradeIdea, trade.idea_id)
        if idea is not None:
            _sync_idea(session, trade, idea)

    session.flush()
    return report


__all__ = ["LivePaperReport", "track_crypto_live"]
