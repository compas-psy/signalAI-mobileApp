from decimal import Decimal

import pytest


def _obs(
    *,
    opportunity: str,
    venue: str,
    snapshot: str = "a" * 64,
    status: str = "EVALUATED",
    cost: str | None = "10",
    ack_ms: str | None = "100",
    slippage: str | None = "2",
    protection_ms: str | None = "150",
    reconcile: str = "EXACT",
    duplicate: bool = False,
    unprotected: bool = False,
):
    from app.experiments.venue_shadow_scorecard_v1 import VenueShadowObservation

    def d(value: str | None):
        return None if value is None else Decimal(value)

    return VenueShadowObservation(
        opportunity_key=opportunity,
        venue=venue,
        market_snapshot_hash=snapshot,
        status=status,
        total_cost_bps=d(cost),
        ack_latency_ms=d(ack_ms),
        fill_slippage_bps=d(slippage),
        protection_latency_ms=d(protection_ms),
        reconciliation_outcome=reconcile,
        duplicate_execution_incident=duplicate,
        unprotected_execution_incident=unprotected,
    )


def _policy(**overrides):
    from app.experiments.venue_shadow_scorecard_v1 import VenueShadowScorecardPolicy

    values = dict(
        min_paired_opportunities=2,
        min_metric_pairs=2,
        max_lighter_cost_delta_bps=Decimal("2"),
        max_lighter_ack_latency_delta_ms=Decimal("50"),
        max_lighter_fill_slippage_delta_bps=Decimal("1"),
        max_lighter_protection_latency_delta_ms=Decimal("50"),
        max_lighter_ambiguity_rate_delta=Decimal("0.10"),
        max_lighter_unavailable_rate=Decimal("0.10"),
    )
    values.update(overrides)
    return VenueShadowScorecardPolicy(**values)


def test_scorecard_requires_exact_bybit_lighter_pair_on_same_market_snapshot() -> None:
    from app.experiments.venue_shadow_scorecard_v1 import evaluate_venue_shadow_scorecard

    rows = (
        _obs(opportunity="o1", venue="BYBIT"),
        _obs(opportunity="o1", venue="LIGHTER", snapshot="b" * 64),
    )
    with pytest.raises(ValueError, match="market snapshot"):
        evaluate_venue_shadow_scorecard(rows, policy=_policy(min_paired_opportunities=1, min_metric_pairs=1))

    with pytest.raises(ValueError, match="exactly one BYBIT and one LIGHTER"):
        evaluate_venue_shadow_scorecard(
            (_obs(opportunity="o1", venue="BYBIT"),),
            policy=_policy(min_paired_opportunities=1, min_metric_pairs=1),
        )


def test_transparent_component_deltas_pass_without_weighted_magic_score() -> None:
    from app.experiments.venue_shadow_scorecard_v1 import VenueShadowStatus, evaluate_venue_shadow_scorecard

    rows = (
        _obs(opportunity="o1", venue="BYBIT", cost="10", ack_ms="100", slippage="2", protection_ms="150"),
        _obs(opportunity="o1", venue="LIGHTER", cost="9", ack_ms="110", slippage="1.8", protection_ms="140"),
        _obs(opportunity="o2", venue="BYBIT", cost="11", ack_ms="120", slippage="2.5", protection_ms="160"),
        _obs(opportunity="o2", venue="LIGHTER", cost="10", ack_ms="125", slippage="2.2", protection_ms="155"),
    )
    result = evaluate_venue_shadow_scorecard(rows, policy=_policy())

    assert result.status is VenueShadowStatus.PASS_EVIDENCE
    assert result.paired_opportunities == 2
    assert result.lighter_minus_bybit.total_cost_bps == Decimal("-1")
    assert result.lighter_minus_bybit.ack_latency_ms == Decimal("7.5")
    assert result.lighter_minus_bybit.fill_slippage_bps == Decimal("-0.25")
    assert result.lighter_minus_bybit.protection_latency_ms == Decimal("-7.5")
    assert result.weighted_score is None
    assert result.eligible_for_testnet is True


def test_hard_safety_incident_fails_even_when_lighter_is_cheaper_and_faster() -> None:
    from app.experiments.venue_shadow_scorecard_v1 import VenueShadowStatus, evaluate_venue_shadow_scorecard

    rows = (
        _obs(opportunity="o1", venue="BYBIT", cost="20", ack_ms="500"),
        _obs(opportunity="o1", venue="LIGHTER", cost="1", ack_ms="10", duplicate=True),
        _obs(opportunity="o2", venue="BYBIT", cost="20", ack_ms="500"),
        _obs(opportunity="o2", venue="LIGHTER", cost="1", ack_ms="10"),
    )
    result = evaluate_venue_shadow_scorecard(rows, policy=_policy())

    assert result.status is VenueShadowStatus.FAIL_EVIDENCE
    assert "LIGHTER_DUPLICATE_EXECUTION_INCIDENT" in result.reasons
    assert result.eligible_for_testnet is False


def test_missing_metric_is_never_imputed_as_zero_and_causes_insufficient_evidence() -> None:
    from app.experiments.venue_shadow_scorecard_v1 import VenueShadowStatus, evaluate_venue_shadow_scorecard

    rows = (
        _obs(opportunity="o1", venue="BYBIT"),
        _obs(opportunity="o1", venue="LIGHTER", slippage=None),
        _obs(opportunity="o2", venue="BYBIT"),
        _obs(opportunity="o2", venue="LIGHTER", slippage=None),
    )
    result = evaluate_venue_shadow_scorecard(rows, policy=_policy())

    assert result.status is VenueShadowStatus.INSUFFICIENT_EVIDENCE
    assert result.lighter.fill_slippage_bps is None
    assert result.lighter_minus_bybit.fill_slippage_bps is None
    assert "FILL_SLIPPAGE_SAMPLE_INSUFFICIENT" in result.reasons
    assert result.eligible_for_testnet is False


def test_unavailable_observation_is_explicit_and_not_treated_as_zero_cost() -> None:
    from app.experiments.venue_shadow_scorecard_v1 import VenueShadowStatus, evaluate_venue_shadow_scorecard

    rows = (
        _obs(opportunity="o1", venue="BYBIT"),
        _obs(
            opportunity="o1",
            venue="LIGHTER",
            status="UNAVAILABLE",
            cost=None,
            ack_ms=None,
            slippage=None,
            protection_ms=None,
            reconcile="UNAVAILABLE",
        ),
        _obs(opportunity="o2", venue="BYBIT"),
        _obs(opportunity="o2", venue="LIGHTER"),
    )
    result = evaluate_venue_shadow_scorecard(
        rows,
        policy=_policy(max_lighter_unavailable_rate=Decimal("0.40")),
    )

    assert result.status is VenueShadowStatus.FAIL_EVIDENCE
    assert result.lighter.unavailable_rate == Decimal("0.5")
    assert result.lighter.total_cost_bps == Decimal("10")
    assert "LIGHTER_UNAVAILABLE_RATE_EXCEEDED" in result.reasons


def test_ambiguity_and_consumed_unknown_count_as_non_exact_reconciliation() -> None:
    from app.experiments.venue_shadow_scorecard_v1 import VenueShadowStatus, evaluate_venue_shadow_scorecard

    rows = (
        _obs(opportunity="o1", venue="BYBIT", reconcile="EXACT"),
        _obs(opportunity="o1", venue="LIGHTER", reconcile="AMBIGUOUS"),
        _obs(opportunity="o2", venue="BYBIT", reconcile="EXACT"),
        _obs(opportunity="o2", venue="LIGHTER", reconcile="CONSUMED_UNKNOWN"),
    )
    result = evaluate_venue_shadow_scorecard(rows, policy=_policy())

    assert result.status is VenueShadowStatus.FAIL_EVIDENCE
    assert result.bybit.ambiguity_rate == Decimal("0")
    assert result.lighter.ambiguity_rate == Decimal("1")
    assert result.lighter_minus_bybit.ambiguity_rate == Decimal("1")
    assert "LIGHTER_AMBIGUITY_DELTA_EXCEEDED" in result.reasons


def test_threshold_breach_fails_dimension_by_dimension_not_by_compensation() -> None:
    from app.experiments.venue_shadow_scorecard_v1 import VenueShadowStatus, evaluate_venue_shadow_scorecard

    rows = (
        _obs(opportunity="o1", venue="BYBIT", cost="20", ack_ms="100"),
        _obs(opportunity="o1", venue="LIGHTER", cost="1", ack_ms="1000"),
        _obs(opportunity="o2", venue="BYBIT", cost="20", ack_ms="100"),
        _obs(opportunity="o2", venue="LIGHTER", cost="1", ack_ms="1000"),
    )
    result = evaluate_venue_shadow_scorecard(rows, policy=_policy())

    assert result.status is VenueShadowStatus.FAIL_EVIDENCE
    assert result.lighter_minus_bybit.total_cost_bps < 0
    assert "LIGHTER_ACK_LATENCY_DELTA_EXCEEDED" in result.reasons
    assert result.eligible_for_testnet is False


def test_measurement_boundary_has_no_provider_writes_or_venue_switching() -> None:
    from app.experiments import venue_shadow_scorecard_v1

    source = open(venue_shadow_scorecard_v1.__file__, encoding="utf-8").read().lower()
    for forbidden in (
        "create_order(",
        "cancel_order(",
        "submit(",
        "send_tx",
        "activate_live",
        "set_execution_mode",
        "preferred_venue =",
        "selected_venue =",
        "httpx",
        "requests",
    ):
        assert forbidden not in source
