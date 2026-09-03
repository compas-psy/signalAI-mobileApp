"""Общие приспособления тестов.

Тесты идут против **настоящего PostgreSQL**, а не против SQLite. Половина
того, что здесь проверяется, в SQLite физически отсутствует: триггеры
append-only, `NUMERIC` с сохранением точности, `CHECK` с условием по
направлению сделки, `JSONB`, массивы. Тест на подменённой базе доказывал бы
свойства подмены, а не системы.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.device_enrollment import token_verifier
from app.market.candles import Candle
from app.market.economic_events import EventAssessment
from app.models import (
    Base,
    DeviceCredential,
    Instrument,
    RetentionAttemptIntent,
    RetentionAttemptOutcome,
)
from app.models.enums import AssetClass, Venue
from tests.calendar_support import (
    CLEAR_EVENT_CALENDAR_BLOCK,
    configure_clear_event_calendar,
)

ADMIN_DSN = os.environ.get(
    "SIGNALAI_ADMIN_DSN",
    "postgresql+psycopg://signalai:signalai_local_dev@127.0.0.1:5432/postgres",
)
TEST_DB = os.environ.get("SIGNALAI_TEST_DB", "signalai_test")
TEST_DSN = ADMIN_DSN.rsplit("/", 1)[0] + f"/{TEST_DB}"
DEVICE_TOKEN = "c" * 43
PAIRING_SESSION_ID = "p" * 43
DEVICE_HEADERS = {"Authorization": f"Bearer {DEVICE_TOKEN}"}
_ACTIONABLE_CALENDAR_MODULES = {
    "test_api",
    "test_decision_replay_diagnostics",
    "test_thin_lifecycle_regressions",
}
_DURABLE_RETENTION_TEST = "test_unresolved_older_intent_blocks_new_period_before_unlink"


@pytest.fixture(autouse=True)
def configured_device_token(monkeypatch):
    """Real app tests cross the same device-auth boundary as production."""
    monkeypatch.setenv("SIGNALAI_DEVICE_TOKEN", DEVICE_TOKEN)
    # A bootstrap token alone must never be enough to mint device bearers.
    # Tests receive a separate, short-lived-in-production pairing capability.
    monkeypatch.setenv("SIGNALAI_DEVICE_PAIRING_SESSION_ID", PAIRING_SESSION_ID)
    monkeypatch.setenv("SIGNALAI_DEVICE_PAIRING_EXPIRES_AT", "2030-01-01T00:00:00Z")
    monkeypatch.setenv("SIGNALAI_DEVICE_PAIRING_MAX_USES", "8")
    monkeypatch.setenv("SIGNALAI_RISK_PREVIEW_SIGNING_KEY", "ci-risk-preview-secret")
    monkeypatch.setenv(
        "SIGNALAI_LIGHTER_LIVE_SECRETS_KEY",
        "ci-only-lighter-live-vault-key-material-000000000001",
    )


@pytest.fixture(autouse=True)
def configured_actionable_calendar(request, monkeypatch, tmp_path):
    """Supply explicit safe prerequisites only to tests that require them."""
    module_name = getattr(request.module, "__name__", "").rsplit(".", 1)[-1]
    if module_name in _ACTIONABLE_CALENDAR_MODULES:
        configure_clear_event_calendar(monkeypatch, tmp_path)
        monkeypatch.setenv("SIGNALAI_TEST_CLEAR_CALENDAR_FIXTURE", "1")
        return
    if module_name == "test_scoring" and hasattr(request.module, "GOOD"):
        # GOOD means every admission prerequisite is known-good. Missing event
        # data is intentionally fail-closed in production, so make CLEAR an
        # explicit part of this legacy convenience fixture rather than changing
        # admit()'s default.
        request.module.GOOD["event_assessment"] = EventAssessment(
            "CLEAR", "NO_BLOCKING_EVENT", "проверка календаря завершена"
        )


@pytest.fixture(autouse=True)
def configured_actionable_forts_path(request, monkeypatch):
    """Legacy approval tests get an explicit known-safe fresh market path.

    Approval now fails closed when FORTS cannot be revalidated. These tests are
    about auth/idempotency/paper lifecycle rather than MOEX availability, so
    give them a complete post-signal candle that has touched neither entry,
    target nor stop. Tests dedicated to live progress use their own stubs and
    are intentionally not covered by this fixture.
    """
    module_name = getattr(request.module, "__name__", "").rsplit(".", 1)[-1]
    if module_name not in _ACTIONABLE_CALENDAR_MODULES:
        return

    def safe_forts_path(*args, **kwargs):
        # Construct at request time, after the test has created its idea. A
        # candle created when this fixture is installed can precede an idea
        # whose signal_time is datetime.now(), and would correctly look like
        # NO_DATA to the production evaluator.
        safe = Candle(
            open_time=datetime.now(UTC),
            open=Decimal("90400"),
            high=Decimal("90500"),
            low=Decimal("90300"),
            close=Decimal("90400"),
            is_closed=True,
            source="test-safe-forts-admission",
        )
        return [safe], None

    monkeypatch.setattr(
        "app.api.v1.idea_progress.guarded_candles",
        safe_forts_path,
    )


def _recreate_database() -> None:
    admin = create_engine(ADMIN_DSN, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": TEST_DB},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    """Чистая база с применёнными миграциями.

    Схема ставится именно `alembic upgrade head`, а не
    `Base.metadata.create_all`: иначе тесты проверяли бы модели, а миграции
    (то, что реально поедет на сервер) оставались бы непроверенными.
    """
    _recreate_database()
    env = dict(os.environ, SIGNALAI_DATABASE_URL=TEST_DSN)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [os.path.join(root, ".venv", "bin", "alembic"), "upgrade", "head"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"миграции не применились:\n{result.stderr}")
    fixture_engine = create_engine(TEST_DSN, future=True)
    with Session(fixture_engine) as session:
        session.add(
            DeviceCredential(
                device_id="ci-device-enrollment-fixture",
                generation=1,
                token_verifier=token_verifier(DEVICE_TOKEN),
                issued_request_hash=hashlib.sha256(
                    b"ci-device-fixture"
                ).hexdigest(),
                metadata_json={"label": "CI", "platform": "test"},
            )
        )
        session.commit()
    fixture_engine.dispose()
    return TEST_DSN


@pytest.fixture(scope="session")
def engine(database_url: str):
    eng = create_engine(database_url, future=True)
    yield eng
    eng.dispose()


def _resolve_committed_retention_intents(db: Session) -> None:
    unresolved = list(
        db.scalars(
            select(RetentionAttemptIntent)
            .outerjoin(
                RetentionAttemptOutcome,
                RetentionAttemptOutcome.attempt_id == RetentionAttemptIntent.attempt_id,
            )
            .where(RetentionAttemptOutcome.attempt_id.is_(None))
        )
    )
    for intent in unresolved:
        db.add(
            RetentionAttemptOutcome(
                attempt_id=intent.attempt_id,
                occurred_at=datetime.now(UTC),
                status="FAILED",
                result_json={
                    "status": "FAILED",
                    "candidate_files": 0,
                    "candidate_bytes": 0,
                    "deleted_files": 0,
                    "deleted_bytes": 0,
                    "errors": ["test fixture resolved committed intent"],
                },
            )
        )
    if unresolved:
        db.commit()


@pytest.fixture
def session(engine, request) -> Session:
    """Normally roll back each test; one retention test needs real durability.

    The retention executor deliberately opens another PostgreSQL connection
    before unlinking. Its unresolved-intent contract therefore cannot be tested
    with an uncommitted outer test transaction. That one test receives a real
    committing session, then teardown appends an outcome so later tests are not
    poisoned by an intentionally unresolved audit record.
    """
    if request.node.name == _DURABLE_RETENTION_TEST:
        db = Session(engine, expire_on_commit=False, future=True)
        try:
            yield db
        finally:
            db.rollback()
            _resolve_committed_retention_intents(db)
            db.close()
        return

    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, expire_on_commit=False, future=True)
    db = factory()
    try:
        yield db
    finally:
        # Тест, проверяющий отказ базы, оставляет транзакцию в состоянии
        # aborted. Сначала откатываем сессию, потом внешнюю транзакцию — и
        # только если она ещё жива, иначе SQLAlchemy справедливо ругается.
        db.rollback()
        db.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def instrument(session: Session) -> Instrument:
    """Фьючерс с настоящей спецификацией.

    Шаг цены и стоимость шага взяты близкими к контракту Si: именно на них
    считается размер позиции, и круглые единицы прятали бы ошибки округления.
    """
    item = Instrument(
        instrument_id="MOEX:FUT:SIU6",
        venue=Venue.MOEX,
        asset_class=AssetClass.FUTURES,
        symbol="SIU6",
        title="Доллар США — рубль, сентябрь 2026",
        currency="RUB",
        tick_size=Decimal("1"),
        tick_value=Decimal("1"),
        lot_size=1,
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
        contract_multiplier=Decimal("1000"),
        correlation_cluster="rub_fx",
        in_universe=True,
    )
    session.add(item)
    session.flush()
    return item


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 7, 29, 10, 0, tzinfo=UTC)


def idea_kwargs(instrument_id: str, moment: datetime, **overrides) -> dict:
    """Минимально валидная идея.

    Собрана так, чтобы проходить все ограничения таблицы: тест на конкретное
    ограничение ломает ровно одно поле и видит именно свою ошибку.
    """
    explanation = (
        {"event_calendar": dict(CLEAR_EVENT_CALENDAR_BLOCK)}
        if os.environ.get("SIGNALAI_TEST_CLEAR_CALENDAR_FIXTURE") == "1"
        else {}
    )
    base = dict(
        instrument_id=instrument_id,
        strategy="TREND_PULLBACK",
        direction="LONG",
        status="WATCH",
        quality_status="WATCH",
        horizon_days=5,
        context_timeframe="1d",
        setup_timeframe="4h",
        trigger_timeframe="1h",
        order_intent="LIMIT_RETEST",
        entry_low=Decimal("90000"),
        entry_high=Decimal("90200"),
        entry_reference=Decimal("90100"),
        stop=Decimal("89400"),
        tp1=Decimal("91000"),
        tp2=Decimal("92000"),
        tp3=Decimal("93000"),
        invalidation="Закрытие 1H под 89400",
        rr_tp1=Decimal("1.2"),
        rr_tp2=Decimal("2.4"),
        score=Decimal("71.5"),
        data_quality=Decimal("0.95"),
        p_tp1_before_sl=Decimal("0.58"),
        p_tp2_before_sl=Decimal("0.41"),
        p_positive_r_after_costs=Decimal("0.55"),
        expected_r=Decimal("0.28"),
        confidence=Decimal("0.52"),
        sample_size=0,
        probability_source="rule_prior",
        risk_pct=Decimal("0.005"),
        risk_amount=Decimal("500"),
        quantity=Decimal("1"),
        risk_per_unit=Decimal("700"),
        correlation_cluster="rub_fx",
        drawdown_multiplier=Decimal("1"),
        explanation_json=explanation,
        data_warnings=[],
        signal_time=moment,
        expires_at=moment + timedelta(days=5),
        config_hash="0" * 64,
        engine_version="0.1.0",
        feature_version="0.1.0",
    )
    base.update(overrides)
    return base