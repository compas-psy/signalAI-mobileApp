"""Подключение к PostgreSQL.

Адрес базы приходит из окружения и никогда не лежит в git (§21: секреты
только через env/vault). По умолчанию — локальная разработочная база, чтобы
тесты и `alembic` работали без настройки.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DSN = "postgresql+psycopg://signalai:signalai_local_dev@127.0.0.1:5432/signalai"


def database_url() -> str:
    explicit = os.environ.get("SIGNALAI_DATABASE_URL", "").strip()
    if explicit:
        return explicit

    # Pytest запускается против отдельной настоящей PostgreSQL-базы. API
    # dependencies в тестах подменяются на транзакционную session fixture,
    # но middleware намеренно открывает собственную короткую сессию и поэтому
    # не видит dependency override. Раньше он уходил в DEFAULT_DSN `/signalai`,
    # тогда как CI создаёт `signalai_test`: после появления lifecycle guard
    # это превратило один неверный DSN в десятки ложных падений API-тестов.
    #
    # SIGNALAI_ADMIN_DSN задаётся только тестовому runner'у. В production его
    # нет, поэтому боевой DSN по-прежнему берётся исключительно из
    # SIGNALAI_DATABASE_URL и никакой тестовый fallback туда попасть не может.
    admin = os.environ.get("SIGNALAI_ADMIN_DSN", "").strip()
    if admin:
        test_db = os.environ.get("SIGNALAI_TEST_DB", "signalai_test").strip()
        if test_db:
            return admin.rsplit("/", 1)[0] + f"/{test_db}"

    return DEFAULT_DSN


_engine = None
_SessionFactory: sessionmaker[Session] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            database_url(),
            pool_pre_ping=True,
            # Движок держит соединение между задачами планировщика; ping
            # дешевле, чем упавший скан из-за разорванного коннекта.
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(), expire_on_commit=False, future=True
        )
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Транзакция с откатом при любой ошибке."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """Зависимость FastAPI."""
    with session_scope() as session:
        yield session


def reset_engine() -> None:
    """Сбросить кэш подключения — нужно тестам, меняющим DSN."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
