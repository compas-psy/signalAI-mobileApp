"""Сопровождение живых идей.

Для H1-trigger неподтверждённый WATCH не может держать instrument lock пять
дней: через сутки это уже другой рынок. Recovery policy поэтому даёт
DISCOVERED/WATCH максимум 24 часа на подтверждение. История не удаляется —
идея переводится в TIMED_OUT и освобождает инструмент для нового setup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..journal.lifecycle import TransitionRequest, transition
from ..models import Bar, TradeIdea
from ..models.enums import Direction, IdeaStatus, Timeframe

WATCHED: frozenset[IdeaStatus] = frozenset(
    {
        IdeaStatus.DISCOVERED,
        IdeaStatus.WATCH,
        IdeaStatus.TRIGGERED,
        IdeaStatus.RISK_BLOCKED,
        IdeaStatus.DATA_BLOCKED,
    }
)

CHECK_TF = Timeframe.H1
WATCH_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class Verdict:
    status: IdeaStatus | None
    reason_code: str = ""
    detail: str = ""

    @property
    def changed(self) -> bool:
        return self.status is not None


@dataclass
class SuperviseReport:
    checked: int = 0
    missed: int = 0
    cancelled: int = 0
    timed_out: int = 0
    no_data: int = 0
    no_data_instruments: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return self.missed + self.cancelled + self.timed_out


def _direction(idea: TradeIdea) -> Direction:
    value = idea.direction
    return value if isinstance(value, Direction) else Direction(str(value))


def _status(idea: TradeIdea) -> IdeaStatus:
    value = idea.status
    return value if isinstance(value, IdeaStatus) else IdeaStatus(str(value))


def _touched_entry(idea: TradeIdea, low: Decimal, high: Decimal) -> bool:
    return low <= idea.entry_high and high >= idea.entry_low


def _final_target(idea: TradeIdea) -> Decimal:
    targets = [t for t in (idea.tp1, idea.tp2, idea.tp3) if t is not None]
    if not targets:
        return idea.tp1
    return max(targets) if _direction(idea) is Direction.LONG else min(targets)


def judge(
    idea: TradeIdea, bars: list[Bar], *, now: datetime
) -> Verdict:
    """Определить, жив ли исходный план по закрытым барам после сигнала."""
    entered = False
    beyond_target = False
    beyond_stop = False
    target = _final_target(idea)
    direction = _direction(idea)

    for bar in bars:
        if not entered and _touched_entry(idea, bar.low, bar.high):
            entered = True
        if direction is Direction.LONG:
            if bar.high >= target:
                beyond_target = True
                break
            if bar.low <= idea.stop:
                beyond_stop = True
                break
        else:
            if bar.low <= target:
                beyond_target = True
                break
            if bar.high >= idea.stop:
                beyond_stop = True
                break

    if entered:
        if beyond_target:
            return Verdict(
                IdeaStatus.MISSED,
                "played_out_without_us",
                f"цена заходила в зону входа {idea.entry_low}–{idea.entry_high} "
                f"и дошла до дальней цели {target}: план отработал целиком, "
                "подтверждения не было",
            )
        if beyond_stop:
            return Verdict(
                IdeaStatus.CANCELLED,
                "stopped_after_entry",
                f"цена заходила в зону входа и затем прошла стоп {idea.stop}: "
                "замысел закончился, входить по нему уже некуда",
            )
        return _expired(idea, now, entered=True)

    if beyond_target:
        return Verdict(
            IdeaStatus.MISSED,
            "price_left_without_entry",
            f"цена дошла до дальней цели {target}, ни разу не зайдя в зону "
            f"входа {idea.entry_low}–{idea.entry_high}: сетап отработал без нас",
        )
    if beyond_stop:
        return Verdict(
            IdeaStatus.CANCELLED,
            "invalidated_before_entry",
            f"цена прошла стоп {idea.stop} до входа: замысел сломан, "
            "это уже другая картина рынка",
        )

    return _expired(idea, now, entered=False)


def _expired(idea: TradeIdea, now: datetime, *, entered: bool) -> Verdict:
    expires = idea.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)

    signal_time = idea.signal_time
    if signal_time is not None and signal_time.tzinfo is None:
        signal_time = signal_time.replace(tzinfo=UTC)

    # Неподтверждённый H1 setup имеет отдельный короткий SLA. Пятидневный
    # horizon относится к сделке/идее после trigger, а не к ожиданию одной
    # свечной формации. Старый WATCH иначе блокирует новые сигналы по symbol.
    status = _status(idea)
    if status in (IdeaStatus.DISCOVERED, IdeaStatus.WATCH) and signal_time is not None:
        watch_expires = signal_time + WATCH_TTL
        if expires is None or watch_expires < expires:
            expires = watch_expires

    if expires is None or now < expires:
        return Verdict(None)
    tail = (
        "цена в зоне входа была, подтверждения не случилось"
        if entered
        else "цена в зону входа так и не пришла"
    )
    return Verdict(
        IdeaStatus.TIMED_OUT,
        "watch_ttl_expired" if status in (IdeaStatus.DISCOVERED, IdeaStatus.WATCH) else "ttl_expired",
        f"срок сигнала вышел {expires:%d.%m %H:%M UTC}, {tail}",
    )


def supervise(
    session: Session, *, now: datetime | None = None, limit: int = 500
) -> SuperviseReport:
    moment = now or datetime.now(UTC)
    report = SuperviseReport()

    ideas = list(
        session.execute(
            select(TradeIdea)
            .where(TradeIdea.status.in_([s.value for s in WATCHED]))
            .order_by(TradeIdea.signal_time.desc())
            .limit(limit)
        ).scalars()
    )

    for idea in ideas:
        report.checked += 1
        since = idea.signal_time
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        bars = list(
            session.execute(
                select(Bar)
                .where(
                    Bar.instrument_id == idea.instrument_id,
                    Bar.timeframe == CHECK_TF,
                    Bar.is_closed.is_(True),
                    Bar.open_time >= since,
                )
                .order_by(Bar.open_time)
            ).scalars()
        )
        if not bars:
            # Даже без новых баров TTL обязан работать. Иначе выпавший из
            # data feed WATCH снова блокирует инструмент навсегда.
            verdict = _expired(idea, moment, entered=False)
            if not verdict.changed:
                report.no_data += 1
                if idea.instrument_id not in report.no_data_instruments:
                    report.no_data_instruments.append(idea.instrument_id)
                continue
        else:
            verdict = judge(idea, bars, now=moment)

        if not verdict.changed:
            continue

        transition(
            session,
            idea,
            TransitionRequest(
                new_status=verdict.status,
                reason_code=verdict.reason_code,
                reason_detail=verdict.detail[:512],
                market_snapshot={
                    "bars_checked": len(bars),
                    "last_close": str(bars[-1].close) if bars else None,
                    "checked_at": moment.isoformat(),
                },
            ),
        )
        if verdict.status is IdeaStatus.MISSED:
            report.missed += 1
        elif verdict.status is IdeaStatus.CANCELLED:
            report.cancelled += 1
        else:
            report.timed_out += 1
        report.details.append(f"{idea.instrument_id}: {verdict.detail}")

    return report


__all__ = [
    "CHECK_TF",
    "WATCH_TTL",
    "SuperviseReport",
    "Verdict",
    "judge",
    "supervise",
    "WATCHED",
]
