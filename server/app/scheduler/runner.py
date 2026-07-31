"""Планировщик (engine-ТЗ §26).

«Не хардкодить торговые часы; использовать calendar.» Календаря торгов у нас
нет, и выдумывать его расписанием вида «с 10 до 23 по будням» нельзя: часы
меняются, бывают праздники, вечерние сессии и внеплановые остановки.

Поэтому расписание выводится **из самих данных**: задача загрузки идёт
регулярно, а сканирование запускается только если появились новые закрытые
бары. Рынок закрыт — новых баров нет — скан не идёт и ресурсов не тратит.
Это не обход требования, а его выполнение доступными средствами: календарь
здесь — сам поток котировок.

Планировщик однопоточный и последовательный намеренно. Параллельный скан
поверх незавершённой загрузки читал бы полуприехавшие данные, а на них
строится план сделки.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Bar, DataQualityEvent, PortfolioModel
from ..models.enums import Timeframe

log = logging.getLogger("signalai.scheduler")


@dataclass
class JobResult:
    name: str
    started_at: datetime
    finished_at: datetime
    ok: bool
    detail: str = ""

    @property
    def elapsed_ms(self) -> int:
        return int((self.finished_at - self.started_at).total_seconds() * 1000)


@dataclass
class Job:
    """Задача с интервалом и последним запуском."""

    name: str
    every: timedelta
    run: Callable[[Session], str]
    last_run: datetime | None = None
    failures: int = 0

    def due(self, now: datetime) -> bool:
        return self.last_run is None or now - self.last_run >= self.every


@dataclass
class Scheduler:
    jobs: list[Job] = field(default_factory=list)
    history: list[JobResult] = field(default_factory=list)
    max_history: int = 200

    def add(self, name: str, every: timedelta, run: Callable[[Session], str]) -> None:
        self.jobs.append(Job(name=name, every=every, run=run))

    def tick(
        self,
        session: Session,
        *,
        now: datetime | None = None,
        autocommit: bool = True,
    ) -> list[JobResult]:
        """Выполнить все назревшие задачи по очереди.

        Отказ одной задачи не отменяет остальные и не останавливает
        планировщик: сломавшаяся загрузка одной площадки не должна лишать
        владельца пересчёта по другой.

        Каждая задача фиксируется отдельно. Это не оптимизация: откат после
        упавшего скана унёс бы вместе с ним и загруженные баром ранее данные,
        то есть отказ на последнем шаге стирал бы работу всех предыдущих.
        ``autocommit=False`` нужен тестам, которые держат свою транзакцию.
        """
        moment = now or datetime.now(UTC)
        results: list[JobResult] = []
        for job in self.jobs:
            if not job.due(moment):
                continue
            started = datetime.now(UTC)
            try:
                detail = job.run(session)
                if autocommit:
                    session.commit()
                job.failures = 0
                ok = True
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                job.failures += 1
                ok = False
                log.exception("задача %s упала", job.name)
                session.rollback()
            finished = datetime.now(UTC)
            job.last_run = moment
            result = JobResult(job.name, started, finished, ok, detail)
            results.append(result)
            self.history.append(result)
        del self.history[: max(0, len(self.history) - self.max_history)]
        return results


def latest_bar_time(
    session: Session, timeframe: Timeframe = Timeframe.H1
) -> datetime | None:
    """Время самого свежего закрытого бара во всей базе."""
    return session.execute(
        select(func.max(Bar.open_time)).where(
            Bar.timeframe == timeframe, Bar.is_closed.is_(True)
        )
    ).scalar_one_or_none()


def build_default_scheduler(
    *,
    universe_every: timedelta = timedelta(hours=6),
    ingest_every: timedelta = timedelta(minutes=15),
    review_every: timedelta = timedelta(hours=6),
    supervise_every: timedelta = timedelta(minutes=15),
    trigger_every: timedelta = timedelta(minutes=15),
    scan_every: timedelta = timedelta(minutes=15),
    portfolio_every: timedelta = timedelta(hours=24),
    fetch=None,
) -> Scheduler:
    """Расписание по умолчанию: вселенная → загрузка → допуск → скан.

    Порядок здесь не косметический, он и есть §5: справочник обновляется
    первым (иначе загружать нечего), допуск идёт после загрузки (иначе
    ликвидность нечем мерить), а скан — последним и только по новым барам.

    Скан объявлен отдельной задачей, но фактически привязан к данным: он
    сравнивает время последнего бара с тем, что было при прошлом прогоне, и
    не делает ничего, если рынок не двигался. Календарь торгов при этом не
    нужен — его роль играет сам поток котировок.

    Вселенная пересобирается редко: состав срочного рынка меняется не чаще
    раза в квартал, а каждый проход — это полная выгрузка доски.
    """
    from ..market.ingest import ingest_universe
    from ..market.investments import classify_funds, sync_investments
    from ..market.universe import review_universe, sync_crypto, sync_futures
    from ..pipeline.scan import scan as run_scan
    from ..pipeline.supervise import supervise as run_supervise
    from ..pipeline.trigger import recheck as run_trigger_recheck
    from ..portfolio.build import build_all

    scheduler = Scheduler()
    state: dict[str, datetime | None] = {"last_bar": None}

    def universe(session: Session) -> str:
        parts = []
        errors = []
        for name, sync in (
            ("MOEX", sync_futures),
            ("crypto", sync_crypto),
            ("инвестиции", sync_investments),
        ):
            try:
                kept = sync(session, fetch=fetch)
                parts.append(f"{name}: {len(kept)}")
            except Exception as exc:
                # Отказ одной площадки не должен обнулять вселенную другой:
                # иначе недоступность биржи выглядит как «инструментов нет».
                errors.append(f"{name} — {type(exc).__name__}: {exc}")
        detail = ", ".join(parts) if parts else "ничего не обновлено"
        if errors:
            detail += "; отказы: " + "; ".join(errors)
        return detail

    def review(session: Session) -> str:
        report = review_universe(session, fetch=fetch)
        rejected = [
            f"{key} — {v.reasons[0]}"
            for key, v in report.verdicts.items()
            if not v.admitted and v.reasons
        ]
        detail = f"проверено {report.checked}, допущено {report.admitted}"
        if rejected:
            detail += "; не допущены: " + "; ".join(rejected[:3])
        return detail

    def ingest(session: Session) -> str:
        reports = ingest_universe(session, fetch=fetch)
        written = sum(r.written for r in reports)
        updated = sum(r.updated for r in reports)
        failed = [r for r in reports if not r.ok]
        detail = f"новых баров {written}, уточнено {updated}"
        if failed:
            detail += f", отказов {len(failed)}: " + "; ".join(
                f"{r.instrument_id} — {r.error}" for r in failed[:3]
            )
        return detail

    def supervise(session: Session) -> str:
        # Идёт до скана, а не после: идея, которую рынок уже обогнал, не
        # должна участвовать в отборе дня и занимать место живой.
        report = run_supervise(session)
        detail = f"проверено {report.checked}"
        if report.no_data:
            detail += f", без баров {report.no_data}"
        if report.changed:
            detail += (
                f", закрыто {report.changed}"
                f" (обогнали {report.missed}, сломано {report.cancelled},"
                f" по сроку {report.timed_out})"
            )
            detail += "; " + "; ".join(report.details[:2])
        else:
            detail += ", живых изменений нет"
        return detail

    def trigger(session: Session) -> str:
        # Идёт после супервизора и до скана. После — потому что подтверждать
        # идею, которую рынок уже обогнал, незачем: супервизор её закроет.
        # До — потому что подтверждённая идея должна успеть в отбор дня.
        report = run_trigger_recheck(session)
        detail = f"проверено {report.checked}"
        if report.not_eligible:
            # Не «пропущено», а «перепроверять нечего»: этим идеям не хватило
            # не подтверждения, а качества, и новые свечи его не добавят.
            detail += f", вне перепроверки {report.not_eligible}"
        if report.no_data:
            detail += f", без баров {report.no_data}"
        if report.promoted:
            detail += f", подтверждено {report.promoted}"
            detail += "; " + "; ".join(report.details[:2])
        else:
            detail += ", новых подтверждений нет"
        return detail

    def scan(session: Session) -> str:
        newest = latest_bar_time(session)
        if newest is None:
            return "баров нет — сканировать нечего"
        if state["last_bar"] is not None and newest <= state["last_bar"]:
            # Рынок не двигался: новый скан дал бы тот же ответ, а идеи
            # продублировались бы в журнале.
            return f"новых баров нет с {newest.isoformat()} — скан пропущен"
        state["last_bar"] = newest
        result = run_scan(session)
        return (
            f"просмотрено {result.scanned}, идей {result.produced}, "
            f"пропущено {len(result.skipped)}, отказов {len(result.rejections)}"
        )

    def portfolio(session: Session) -> str:
        # Прогон тяжёлый — сотня оптимизаций на каждый вариант, — а
        # планировщик последователен: пока он идёт, скан идей не работает.
        # Поэтому сначала дешёвый вопрос: появились ли вообще новые данные
        # с прошлой сборки. Не появились — считать нечего, тот же состав
        # получится тот же.
        newest = session.execute(
            select(func.max(Bar.open_time)).where(
                Bar.timeframe == Timeframe.D1, Bar.is_closed.is_(True)
            )
        ).scalar_one_or_none()
        built = session.execute(
            select(func.min(PortfolioModel.generated_at))
        ).scalar_one_or_none()
        if newest is not None and built is not None and built > newest:
            return f"новых дневок с {newest.date().isoformat()} нет — пересчёт пропущен"

        # Класс фонда уточняется перед сборкой, а не при загрузке: он
        # выводится из накопленного ряда цен, и до первой истории мерить
        # нечего. Дешёвая операция, ошибиться порядком запуска дороже.
        classified = classify_funds(session)
        report = build_all(session)
        detail = (
            f"вселенная {report.universe}, срез прошли {report.screened}, "
            f"кандидатов {report.candidates}, пакетов допущено "
            f"{report.admitted} из {len(report.packages)}"
        )
        # Сколько доходных бумаг дошло до отбора. «Рынок акций падает» и
        # «доходных бумаг во вселенной нет» — разные поломки, и по общему
        # числу пакетов их не различить.
        if report.income_note:
            detail += f"; {report.income_note}"
        else:
            detail += f", из них доходных {report.income_candidates}"
        if classified:
            detail += f", классов фондов уточнено {len(classified)}"
        if report.note:
            detail += f"; {report.note}"
        return detail

    scheduler.add("universe", universe_every, universe)
    scheduler.add("ingest", ingest_every, ingest)
    scheduler.add("review", review_every, review)
    scheduler.add("supervise", supervise_every, supervise)
    scheduler.add("trigger", trigger_every, trigger)
    scheduler.add("scan", scan_every, scan)
    scheduler.add("portfolio", portfolio_every, portfolio)
    return scheduler


def run_forever(
    session_factory,
    scheduler: Scheduler,
    *,
    interval_seconds: int = 60,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Основной цикл. Каждая итерация — своя сессия.

    Одна сессия на всё время работы копила бы объекты и держала соединение
    открытым сутками. Транзакции внутри тика закрывает сам ``tick`` — по
    одной на задачу.
    """
    should_stop = stop or (lambda: False)
    while not should_stop():
        session = session_factory()
        try:
            for result in scheduler.tick(session):
                log.info(
                    "%s: %s (%d мс) — %s",
                    result.name,
                    "ок" if result.ok else "ОТКАЗ",
                    result.elapsed_ms,
                    result.detail,
                )
                # Итог задачи пишется всегда, а не только при отказе.
                #
                # Планировщик — отдельный процесс без входящих портов, и
                # увидеть его работу можно было лишь в логах контейнера,
                # то есть с доступом к серверу. Владелец, глядя на пустой
                # экран, не мог отличить «движок считает прямо сейчас» от
                # «движок молчит третий день». Длинная задача при этом не
                # пишет ничего, пока не закончится, — по логу деплоя это
                # выглядело как полная тишина.
                session.add(
                    DataQualityEvent(
                        source="scheduler",
                        flag="JOB_FAILED" if not result.ok else "JOB_DONE",
                        detail=(
                            f"{result.name}: "
                            f"{'ок' if result.ok else 'ОТКАЗ'} "
                            f"за {result.elapsed_ms // 1000} с — {result.detail}"
                        )[:512],
                    )
                )
            session.commit()
        except Exception:
            session.rollback()
            log.exception("тик планировщика упал целиком")
        finally:
            session.close()
        time.sleep(interval_seconds)
