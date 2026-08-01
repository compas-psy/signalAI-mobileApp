"""Контракт сбора §10.1–10.2 и разрешение перед запросом §2.1.

Главный критерий приёмки фазы 0 ТЗ: запрещённый или истёкший источник
делает ноль сетевых вызовов. Здесь это проверяется буквально — счётчиком
обращений к сети.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import ResearchSource
from app.models.enums import LicenseStatus
from app.research.collect import (
    MAX_ATTEMPTS,
    Cursor,
    Fetched,
    RawStore,
    RemoteObject,
    backoff,
    conditional_headers,
    should_retry,
)
from app.research.policy import CollectionDenied, authorize

NOW = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def fetched(body: bytes = b"payload", *, status: int = 200) -> Fetched:
    return Fetched(
        url="https://example.test/data.json",
        status=status,
        body=body,
        requested_at=NOW,
        responded_at=NOW,
    )


# ── Повторы и лимиты ────────────────────────────────────────────────────────


def test_не_найдено_повтором_не_лечится():
    """Стучаться в закрытую дверь — значит приближать блокировку."""
    assert not should_retry(404, attempt=1)
    assert not should_retry(403, attempt=1)


def test_слишком_часто_повторяется():
    assert should_retry(429, attempt=1)
    assert should_retry(503, attempt=2)


def test_попытки_кончаются():
    assert not should_retry(429, attempt=MAX_ATTEMPTS)


def test_задержка_растёт_экспоненциально():
    assert backoff(2) > backoff(1)


def test_указание_источника_перекрывает_расчёт():
    """Если источник сказал, когда возвращаться, спорить незачем."""
    assert backoff(5, retry_after=1.0) == timedelta(seconds=1)


def test_условный_запрос_не_тратит_лимит():
    headers = conditional_headers(
        RemoteObject(url="u", etag='W/"abc"', last_modified="Mon, 01 Jan 2026")
    )
    assert headers["If-None-Match"] == 'W/"abc"'
    assert "If-Modified-Since" in headers


# ── Сырое хранилище ─────────────────────────────────────────────────────────


def test_одинаковый_объект_не_дублируется_но_считается():
    """Факт «источник отдал это ещё раз» — самостоятельное наблюдение."""
    store = RawStore()
    key1, fresh1 = store.put("cbr_data", fetched())
    key2, fresh2 = store.put("cbr_data", fetched())

    assert key1 == key2
    assert fresh1 and not fresh2
    assert store.seen[key1] == 2
    assert len(store.objects) == 1


def test_разное_содержимое_даёт_разные_ключи():
    store = RawStore()
    a, _ = store.put("cbr_data", fetched(b"one"))
    b, _ = store.put("cbr_data", fetched(b"two"))
    assert a != b


def test_ключ_строится_по_источнику_и_дате():
    key = RawStore.key("rosstat", NOW, "abc", "xml")
    assert key == "raw/rosstat/2026/08/01/abc.xml"


def test_сырое_сохраняется_без_изменений():
    """Парсер меняется, документ — нет.

    Через год единственный способ понять, откуда взялось число, — открыть
    тот самый файл.
    """
    store = RawStore()
    body = b'{"value": 42, "note": "\\u0441\\u044b\\u0440\\u043e\\u0435"}'
    key, _ = store.put("eis_open_data", fetched(body))
    assert store.objects[key] == body


# ── Разрешение до сети ──────────────────────────────────────────────────────


class CountingNetwork:
    """Сеть, которая считает обращения. Ни одно не должно случиться."""

    def __init__(self):
        self.calls = 0

    def get(self, url: str) -> Fetched:
        self.calls += 1
        return fetched()


def collect(session: Session, source_id: str, network: CountingNetwork) -> Fetched:
    """Сбор так, как он должен быть написан: пропуск первым действием."""
    permit = authorize(session, source_id, {"fetch", "store_raw"}, now=NOW)
    assert permit.valid_at(NOW)
    return network.get("https://example.test/data.json")


def add_source(session: Session, **changes) -> ResearchSource:
    values = dict(
        source_id="test_source",
        name="Тест",
        owner="никто",
        access_method="api",
        license_status=LicenseStatus.APPROVED,
        allowed_operations={
            "fetch": True,
            "store_raw": True,
            "transform": True,
            "display_derived": True,
            "redistribute_raw": False,
        },
        terms_checked_at=NOW - timedelta(days=1),
        terms_review_due_at=NOW + timedelta(days=90),
        enabled=True,
    )
    values.update(changes)
    row = ResearchSource(**values)
    session.add(row)
    session.flush()
    return row


def test_запрещённый_источник_делает_ноль_сетевых_вызовов(session: Session):
    """Главный критерий приёмки фазы 0.

    Разница между «спросили и не получили» и «не спрашивали» — это разница
    между нарушением условий использования и его отсутствием.
    """
    add_source(session, license_status=LicenseStatus.PROHIBITED)
    network = CountingNetwork()

    with pytest.raises(CollectionDenied):
        collect(session, "test_source", network)

    assert network.calls == 0


def test_истёкшая_проверка_условий_делает_ноль_вызовов(session: Session):
    add_source(session, terms_review_due_at=NOW - timedelta(days=1))
    network = CountingNetwork()

    with pytest.raises(CollectionDenied):
        collect(session, "test_source", network)

    assert network.calls == 0


def test_разрешённый_источник_доходит_до_сети(session: Session):
    add_source(session)
    network = CountingNetwork()

    result = collect(session, "test_source", network)

    assert network.calls == 1
    assert result.ok
