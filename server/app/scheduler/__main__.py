"""Точка входа планировщика: ``python -m app.scheduler``.

Отдельный процесс, а не фоновая задача внутри API. Причины две, и обе
практические. Uvicorn с несколькими воркерами запустил бы столько же копий
расписания, и каждая пошла бы грузить биржу и писать бары. А перезапуск API
ради выкатки не должен обрывать загрузку на середине.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from datetime import timedelta

from ..db import get_session_factory
from ..market.review_resilience import install as install_review_resilience
from ..notification_outbox import emit, materialize
from ..paper.live_tracker import track_crypto_live
from ..pipeline.risk_equity_runtime import install as install_risk_equity
from ..portfolio.equity_ranking_refresh import refresh as refresh_equity_ranking
from ..version import ENGINE_VERSION
from .runner import build_default_scheduler, run_forever

log = logging.getLogger("signalai.scheduler")


def _minutes(name: str, default: int) -> timedelta:
    raw = os.environ.get(name)
    if not raw:
        return timedelta(minutes=default)
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{name}={raw!r} — ожидались минуты целым числом") from None
    if value < 1:
        raise SystemExit(f"{name}={value} — интервал меньше минуты бессмыслен")
    return timedelta(minutes=value)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("SIGNALAI_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Universe review должен различать «Bybit измерил и тикер не прошёл» и
    # «Bybit вообще не удалось измерить». Один временный отказ snapshot раньше
    # делал все crypto is_tradable=false на шесть часов. Устанавливаем wrapper
    # до build_default_scheduler(), потому что runner захватывает ссылку на
    # review_universe именно при сборке расписания.
    install_review_resilience()

    # Аналогично убираем скрытый 100k fallback scan(): scheduler обязан считать
    # FORTS + crypto от явно выбранного владельцем risk.equity_rub=300000.
    # Явный RiskState из теста/бэктеста wrapper не перезаписывает.
    install_risk_equity()

    scheduler = build_default_scheduler(
        universe_every=_minutes("SIGNALAI_UNIVERSE_EVERY_MINUTES", 360),
        ingest_every=_minutes("SIGNALAI_INGEST_EVERY_MINUTES", 15),
        review_every=_minutes("SIGNALAI_REVIEW_EVERY_MINUTES", 360),
        scan_every=_minutes("SIGNALAI_SCAN_EVERY_MINUTES", 15),
        portfolio_every=_minutes("SIGNALAI_PORTFOLIO_EVERY_MINUTES", 60),
    )

    # Portfolio → Signals is a daily market snapshot, not a trading scanner.
    # Check hourly so restarts cannot make us miss a refresh. The refresh
    # policy follows a *new closed MOEX equity D1*, not midnight: on weekends
    # and before the exchange has produced a new daily bar the previous good
    # ranking remains untouched; after the new D1 arrives it is rebuilt once.
    def equity_ranking(session):
        return refresh_equity_ranking(session)

    scheduler.add(
        "equity-ranking",
        _minutes("SIGNALAI_EQUITY_RANKING_EVERY_MINUTES", 60),
        equity_ranking,
    )
    ranking_job = scheduler.jobs.pop()
    portfolio_index = next(
        (i for i, job in enumerate(scheduler.jobs) if job.name == "portfolio"),
        len(scheduler.jobs),
    )
    scheduler.jobs.insert(portfolio_index, ranking_job)

    # Execution monitoring is intentionally faster than signal discovery.
    # H1/H4/D1 closed bars remain the only source for setups; after explicit
    # approval a crypto limit/TP/SL is an execution fact and must not wait up
    # to an hour. Closed Bybit 1m bars cap the delay at about one minute.
    # Materialising the outbox in the same committed scheduler job makes the
    # notification genuinely server-originated even when the phone is offline.
    def paper_live(session):
        report = track_crypto_live(session)
        queued = materialize(session, include_smoke=False)
        return f"{report.summary()}, уведомлений в outbox {queued}"

    scheduler.add(
        "paper-live",
        _minutes("SIGNALAI_PAPER_LIVE_EVERY_MINUTES", 1),
        paper_live,
    )
    # Run it next to the ordinary paper tracker, before scan/portfolio/research
    # can occupy the sequential scheduler with heavier work.
    live_job = scheduler.jobs.pop()
    paper_index = next(
        (i for i, job in enumerate(scheduler.jobs) if job.name == "paper"),
        len(scheduler.jobs),
    )
    scheduler.jobs.insert(paper_index, live_job)

    session_factory = get_session_factory()

    # This is deliberately server-originated. On every deployment/restart the
    # VPS reconciles owner-facing lifecycle before any phone connects. The v2
    # key is new for the native Android SSE transport, so this deployment
    # creates one fresh smoke event instead of reusing the already-consumed v1
    # event from the broken secondary-Flutter-isolate implementation.
    startup_session = session_factory()
    try:
        created = materialize(startup_session, include_smoke=True)
        native_smoke = emit(
            startup_session,
            key="system:server-push-ready:v2-native",
            kind="SYSTEM",
            title="SignalAI · server push работает",
            body=(
                "Это событие создано на VPS после запуска нативного Android "
                "SSE-канала и доставлено без локального опроса приложения."
            ),
        )
        created += int(native_smoke is not None)
        startup_session.commit()
        log.info("server push outbox ready: queued %d new event(s)", created)
    except Exception:
        startup_session.rollback()
        # Delivery bootstrap must not stop market ingestion. The exact-alarm
        # client remains a fallback and the SSE request will retry materialize.
        log.exception("server push outbox startup reconciliation failed")
    finally:
        startup_session.close()

    stopping = {"now": False}

    def handle(signum, _frame):
        log.info("получен сигнал %s — остановлюсь после текущей задачи", signum)
        stopping["now"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    log.info(
        "планировщик %s запущен: %s",
        ENGINE_VERSION,
        ", ".join(
            f"{j.name} каждые {int(j.every.total_seconds() // 60)} мин"
            for j in scheduler.jobs
        ),
    )
    run_forever(
        session_factory,
        scheduler,
        interval_seconds=int(os.environ.get("SIGNALAI_TICK_SECONDS", "60")),
        stop=lambda: stopping["now"],
    )
    log.info("планировщик остановлен")
    return 0


if __name__ == "__main__":
    sys.exit(main())
