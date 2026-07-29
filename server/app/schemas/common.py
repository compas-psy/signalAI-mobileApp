"""Общие схемы ответов.

Два принципа, которым подчинены все схемы:

* **Числа уходят строками там, где это деньги и цены.** JSON-число — это
  double, и `0.005` на другом конце может стать `0.004999999999999999`.
  Клиент разбирает такие поля как Decimal/BigDecimal, а не как float.
* **Отсутствие данных называет себя.** Нет значения — значит есть причина;
  она едет рядом. Пустое место, которое клиент дорисует нулём, — это
  выдуманные данные.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

# Decimal, который сериализуется строкой и не теряет точность по дороге.
Money = Annotated[Decimal, PlainSerializer(str, return_type=str, when_used="json")]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Unavailable(ApiModel):
    """Честный отказ вместо выдуманного значения.

    UX-ТЗ §24 и §27: приложение показывает деградацию, а не маскирует её под
    живые данные. Поэтому у любого «нет данных» есть код и человеческая
    причина — их и показывает интерфейс.
    """

    available: Literal[False] = False
    reason_code: str
    reason: str
    since: datetime | None = None
    retry_after_seconds: int | None = None


class HealthResponse(ApiModel):
    """Состояние сервера — то, по чему судят, можно ли ему верить."""

    status: Literal["ok", "degraded"]
    engine_version: str
    feature_version: str
    api_version: str
    # Отпечаток конфигурации: по нему видно, теми ли числами сейчас считают
    # (§27). Расхождение с отпечатком в идее означает, что параметры меняли.
    config_hash: str
    execution_mode: str
    paper_only: bool
    kill_switch: bool
    database: Literal["ok", "unavailable"]
    server_time: datetime
    notes: list[str] = Field(default_factory=list)


class Page(ApiModel):
    """Постраничная выдача с курсором (UX-ТЗ §18)."""

    items: list[Any]
    next_cursor: str | None = None
    total: int | None = None


class ErrorResponse(ApiModel):
    error: str
    detail: str = ""
    trace_id: str | None = None
