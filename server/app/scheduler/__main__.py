"""Точка входа планировщика: ``python -m app.scheduler``.

Отдельный процесс, а не фоновая задача внутри API. Причины две, и обе
практические. Uvicorn с несколькими воркерами запустил бы столько же копий
расписания, и каждая пошла бы грузить биржу и писать бары. А перезапуск API
ради выкатки не должен обрывать загрузку на середине.

Процесс намеренно не имеет входящих портов: он читает биржи и пишет в базу,
принимать команды снаружи ему незачем.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from datetime import timedelta

from ..db import get_session_factory
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
        # Опечатка в переменной окружения не должна тихо превращаться в
        # расписание по умолчанию: это выглядело бы как «настройка не
        # применилась» без единого следа.
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
    )

    stopping = {"now": False}

    def handle(signum, _frame):
        # Останов между задачами, а не посреди задачи: прерванная загрузка
        # оставила бы половину баров периода.
        log.info("получен сигнал %s — остановлюсь после текущей задачи", signum)
        stopping["now"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    log.info(
        "планировщик %s запущен: %s",
        ENGINE_VERSION,
        ", ".join(f"{j.name} каждые {int(j.every.total_seconds() // 60)} мин"
                  for j in scheduler.jobs),
    )
    run_forever(
        get_session_factory(),
        scheduler,
        interval_seconds=int(os.environ.get("SIGNALAI_TICK_SECONDS", "60")),
        stop=lambda: stopping["now"],
    )
    log.info("планировщик остановлен")
    return 0


if __name__ == "__main__":
    sys.exit(main())
