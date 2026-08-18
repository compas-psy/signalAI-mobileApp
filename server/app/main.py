"""Точка входа сервера SignalAI.

Запуск одной командой (engine-ТЗ §29.1):

    uvicorn app.main:app --host 0.0.0.0 --port 8000

Что здесь принципиально:

* ``/health`` отдаёт отпечаток конфигурации и режим исполнения. По ним видно,
  теми ли числами сейчас считает движок и закрыта ли боевая торговля.
* Эндпоинты, за которыми ещё нет движка, отвечают 503 с причиной.
* Все внешние ``/api/*`` требуют токен устройства.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from .api.v1 import capital as capital_routes
from .api.v1 import capacity as capacity_routes
from .api.v1 import control as control_routes
from .api.v1 import diagnostics as diagnostics_routes
from .api.v1 import equity_rankings as equity_ranking_routes
from .api.v1 import experiments as experiment_routes
from .api.v1 import idea_progress as idea_progress_routes
from .api.v1 import ideas as ideas_routes
from .api.v1 import integrations as integrations_routes
from .api.v1 import investment_signals as investment_signal_routes
from .api.v1 import journal as journal_routes
from .api.v1 import live_market as live_market_routes
from .api.v1 import market as market_routes
from .api.v1 import measurements as measurement_routes
from .api.v1 import notifications as notification_routes
from .api.v1 import paper as paper_routes
from .api.v1 import portfolio as portfolio_routes
from .api.v1 import portfolio_headlines as portfolio_headline_routes
from .api.v1 import research as research_routes
from .api.v1 import risk as risk_routes
from .config import get_config
from .db import get_engine
from .models.enums import ExecutionMode
from .operational_guard import OperationalLifecycleMiddleware
from .ops.metrics import ObservabilityMiddleware, metrics_response
from .paper.management_policy import install as install_paper_management
from .request_context import RequestIdMiddleware
from .schemas.common import HealthResponse
from .security import DeviceTokenMiddleware
from .version import API_VERSION, ENGINE_VERSION, FEATURE_VERSION

# approve-paper выполняется в API-процессе, поэтому policy ставится здесь, а
# не только в scheduler. Новый PaperTrade сразу получает подписанные доли
# 40/40/20; tracker потом читает уже сохранённый snapshot сделки.
install_paper_management()

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
# add_middleware inserts the newest middleware outside the previous one.
# RequestId is deliberately outside business middleware so even fail-closed
# auth responses carry a correlation id. Observability is outermost and only
# measures transport latency/status; it never changes request bodies or
# trading decisions. /metrics itself is skipped by the middleware and has a
# separate dedicated owner token inside metrics_response().
app.add_middleware(OperationalLifecycleMiddleware)
app.add_middleware(DeviceTokenMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(ObservabilityMiddleware)

v1 = APIRouter(prefix="/api/v1")
v1.include_router(market_routes.router)
v1.include_router(live_market_routes.router)
v1.include_router(ideas_routes.router)
v1.include_router(idea_progress_routes.router)
v1.include_router(risk_routes.router)
v1.include_router(portfolio_routes.router)
v1.include_router(portfolio_headline_routes.router)
v1.include_router(research_routes.router)
v1.include_router(equity_ranking_routes.router)
v1.include_router(investment_signal_routes.router)
v1.include_router(paper_routes.router)
v1.include_router(integrations_routes.router)
v1.include_router(capital_routes.router)
v1.include_router(journal_routes.router)
v1.include_router(notification_routes.router)
v1.include_router(control_routes.router)
v1.include_router(diagnostics_routes.router)
v1.include_router(measurement_routes.router)
v1.include_router(experiment_routes.router)
v1.include_router(capacity_routes.router)
app.include_router(v1)


@app.get("/open/idea/{idea_id}", response_class=HTMLResponse, include_in_schema=False)
def open_idea(idea_id: UUID) -> HTMLResponse:
    """HTTPS bridge for Telegram buttons into the Android app."""
    target = f"signalai://idea/{idea_id}"
    return HTMLResponse(
        "<!doctype html><html><head>"
        f'<meta http-equiv="refresh" content="0;url={target}">'
        "<meta name="
        '"viewport" content="width=device-width,initial-scale=1">'
        "</head><body style=\"font-family:sans-serif;background:#111318;"
        "color:#f4f5f7;padding:32px\">"
        f'<a href="{target}" style="color:#ffd400;font-size:20px">'
        "Открыть идею в SignalAI</a>"
        f'<script>location.replace("{target}")</script>'
        "</body></html>"
    )


@app.get("/metrics", include_in_schema=False)
def metrics(request: Request):
    """Prometheus text for the trusted owner boundary only."""
    return metrics_response(request)


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
    except Exception as exc:
        database = "unavailable"
        notes.append(f"база недоступна: {type(exc).__name__}")

    paper_only = bool(cfg.get("risk.paper_only"))
    if paper_only and mode not in (ExecutionMode.PAPER, ExecutionMode.ANALYTICS_ONLY):
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
