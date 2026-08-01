"""Контракт сбора и общая политика запросов (ТЗ §10.1, §10.2).

Коллектор получает байты и метаданные транспорта. Он не интерпретирует
инвестиционный смысл, не решает, что важно, и не пишет наблюдения — это
разные слои, и смешение их означает, что причину странного числа придётся
искать в коде адаптера.

Первое, что делает любой сбор, — спрашивает разрешение у правового шлюза.
Не «желательно спрашивает», а физически не может иначе: ``fetch`` требует
пропуск аргументом, и вызвать его без пропуска нельзя. Разница между
«спросили и не получили» и «не спрашивали» — это разница между нарушением
условий использования и его отсутствием.

Про сохранение сырого. §2 требует сохранять полученное **без изменений**:
нормализованные данные не заменяют оригинал. Причина простая — парсер
меняется, а документ нет, и через год единственный способ понять, откуда
взялось число, — открыть тот самый файл. Хеш содержимого служит и ключом
дедупликации, и доказательством, что файл не подменён.

Повторное обнаружение того же объекта не создаёт дубликата, но и не
теряется: факт «источник отдал это ещё раз» — самостоятельное наблюдение,
по которому видно, живёт ли источник вообще.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .policy import Permit

# Политика повторов §10.2. Без повторов для 4xx, кроме перечисленных:
# «не найдено» повтором не лечится, а «слишком часто» — лечится.
RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
MAX_ATTEMPTS = 5
BASE_DELAY = timedelta(seconds=2)

# Предел размера объекта. Больший скачивается потоком либо не скачивается
# вовсе: разбор гигабайтного архива в памяти роняет весь прогон.
MAX_INLINE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RemoteObject:
    """Что коллектор нашёл и собирается забрать."""

    url: str
    kind: str = ""
    published_at: datetime | None = None
    etag: str = ""
    last_modified: str = ""


@dataclass(frozen=True, slots=True)
class Fetched:
    """Полученный объект вместе с метаданными транспорта."""

    url: str
    status: int
    body: bytes
    requested_at: datetime
    responded_at: datetime
    headers: dict[str, str] = field(default_factory=dict)
    published_at: datetime | None = None
    content_type: str = ""

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@dataclass
class Cursor:
    """Где остановились в прошлый раз.

    Курсор — это не оптимизация. Источник, который перечитывается целиком
    при каждом прогоне, рано или поздно упрётся в лимит и перестанет
    отвечать вовсе.
    """

    value: str = ""
    updated_at: datetime | None = None


class Collector(Protocol):
    """Контракт §10.1. Три метода и ни одного решения о смысле данных."""

    source_id: str

    def discover(self, cursor: Cursor | None) -> list[RemoteObject]: ...

    def fetch(self, obj: RemoteObject, permit: Permit) -> Fetched: ...

    def checkpoint(self, fetched: list[Fetched]) -> Cursor: ...


def should_retry(status: int, attempt: int) -> bool:
    """Стоит ли повторять запрос.

    «Не найдено» и «запрещено» повтором не лечатся: повторять их — значит
    стучаться в закрытую дверь и приближать блокировку.
    """
    if attempt >= MAX_ATTEMPTS:
        return False
    return status in RETRYABLE_STATUSES


def backoff(attempt: int, *, retry_after: float | None = None) -> timedelta:
    """Задержка перед повтором.

    ``Retry-After`` уважается всегда и перекрывает расчёт: если источник
    сказал, когда возвращаться, спорить с ним незачем.
    """
    if retry_after is not None:
        return timedelta(seconds=max(0.0, retry_after))
    return BASE_DELAY * (2 ** max(0, attempt - 1))


def conditional_headers(obj: RemoteObject) -> dict[str, str]:
    """Заголовки условного запроса §10.2.

    Источник, который отвечает «не менялось», не тратит ни свой трафик, ни
    наш лимит.
    """
    headers: dict[str, str] = {}
    if obj.etag:
        headers["If-None-Match"] = obj.etag
    if obj.last_modified:
        headers["If-Modified-Since"] = obj.last_modified
    return headers


@dataclass
class RawStore:
    """Хранилище сырых объектов с дедупликацией по содержимому.

    В памяти — для тестов и для того, чтобы контракт был проверяем без
    объектного хранилища. Ключ строится как в §4.1.4:
    ``raw/{source}/{yyyy}/{mm}/{dd}/{sha256}``.
    """

    objects: dict[str, bytes] = field(default_factory=dict)
    seen: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def key(source_id: str, moment: datetime, digest: str, ext: str = "bin") -> str:
        return f"raw/{source_id}/{moment:%Y/%m/%d}/{digest}.{ext}"

    def put(self, source_id: str, fetched: Fetched, *, ext: str = "bin") -> tuple[str, bool]:
        """Сохранить объект. Возвращает (ключ, новый ли он).

        Повторное обнаружение не создаёт дубликата, но считается: по этому
        счётчику видно, живёт ли источник или отдаёт одно и то же.
        """
        digest = fetched.sha256
        key = self.key(source_id, fetched.responded_at, digest, ext)
        fresh = key not in self.objects
        if fresh:
            self.objects[key] = fetched.body
        self.seen[key] = self.seen.get(key, 0) + 1
        return key, fresh


__all__ = [
    "BASE_DELAY",
    "Collector",
    "Cursor",
    "Fetched",
    "MAX_ATTEMPTS",
    "MAX_INLINE_BYTES",
    "RETRYABLE_STATUSES",
    "RawStore",
    "RemoteObject",
    "backoff",
    "conditional_headers",
    "should_retry",
]
