"""Сопровождение живых идей (engine-ТЗ §9, §18).

Идея рождалась и висела до истечения срока. Что с ней случилось за эти дни —
не спрашивал никто: цена могла уйти к третьей цели без единого касания зоны
входа, а карточка по-прежнему предлагала «Watch, сигнал живёт 4 дн 6 ч».
Владелец смотрел на график, видел цену у TP3 и не мог ответить на простой
вопрос: это ещё сделка или уже история.

Ответить обязана система, а не человек. Здесь идея сверяется с барами,
пришедшими **после** её появления, и переводится в состояние, которое
соответствует случившемуся:

* цена прошла все цели, не задев зону входа — ``MISSED``: сетап отработал
  без нас, входить по этому плану уже некуда;
* цена прошла стоп, не задев зону входа — ``CANCELLED``: замысел сломан,
  это не «поздний вход», а другая рыночная картина;
* цена **была** в зоне входа и после этого дошла до дальней цели —
  ``MISSED`` с другой причиной: войти было можно, подтверждения не
  случилось, план разыгрался без нас;
* цена была в зоне и после этого прошла стоп — ``CANCELLED``;
* срок вышел — ``TIMED_OUT``.

Четвёртый и пятый случаи появились не сразу, и их отсутствие было дырой в
самом заметном месте. Раньше касание зоны обрывало проверку: «вход был
возможен, дальше это сопровождение сделки, молчим». Но сопровождать было
некому — подтверждения не было, сделки не существовало, — и идея замолкала
навсегда. Полностью отработавший план оставался в списке живых со
состоянием «Watch» и ценой у третьей цели.

Чего здесь **нет** и быть не должно: автоматического перевода в
``TRIGGERED``. Триггер — это §11 с подтверждением на своём таймфрейме, а не
«цена коснулась зоны». Подменить одно другим значит выдать за сигнал
случайный прокол. Подтверждением занимается отдельный шаг —
``pipeline.trigger``, — и он перепроверяет тот же детектор на новых барах, а
не гадает по касанию.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..journal.lifecycle import TransitionRequest, transition
from ..models import Bar, TradeIdea
from ..models.enums import Direction, IdeaStatus, Timeframe

# Состояния, за которыми имеет смысл следить: сделки ещё нет, а план живёт.
#
# ``TRIGGERED`` входит сюда намеренно: сработавший сигнал, который владелец не
# подтвердил, тоже может быть обогнан рынком — и тогда он такой же
# «MISSED», как и наблюдение.
WATCHED: frozenset[IdeaStatus] = frozenset(
    {
        IdeaStatus.DISCOVERED,
        IdeaStatus.WATCH,
        IdeaStatus.TRIGGERED,
        IdeaStatus.RISK_BLOCKED,
        IdeaStatus.DATA_BLOCKED,
    }
)

# Таймфрейм сверки. Часовой, а не дневной: зона входа шириной в полпроцента
# на дневном баре неотличима от его тела, и «цена заходила в зону» стало бы
# правдой почти всегда.
CHECK_TF = Timeframe.H1


@dataclass(frozen=True, slots=True)
class Verdict:
    """Что случилось с идеей после её появления."""

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
    #: Кто именно остался без баров. Счётчик без имён три дня прятал
    #: слепоту по ETHUSDT: идея с пройденным стопом висела в Watch, потому
    #: что судить её было не по чему, а «без баров 1» не говорил, у кого.
    no_data_instruments: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return self.missed + self.cancelled + self.timed_out


def _touched_entry(idea: TradeIdea, low: Decimal, high: Decimal) -> bool:
    """Заходила ли цена в зону входа.

    Пересечение диапазонов, а не «закрытие внутри»: лимитная заявка стоит в
    зоне и исполняется тенью, а не телом свечи.
    """
    return low <= idea.entry_high and high >= idea.entry_low


def _final_target(idea: TradeIdea) -> Decimal:
    """Дальняя цель плана. Именно она отделяет «поздно» от «всё уже было»."""
    targets = [t for t in (idea.tp1, idea.tp2, idea.tp3) if t is not None]
    if not targets:
        return idea.tp1
    return max(targets) if idea.direction is Direction.LONG else min(targets)


def judge(
    idea: TradeIdea, bars: list[Bar], *, now: datetime
) -> Verdict:
    """Приговор по барам, пришедшим после появления идеи.

    Порядок проверок не косметический. Сначала — то, что случилось на рынке,
    и только потом срок: идея, которую рынок обогнал во вторник, обязана
    остаться «обогнанной», даже если смотрят на неё в пятницу. Иначе
    единственной причиной в журнале навсегда стало бы «срок вышел», и
    статистика пропущенных сетапов перестала бы существовать.
    """
    entered = False
    beyond_target = False
    beyond_stop = False
    target = _final_target(idea)

    for bar in bars:
        if not entered and _touched_entry(idea, bar.low, bar.high):
            entered = True
            # Не выходим из цикла: то, что случилось **после** входа, и есть
            # ответ на вопрос «это ещё сделка или уже история». Раньше здесь
            # стоял break, и идея, в зону которой цена зашла, замолкала
            # навсегда. Потом break стал continue — и появилась вторая
            # дыра: свеча, которая коснулась зоны и в тот же час прошла
            # стоп, проверку стопа пропускала. Быстрый прокол — вошёл и
            # выбил за одну свечу, цена вернулась — оставлял идею живой
            # навсегда: следующие бары стоп уже не трогали. Ровно так
            # выглядел ETHUSDT: спринг за час, идея неделю в «наблюдении».
            # Поэтому свеча входа проверяется дальше наравне со всеми.
        if idea.direction is Direction.LONG:
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
        # Вход был возможен — значит план разыгрался, а мы его не взяли.
        #
        # Это не «пропущенный сетап» в прежнем смысле (цена ушла мимо зоны),
        # а нечто хуже: войти было можно, подтверждения не случилось, и
        # рынок довёл замысел до конца без нас. Причина названа отдельным
        # кодом — иначе в статистике два разных промаха слились бы в один и
        # чинить было бы нечего.
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
        # Цена в зоне, план ещё жив — это рабочая идея. Срок при этом
        # проверяется наравне с остальными: лимитка, простоявшая в зоне до
        # истечения TTL, тоже перестаёт быть предложением.
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
    """Вышел ли срок сигнала.

    Причина различает два случая: цена так и не пришла в зону — или пришла,
    но подтверждения не случилось. Действия из них следуют разные: первое
    говорит о зоне, второе — о пороге подтверждения.
    """
    expires = idea.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires is None or now < expires:
        return Verdict(None)
    tail = (
        "цена в зоне входа была, подтверждения не случилось"
        if entered
        else "цена в зону входа так и не пришла"
    )
    return Verdict(
        IdeaStatus.TIMED_OUT,
        "ttl_expired",
        f"срок сигнала вышел {expires:%d.%m %H:%M UTC}, {tail}",
    )


def supervise(
    session: Session, *, now: datetime | None = None, limit: int = 500
) -> SuperviseReport:
    """Пройти по живым идеям и закрыть те, что рынок оставил позади."""
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
            # Баров нет — сказать нечего. Закрывать идею по отсутствию данных
            # нельзя: это отказ загрузки, а не событие рынка.
            report.no_data += 1
            report.no_data_instruments.append(idea.instrument_id)
            continue

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
                    "last_close": str(bars[-1].close),
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


__all__ = ["CHECK_TF", "SuperviseReport", "Verdict", "judge", "supervise", "WATCHED"]
