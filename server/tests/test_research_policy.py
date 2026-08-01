"""Правовой шлюз источников §2.1, §5.3.

Главный критерий приёмки фазы 0 звучит так: «запрещённый или истёкший
source делает zero network calls». Проверяется именно это — что отказ
случается **до** соединения и что сомнение трактуется как запрет.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CollectionPermit, ResearchSource
from app.models.enums import LicenseStatus
from app.research.policy import CollectionDenied, authorize, blocked_sources
from app.research.sources import ALL_SOURCES, CONNECT_ORDER, readiness, sync_registry

NOW = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)

FULL = {
    "fetch": True,
    "store_raw": True,
    "transform": True,
    "display_derived": True,
    "redistribute_raw": False,
}


def add_source(session: Session, **changes) -> ResearchSource:
    values = dict(
        source_id="test_source",
        name="Тестовый источник",
        owner="никто",
        access_method="api",
        license_status=LicenseStatus.APPROVED,
        allowed_operations=dict(FULL),
        terms_checked_at=NOW - timedelta(days=1),
        terms_review_due_at=NOW + timedelta(days=90),
        enabled=True,
    )
    values.update(changes)
    row = ResearchSource(**values)
    session.add(row)
    session.flush()
    return row


def test_разрешённый_источник_выдаёт_пропуск(session: Session):
    add_source(session)
    permit = authorize(session, "test_source", {"fetch", "store_raw"}, now=NOW)
    assert permit.valid_at(NOW)
    assert not permit.valid_at(NOW + timedelta(days=1))


def test_неизвестный_источник_запрещён(session: Session):
    """Источника нет в реестре — значит его право никто не проверял."""
    with pytest.raises(CollectionDenied) as exc:
        authorize(session, "какой-то", {"fetch"}, now=NOW)
    assert "не проверялся" in exc.value.reason


def test_неизвестный_правовой_статус_блокирует(session: Session):
    """Fail-closed: «мы ещё не разобрались» запрещает так же, как запрет."""
    add_source(session, license_status=LicenseStatus.UNKNOWN)
    with pytest.raises(CollectionDenied) as exc:
        authorize(session, "test_source", {"fetch"}, now=NOW)
    assert "unknown" in exc.value.reason


def test_договорный_источник_не_собирается_автоматически(session: Session):
    add_source(session, license_status=LicenseStatus.REQUIRES_CONTRACT)
    with pytest.raises(CollectionDenied):
        authorize(session, "test_source", {"fetch"}, now=NOW)


def test_просроченная_проверка_условий_отзывает_доступ_сама(session: Session):
    """Источник, разрешённый год назад, сегодня разрешён не обязательно.

    Срок обязан отзывать доступ без чьего-либо участия — иначе «проверить
    условия» навсегда останется в списке дел.
    """
    add_source(session, terms_review_due_at=NOW - timedelta(days=1))
    with pytest.raises(CollectionDenied) as exc:
        authorize(session, "test_source", {"fetch"}, now=NOW)
    assert "просрочена" in exc.value.reason


def test_срок_проверки_обязателен(session: Session):
    add_source(session, terms_review_due_at=None)
    with pytest.raises(CollectionDenied) as exc:
        authorize(session, "test_source", {"fetch"}, now=NOW)
    assert "не задан" in exc.value.reason


def test_операция_вне_разрешённых_отклоняется(session: Session):
    """Право читать не есть право перераспространять."""
    add_source(session)
    with pytest.raises(CollectionDenied) as exc:
        authorize(session, "test_source", {"fetch", "redistribute_raw"}, now=NOW)
    assert "redistribute_raw" in exc.value.reason


def test_отказ_пишется_в_журнал(session: Session):
    """Журнал, где есть только успехи, не отвечает на вопрос аудита.

    А вопрос ровно один: пытались ли обойти запрет.
    """
    add_source(session, license_status=LicenseStatus.PROHIBITED)
    with pytest.raises(CollectionDenied):
        authorize(session, "test_source", {"fetch"}, now=NOW)

    rows = list(session.execute(select(CollectionPermit)).scalars())
    assert len(rows) == 1
    assert rows[0].granted is False
    assert rows[0].reason


def test_реестр_источников_записывается_и_обновляется(session: Session):
    added, updated = sync_registry(session, now=NOW)
    assert added == len(ALL_SOURCES)
    assert updated == 0

    added2, updated2 = sync_registry(session, now=NOW)
    assert added2 == 0
    assert updated2 == len(ALL_SOURCES)


def test_бесплатное_ядро_есть_но_ждёт_проверки_условий(session: Session):
    """Бесплатный маршрут — это ещё не разрешение собирать.

    Половина ядра собирается без покупки данных, и это хорошая новость. Но
    ``approved`` в реестре означает «маршрут официальный и бесплатный», а не
    «можно не думать»: пока срок проверки условий не проставлен, шлюз не
    пускает. Источник не начинает собираться сам только потому, что кто-то
    написал в таблице нужное слово.
    """
    sync_registry(session, now=NOW)
    state = readiness(session)

    assert state["total"] == len(ALL_SOURCES)
    # Бесплатных маршрутов больше половины — ядро действительно собирается.
    assert state["free_route"] >= 12
    # И ни один из них пока не пропускает сбор.
    assert set(state["awaiting_terms_check"]) and all(
        item for item in state["awaiting_terms_check"]
    )
    for source_id in state["awaiting_terms_check"]:
        with pytest.raises(CollectionDenied) as exc:
            authorize(session, source_id, {"fetch"}, now=NOW)
        assert "просрочена" in exc.value.reason or "не задан" in exc.value.reason


def test_у_каждого_недоступного_источника_названа_замена(session: Session):
    """Заблокированный источник без замены — тупик, а не решение."""
    sync_registry(session, now=NOW)
    state = readiness(session)
    for item in state["paid_or_prohibited"]:
        assert item["note"], item["source_id"]
    # У двух главных запретов замена названа прямо в причине.
    notes = {i["source_id"]: i["note"] for i in state["paid_or_prohibited"]}
    assert "Работа России" in notes["hh_ru"]
    assert "API брокера" in notes["moex_iss"]


def test_порядок_подключения_ссылается_на_существующие_источники(session: Session):
    known = {spec.source_id for spec in ALL_SOURCES}
    assert set(CONNECT_ORDER) <= known


def test_hh_ru_запрещён_прямо(session: Session):
    """§6.1.2: стандартные условия ограничивают тематику трудоустройства."""
    sync_registry(session, now=NOW)
    with pytest.raises(CollectionDenied) as exc:
        authorize(session, "hh_ru", {"fetch"}, now=NOW)
    assert "prohibited" in exc.value.reason


def test_moex_iss_запрещён_а_не_просто_непроверен(session: Session):
    """Биржа прямо запрещает применять задержанные данные ради прибыли.

    Это не «мы не разобрались», а известный запрет, и статус обязан его
    отражать: непроверенный источник когда-нибудь проверят, запрещённый —
    нет. Замена — API брокера, токен которого у приложения уже есть.
    """
    sync_registry(session, now=NOW)
    with pytest.raises(CollectionDenied) as exc:
        authorize(session, "moex_iss", {"fetch"}, now=NOW)
    assert "prohibited" in exc.value.reason

    blocked = dict((s, note) for s, _, note in blocked_sources(session))
    assert "API брокера" in blocked["moex_iss"]


def test_апи_отдаёт_гипотезы_и_состояние_источников(session: Session):
    """Пустая выдача обязана называть причину.

    «Гипотез нет, потому что рынок спокоен» и «гипотез нет, потому что ни
    один источник не подключён» — разные новости, и вторая требует действия
    владельца, а не ожидания.
    """
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    sync_registry(session, now=NOW)
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app) as client:
            body = client.get("/api/v1/research/hypotheses").json()
    finally:
        app.dependency_overrides.clear()

    assert body["hypotheses"] == []
    status = body["status"]
    assert status["free_route"] >= 12
    assert status["awaiting_terms_check"]
    assert "проверки условий" in status["reason"]
    # Что нельзя подключить вовсе — тоже видно, с причиной и заменой.
    assert any(item["source_id"] == "hh_ru" for item in status["unavailable"])


def test_в_выдаче_нет_ни_одной_торговой_команды(session: Session):
    """§16.4: ни BUY, ни SELL, ни размера позиции.

    Проверяется по схеме ответа, а не по глазам: поле, которого нет в
    контракте, невозможно заполнить по недосмотру.
    """
    from app.api.v1.research import HypothesisOut

    fields = set(HypothesisOut.model_fields)
    assert not fields & {"side", "quantity", "executable", "entry", "stop_loss"}
