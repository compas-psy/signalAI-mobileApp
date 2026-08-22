from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from app.execution.promotion_evidence import (
    PromotionEvidenceKind,
    PromotionEvidenceScope,
    PromotionEvidenceSnapshotInput,
    PromotionVenueCapability,
    persist_promotion_evidence_snapshot,
)


SOURCE_SHA = "a" * 40
CONFIG_HASH = "b" * 64
ADR_HASH = hashlib.sha256(b"ADR-0002").hexdigest()
STRATEGY_FAMILY = "TREND_PULLBACK"
STRATEGY_VERSION = "trend-pullback-v2"


def _scope(*, source_sha: str = SOURCE_SHA, config_hash: str = CONFIG_HASH):
    return PromotionEvidenceScope(
        strategy_family=STRATEGY_FAMILY,
        strategy_version=STRATEGY_VERSION,
        venue="LIGHTER",
        source_hash=hashlib.sha256(source_sha.encode("utf-8")).hexdigest(),
        config_hash=config_hash,
        policy_hash=ADR_HASH,
    )


def _snapshot(
    session,
    *,
    key: str,
    kind: PromotionEvidenceKind,
    source: str,
    capability: PromotionVenueCapability | None = None,
    reconciliation: bool = False,
    protection: bool = False,
    kill_switch: bool = False,
    gate_passed: bool = True,
    fresh_until: datetime | None = None,
    scope: PromotionEvidenceScope | None = None,
):
    now = datetime.now(UTC) - timedelta(minutes=1)
    row = persist_promotion_evidence_snapshot(
        session,
        PromotionEvidenceSnapshotInput(
            kind=kind,
            scope=scope or _scope(),
            source=source,
            evidence_version=1,
            observed_at=now,
            fresh_until=fresh_until or (now + timedelta(hours=1)),
            sample_size=1,
            error_count=0,
            gate_passed=gate_passed,
            reconciliation_verified=reconciliation,
            protection_verified=protection,
            kill_switch_verified=kill_switch,
            capability=capability,
        ),
    )
    session.flush()
    return key, str(row.id)


def _valid_refs(session) -> dict[str, str]:
    pairs = (
        _snapshot(
            session,
            key="strategy_performance",
            kind=PromotionEvidenceKind.PERFORMANCE,
            source="canary-strategy-performance/v1",
        ),
        _snapshot(
            session,
            key="shadow",
            kind=PromotionEvidenceKind.PERFORMANCE,
            source="canary-shadow/v1",
        ),
        _snapshot(
            session,
            key="testnet",
            kind=PromotionEvidenceKind.TECHNICAL,
            source="canary-lighter-testnet/v1",
            capability=PromotionVenueCapability.LIGHTER_TESTNET_ACTIONS_ONLY,
        ),
        _snapshot(
            session,
            key="protection_reconciliation",
            kind=PromotionEvidenceKind.TECHNICAL,
            source="canary-protection-reconciliation/v1",
            reconciliation=True,
            protection=True,
        ),
        _snapshot(
            session,
            key="kill_switch_drill",
            kind=PromotionEvidenceKind.OPERATIONS,
            source="canary-kill-switch-drill/v1",
            kill_switch=True,
        ),
        _snapshot(
            session,
            key="security_scan",
            kind=PromotionEvidenceKind.OPERATIONS,
            source="canary-security-scan/v1",
        ),
        _snapshot(
            session,
            key="operational_health",
            kind=PromotionEvidenceKind.OPERATIONS,
            source="canary-operational-health/v1",
            reconciliation=True,
        ),
    )
    return dict(pairs)


def _evaluate(session, refs: dict[str, str], *, source_sha: str = SOURCE_SHA):
    from app.execution.canary_evidence import evaluate_canary_evidence_bindings

    return evaluate_canary_evidence_bindings(
        session,
        evidence_refs=refs,
        source_sha=source_sha,
        config_hash=CONFIG_HASH,
        strategy_family=STRATEGY_FAMILY,
        strategy_version=STRATEGY_VERSION,
        venue="LIGHTER",
        now=datetime.now(UTC),
    )


def test_exact_server_snapshots_satisfy_authoritative_evidence_binding(session) -> None:
    refs = _valid_refs(session)
    result = _evaluate(session, refs)

    assert result.ready is True
    assert result.blockers == ()
    assert set(result.snapshot_ids) == set(refs.values())


def test_missing_malformed_or_reused_ref_fails_closed(session) -> None:
    refs = _valid_refs(session)
    refs.pop("security_scan")
    missing = _evaluate(session, refs)
    assert missing.ready is False
    assert "CANARY_EVIDENCE_REFS_INCOMPLETE" in missing.blockers

    refs = _valid_refs(session)
    refs["security_scan"] = "not-a-uuid"
    malformed = _evaluate(session, refs)
    assert "CANARY_EVIDENCE_SECURITY_SCAN_REF_INVALID" in malformed.blockers

    refs = _valid_refs(session)
    refs["shadow"] = refs["strategy_performance"]
    reused = _evaluate(session, refs)
    assert "CANARY_EVIDENCE_REF_REUSED" in reused.blockers


def test_ref_must_match_exact_type_source_and_release_scope(session) -> None:
    refs = _valid_refs(session)
    _, wrong = _snapshot(
        session,
        key="security_scan",
        kind=PromotionEvidenceKind.TECHNICAL,
        source="canary-security-scan/v1",
    )
    refs["security_scan"] = wrong
    wrong_type = _evaluate(session, refs)
    assert "CANARY_EVIDENCE_SECURITY_SCAN_TYPE_MISMATCH" in wrong_type.blockers

    refs = _valid_refs(session)
    _, wrong = _snapshot(
        session,
        key="shadow",
        kind=PromotionEvidenceKind.PERFORMANCE,
        source="some-other-producer/v1",
    )
    refs["shadow"] = wrong
    wrong_source = _evaluate(session, refs)
    assert "CANARY_EVIDENCE_SHADOW_SOURCE_MISMATCH" in wrong_source.blockers

    refs = _valid_refs(session)
    _, wrong = _snapshot(
        session,
        key="operational_health",
        kind=PromotionEvidenceKind.OPERATIONS,
        source="canary-operational-health/v1",
        reconciliation=True,
        scope=_scope(source_sha="c" * 40),
    )
    refs["operational_health"] = wrong
    wrong_release = _evaluate(session, refs)
    assert "CANARY_EVIDENCE_OPERATIONAL_HEALTH_SCOPE_MISMATCH" in wrong_release.blockers


def test_stale_failed_or_incomplete_semantic_evidence_fails_closed(session) -> None:
    refs = _valid_refs(session)
    now = datetime.now(UTC)
    _, stale = _snapshot(
        session,
        key="strategy_performance",
        kind=PromotionEvidenceKind.PERFORMANCE,
        source="canary-strategy-performance/v1",
        fresh_until=now - timedelta(seconds=1),
    )
    refs["strategy_performance"] = stale
    stale_result = _evaluate(session, refs)
    assert "CANARY_EVIDENCE_STRATEGY_PERFORMANCE_STALE" in stale_result.blockers

    refs = _valid_refs(session)
    _, failed = _snapshot(
        session,
        key="security_scan",
        kind=PromotionEvidenceKind.OPERATIONS,
        source="canary-security-scan/v1",
        gate_passed=False,
    )
    refs["security_scan"] = failed
    failed_result = _evaluate(session, refs)
    assert "CANARY_EVIDENCE_SECURITY_SCAN_GATE_FAILED" in failed_result.blockers

    refs = _valid_refs(session)
    _, incomplete = _snapshot(
        session,
        key="protection_reconciliation",
        kind=PromotionEvidenceKind.TECHNICAL,
        source="canary-protection-reconciliation/v1",
        reconciliation=True,
        protection=False,
    )
    refs["protection_reconciliation"] = incomplete
    incomplete_result = _evaluate(session, refs)
    assert "CANARY_EVIDENCE_PROTECTION_RECONCILIATION_INCOMPLETE" in incomplete_result.blockers


def test_testnet_ref_is_only_evidence_and_never_can_authorize_canary(session) -> None:
    refs = _valid_refs(session)
    result = _evaluate(session, refs)
    assert result.ready is True

    # Binding durable evidence is deliberately not an authorization primitive.
    public = result.to_public_dict()
    assert "eligible_for_canary" not in public
    assert "authorization" not in public
    assert "credential" not in public
