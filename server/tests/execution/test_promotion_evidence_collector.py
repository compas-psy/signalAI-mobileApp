from __future__ import annotations

from datetime import UTC, datetime

from app.execution.enums import ExecutionLifecycleMode


def test_collector_appends_strict_server_derived_snapshot_set(session):
    from app.execution.promotion_evidence import (
        PromotionEvidenceScope,
        collect_promotion_evidence,
        current_persisted_promotion_evidence,
    )
    from app.models import PromotionEvidenceSnapshot

    scope = PromotionEvidenceScope(
        strategy_family="TREND_PULLBACK",
        strategy_version="candidate_v1",
        venue="LIGHTER",
        source_hash="a" * 64,
        config_hash="b" * 64,
        policy_hash="c" * 64,
    )
    report = collect_promotion_evidence(
        session,
        scope=scope,
        observed_at=datetime(2026, 8, 21, 10, tzinfo=UTC),
    )
    session.flush()

    assert report.snapshot_count == 3
    assert session.query(PromotionEvidenceSnapshot).count() == 3
    evidence = current_persisted_promotion_evidence(
        session,
        scope=scope,
        target=ExecutionLifecycleMode.SANDBOX,
        now=datetime(2026, 8, 21, 10, tzinfo=UTC),
    )
    assert evidence.technical_sandbox_ready is False
    assert "Lighter testnet-actions-only capability evidence missing" in evidence.notes


def test_collector_does_not_project_global_execution_facts_into_strategy_scope(session):
    from app.execution.promotion_evidence import (
        PromotionEvidenceScope,
        collect_promotion_evidence,
    )
    from app.models import ExecutionModeEvent, LighterReconciliationEvidence

    session.add_all(
        (
            ExecutionModeEvent(
                from_mode=ExecutionLifecycleMode.PAPER,
                to_mode=ExecutionLifecycleMode.SANDBOX,
                actor="global-worker",
                reason="unscoped mode event",
                detail_json={},
            ),
            LighterReconciliationEvidence(
                evidence_key="a" * 64,
                action_key="unscoped-lighter-action",
                outcome="ORDER_FOUND",
                account_index=1,
                api_key_index=1,
                reserved_nonce=1,
                provider_next_nonce=2,
                provider_order_id="provider-1",
                provider_order_status="OPEN",
                provider_tx_hash=None,
                provider_tx_status=None,
                observed_at=datetime(2026, 8, 21, 10, tzinfo=UTC),
            ),
        )
    )
    session.flush()
    scope = PromotionEvidenceScope(
        strategy_family="TREND_PULLBACK",
        strategy_version="candidate_v1",
        venue="LIGHTER",
        source_hash="a" * 64,
        config_hash="b" * 64,
        policy_hash="c" * 64,
    )

    report = collect_promotion_evidence(
        session,
        scope=scope,
        observed_at=datetime(2026, 8, 21, 10, tzinfo=UTC),
    )

    assert report.technical_samples == 0
    assert report.operations_samples == 0
