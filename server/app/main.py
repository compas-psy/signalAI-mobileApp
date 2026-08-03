"""Точка входа сервера SignalAI.

Запуск одной командой (engine-ТЗ §29.1):

    uvicorn app.main:app --host 0.0.0.0 --port 8000

Что здесь принципиально:

* ``/health`` отдаёт отпечаток конфигурации и режим исполнения. По ним видно,
  теми ли числами сейчас считает движок и закрыта ли боевая торговля — это
  первое, что надо проверить, увидев неожиданную идею.
* Эндпоинты, за которыми ещё нет движка, отвечают 503 с причиной. Пустой
  список вместо этого читался бы как «сегодня нет сетапов» (§32: приложение
  не создаёт иллюзию точности).
* Все внешние ``/api/*`` требуют токен устройства. APK и так передаёт его в
  заголовке Bearer; сервер обязан проверять этот заголовок, а не оставлять
  торговый контур публичным.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, FastAPI
from sqlalchemy import text

from .api.v1 import ideas as ideas_routes
from .api.v1 import paper as paper_routes
from .api.v1 import market as market_routes
from .api.v1 import portfolio as portfolio_routes
from .api.v1 import research as research_routes
from .api.v1 import risk as risk_routes
from .config import get_config
from .db import get_engine
from .models.enums import ExecutionMode
from .schemas.common import HealthResponse
from .version import API_VERSION, ENGINE_VERSION, FEATURE_VERSION

app = FastAPI(
    title="SignalAI Engine",
    version=ENGINE_VERSION,
    summary="Инвестиционный и торговый движок: аналитика и paper trading",
    description=(
        "Реализация SIGNAL_AI_TRADING_ENGINE_TZ_v1.0. Боевые заявки в этом "
        "режиме запрещены (paper_only). Вероятность имеет строгое "
        "определение: P(TP1 раньше SL в пределах горизонта)."
    ),
)
# Решение владельца от 30.07: проверка токена устройства временно снята —
# секрет ещё не заведён, и требование ломало приложение. Middleware
# (security.py) сохранён; вернуть — одной строкой, когда SIGNALAI_DEVICE_TOKEN
# появится в GitHub Secrets и на VPS.
# app.add_middleware(DeviceTokenMiddleware)

v1 = APIRouter(prefix="/api/v1")
v1.include_router(market_routes.router)
v1.include_router(ideas_routes.router)
v1.include_router(risk_routes.router)
v1.include_router(portfolio_routes.router)
v1.include_router(research_routes.router)
v1.include_router(paper_routes.router)
app.include_router(v1)


@app.get("/health", response_model=HealthResponse, tags=["service"])
def health() -> HealthResponse:
    cfg = get_config()
    notes: list[str] = []

    database = "ok"
    kill_switch = False
    mode = str(cfg.get("execution.mode"))
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            row = conn.execute(
                text("SELECT execution_mode, kill_switch FROM risk_state WHERE id = 1")
            ).first()
            if row is not None:
                mode, kill_switch = row[0], bool(row[1])
    except Exception as exc:  # база недоступна — это состояние, а не падение
        database = "unavailable"
        notes.append(f"база недоступна: {type(exc).__name__}")

    paper_only = bool(cfg.get("risk.paper_only"))
    if paper_only and mode not in (ExecutionMode.PAPER, ExecutionMode.ANALYTICS_ONLY):
        # Противоречие конфигурации и состояния — повод сказать вслух, а не
        # выбрать одно из двух молча.
        notes.append(
            f"paper_only=true, но режим исполнения {mode}: боевые заявки "
            "останутся заблокированными"
        )
    if kill_switch:
        notes.append("включена аварийная остановка: новые входы запрещены")

    return HealthResponse(
        status="ok" if database == "ok" and not notes else "degraded",
        engine_version=ENGINE_VERSION,
        feature_version=FEATURE_VERSION,
        api_version=API_VERSION,
        config_hash=cfg.config_hash,
        execution_mode=mode,
        paper_only=paper_only,
        kill_switch=kill_switch,
        database=database,
        server_time=datetime.now(UTC),
        notes=notes,
    )
