from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.execution.enums import ExecutionLifecycleMode


def _scope():
    from app.execution.promotion_evidence import PromotionEvidenceScope

    return PromotionEvidenceScope(
        strategy_family="TREND_PULLBACK",
        strategy_version="candidate_v1",
        venue="LIGHTER",
        source_hash="a" * 64,
        config_hash="b" * 64,
        policy_hash="c" * 64,
    )


def test_persisted_evidence_is_append_only_and_evaluated_by_exact_scope(session):
    from app.execution.promotion_evidence import (
        PromotionEvidenceKind,
        PromotionEvidenceSnapshotInput,
        current_persisted_promotion_evidence,
        persist_promotion_evidence_snapshot,
    )
    from app.models import PromotionEvidenceSnapshot

    now = datetime(2026, 8, 21, 10, tzinfo=UTC)
    scope = _scope()
    for kind in PromotionEvidenceKind:
        persist_promotion_evidence_snapshot(
            session,
            PromotionEvidenceSnapshotInput(
                kind=kind,
                scope=scope,
                source="server-test",
                evidence_version=1,
                observed_at=now,
                fresh_until=now + timedelta(hours=1),
                sample_size=3,
                error_count=0,
                gate_passed=True,
                reconciliation_verified=True,
                protection_verified=True,
                kill_switch_verified=True,
            ),
        )
    session.flush()

    evidence = current_persisted_promotion_evidence(
        session,
        scope=scope,
        target=ExecutionLifecycleMode.SANDBOX,
        now=now + timedelta(minutes=1),
    )
    assert evidence.technical_sandbox_ready is True

    row = session.query(PromotionEvidenceSnapshot).first()
    assert row is not None
    with pytest.raises(Exception):
        session.execute(
            text(
                "UPDATE promotion_evidence_snapshots SET source = 'mutated' "
                "WHERE id = :id"
            ),
            {"id": row.id},
        )


def test_promotion_preview_records_append_only_decision_correlation(session):
    from app.execution import promotion_guard
    from app.models import PromotionEvidenceDecision

    decision = promotion_guard.preview_promotion(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
    )
    session.flush()

    assert decision.allowed is False
    assert decision.correlation_id
    row = session.query(PromotionEvidenceDecision).filter_by(
        correlation_id=decision.correlation_id
    ).one()
    assert row.allowed is False
    assert row.target_mode == ExecutionLifecycleMode.SANDBOX
    assert row.blockers_json == ["technical sandbox readiness not verified"]
    with pytest.raises(Exception):
        session.execute(
            text("DELETE FROM promotion_evidence_decisions WHERE id = :id"),
            {"id": row.id},
        )
