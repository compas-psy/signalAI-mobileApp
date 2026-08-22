from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

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


def _fact(kind, *, scope=None, now=None, **overrides):
    from app.execution.promotion_evidence import PromotionEvidenceSnapshotFact

    now = now or datetime(2026, 8, 21, 10, tzinfo=UTC)
    values = {
        "kind": kind,
        "scope": scope or _scope(),
        "source": "server-test",
        "evidence_version": 1,
        "observed_at": now,
        "fresh_until": now + timedelta(hours=1),
        "sample_size": 3,
        "error_count": 0,
        "gate_passed": True,
        "reconciliation_verified": True,
        "protection_verified": True,
        "kill_switch_verified": True,
        "capability": None,
        "snapshot_id": f"{kind.value}-id",
    }
    values.update(overrides)
    return PromotionEvidenceSnapshotFact(**values)


def test_evaluator_uses_only_matching_fresh_persisted_facts():
    from app.execution.promotion_evidence import (
        PromotionEvidenceKind,
        PromotionVenueCapability,
        evaluate_persisted_promotion_evidence,
    )

    scope = _scope()
    evidence = evaluate_persisted_promotion_evidence(
        scope=scope,
        target=ExecutionLifecycleMode.SANDBOX,
        facts=(
            _fact(
                PromotionEvidenceKind.TECHNICAL,
                scope=scope,
                capability=PromotionVenueCapability.LIGHTER_TESTNET_ACTIONS_ONLY,
            ),
            _fact(PromotionEvidenceKind.PERFORMANCE, scope=scope),
            _fact(PromotionEvidenceKind.OPERATIONS, scope=scope),
        ),
        now=datetime(2026, 8, 21, 10, 30, tzinfo=UTC),
    )

    assert evidence.technical_sandbox_ready is True
    assert evidence.performance_gates_passed is True
    assert evidence.ops_gates_passed is True
    assert evidence.notes == ()


def test_evaluator_blocks_stale_and_provenance_mismatched_facts_precisely():
    from app.execution.promotion_evidence import (
        PromotionEvidenceKind,
        PromotionEvidenceScope,
        evaluate_persisted_promotion_evidence,
    )

    scope = _scope()
    mismatched = PromotionEvidenceScope(
        strategy_family=scope.strategy_family,
        strategy_version=scope.strategy_version,
        venue=scope.venue,
        source_hash="d" * 64,
        config_hash=scope.config_hash,
        policy_hash=scope.policy_hash,
    )
    now = datetime(2026, 8, 21, 10, tzinfo=UTC)
    evidence = evaluate_persisted_promotion_evidence(
        scope=scope,
        target=ExecutionLifecycleMode.LIVE,
        facts=(
            _fact(
                PromotionEvidenceKind.PERFORMANCE,
                scope=scope,
                now=now - timedelta(hours=1),
                fresh_until=now - timedelta(seconds=1),
            ),
            _fact(PromotionEvidenceKind.OPERATIONS, scope=mismatched, now=now),
        ),
        now=now,
    )

    assert evidence.performance_gates_passed is False
    assert evidence.ops_gates_passed is False
    assert "performance evidence stale" in evidence.notes
    assert "operations evidence provenance mismatch" in evidence.notes


@pytest.mark.parametrize(
    ("venue", "target", "expected_note"),
    [
        (
            "TINVEST",
            ExecutionLifecycleMode.SANDBOX,
            "T-Invest stop-entry capability is unsupported",
        ),
        (
            "LIGHTER",
            ExecutionLifecycleMode.CANARY,
            "Lighter testnet actions are not LIVE/mainnet activation evidence",
        ),
    ],
)
def test_known_venue_capability_evidence_never_authorizes_beyond_proven_scope(
    venue, target, expected_note
):
    from app.execution.promotion_evidence import (
        PromotionEvidenceKind,
        PromotionEvidenceScope,
        PromotionVenueCapability,
        evaluate_persisted_promotion_evidence,
    )

    scope = PromotionEvidenceScope(
        strategy_family="TREND_PULLBACK",
        strategy_version="candidate_v1",
        venue=venue,
        source_hash="a" * 64,
        config_hash="b" * 64,
        policy_hash="c" * 64,
    )
    evidence = evaluate_persisted_promotion_evidence(
        scope=scope,
        target=target,
        facts=(
            _fact(
                PromotionEvidenceKind.TECHNICAL,
                scope=scope,
                capability=(
                    PromotionVenueCapability.TINVEST_STOP_ENTRY_UNSUPPORTED
                    if venue == "TINVEST"
                    else PromotionVenueCapability.LIGHTER_TESTNET_ACTIONS_ONLY
                ),
            ),
            _fact(PromotionEvidenceKind.PERFORMANCE, scope=scope),
            _fact(PromotionEvidenceKind.OPERATIONS, scope=scope),
        ),
        now=datetime(2026, 8, 21, 10, 30, tzinfo=UTC),
    )

    assert expected_note in evidence.notes
    assert evidence.technical_sandbox_ready is False


def test_lighter_testnet_capability_blocks_canary_to_live_even_with_other_gates():
    from app.execution import promotion_guard
    from app.execution.promotion_evidence import (
        PromotionEvidenceKind,
        PromotionVenueCapability,
        evaluate_persisted_promotion_evidence,
    )

    scope = _scope()
    now = datetime(2026, 8, 21, 10, tzinfo=UTC)
    evidence = evaluate_persisted_promotion_evidence(
        scope=scope,
        target=ExecutionLifecycleMode.LIVE,
        facts=(
            _fact(
                PromotionEvidenceKind.TECHNICAL,
                scope=scope,
                now=now,
                capability=PromotionVenueCapability.LIGHTER_TESTNET_ACTIONS_ONLY,
            ),
            _fact(PromotionEvidenceKind.PERFORMANCE, scope=scope, now=now),
            _fact(PromotionEvidenceKind.OPERATIONS, scope=scope, now=now),
        ),
        now=now,
    )

    decision = promotion_guard.evaluate_promotion(
        current=ExecutionLifecycleMode.CANARY,
        target=ExecutionLifecycleMode.LIVE,
        evidence=replace(evidence, owner_confirmed=True),
    )

    assert evidence.adr_gates_passed is False
    assert "Lighter testnet actions are not LIVE/mainnet activation evidence" in evidence.notes
    assert decision.allowed is False
    assert decision.blockers == ("ADR gates not verified",)


def test_venue_capability_must_be_a_matching_persisted_technical_fact():
    from app.execution.promotion_evidence import (
        PromotionEvidenceKind,
        PromotionEvidenceScope,
        evaluate_persisted_promotion_evidence,
    )

    scope = PromotionEvidenceScope(
        strategy_family="TREND_PULLBACK",
        strategy_version="candidate_v1",
        venue="TINVEST",
        source_hash="a" * 64,
        config_hash="b" * 64,
        policy_hash="c" * 64,
    )
    evidence = evaluate_persisted_promotion_evidence(
        scope=scope,
        target=ExecutionLifecycleMode.SANDBOX,
        facts=(_fact(PromotionEvidenceKind.TECHNICAL, scope=scope),),
        now=datetime(2026, 8, 21, 10, 30, tzinfo=UTC),
    )

    assert "T-Invest stop-entry capability evidence missing" in evidence.notes
    assert evidence.technical_sandbox_ready is False


def test_future_observation_is_rejected_on_write_and_does_not_authorize_reader():
    from app.execution.promotion_evidence import (
        PromotionEvidenceInputError,
        PromotionEvidenceKind,
        PromotionEvidenceSnapshotInput,
        evaluate_persisted_promotion_evidence,
    )

    now = datetime.now(UTC)
    with pytest.raises(PromotionEvidenceInputError, match="future"):
        PromotionEvidenceSnapshotInput(
            kind=PromotionEvidenceKind.TECHNICAL,
            scope=_scope(),
            source="server-test",
            evidence_version=1,
            observed_at=now + timedelta(seconds=1),
            fresh_until=now + timedelta(minutes=1),
            sample_size=1,
            error_count=0,
            gate_passed=True,
            reconciliation_verified=True,
            protection_verified=True,
            kill_switch_verified=True,
        )

    reader_now = datetime(2026, 8, 21, 10, tzinfo=UTC)
    evidence = evaluate_persisted_promotion_evidence(
        scope=_scope(),
        target=ExecutionLifecycleMode.SANDBOX,
        facts=(
            _fact(
                PromotionEvidenceKind.TECHNICAL,
                now=reader_now + timedelta(seconds=1),
            ),
        ),
        now=reader_now,
    )
    assert "technical evidence observed in the future" in evidence.notes
    assert evidence.technical_sandbox_ready is False


def test_lighter_sandbox_requires_explicit_testnet_actions_only_capability():
    from app.execution.promotion_evidence import (
        PromotionEvidenceKind,
        PromotionVenueCapability,
        evaluate_persisted_promotion_evidence,
    )

    now = datetime(2026, 8, 21, 10, tzinfo=UTC)
    missing = evaluate_persisted_promotion_evidence(
        scope=_scope(),
        target=ExecutionLifecycleMode.SANDBOX,
        facts=(_fact(PromotionEvidenceKind.TECHNICAL, now=now),),
        now=now,
    )
    asserted = evaluate_persisted_promotion_evidence(
        scope=_scope(),
        target=ExecutionLifecycleMode.SANDBOX,
        facts=(
            _fact(
                PromotionEvidenceKind.TECHNICAL,
                now=now,
                capability=PromotionVenueCapability.LIGHTER_TESTNET_ACTIONS_ONLY,
            ),
        ),
        now=now,
    )

    assert "Lighter testnet-actions-only capability evidence missing" in missing.notes
    assert missing.technical_sandbox_ready is False
    assert asserted.technical_sandbox_ready is True
