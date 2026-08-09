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
from ..notification_outbox import materialize
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

    scheduler = build_default_scheduler(
        universe_every=_minutes("SIGNALAI_UNIVERSE_EVERY_MINUTES", 360),
        ingest_every=_minutes("SIGNALAI_INGEST_EVERY_MINUTES", 15),
        review_every=_minutes("SIGNALAI_REVIEW_EVERY_MINUTES", 360),
        scan_every=_minutes("SIGNALAI_SCAN_EVERY_MINUTES", 15),
        portfolio_every=_minutes("SIGNALAI_PORTFOLIO_EVERY_MINUTES", 60),
    )
    session_factory = get_session_factory()

    # This is deliberately server-originated. On every deployment/restart the
    # VPS reconciles owner-facing lifecycle and queues the stable smoke event
    # before any phone connects. The unique outbox key prevents duplicates;
    # the new Android SSE client later replays it from its durable cursor.
    startup_session = session_factory()
    try:
        created = materialize(startup_session, include_smoke=True)
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
