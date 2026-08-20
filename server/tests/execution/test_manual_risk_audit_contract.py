from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.db import get_db
from app.main import app
from app.models import AuditEvent, RiskSnapshot, TradeIdea
from app.risk.manual_audit import (
    ManualRiskAuditRecord,
    persist_manual_risk_audit,
)
from tests.conftest import ADMIN_DSN, DEVICE_HEADERS, idea_kwargs


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def isolated_audit_engine():
    """Real migrated PostgreSQL DB for the one test that must commit durably.

    Normal tests share a session-scoped database and rely on an outer rollback.
    SAI-046 intentionally proves that a rejection audit commits independently of
    its caller, so using that shared database would leak an append-only row into
    later tests. A short-lived database preserves the real transaction/trigger
    semantics without weakening global test isolation or deleting audit facts.
    """

    db_name = f"signalai_audit_{uuid.uuid4().hex[:12]}"
    database_url = ADMIN_DSN.rsplit("/", 1)[0] + f"/{db_name}"
    admin = create_engine(ADMIN_DSN, isolation_level="AUTOCOMMIT", future=True)
    isolated = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))

        root = Path(__file__).resolve().parents[2]
        env = dict(os.environ, SIGNALAI_DATABASE_URL=database_url)
        migration = subprocess.run(
            [str(root / ".venv" / "bin" / "alembic"), "upgrade", "head"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        if migration.returncode != 0:
            raise RuntimeError(
                "isolated SAI-046 audit database migrations failed:\n"
                f"{migration.stderr}"
            )
        isolated = create_engine(database_url, future=True)
        yield isolated
    finally:
        if isolated is not None:
            isolated.dispose()
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": db_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()


def _seed(session, instrument, now):
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            status="TRIGGERED",
            quality_status="ACTIVE",
            risk_pct=Decimal("0.005"),
            risk_amount=Decimal("1000"),
            quantity=Decimal("1"),
        )
    )
    risk = RiskSnapshot(
        risk_equity=Decimal("200000"),
        open_risk=Decimal("0"),
        day_pnl_pct=Decimal("0"),
        week_pnl_pct=Decimal("0"),
        month_pnl_pct=Decimal("0"),
        current_drawdown=Decimal("0"),
        drawdown_multiplier=Decimal("1"),
        cluster_risk_json={"rub_fx": "0"},
    )
    session.add_all([idea, risk])
    session.flush()
    return idea, risk


def _preview(client, idea, *, preset_id: str = "BOOST_2") -> dict:
    response = client.post(
        "/api/v1/risk/preview",
        json={
            "idea_id": str(idea.id),
            "preset_id": preset_id,
            "current_mode": "PAPER",
        },
    )
    assert response.status_code == 200
    return response.json()


def _apply(client, idea, preview: dict, *, key: str, token: str | None = None):
    return client.post(
        "/api/v1/risk/override",
        json={
            "idea_id": str(idea.id),
            "preset_id": preview["preset_id"],
            "current_mode": preview["execution_mode"],
            "preview_hash": token if token is not None else preview["preview_hash"],
            "owner_confirmed": True,
            "reason": "owner confirmed bounded risk boost",
        },
        headers={"X-Idempotency-Key": key},
    )


def _event_text(event: AuditEvent) -> str:
    return json.dumps(
        {
            "detail": event.detail,
            "before": event.before_json,
            "after": event.after_json,
        },
        sort_keys=True,
        default=str,
    )


def test_sai_046_preview_audit_captures_exact_owner_view_without_raw_token(
    client,
    session,
    instrument,
    now,
):
    idea, risk = _seed(session, instrument, now)

    preview = _preview(client, idea)

    assert preview["allowed"] is True
    events = session.execute(
        select(AuditEvent).where(
            AuditEvent.action == "manual_risk_previewed",
            AuditEvent.subject == str(idea.id),
        )
    ).scalars().all()
    assert len(events) == 1
    event = events[0]
    assert event.actor == "owner"
    assert event.after_json["outcome"] == "ALLOWED"
    assert event.after_json["risk_snapshot_id"] == str(risk.id)
    assert event.after_json["preset_id"] == "BOOST_2"
    assert event.after_json["execution_mode"] == "PAPER"
    assert Decimal(event.after_json["auto_risk_pct"]) == Decimal("0.005")
    assert Decimal(event.after_json["effective_risk_pct"]) == Decimal(
        str(preview["effective_risk_pct"])
    )
    assert Decimal(event.after_json["effective_quantity"]) == Decimal(
        str(preview["quantity"])
    )
    assert event.after_json["binding_constraint"] == preview["binding_constraint"]
    assert event.after_json["preview_token_sha256"] == hashlib.sha256(
        preview["preview_hash"].encode("utf-8")
    ).hexdigest()
    assert preview["preview_hash"] not in _event_text(event)


def test_sai_046_apply_audit_distinguishes_applied_and_replayed_and_hashes_keys(
    client,
    session,
    instrument,
    now,
):
    idea, _ = _seed(session, instrument, now)
    preview = _preview(client, idea)
    first_key = "audit-apply-first"
    replay_key = "audit-apply-replay"

    first = _apply(client, idea, preview, key=first_key)
    replay = _apply(client, idea, preview, key=replay_key)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["created"] is True
    assert replay.json()["created"] is False
    events = session.execute(
        select(AuditEvent)
        .where(
            AuditEvent.action == "manual_risk_apply_outcome",
            AuditEvent.subject == str(idea.id),
        )
        .order_by(AuditEvent.occurred_at.asc())
    ).scalars().all()
    assert [item.after_json["outcome"] for item in events] == ["APPLIED", "REPLAYED"]
    assert events[0].after_json["override_id"] == first.json()["override_id"]
    assert events[1].after_json["override_id"] == first.json()["override_id"]
    assert events[0].after_json["preview_token_sha256"] == events[1].after_json[
        "preview_token_sha256"
    ]
    assert events[0].after_json["idempotency_key_sha256"] == hashlib.sha256(
        first_key.encode("utf-8")
    ).hexdigest()
    assert events[1].after_json["idempotency_key_sha256"] == hashlib.sha256(
        replay_key.encode("utf-8")
    ).hexdigest()
    combined = "\n".join(_event_text(item) for item in events)
    assert preview["preview_hash"] not in combined
    assert first_key not in combined
    assert replay_key not in combined


def test_sai_046_rejected_apply_uses_durable_audit_boundary_without_raw_secrets(
    client,
    session,
    instrument,
    now,
    monkeypatch,
):
    idea, _ = _seed(session, instrument, now)
    preview = _preview(client, idea)
    raw_token = preview["preview_hash"]
    tampered = f"{raw_token[:-1]}{'0' if raw_token[-1] != '0' else '1'}"
    raw_key = "audit-rejected-key"
    captured: list[ManualRiskAuditRecord] = []

    def capture(record: ManualRiskAuditRecord, **_kwargs) -> None:
        captured.append(record)

    monkeypatch.setattr("app.api.v1.risk.persist_manual_risk_audit", capture)

    response = _apply(client, idea, preview, key=raw_key, token=tampered)

    assert response.status_code == 409
    assert len(captured) == 1
    record = captured[0]
    assert record.action == "manual_risk_apply_outcome"
    assert record.subject == str(idea.id)
    assert record.outcome == "REJECTED"
    assert record.context["preview_token_sha256"] == hashlib.sha256(
        tampered.encode("utf-8")
    ).hexdigest()
    assert record.context["idempotency_key_sha256"] == hashlib.sha256(
        raw_key.encode("utf-8")
    ).hexdigest()
    serialized = json.dumps(record.context, sort_keys=True, default=str)
    assert tampered not in serialized
    assert raw_key not in serialized
    assert record.context["rejection_detail"]


def test_sai_046_rejection_audit_commit_survives_caller_transaction_rollback(
    isolated_audit_engine,
):
    marker = "sai-046-independent-rejection-audit"
    factory = sessionmaker(
        bind=isolated_audit_engine,
        expire_on_commit=False,
        future=True,
    )
    record = ManualRiskAuditRecord(
        action="manual_risk_apply_outcome",
        subject=marker,
        outcome="REJECTED",
        detail="test durable rejection audit",
        context={
            "preset_id": "BOOST_2",
            "execution_mode": "PAPER",
            "rejection_detail": "signed preview is stale",
            "preview_token_sha256": "a" * 64,
            "idempotency_key_sha256": "b" * 64,
        },
    )

    with factory() as caller:
        persist_manual_risk_audit(record, session_factory=factory)
        caller.rollback()

    with factory() as verify:
        events = verify.execute(
            select(AuditEvent).where(
                AuditEvent.action == "manual_risk_apply_outcome",
                AuditEvent.subject == marker,
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].after_json["outcome"] == "REJECTED"
        assert events[0].after_json["preview_token_sha256"] == "a" * 64
