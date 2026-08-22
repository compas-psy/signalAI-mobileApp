from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text


REQUIRED_REFS = {
    "strategy_performance": "strategy-performance-evidence-001",
    "shadow": "shadow-evidence-001",
    "testnet": "testnet-evidence-001",
    "protection_reconciliation": "protection-reconciliation-evidence-001",
    "kill_switch_drill": "kill-switch-drill-evidence-001",
    "security_scan": "security-scan-evidence-001",
    "operational_health": "operational-health-evidence-001",
}


def _scope():
    from app.execution.canary_evidence import CanaryEvidenceScope

    return CanaryEvidenceScope(
        source_sha="a" * 40,
        engine_config_hash="b" * 64,
        strategy_family="TREND_PULLBACK",
        strategy_version="trend-pullback-v2",
        venue="LIGHTER",
    )


def _input(category: str, *, now: datetime, ref: str | None = None, **overrides):
    from app.execution.canary_evidence import CanaryEvidenceReferenceInput

    values = {
        "category": category,
        "evidence_ref": ref or REQUIRED_REFS[category],
        "scope": _scope(),
        "source": "server-evidence-test/v1",
        "artifact_sha256": "c" * 64,
        "verdict": "VERIFIED",
        "observed_at": now,
        "fresh_until": now + timedelta(hours=1),
    }
    values.update(overrides)
    return CanaryEvidenceReferenceInput(**values)


def test_registry_requires_exact_typed_refs_and_is_append_only(session):
    from app.execution.canary_evidence import (
        CanaryEvidenceCategory,
        persist_canary_evidence_reference,
    )

    now = datetime.now(UTC)
    row = persist_canary_evidence_reference(
        session,
        _input(CanaryEvidenceCategory.STRATEGY_PERFORMANCE.value, now=now),
    )
    session.flush()
    assert row.evidence_ref == REQUIRED_REFS["strategy_performance"]

    with pytest.raises(Exception):
        session.execute(
            text(
                "UPDATE canary_evidence_references "
                "SET verdict = 'FAILED' WHERE id = :id"
            ),
            {"id": row.id},
        )


def test_binding_is_complete_only_when_all_seven_exact_refs_are_verified_and_fresh(session):
    from app.execution.canary_evidence import (
        persist_canary_evidence_reference,
        verify_canary_evidence_refs,
    )

    now = datetime.now(UTC)
    for category, ref in REQUIRED_REFS.items():
        persist_canary_evidence_reference(
            session,
            _input(category, now=now, ref=ref),
        )
    session.flush()

    result = verify_canary_evidence_refs(
        session,
        evidence_refs=REQUIRED_REFS,
        scope=_scope(),
        now=now + timedelta(minutes=1),
    )
    assert result.complete is True
    assert result.blockers == ()
    assert set(result.evidence_refs) == set(REQUIRED_REFS.values())


def test_binding_fails_closed_on_missing_wrong_category_failed_stale_or_scope_drift(session):
    from app.execution.canary_evidence import (
        persist_canary_evidence_reference,
        verify_canary_evidence_refs,
    )

    now = datetime.now(UTC)
    persist_canary_evidence_reference(
        session,
        _input(
            "operational_health",
            now=now,
            ref=REQUIRED_REFS["strategy_performance"],
        ),
    )
    persist_canary_evidence_reference(
        session,
        _input("shadow", now=now, verdict="FAILED"),
    )
    persist_canary_evidence_reference(
        session,
        _input(
            "testnet",
            now=now - timedelta(hours=2),
            fresh_until=now - timedelta(minutes=1),
        ),
    )
    drifted_scope = type(_scope())(
        source_sha="d" * 40,
        engine_config_hash=_scope().engine_config_hash,
        strategy_family=_scope().strategy_family,
        strategy_version=_scope().strategy_version,
        venue=_scope().venue,
    )
    persist_canary_evidence_reference(
        session,
        _input(
            "protection_reconciliation",
            now=now,
            scope=drifted_scope,
        ),
    )
    session.flush()

    result = verify_canary_evidence_refs(
        session,
        evidence_refs=REQUIRED_REFS,
        scope=_scope(),
        now=now,
    )
    assert result.complete is False
    assert "CANARY_EVIDENCE_CATEGORY_MISMATCH:strategy_performance" in result.blockers
    assert "CANARY_EVIDENCE_FAILED:shadow" in result.blockers
    assert "CANARY_EVIDENCE_STALE:testnet" in result.blockers
    assert "CANARY_EVIDENCE_SCOPE_MISMATCH:protection_reconciliation" in result.blockers
    assert "CANARY_EVIDENCE_MISSING:kill_switch_drill" in result.blockers
    assert "CANARY_EVIDENCE_MISSING:security_scan" in result.blockers
    assert "CANARY_EVIDENCE_MISSING:operational_health" in result.blockers


def test_registry_rejects_untrusted_shape_future_observation_and_unknown_category():
    from app.execution.canary_evidence import (
        CanaryEvidenceInputError,
        CanaryEvidenceReferenceInput,
    )

    now = datetime.now(UTC)
    with pytest.raises(CanaryEvidenceInputError):
        CanaryEvidenceReferenceInput(
            category="not_a_real_gate",
            evidence_ref="x",
            scope=_scope(),
            source="test",
            artifact_sha256="c" * 64,
            verdict="VERIFIED",
            observed_at=now,
            fresh_until=now + timedelta(minutes=1),
        )
    with pytest.raises(CanaryEvidenceInputError, match="future"):
        _input(
            "security_scan",
            now=now + timedelta(seconds=1),
            fresh_until=now + timedelta(minutes=1),
        )
