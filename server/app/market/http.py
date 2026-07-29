"""Тонкий HTTP-слой для биржевых адаптеров.

Стандартная библиотека, без внешних зависимостей: адаптеры ходят в интернет
из контейнера, который держит торговые ключи, и каждая лишняя зависимость там
— лишняя поверхность.

Отказы **классифицируются**, а не сваливаются в одно исключение. Разница
существенная: таймаут чтения тела значит, что биржа жива и запрос надо
сузить; отказ соединения — что её не видно; код 429 — что мы сами частим.
Лечатся эти три случая по-разному, и различать их надо в момент отказа, а не
гадать по логам.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


class FetchError(RuntimeError):
    """Отказ запроса с распознанной причиной."""

    def __init__(self, kind: str, url: str, detail: str, status: int | None = None):
        self.kind = kind
        self.url = url
        self.detail = detail
        self.status = status
        super().__init__(f"{kind}: {detail} ({url})")


@dataclass(frozen=True, slots=True)
class FetchReport:
    """Что произошло с запросом — для экрана диагностики."""

    url: str
    status: int | None
    elapsed_ms: int
    bytes_read: int
    ok: bool
    detail: str = ""


# Тип внедряемого загрузчика: тесты подставляют фикстуры вместо сети.
Fetcher = Callable[[str], Any]


def http_json(
    url: str,
    *,
    timeout: float = 20.0,
    retries: int = 2,
    backoff: float = 1.5,
    user_agent: str = "SignalAI/0.1 (personal trading terminal)",
) -> tuple[Any, FetchReport]:
    """Загрузить JSON с классификацией отказа и повтором.

    Повторяются только те отказы, которые имеет смысл повторять: сетевые и
    5xx. Ответ 4xx повторять бессмысленно — запрос не станет правильнее от
    настойчивости, а 429 требует не повтора, а паузы.
    """
    last: FetchError | None = None
    for attempt in range(retries + 1):
        started = time.monotonic()
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                elapsed = int((time.monotonic() - started) * 1000)
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise FetchError(
                        "bad_json", url, f"ответ не разбирается как JSON: {exc}",
                        response.status,
                    ) from exc
                return data, FetchReport(
                    url=url, status=response.status, elapsed_ms=elapsed,
                    bytes_read=len(raw), ok=True,
                )
        except urllib.error.HTTPError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            kind = "rate_limited" if exc.code == 429 else (
                "server_error" if exc.code >= 500 else "rejected"
            )
            last = FetchError(kind, url, f"код {exc.code}", exc.code)
            if kind == "rejected":
                raise last from exc      # повтор не сделает запрос правильнее
        except TimeoutError as exc:
            last = FetchError("timeout", url, f"нет ответа за {timeout} с")
        except urllib.error.URLError as exc:
            last = FetchError("unreachable", url, f"{exc.reason}")
        except OSError as exc:
            last = FetchError("network", url, f"{type(exc).__name__}: {exc}")

        if attempt < retries:
            time.sleep(backoff ** attempt)

    assert last is not None
    raise last


def iss_rows(payload: Any, block: str) -> list[dict[str, Any]]:
    """Развернуть блок ответа MOEX ISS в список словарей.

    ISS отдаёт данные колоночно: отдельно имена, отдельно строки. Разбор
    вынесен сюда, чтобы адаптер работал со словарями, а не с индексами —
    перепутанный индекс колонки даёт правдоподобное, но неверное число.
    """
    if not isinstance(payload, dict):
        return []
    section = payload.get(block)
    if not isinstance(section, dict):
        return []
    columns = section.get("columns")
    rows = section.get("data")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return []
    return [
        dict(zip(columns, row, strict=False))
        for row in rows
        if isinstance(row, list)
    ]
