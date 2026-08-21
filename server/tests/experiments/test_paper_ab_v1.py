from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.experiments.paper_ab_v1 import (
    PaperAbArmObservation,
    PaperAbArmRole,
    PaperAbEvidenceStatus,
    build_rolling_paper_report,
)
from app.measurement.report import MeasurementDataset


AT = datetime(2026, 8, 21, 5, 0, tzinfo=UTC)
COST_A = "a" * 64
COST_B = "b" * 64
MARKET = "c" * 64
CONTROL = "legacy_control_v1"
CANDIDATE = "momentum_v2"


def _row(
    pair: str,
    *,
    role: PaperAbArmRole,
    version: str,
    at: datetime,
    net_r: Decimal | None,
    emitted: bool = True,
    status: PaperAbEvidenceStatus = PaperAbEvidenceStatus.EVALUATED,
    cost_hash: str = COST_A,
    market_hash: str = MARKET,
    reason: str | None = None,
    confidence: Decimal | None = None,
) -> PaperAbArmObservation:
    return PaperAbArmObservation(
        pair_key=pair,
        candidate_version=CANDIDATE,
        arm_role=role,
        strategy_version=version,
        instrument_id="CRYPTO:BTCUSDT",
        venue="CRYPTO",
        regime="TREND|NORMAL|GOOD",
        decision_at=at,
        market_snapshot_hash=market_hash,
        cost_model_hash=cost_hash,
        signal_emitted=emitted,
        net_r=net_r,
        confidence=confidence,
        evidence_status=status,
        reason_code=reason,
    )


def _pair(
    pair: str,
    *,
    at: datetime,
    control_r: str | None,
    candidate_r: str | None,
    cost_hash: str = COST_A,
) -> tuple[PaperAbArmObservation, PaperAbArmObservation]:
    control = _row(
        pair,
        role=PaperAbArmRole.CONTROL,
        version=CONTROL,
        at=at,
        net_r=None if control_r is None else Decimal(control_r),
        emitted=control_r is not None,
        cost_hash=cost_hash,
    )
    candidate = _row(
        pair,
        role=PaperAbArmRole.CANDIDATE,
        version=CANDIDATE,
        at=at,
        net_r=None if candidate_r is None else Decimal(candidate_r),
        emitted=candidate_r is not None,
        cost_hash=cost_hash,
    )
    return control, candidate


def test_rolling_report_reuses_canonical_paired_paper_evaluator() -> None:
    control: list[PaperAbArmObservation] = []
    candidate: list[PaperAbArmObservation] = []
    for pair, days, control_r, candidate_r in (
        ("p1", 3, "0.10", "0.30"),
        ("p2", 2, "-0.20", "0.10"),
    ):
        c, k = _pair(
            pair,
            at=AT - timedelta(days=days),
            control_r=control_r,
            candidate_r=candidate_r,
        )
        control.append(c)
        candidate.append(k)

    report = build_rolling_paper_report(
        control,
        candidate,
        control_version=CONTROL,
        candidate_version=CANDIDATE,
        as_of=AT,
        window=timedelta(days=30),
        min_sample=2,
    )

    assert report.policy_version == "paper_ab_rolling_v1"
    assert report.window_start == AT - timedelta(days=30)
    assert report.window_end == AT
    assert report.total_pairs == 2
    assert report.total_usable_pairs == 2
    assert len(report.segments) == 1
    evaluation = report.segments[0].evaluation
    assert evaluation is not None
    assert evaluation.dataset is MeasurementDataset.PAPER
    assert evaluation.control_version == CONTROL
    assert evaluation.candidate_version == CANDIDATE
    assert evaluation.sample_adequate is True
    assert evaluation.incremental_net_expectancy_r == pytest.approx(0.25)
    assert report.recommendation == "MEASURE_ONLY"


def test_no_signal_is_a_real_zero_return_decision_not_missing_evidence() -> None:
    control = _row(
        "p1",
        role=PaperAbArmRole.CONTROL,
        version=CONTROL,
        at=AT - timedelta(days=1),
        net_r=None,
        emitted=False,
    )
    candidate = _row(
        "p1",
        role=PaperAbArmRole.CANDIDATE,
        version=CANDIDATE,
        at=AT - timedelta(days=1),
        net_r=Decimal("0.40"),
        emitted=True,
    )

    report = build_rolling_paper_report(
        [control],
        [candidate],
        control_version=CONTROL,
        candidate_version=CANDIDATE,
        as_of=AT,
        window=timedelta(days=7),
        min_sample=1,
    )

    evaluation = report.segments[0].evaluation
    assert evaluation is not None
    assert evaluation.paired_usable_sample_size == 1
    assert evaluation.incremental_net_expectancy_r == pytest.approx(0.40)
    assert evaluation.candidate_only_wins == 1


def test_input_unavailable_stays_in_denominator_but_not_usable_sample() -> None:
    control, candidate = _pair(
        "p1",
        at=AT - timedelta(days=1),
        control_r="0.10",
        candidate_r="0.20",
    )
    candidate = _row(
        "p1",
        role=PaperAbArmRole.CANDIDATE,
        version=CANDIDATE,
        at=candidate.decision_at,
        net_r=None,
        emitted=True,
        status=PaperAbEvidenceStatus.INPUT_UNAVAILABLE,
        reason="EXIT_BAR_UNAVAILABLE",
    )

    report = build_rolling_paper_report(
        [control],
        [candidate],
        control_version=CONTROL,
        candidate_version=CANDIDATE,
        as_of=AT,
        window=timedelta(days=7),
        min_sample=1,
    )

    evaluation = report.segments[0].evaluation
    assert evaluation is not None
    assert evaluation.paired_sample_size == 1
    assert evaluation.paired_usable_sample_size == 0
    assert evaluation.sample_adequate is False
    assert report.total_pairs == 1
    assert report.total_usable_pairs == 0


def test_pending_outcome_is_not_usable_and_requires_no_fake_return() -> None:
    row = _row(
        "p1",
        role=PaperAbArmRole.CANDIDATE,
        version=CANDIDATE,
        at=AT - timedelta(hours=3),
        net_r=None,
        emitted=True,
        status=PaperAbEvidenceStatus.PENDING,
    )
    assert row.net_r is None
    assert row.label_usable is False


def test_rolling_window_excludes_old_pairs_before_evaluation() -> None:
    old_c, old_k = _pair(
        "old",
        at=AT - timedelta(days=40),
        control_r="5.0",
        candidate_r="-5.0",
    )
    new_c, new_k = _pair(
        "new",
        at=AT - timedelta(days=2),
        control_r="0.0",
        candidate_r="0.2",
    )

    report = build_rolling_paper_report(
        [old_c, new_c],
        [old_k, new_k],
        control_version=CONTROL,
        candidate_version=CANDIDATE,
        as_of=AT,
        window=timedelta(days=30),
        min_sample=1,
    )

    assert report.total_pairs == 1
    assert report.segments[0].evaluation is not None
    assert report.segments[0].evaluation.incremental_net_expectancy_r == pytest.approx(0.2)


def test_cost_model_changes_are_segmented_not_silently_mixed() -> None:
    a_c, a_k = _pair(
        "a",
        at=AT - timedelta(days=3),
        control_r="0.0",
        candidate_r="0.1",
        cost_hash=COST_A,
    )
    b_c, b_k = _pair(
        "b",
        at=AT - timedelta(days=2),
        control_r="0.0",
        candidate_r="0.2",
        cost_hash=COST_B,
    )

    report = build_rolling_paper_report(
        [a_c, b_c],
        [a_k, b_k],
        control_version=CONTROL,
        candidate_version=CANDIDATE,
        as_of=AT,
        window=timedelta(days=30),
        min_sample=1,
    )

    assert {segment.cost_model_hash for segment in report.segments} == {COST_A, COST_B}
    assert all(segment.evaluation is not None for segment in report.segments)
    assert report.total_pairs == 2


def test_pair_context_mismatch_fails_closed() -> None:
    control, candidate = _pair(
        "p1",
        at=AT - timedelta(days=1),
        control_r="0.0",
        candidate_r="0.1",
    )
    candidate = _row(
        "p1",
        role=PaperAbArmRole.CANDIDATE,
        version=CANDIDATE,
        at=candidate.decision_at,
        net_r=Decimal("0.1"),
        market_hash="d" * 64,
    )

    with pytest.raises(ValueError, match="market snapshot mismatch"):
        build_rolling_paper_report(
            [control],
            [candidate],
            control_version=CONTROL,
            candidate_version=CANDIDATE,
            as_of=AT,
            window=timedelta(days=7),
            min_sample=1,
        )


def test_future_rows_are_rejected_not_clipped_into_present() -> None:
    future = AT + timedelta(minutes=1)
    control, candidate = _pair(
        "future",
        at=future,
        control_r="0.0",
        candidate_r="0.1",
    )

    with pytest.raises(ValueError, match="future"):
        build_rolling_paper_report(
            [control],
            [candidate],
            control_version=CONTROL,
            candidate_version=CANDIDATE,
            as_of=AT,
            window=timedelta(days=7),
            min_sample=1,
        )


def test_arm_contract_rejects_fake_or_inconsistent_labels() -> None:
    with pytest.raises(ValueError, match="usable emitted signal requires net_r"):
        _row(
            "bad",
            role=PaperAbArmRole.CANDIDATE,
            version=CANDIDATE,
            at=AT,
            net_r=None,
            emitted=True,
            status=PaperAbEvidenceStatus.EVALUATED,
        )
    with pytest.raises(ValueError, match="reason_code"):
        _row(
            "bad2",
            role=PaperAbArmRole.CANDIDATE,
            version=CANDIDATE,
            at=AT,
            net_r=None,
            emitted=True,
            status=PaperAbEvidenceStatus.INPUT_UNAVAILABLE,
            reason=None,
        )


def test_paper_ab_contract_has_no_promotion_risk_or_execution_surface() -> None:
    row = _row(
        "p1",
        role=PaperAbArmRole.CANDIDATE,
        version=CANDIDATE,
        at=AT,
        net_r=Decimal("0.1"),
    )
    for forbidden in (
        "quantity",
        "risk_amount",
        "leverage",
        "order_intent",
        "execution_mode",
        "promote",
        "live",
    ):
        assert not hasattr(row, forbidden)
