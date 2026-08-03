"""Сопровождение бумажной сделки (engine-ТЗ §18, §21).

Владелец сформулировал требование одной фразой: «она сама отслеживается и
закрывается, не зависает до времён, пока срок жизни идеи закончился».
Здесь это и реализовано — и здесь же исправлены четыре причины, по
которым раньше не работало.

**Стоп двигается и это видно.** После первой цели он встаёт в безубыток,
после второй — подтягивается к первой. Раньше перенос существовал только
внутри пересчёта, во временной переменной, которая не сохранялась: на
экране позиция всегда показывала исходный стоп, даже когда фактически
была защищена.

**Стоп проверяется раньше цели.** Внутри одного бара мы не знаем порядок
событий, и предполагать выгодный — значит рисовать себе прибыль, которой
не было. Худший случай честнее.

**Срок считается календарём, а не барами.** Прежний горизонт мерился
числом пришедших свечей, поэтому инструмент, по которому свечи перестали
приходить, давал бессмертную позицию.

**Молчание источника видно.** ``last_reconciled_at`` пишется только когда
бары действительно смотрели. Позиция, которую нечем сверить, обязана
объяснять это, а не выглядеть живой.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..journal.lifecycle import TransitionRequest, transition
from ..market.blindness import annotate as annotate_blind
from ..models import Bar, Instrument, PaperTrade, TradeIdea
from ..models.enums import Direction, IdeaStatus, PaperStatus, Timeframe

#: На каком таймфрейме ведётся сделка. Часовой: зона входа шириной в
#: полпроцента на дневном баре неотличима от его тела.
TRACK_TF = Timeframe.H1

#: Сколько ждать исполнения лимитки, прежде чем признать, что цена до неё
#: не дошла. В днях горизонта самой идеи — план и заявка живут один срок.
DEFAULT_HORIZON_DAYS = 5

#: Сколько сделка живёт после входа. Календарно: инструмент, по которому
#: бары перестали приходить, обязан протухнуть, а не жить вечно.
MAX_HOLD_DAYS = 30


@dataclass
class PaperReport:
    """Что вышло из одного прогона сопровождения."""

    opened: int = 0
    reconciled: int = 0
    filled: int = 0
    tp_hits: int = 0
    closed: int = 0
    stale: int = 0
    no_data: int = 0
    no_data_instruments: list[str] = field(default_factory=list)
    #: Почему по этим инструментам нет баров. Имя отвечает «по кому мы
    #: слепы» и молчит о том, почему, — а «биржа не знает символа» и
    #: «инструмент только что попал в отбор» требуют разных действий.
    no_data_reasons: str = ""
    details: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"сверено {self.reconciled}"]
        if self.opened:
            parts.append(f"открыто {self.opened}")
        if self.filled:
            parts.append(f"вход {self.filled}")
        if self.tp_hits:
            parts.append(f"целей взято {self.tp_hits}")
        if self.closed:
            parts.append(f"закрыто {self.closed}")
        if self.no_data:
            named = self.no_data_reasons or ", ".join(self.no_data_instruments[:3])
            parts.append(f"без баров {self.no_data} ({named})")
        if self.details:
            parts.append("; ".join(self.details[:2]))
        return ", ".join(parts)


def _targets(idea: TradeIdea) -> tuple[list[Decimal], list[Decimal]]:
    """Цели плана и доли выхода.

    Доли равные, если стратегия не сказала иначе: выдумывать распределение
    за неё — значит показать владельцу R, которого план не обещал.
    """
    prices = [p for p in (idea.tp1, idea.tp2, idea.tp3) if p is not None]
    if not prices:
        return [], []
    share = Decimal(1) / Decimal(len(prices))
    return prices, [share] * len(prices)


def open_for(
    session: Session, idea: TradeIdea, *, now: datetime | None = None
) -> PaperTrade | None:
    """Завести бумажную сделку по сработавшей идее.

    Владелец разрешил стартовать без подтверждения. Но не по любой идее: у
    плана должны быть цели, иначе вести нечего.
    """
    moment = now or datetime.now(UTC)
    prices, shares = _targets(idea)
    if not prices:
        return None

    existing = session.execute(
        select(PaperTrade).where(
            PaperTrade.idea_id == idea.id,
            PaperTrade.status.in_([PaperStatus.PENDING, PaperStatus.OPEN]),
        )
    ).scalars().first()
    if existing is not None:
        return None

    horizon = idea.horizon_days or DEFAULT_HORIZON_DAYS
    # Сделка начинается там, где родился план, а не там, где мы завели
    # запись. Иначе вся история между появлением идеи и первым прогоном
    # теряется: заявка, исполнившаяся вчера, выглядит невыкупленной, а
    # взятые цели — не взятыми. Для досчёта уже висящих сделок это и есть
    # главное: переиграть по настоящим барам, а не с сегодняшнего дня.
    born = idea.signal_time or moment
    if born.tzinfo is None:
        born = born.replace(tzinfo=UTC)
    trade = PaperTrade(
        idea_id=idea.id,
        instrument_id=idea.instrument_id,
        direction=idea.direction,
        status=PaperStatus.PENDING,
        entry=idea.entry_reference,
        initial_stop=idea.stop,
        current_stop=idea.stop,
        tp_prices=[str(p) for p in prices],
        tp_shares=[str(s) for s in shares],
        tps_taken=0,
        realized_r=Decimal(0),
        opened_at=born,
        expires_at=born + timedelta(days=horizon),
    )
    session.add(trade)
    return trade


def _bars_since(session: Session, trade: PaperTrade) -> list[Bar]:
    since = trade.last_reconciled_at or trade.opened_at
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return list(
        session.execute(
            select(Bar)
            .where(
                Bar.instrument_id == trade.instrument_id,
                Bar.timeframe == TRACK_TF,
                Bar.is_closed.is_(True),
                Bar.open_time >= since,
            )
            .order_by(Bar.open_time)
        ).scalars()
    )


def _is_long(trade: PaperTrade) -> bool:
    """Направление сделки, как бы оно ни было представлено.

    Сравнение по тождеству (`is Direction.LONG`) здесь опасно: до сброса в
    базу поле хранит ту же строку, что пришла из идеи, и тождество даёт
    False. Ошибка тихая и самая дорогая из возможных — лонг начинает
    вестись как шорт: стоп срабатывает сверху, цели снизу, и сделка
    закрывается убытком в тот же час, когда открылась.
    """
    return str(trade.direction) == str(Direction.LONG)


def _risk(trade: PaperTrade) -> Decimal:
    risk = abs(trade.entry - trade.initial_stop)
    return risk if risk > 0 else Decimal(1)


def _r_of(trade: PaperTrade, price: Decimal) -> Decimal:
    """Сколько R даёт выход по этой цене."""
    move = (
        price - trade.entry if _is_long(trade) else trade.entry - price
    )
    return move / _risk(trade)


def advance(trade: PaperTrade, bars: list[Bar], *, now: datetime) -> list[str]:
    """Провести сделку по барам. Возвращает события для журнала.

    Порядок внутри бара: сначала стоп, потом цели. Внутри одного часа мы не
    знаем, что случилось раньше, и выбирать выгодный порядок — значит
    рисовать себе прибыль, которой не было.
    """
    events: list[str] = []
    prices = [Decimal(p) for p in trade.tp_prices]
    shares = [Decimal(s) for s in trade.tp_shares]
    long = _is_long(trade)

    for bar in bars:
        if trade.status is PaperStatus.PENDING:
            touched = bar.low <= trade.entry <= bar.high
            if not touched:
                continue
            trade.status = PaperStatus.OPEN
            events.append("вход исполнен")
            # Свеча входа проверяется дальше наравне со всеми: быстрый
            # прокол — вошли и выбило в тот же час — реальный исход.

        if trade.status is not PaperStatus.OPEN:
            break

        stop_hit = bar.low <= trade.current_stop if long else bar.high >= trade.current_stop
        if stop_hit:
            remaining = Decimal(1) - sum(shares[: trade.tps_taken], Decimal(0))
            trade.realized_r += _r_of(trade, trade.current_stop) * remaining
            trade.status = PaperStatus.CLOSED
            trade.closed_at = bar.open_time
            trade.outcome = "BE" if trade.breakeven_at else "SL"
            trade.close_reason = (
                "стоп в безубытке" if trade.breakeven_at else "стоп"
            )
            events.append(trade.close_reason)
            return events

        while trade.tps_taken < len(prices):
            target = prices[trade.tps_taken]
            reached = bar.high >= target if long else bar.low <= target
            if not reached:
                break
            trade.realized_r += _r_of(trade, target) * shares[trade.tps_taken]
            trade.tps_taken += 1
            events.append(f"цель {trade.tps_taken} взята")

            if trade.tps_taken == 1:
                # Безубыток. Не «страховка», а изменение сделки: дальше она
                # не может стать убыточной, и владелец обязан это видеть.
                trade.current_stop = trade.entry
                trade.breakeven_at = bar.open_time
                events.append("стоп в безубытке")
            elif trade.tps_taken == 2:
                # Трейлинг к первой цели. Прежняя реализация ставила
                # безубыток один раз и больше стоп не двигала.
                trade.current_stop = prices[0]
                events.append("стоп подтянут к первой цели")

        if trade.tps_taken >= len(prices):
            trade.status = PaperStatus.CLOSED
            trade.closed_at = bar.open_time
            trade.outcome = "TP"
            trade.close_reason = "все цели взяты"
            events.append(trade.close_reason)
            return events

    return events


def _expire(trade: PaperTrade, now: datetime) -> str | None:
    """Календарное протухание — то, чего не было вовсе."""
    expires = trade.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if trade.status is PaperStatus.PENDING and now >= expires:
        trade.status = PaperStatus.CANCELLED
        trade.closed_at = now
        trade.outcome = "отм."
        trade.close_reason = "цена до заявки не дошла, срок вышел"
        return trade.close_reason

    opened = trade.opened_at
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=UTC)
    if trade.status is PaperStatus.OPEN and now - opened >= timedelta(
        days=MAX_HOLD_DAYS
    ):
        trade.status = PaperStatus.CLOSED
        trade.closed_at = now
        trade.outcome = "гор."
        trade.close_reason = f"держим дольше {MAX_HOLD_DAYS} дней — выходим"
        return trade.close_reason
    return None


#: Как состояние сделки отражается в статусе идеи. Половина этих статусов
#: была объявлена на сервере и мертва: в них не переводил никто.
_IDEA_STATUS = {
    0: IdeaStatus.FILLED,
    1: IdeaStatus.TP1_HIT,
    2: IdeaStatus.TP2_HIT,
}


def _path_to(start, goal: IdeaStatus) -> list[IdeaStatus]:
    """Разрешённый путь между статусами, если он есть.

    Переходы ступенчатые: `TRIGGERED → FILLED` напрямую запрещён, идея
    обязана пройти через `ACTIVE`. Ходить в обход таблицы нельзя — она и
    есть жизненный цикл §18, — а прописывать цепочки руками значит
    разойтись с ней при первой же правке. Поэтому путь ищется по самой
    таблице.
    """
    from ..journal.lifecycle import ALLOWED

    start = IdeaStatus(str(start))
    if start is goal:
        return []
    seen = {start}
    queue: list[tuple[IdeaStatus, list[IdeaStatus]]] = [(start, [])]
    while queue:
        node, path = queue.pop(0)
        for nxt in ALLOWED.get(node, frozenset()):
            if nxt in seen:
                continue
            step = [*path, nxt]
            if nxt is goal:
                return step
            seen.add(nxt)
            queue.append((nxt, step))
    return []


def _sync_idea(session: Session, trade: PaperTrade, idea: TradeIdea) -> None:
    if trade.status is PaperStatus.CLOSED:
        target = (
            IdeaStatus.STOPPED
            if trade.outcome in ("SL", "BE")
            else IdeaStatus.CLOSED
        )
    elif trade.status is PaperStatus.CANCELLED:
        target = IdeaStatus.TIMED_OUT
    elif trade.status is PaperStatus.OPEN:
        target = _IDEA_STATUS.get(trade.tps_taken, IdeaStatus.MANAGING)
    else:
        return
    if str(idea.status) == str(target):
        return
    detail = (trade.close_reason or f"взято целей {trade.tps_taken}")[:512]
    for step in _path_to(idea.status, target):
        transition(
            session,
            idea,
            TransitionRequest(
                new_status=step,
                reason_code="paper_tracking",
                reason_detail=detail,
                market_snapshot={
                    "current_stop": str(trade.current_stop),
                    "realized_r": str(trade.realized_r),
                },
            ),
        )


def track(session: Session, *, now: datetime | None = None) -> PaperReport:
    """Один прогон: открыть новые сделки и продвинуть живые."""
    moment = now or datetime.now(UTC)
    report = PaperReport()

    triggered = list(
        session.execute(
            select(TradeIdea).where(TradeIdea.status == IdeaStatus.TRIGGERED)
        ).scalars()
    )
    for idea in triggered:
        if open_for(session, idea, now=moment) is not None:
            report.opened += 1
    session.flush()

    live = list(
        session.execute(
            select(PaperTrade).where(
                PaperTrade.status.in_([PaperStatus.PENDING, PaperStatus.OPEN])
            )
        ).scalars()
    )
    for trade in live:
        bars = _bars_since(session, trade)
        if not bars:
            report.no_data += 1
            if trade.instrument_id not in report.no_data_instruments:
                report.no_data_instruments.append(trade.instrument_id)
        else:
            was_open = trade.status is PaperStatus.OPEN
            events = advance(trade, bars, now=moment)
            trade.last_reconciled_at = bars[-1].open_time
            report.reconciled += 1
            if not was_open and trade.status is not PaperStatus.PENDING:
                report.filled += 1
            report.tp_hits += sum(1 for e in events if e.startswith("цель"))
            if events:
                report.details.append(f"{trade.instrument_id}: {events[-1]}")

        # Срок проверяется всегда, даже когда баров нет: именно этого и не
        # хватало, чтобы позиция не висела вечно при молчащем источнике.
        expired = _expire(trade, moment)
        if expired:
            report.details.append(f"{trade.instrument_id}: {expired}")

        if trade.status in (PaperStatus.CLOSED, PaperStatus.CANCELLED):
            report.closed += 1

        idea = session.get(TradeIdea, trade.idea_id)
        if idea is not None:
            _sync_idea(session, trade, idea)

    if report.no_data_instruments:
        report.no_data_reasons = annotate_blind(
            session, report.no_data_instruments, now=moment
        )
    return report


__all__ = [
    "MAX_HOLD_DAYS",
    "PaperReport",
    "TRACK_TF",
    "advance",
    "open_for",
    "track",
]
