"""Вызов локальной модели (ТЗ §15, §26.2).

До сих пор §26.2 числился блокером: модельного доступа на сервере не было,
и критик умел проверять чужой ответ, но не мог его получить. Локальная
модель на самом VPS блокер снимает и не заводит нового: ключей нет, счёта
нет, наружу ничего не уходит.

Чего этот модуль **не** делает и делать не будет. Он не считает чисел, не
меняет состояние гипотезы и не принимает решений: §15.1 запрещает, а
проверки в `critic` ловят нарушение независимо от того, что модель себе
позволила. Здесь только транспорт и форма ответа.

Про форму. Ответ запрашивается по JSON Schema, а не «попросили JSON и
надеемся». Разница не косметическая: свободный ответ модели приходится
разбирать регулярками, и первый же неожиданный перенос строки превращается
в потерянное наблюдение — молча.

Про одновременность. Запрос идёт строго один. На четырёх ядрах и восьми
гигабайтах, где рядом живут Postgres, Redis, API и планировщик, вторая
одновременная генерация не ускоряет ничего: обе упираются в те же ядра, а
память уходит в своп. Ограничение стоит и в коде, а не только в настройке
сервиса, потому что настройку на сервере может поменять кто угодно, а
последствия увидит планировщик.

Про недоступность. Если модели нет — это `GatewayUnavailable`, а не
пустой результат. Гипотеза без разбора критиком остаётся в своём
состоянии: она не становится ни лучше, ни хуже от того, что критика не
спросили. Подменить это словами «модель ничего не нашла» значило бы
выдать молчание за проверку.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field


class GatewayUnavailable(RuntimeError):
    """Модельного доступа нет.

    Живёт там, где возбуждается. Раньше тип был объявлен в `critic`, и это
    заставляло транспорт импортировать домен — а домену нужен транспорт.
    Круг разрывается направлением: `critic` знает про `gateway`, обратное
    неверно. Транспорт не должен знать, что такое гипотеза.

    Отдельный тип нужен потому, что §26.2 требует называть отсутствие
    доступа блокером, а не молча пропускать шаг.
    """

#: Переменные окружения. Пустой адрес означает «модели нет» — не «локальная
#: по умолчанию»: догадка о запущенном сервисе привела бы к тому, что
#: планировщик каждые двенадцать часов ждал бы таймаута впустую.
ENV_BASE_URL = "SIGNALAI_LLM_BASE_URL"
ENV_MODEL = "SIGNALAI_LLM_MODEL"
ENV_TIMEOUT = "SIGNALAI_LLM_TIMEOUT_SECONDS"
ENV_NUM_CTX = "SIGNALAI_LLM_NUM_CTX"

#: Модель называется настройкой, а не константой в коде. Тег в реестре
#: Ollama живёт своей жизнью, а перевыкладка сервера ради смены строки —
#: плохая причина для перевыкладки.
DEFAULT_MODEL = "qwen3.5:4b"

#: Четыре тысячи токенов — не осторожность, а цена. Контекст на CPU стоит
#: и памяти, и времени на каждом шаге генерации; документы дробятся на
#: фрагменты именно поэтому.
DEFAULT_NUM_CTX = 4096

#: Генерация 4B на четырёх ядрах идёт единицами токенов в секунду, а первый
#: запрос после простоя ещё и поднимает модель с диска. Короткий таймаут
#: означал бы, что контур «не работает», хотя он просто медленный.
DEFAULT_TIMEOUT = 180.0

#: Разброс не нужен: задача — извлечь сказанное, а не сочинить.
TEMPERATURE = 0.1

# Один запрос за раз в пределах процесса.
_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    base_url: str = ""
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    num_ctx: int = DEFAULT_NUM_CTX

    @property
    def configured(self) -> bool:
        return bool(self.base_url)


def config_from_env(env: dict[str, str] | None = None) -> GatewayConfig:
    """Настройка из окружения. Отсутствие адреса — это выключено."""
    source = env if env is not None else dict(os.environ)
    return GatewayConfig(
        base_url=source.get(ENV_BASE_URL, "").strip().rstrip("/"),
        model=source.get(ENV_MODEL, "").strip() or DEFAULT_MODEL,
        timeout=_positive(source.get(ENV_TIMEOUT), DEFAULT_TIMEOUT),
        num_ctx=int(_positive(source.get(ENV_NUM_CTX), DEFAULT_NUM_CTX)),
    )


def _positive(raw: str | None, fallback: float) -> float:
    try:
        value = float(str(raw))
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


@dataclass
class Exchange:
    """Что ушло и что пришло — для журнала качества данных (§15.5)."""

    attempts: int = 0
    raw: str = ""


class UnsafeOutput(ValueError):
    """Модель вернула то, чего в выдаче быть не должно (§15.1, §16.4).

    Отдельный тип, а не отфильтрованная строка: торговая команда в ответе
    — это не грязь, которую надо вычистить, а признак того, что разбору
    этого ответа доверять нельзя целиком.
    """


def ask(
    *,
    system: str,
    document: str,
    schema: dict,
    forbidden: Sequence[str] = (),
    config: GatewayConfig | None = None,
    transport=None,
    attempts: int = 2,
) -> tuple[dict, Exchange]:
    """Спросить модель и вернуть разобранный ответ по схеме.

    ``forbidden`` передаётся, а не зашит: транспорт не знает, что можно
    говорить о гипотезах, — это знание домена, и держать его здесь значило
    бы, что смена правил §16.4 требует правки сетевого кода.

    ``document`` — недоверенные данные, и они уходят как есть. Вычищенный
    текст перестал бы соответствовать сохранённому оригиналу, а именно по
    нему проверяются цитаты; поиск попыток перебить инструкции — дело
    вызывающего, до вызова.
    """
    settings = config or config_from_env()
    if not settings.configured:
        raise GatewayUnavailable(
            "модельный доступ не настроен: "
            f"переменная {ENV_BASE_URL} пуста. Это блокер, а не пустой ответ"
        )

    send = transport or _post
    exchange = Exchange()

    last: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        exchange.attempts = attempt
        try:
            raw = send(settings, _payload(settings, system, document, schema))
        except GatewayUnavailable:
            raise
        except Exception as error:  # noqa: BLE001 — причина уедет в отчёт
            last = error
            continue

        exchange.raw = raw
        _refuse_trading_language(raw, forbidden)
        try:
            return json.loads(raw), exchange
        except json.JSONDecodeError as error:
            # Ответ вне схемы — повод переспросить, а не додумать (§15.4).
            last = error

    raise GatewayUnavailable(
        f"модель не вернула ответ по схеме за {exchange.attempts} попыт(ки): {last}"
    )


def _refuse_trading_language(raw: str, forbidden: Sequence[str]) -> None:
    """Запрещённое слово — не грязь, которую вычищают.

    Торговая команда в ответе означает, что модель вышла за роль, и
    доверять разбору этого ответа нельзя целиком. Вырезать слово и
    продолжить было бы худшим из решений: нарушение исчезает из виду,
    оставаясь в силе.
    """
    lowered = raw.lower()
    for word in forbidden:
        if word.lower() in lowered:
            raise UnsafeOutput(f"в ответе модели запрещённое слово «{word}»")


def _payload(config: GatewayConfig, system: str, document: str, schema: dict) -> dict:
    """Тело запроса к native `/api/chat`.

    Родной адрес, а не OpenAI-совместимый: только он принимает JSON Schema
    прямо в запросе. Совместимый слой пришлось бы просить «ответь JSON-ом»
    словами, то есть надеяться.
    """
    return {
        "model": config.model,
        "stream": False,
        "format": schema,
        "options": {
            "temperature": TEMPERATURE,
            "num_ctx": config.num_ctx,
        },
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": document},
        ],
    }


def _post(config: GatewayConfig, payload: dict) -> str:
    """Один запрос к локальному сервису. Строго последовательно."""
    request = urllib.request.Request(
        f"{config.base_url}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _lock:
        try:
            with urllib.request.urlopen(request, timeout=config.timeout) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raise GatewayUnavailable(
                f"локальная модель ответила {error.code}: {error.reason}"
            ) from error
        except OSError as error:
            raise GatewayUnavailable(
                f"локальная модель недоступна по {config.base_url}: {error}"
            ) from error
    return str(body.get("message", {}).get("content", ""))


def health(config: GatewayConfig | None = None, transport=None) -> dict:
    """Есть ли модель и какая. Для диагностики после деплоя."""
    settings = config or config_from_env()
    if not settings.configured:
        return {"configured": False, "reason": f"{ENV_BASE_URL} не задан"}
    probe = transport or _tags
    try:
        installed = probe(settings)
    except Exception as error:  # noqa: BLE001 — причина важнее типа
        return {
            "configured": True,
            "reachable": False,
            "model": settings.model,
            "reason": f"{type(error).__name__}: {error}",
        }
    return {
        "configured": True,
        "reachable": True,
        "model": settings.model,
        # Настроенная модель и установленная — разные вещи, и опечатка в
        # теге выглядит как «модель молчит».
        "model_installed": settings.model in installed,
        "installed": installed,
    }


def _tags(config: GatewayConfig) -> list[str]:
    request = urllib.request.Request(f"{config.base_url}/api/tags", method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.loads(response.read().decode())
    return [str(item.get("name", "")) for item in body.get("models", [])]


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_NUM_CTX",
    "DEFAULT_TIMEOUT",
    "ENV_BASE_URL",
    "ENV_MODEL",
    "Exchange",
    "GatewayConfig",
    "GatewayUnavailable",
    "UnsafeOutput",
    "ask",
    "config_from_env",
    "health",
]
