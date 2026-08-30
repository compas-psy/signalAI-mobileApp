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

from ..models import Bar, DataQualityEvent, Instrument
from ..models.enums import AssetClass, Timeframe, Venue
from ..market.blindness import annotate as annotate_blind
from ..ops.retention import RetentionAutopilotConfig
from .market_watermark import Watermarks, changed_lanes, snapshot as market_watermark_snapshot

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
        """Выполнить все назревшие задачи по очереди."""
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


def _admission_reason_code(reason: str) -> str:
    codes: list[str] = []
    probes = (
        ("R/R", "RR"),
        ("EV=", "EXPECTED_R"),
        ("p=", "PROBABILITY"),
        ("confidence=", "CONFIDENCE"),
        ("триггер", "TRIGGER"),
        ("календар", "ECONOMIC_EVENT"),
        ("ликвид", "LIQUIDITY"),
        ("лимит", "RISK_LIMIT"),
    )
    lowered = reason.lower()
    for needle, code in probes:
        if (needle.lower() if needle.isascii() else needle) in (
            lowered if needle.isascii() else reason.lower()
        ):
            if code not in codes:
                codes.append(code)
    return "+".join(codes) if codes else "ADMISSION_GATE"


def _terminal_from_skip(stage: str, reason: str) -> tuple[str, str]:
    if stage == "данные":
        lowered = reason.lower()
        if "дневн" in lowered:
            return "DATA_BLOCKED", "DATA_D1_INSUFFICIENT"
        if "часов" in lowered:
            return "DATA_BLOCKED", "DATA_H1_INSUFFICIENT"
        if "4h" in lowered:
            return "DATA_BLOCKED", "DATA_H4_INSUFFICIENT"
        return "DATA_BLOCKED", "DATA_INSUFFICIENT"
    if stage == "ликвидность":
        return "LIQUIDITY_BLOCKED", "LIQUIDITY_UNTRADEABLE"
    if stage == "допуск":
        return "ADMISSION_REJECTED", _admission_reason_code(reason)
    if stage == "дубль":
        return "DUPLICATE", "LIVE_IDEA_EXISTS"
    if stage == "ошибка":
        return "ERROR", "SCAN_EXCEPTION"
    return "ERROR", "UNKNOWN_SCAN_SKIP"


def _terminal_from_rejections(rows) -> tuple[str, str, str]:
    """Convert attributed strategy rejections to one deterministic terminal fact."""
    rows = tuple(rows)
    if not rows:
        return "SETUP_REJECTED", "NO_VALID_SETUP", ""

    parts: list[str] = []
    all_regime = True
    primary_code = "NO_VALID_SETUP"
    for index, row in enumerate(rows):
        rejection = row.rejection
        failed = rejection.failed
        first = failed[0] if failed else None
        check_name = "REJECTED" if first is None else first.name.upper()
        if first is None or first.name.lower() != "regime":
            all_regime = False
        code = f"{rejection.strategy.value}:{check_name}"
        if index == 0:
            primary_code = code
        parts.append(f"{code} — {rejection.reason}")
    stage = "REGIME_REJECTED" if all_regime else "SETUP_REJECTED"
    return stage, primary_code, " | ".join(parts)


def _record_bybit_scan_funnel(session: Session, result, *, occurred_at: datetime) -> None:
    """Persist one terminal machine-readable fact for every active Bybit symbol.

    The generic Scheduler has unit-test seams that intentionally pass a minimal
    non-database object while replacing the scan function. Observability must
    never make those seams stricter than the trading job itself, so only a
    DB-capable session attempts persistence. Production SQLAlchemy sessions
    always expose ``execute``.
    """

    if not callable(getattr(session, "execute", None)):
        return

    from ..control.bybit_funnel import FunnelFact, record_funnel_fact

    instrument_ids = set(
        session.execute(
            select(Instrument.instrument_id).where(
                Instrument.venue == Venue.CRYPTO,
                Instrument.asset_class == AssetClass.CRYPTO_PERPETUAL,
                Instrument.in_universe.is_(True),
                Instrument.is_tradable.is_(True),
            )
        ).scalars()
    )
    if not instrument_ids:
        return

    published = {
        idea.instrument_id for idea in result.ideas if idea.instrument_id in instrument_ids
    }
    skipped = {
        item.instrument_id: item
        for item in result.skipped
        if item.instrument_id in instrument_ids
    }
    rejected: dict[str, list] = {}
    for item in getattr(result, "attributed_rejections", ()):
        if item.instrument_id in instrument_ids:
            rejected.setdefault(item.instrument_id, []).append(item)
    sequence = max(0, int(occurred_at.timestamp() * 1_000_000))
    for instrument_id in sorted(instrument_ids):
        if instrument_id in published:
            terminal, code, detail = "PUBLISHED", "ACTIVE", "TradeIdea published"
        elif instrument_id in skipped:
            item = skipped[instrument_id]
            terminal, code = _terminal_from_skip(item.stage, item.reason)
            detail = item.reason
        elif instrument_id in rejected:
            terminal, code, detail = _terminal_from_rejections(rejected[instrument_id])
        else:
            terminal, code, detail = "SETUP_REJECTED", "NO_VALID_SETUP", ""
        record_funnel_fact(
            session,
            FunnelFact(instrument_id, terminal, code, sequence=sequence),
            detail=detail,
            occurred_at=occurred_at,
        )


def build_default_scheduler(
    *,
    universe_every: timedelta = timedelta(hours=6),
    ingest_every: timedelta = timedelta(minutes=15),
    review_every: timedelta = timedelta(hours=6),
    supervise_every: timedelta = timedelta(minutes=15),
    trigger_every: timedelta = timedelta(minutes=15),
    scan_every: timedelta = timedelta(minutes=15),
    portfolio_every: timedelta = timedelta(hours=24),
    research_every: timedelta = timedelta(hours=12),
    retention_every: timedelta = timedelta(minutes=15),
    retention_config: RetentionAutopilotConfig | None = None,
    resource_snapshot_provider=None,
    fetch=None,
) -> Scheduler:
    """Расписание по умолчанию: вселенная → загрузка → допуск → скан."""
    from ..market.ingest import ingest_universe
    from ..market.investments import classify_funds, sync_investments
    from ..market.universe import review_universe, sync_crypto, sync_futures
    from ..pipeline.scan import scan as run_scan
    from ..pipeline.supervise import supervise as run_supervise
    from ..paper.tracker import track as run_paper
    from ..pipeline.trigger import recheck as run_trigger_recheck
    from ..research.collector import collect_all
    from ..research.run_engines import run_demand
    from ..research.pipeline import expire as expire_hypotheses
    from ..research.sources import readiness, sync_registry
    from ..portfolio.build import build_all
    from ..portfolio.lifecycle import rebuild_due
    from ..ops.backpressure import build_backpressure_plan
    from ..ops.ollama_shed import shed_ollama_for_plan
    from ..ops.pressure import PressureClassifier
    from ..ops.remediation import record_resource_remediation
    from ..ops.resources import collect_resource_snapshot
    from ..ops.retention import run_safe_retention
    from ..ops.retention_attempts import (
        derive_retention_attempt_id,
        execute_retention_attempt,
    )

    scheduler = Scheduler()
    state: dict[str, Watermarks | None] = {"watermarks": None}
    retention = retention_config or RetentionAutopilotConfig()
    resource_snapshot = resource_snapshot_provider or collect_resource_snapshot
    pressure_classifier = PressureClassifier()

    def universe(session: Session) -> str:
        parts = []
        errors = []
        for name, sync in (
            ("MOEX", sync_futures),
            ("crypto", sync_crypto),
            ("инвестиции", sync_investments),
        ):
            try:
                with session.begin_nested():
                    kept = sync(session, fetch=fetch)
                parts.append(f"{name}: {len(kept)}")
            except Exception as exc:
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
        report = run_supervise(session)
        detail = f"проверено {report.checked}"
        if report.no_data:
            named = annotate_blind(session, report.no_data_instruments)
            detail += f", без баров {report.no_data} ({named}) — надзор по ним слеп"
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

    def paper(session: Session) -> str:
        return run_paper(session).summary()

    def trigger(session: Session) -> str:
        report = run_trigger_recheck(session)
        detail = f"проверено {report.checked}"
        if report.not_eligible:
            detail += f", вне перепроверки {report.not_eligible}"
        if report.no_data:
            named = annotate_blind(session, report.no_data_instruments)
            detail += f", без баров {report.no_data} ({named}) — надзор по ним слеп"
        if report.confirmed_but_blocked:
            detail += f", подтверждено но не допущено {report.confirmed_but_blocked}"
        if report.promoted:
            detail += f", подтверждено {report.promoted}"
            detail += "; " + "; ".join(report.details[:2])
        else:
            detail += ", новых подтверждений нет"
        return detail

    def scan(session: Session) -> str:
        current = market_watermark_snapshot(session)
        if not current:
            return "баров нет — сканировать нечего"

        previous = state["watermarks"]
        if previous is None:
            changed = tuple(sorted(current))
        else:
            changed = changed_lanes(previous, current)
            if not changed:
                newest = max(mark[0] for mark in current.values())
                return f"новых баров нет с {newest.isoformat()} — скан пропущен"

        from ..market.economic_events import load_owned_calendar

        occurred_at = datetime.now(UTC)
        result = run_scan(
            session,
            event_calendar=load_owned_calendar(now=occurred_at),
        )
        _record_bybit_scan_funnel(session, result, occurred_at=occurred_at)
        state["watermarks"] = current
        return (
            f"просмотрено {result.scanned}, идей {result.produced}, "
            f"пропущено {len(result.skipped)}, отказов {len(result.rejections)}; "
            f"обновились площадки {', '.join(changed)}"
        )

    def portfolio(session: Session) -> str:
        decision = rebuild_due(session)
        if not decision.due:
            return f"пересчёт пропущен — {decision.reason}"

        classified = classify_funds(session)
        report = build_all(session)
        detail = (
            f"причина {decision.reason}; вселенная {report.universe}, "
            f"срез прошли {report.screened}, кандидатов {report.candidates}, "
            f"пакетов допущено {report.admitted} из {len(report.packages)}"
        )
        if report.income_note:
            detail += f"; {report.income_note}"
        else:
            detail += f", из них доходных {report.income_candidates}"
        if classified:
            detail += f", классов фондов уточнено {len(classified)}"
        if report.note:
            detail += f"; {report.note}"
        return detail

    def research(session: Session) -> str:
        sync_registry(session)
        collected = collect_all(session)
        session.flush()
        engines = run_demand(session)
        expired = expire_hypotheses(session)
        source_state = readiness(session)
        detail = (
            f"источников {source_state['total']}, бесплатный маршрут у "
            f"{source_state['free_route']}; сбор: {collected.summary()}"
            f"; движки: {engines.summary()}"
        )
        if source_state["awaiting_terms_check"]:
            detail += (
                f", ждут проверки условий {len(source_state['awaiting_terms_check'])}"
                " — до неё сбор не запускается"
            )
        if expired:
            detail += f", протухло гипотез {expired}"
        return detail

    def resource_autopilot(session: Session) -> str:
        resource = resource_snapshot()
        assessment = pressure_classifier.evaluate(resource)
        plan = build_backpressure_plan(state=assessment.state)
        retention_attempt_id = None
        if retention.dry_run:
            retention_result = run_safe_retention(
                assessment=assessment,
                targets=retention.targets,
                now=resource.collected_at,
                dry_run=True,
            )
            attempt_detail = "dry-run"
        else:
            stable_attempt_id = derive_retention_attempt_id(
                targets=retention.targets,
                now=resource.collected_at,
                budget_period=retention.budget_period,
            )
            attempt = execute_retention_attempt(
                session,
                assessment=assessment,
                targets=retention.targets,
                now=resource.collected_at,
                dry_run=False,
                attempt_id=stable_attempt_id,
            )
            retention_result = attempt.retention
            retention_attempt_id = attempt.attempt_id
            attempt_detail = f"attempt={attempt.attempt_id}; execution={attempt.status.value}"
        ollama = shed_ollama_for_plan(plan)
        evidence = record_resource_remediation(
            session,
            assessment=assessment,
            plan=plan,
            ollama=ollama,
            retention=retention_result,
            now=resource.collected_at,
            force_audit=True,
            retention_attempt_id=retention_attempt_id,
        )
        return (
            f"pressure={assessment.state.value}; retention={retention_result.status.value}; "
            f"audit={'recorded' if evidence.recorded else 'deduplicated'}; {attempt_detail}"
        )

    scheduler.add("universe", universe_every, universe)
    scheduler.add("ingest", ingest_every, ingest)
    scheduler.add("review", review_every, review)
    scheduler.add("supervise", supervise_every, supervise)
    scheduler.add("trigger", trigger_every, trigger)
    scheduler.add("paper", supervise_every, paper)
    scheduler.add("scan", scan_every, scan)
    scheduler.add("portfolio", portfolio_every, portfolio)
    scheduler.add("research", research_every, research)
    if retention.enabled:
        scheduler.add("resource-autopilot", retention_every, resource_autopilot)
    return scheduler


def run_forever(
    session_factory,
    scheduler: Scheduler,
    *,
    interval_seconds: int = 60,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Основной цикл. Каждая итерация — своя сессия."""
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
