from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.ensemble.meta_score_v1 import EnsembleMetaScoreResult
from app.models.enums import Direction
from app.portfolio.conflict_resolver_v1 import (
    AssetClass,
    ExistingPortfolioExposure,
    PairwiseCorrelationObservation,
    PortfolioCandidateInput,
    PortfolioConflictPolicy,
    PortfolioExposureSnapshot,
    ResolutionStatus,
    RiskBucket,
    resolve_portfolio_conflicts,
)
from app.regime.strategy_gate_v1 import RegimeGateDecision


AT = datetime(2026, 8, 20, 20, 55, tzinfo=UTC)


def _meta(
    *,
    key: str,
    decision: RegimeGateDecision = RegimeGateDecision.ALLOW,
    adjusted_edge: str = "20",
    weight: str = "0.40",
) -> EnsembleMetaScoreResult:
    edge = Decimal(adjusted_edge)
    evidence_weight = Decimal(weight)
    return EnsembleMetaScoreResult(
        policy_version="evidence_weighted_meta_v1",
        candidate_key=key,
        strategy_family="MOMENTUM",
        strategy_version="momentum_v2",
        admission_decision=decision,
        cost_edge_surplus_bps=Decimal("50"),
        oos_evidence_score=Decimal("0.70"),
        regime_score=Decimal("0.80"),
        calibration_score=Decimal("0.75"),
        recent_stability_score=Decimal("0.90"),
        sample_adequacy_score=Decimal("1"),
        evidence_weight=evidence_weight,
        evidence_adjusted_edge_bps=edge,
        paired_usable_sample_size=60,
        sample_adequate=True,
        reasons=("EVIDENCE_WEIGHTED",),
    )


def _candidate(
    key: str,
    *,
    underlying: str | None = None,
    direction: Direction = Direction.LONG,
    venue: str = "BYBIT",
    asset_class: AssetClass = AssetClass.CRYPTO,
    cluster: str | None = None,
    adjusted_edge: str = "20",
    decision: RegimeGateDecision = RegimeGateDecision.ALLOW,
) -> PortfolioCandidateInput:
    return PortfolioCandidateInput(
        candidate_key=key,
        meta_score=_meta(
            key=key,
            decision=decision,
            adjusted_edge=adjusted_edge,
        ),
        instrument_id=key.split(":", 1)[0],
        underlying_key=underlying or key.split(":", 1)[0],
        direction=direction,
        venue=venue,
        asset_class=asset_class,
        correlation_cluster=cluster,
    )


def _policy() -> PortfolioConflictPolicy:
    return PortfolioConflictPolicy(
        max_total_open_risk_pct=Decimal("0.020"),
        max_cluster_open_risk_pct=Decimal("0.010"),
        max_directional_open_risk_pct=Decimal("0.015"),
        max_venue_open_risk_pct=Decimal("0.018"),
        strong_risk_correlation_threshold=Decimal("0.80"),
    )


def _snapshot(
    *,
    total: str = "0.005",
    clusters: tuple[tuple[str, str], ...] = (),
    directions: tuple[tuple[str, str], ...] = (),
    venues: tuple[tuple[str, str], ...] = (),
    existing: tuple[ExistingPortfolioExposure, ...] = (),
    observed_at: datetime = AT,
    tradable_at: datetime = AT,
) -> PortfolioExposureSnapshot:
    return PortfolioExposureSnapshot(
        observed_at=observed_at,
        tradable_at=tradable_at,
        total_open_risk_pct=Decimal(total),
        cluster_open_risk=tuple(
            RiskBucket(key=key, open_risk_pct=Decimal(value))
            for key, value in clusters
        ),
        directional_open_risk=tuple(
            RiskBucket(key=key, open_risk_pct=Decimal(value))
            for key, value in directions
        ),
        venue_open_risk=tuple(
            RiskBucket(key=key, open_risk_pct=Decimal(value))
            for key, value in venues
        ),
        existing_exposures=existing,
    )


def _corr(
    left: str,
    right: str,
    value: str,
    *,
    observed_at: datetime = AT,
    tradable_at: datetime = AT,
) -> PairwiseCorrelationObservation:
    return PairwiseCorrelationObservation(
        left_candidate_key=left,
        right_candidate_key=right,
        price_correlation=Decimal(value),
        observed_at=observed_at,
        tradable_at=tradable_at,
        source="fixture:rolling-correlation",
    )


def test_independent_candidates_survive_without_turning_resolver_into_sizer() -> None:
    first = _candidate("BTCUSDT:LONG", underlying="BTC")
    second = _candidate("ETHUSDT:LONG", underlying="ETH")

    result = resolve_portfolio_conflicts(
        (first, second),
        snapshot=_snapshot(),
        policy=_policy(),
        correlations=(),
        evaluated_at=AT,
    )

    assert [item.candidate_key for item in result] == ["BTCUSDT:LONG", "ETHUSDT:LONG"]
    assert all(item.status is ResolutionStatus.SELECTED for item in result)
    for item in result:
        for forbidden in (
            "risk_amount",
            "quantity",
            "leverage",
            "order_intent",
            "stop",
            "targets",
        ):
            assert not hasattr(item, forbidden)


def test_total_open_risk_capacity_exhaustion_blocks_every_new_candidate() -> None:
    result = resolve_portfolio_conflicts(
        (_candidate("BTCUSDT:LONG", underlying="BTC"),),
        snapshot=_snapshot(total="0.020"),
        policy=_policy(),
        correlations=(),
        evaluated_at=AT,
    )

    assert result[0].status is ResolutionStatus.BLOCKED
    assert result[0].reasons == ("TOTAL_RISK_CAPACITY_EXHAUSTED",)


def test_cluster_capacity_blocks_only_candidate_in_exhausted_cluster() -> None:
    crowded = _candidate("BTCUSDT:LONG", underlying="BTC", cluster="CRYPTO_BETA")
    free = _candidate("GOLD:FORTS:LONG", underlying="GOLD", asset_class=AssetClass.FORTS)

    result = resolve_portfolio_conflicts(
        (crowded, free),
        snapshot=_snapshot(clusters=(("CRYPTO_BETA", "0.010"),)),
        policy=_policy(),
        correlations=(_corr(crowded.candidate_key, free.candidate_key, "0.10"),),
        evaluated_at=AT,
    )
    by_key = {item.candidate_key: item for item in result}

    assert by_key[crowded.candidate_key].status is ResolutionStatus.BLOCKED
    assert "CLUSTER_RISK_CAPACITY_EXHAUSTED" in by_key[crowded.candidate_key].reasons
    assert by_key[free.candidate_key].status is ResolutionStatus.SELECTED


def test_directional_and_venue_concentration_are_preapproval_gates() -> None:
    long_bybit = _candidate("BTCUSDT:LONG", underlying="BTC")
    short_moex = _candidate(
        "SiU6:SHORT",
        underlying="USD_RUB",
        direction=Direction.SHORT,
        venue="MOEX",
        asset_class=AssetClass.FORTS,
    )

    result = resolve_portfolio_conflicts(
        (long_bybit, short_moex),
        snapshot=_snapshot(
            directions=((Direction.LONG.value, "0.015"),),
            venues=(("MOEX", "0.018"),),
        ),
        policy=_policy(),
        correlations=(_corr(long_bybit.candidate_key, short_moex.candidate_key, "0.10"),),
        evaluated_at=AT,
    )
    by_key = {item.candidate_key: item for item in result}

    assert by_key[long_bybit.candidate_key].reasons == (
        "DIRECTIONAL_CONCENTRATION_CAPACITY_EXHAUSTED",
    )
    assert by_key[short_moex.candidate_key].reasons == (
        "VENUE_CONCENTRATION_CAPACITY_EXHAUSTED",
    )


def test_existing_same_underlying_exposure_blocks_new_duplicate_before_approval() -> None:
    candidate = _candidate("BTC-PERP:LONG", underlying="BTC")
    existing = ExistingPortfolioExposure(
        exposure_key="open-btc",
        underlying_key="BTC",
        direction=Direction.LONG,
        venue="BYBIT",
        asset_class=AssetClass.CRYPTO,
        correlation_cluster="CRYPTO_BETA",
    )

    result = resolve_portfolio_conflicts(
        (candidate,),
        snapshot=_snapshot(existing=(existing,)),
        policy=_policy(),
        correlations=(),
        evaluated_at=AT,
    )

    assert result[0].status is ResolutionStatus.BLOCKED
    assert result[0].reasons == ("SAME_UNDERLYING_ALREADY_OPEN",)
    assert result[0].conflicts_with == ("open-btc",)


def test_two_same_underlying_candidate_ideas_choose_best_not_multiply_risk() -> None:
    weaker = _candidate(
        "BTC-breakout:LONG",
        underlying="BTC",
        adjusted_edge="12",
    )
    stronger = _candidate(
        "BTC-momentum:LONG",
        underlying="BTC",
        adjusted_edge="25",
    )

    result = resolve_portfolio_conflicts(
        (weaker, stronger),
        snapshot=_snapshot(),
        policy=_policy(),
        correlations=(),
        evaluated_at=AT,
    )
    by_key = {item.candidate_key: item for item in result}

    assert by_key[stronger.candidate_key].status is ResolutionStatus.SELECTED
    assert by_key[weaker.candidate_key].status is ResolutionStatus.BLOCKED
    assert by_key[weaker.candidate_key].reasons == ("SAME_UNDERLYING_CANDIDATE_CONFLICT",)
    assert by_key[weaker.candidate_key].conflicts_with == (stronger.candidate_key,)


def test_allow_tier_beats_reduce_even_when_reduce_has_larger_adjusted_edge() -> None:
    allowed = _candidate(
        "BTC-allow:LONG",
        underlying="BTC",
        adjusted_edge="10",
        decision=RegimeGateDecision.ALLOW,
    )
    reduced = _candidate(
        "BTC-reduce:LONG",
        underlying="BTC",
        adjusted_edge="100",
        decision=RegimeGateDecision.REDUCE,
    )

    result = resolve_portfolio_conflicts(
        (reduced, allowed),
        snapshot=_snapshot(),
        policy=_policy(),
        correlations=(),
        evaluated_at=AT,
    )
    by_key = {item.candidate_key: item for item in result}

    assert by_key[allowed.candidate_key].status is ResolutionStatus.SELECTED
    assert by_key[reduced.candidate_key].status is ResolutionStatus.BLOCKED
    assert by_key[reduced.candidate_key].conflicts_with == (allowed.candidate_key,)


def test_strong_crypto_forts_risk_correlation_keeps_only_better_candidate() -> None:
    crypto = _candidate(
        "BTCUSDT:LONG",
        underlying="BTC",
        adjusted_edge="30",
        asset_class=AssetClass.CRYPTO,
    )
    forts = _candidate(
        "SiU6:LONG",
        underlying="USD_RUB",
        adjusted_edge="15",
        venue="MOEX",
        asset_class=AssetClass.FORTS,
    )

    result = resolve_portfolio_conflicts(
        (forts, crypto),
        snapshot=_snapshot(),
        policy=_policy(),
        correlations=(_corr(crypto.candidate_key, forts.candidate_key, "0.85"),),
        evaluated_at=AT,
    )
    by_key = {item.candidate_key: item for item in result}

    assert by_key[crypto.candidate_key].status is ResolutionStatus.SELECTED
    assert by_key[forts.candidate_key].status is ResolutionStatus.BLOCKED
    assert by_key[forts.candidate_key].reasons == ("STRONG_RISK_CORRELATION_CONFLICT",)
    assert by_key[forts.candidate_key].risk_correlation == Decimal("0.85")


def test_negative_price_correlation_plus_opposite_directions_can_be_same_risk() -> None:
    first = _candidate("A:LONG", underlying="A", direction=Direction.LONG, adjusted_edge="30")
    second = _candidate(
        "B:SHORT",
        underlying="B",
        direction=Direction.SHORT,
        adjusted_edge="20",
    )

    result = resolve_portfolio_conflicts(
        (first, second),
        snapshot=_snapshot(),
        policy=_policy(),
        correlations=(_corr(first.candidate_key, second.candidate_key, "-0.90"),),
        evaluated_at=AT,
    )
    by_key = {item.candidate_key: item for item in result}

    assert by_key[first.candidate_key].status is ResolutionStatus.SELECTED
    assert by_key[second.candidate_key].status is ResolutionStatus.BLOCKED
    assert by_key[second.candidate_key].risk_correlation == Decimal("0.90")


def test_cross_asset_correlation_below_threshold_allows_both() -> None:
    crypto = _candidate("BTC:LONG", underlying="BTC", asset_class=AssetClass.CRYPTO)
    forts = _candidate(
        "GOLD:LONG",
        underlying="GOLD",
        venue="MOEX",
        asset_class=AssetClass.FORTS,
    )

    result = resolve_portfolio_conflicts(
        (crypto, forts),
        snapshot=_snapshot(),
        policy=_policy(),
        correlations=(_corr(crypto.candidate_key, forts.candidate_key, "0.35"),),
        evaluated_at=AT,
    )

    assert all(item.status is ResolutionStatus.SELECTED for item in result)


def test_missing_crypto_forts_correlation_fails_closed_for_lower_ranked_candidate() -> None:
    crypto = _candidate(
        "BTC:LONG",
        underlying="BTC",
        adjusted_edge="30",
        asset_class=AssetClass.CRYPTO,
    )
    forts = _candidate(
        "SiU6:LONG",
        underlying="USD_RUB",
        adjusted_edge="20",
        venue="MOEX",
        asset_class=AssetClass.FORTS,
    )

    result = resolve_portfolio_conflicts(
        (forts, crypto),
        snapshot=_snapshot(),
        policy=_policy(),
        correlations=(),
        evaluated_at=AT,
    )
    by_key = {item.candidate_key: item for item in result}

    assert by_key[crypto.candidate_key].status is ResolutionStatus.SELECTED
    assert by_key[forts.candidate_key].status is ResolutionStatus.BLOCKED
    assert by_key[forts.candidate_key].reasons == ("CROSS_ASSET_CORRELATION_MISSING",)


def test_upstream_blocked_candidate_cannot_be_rescued_by_portfolio_layer() -> None:
    blocked = _candidate(
        "BTC:BLOCKED",
        underlying="BTC",
        decision=RegimeGateDecision.BLOCK,
        adjusted_edge="0",
    )

    result = resolve_portfolio_conflicts(
        (blocked,),
        snapshot=_snapshot(),
        policy=_policy(),
        correlations=(),
        evaluated_at=AT,
    )

    assert result[0].status is ResolutionStatus.BLOCKED
    assert result[0].reasons == ("UPSTREAM_ADMISSION_BLOCKED",)


def test_future_or_not_yet_tradable_portfolio_evidence_is_rejected() -> None:
    candidate = _candidate("BTC:LONG", underlying="BTC")

    with pytest.raises(ValueError, match="portfolio snapshot"):
        resolve_portfolio_conflicts(
            (candidate,),
            snapshot=_snapshot(observed_at=AT + timedelta(seconds=1), tradable_at=AT + timedelta(seconds=1)),
            policy=_policy(),
            correlations=(),
            evaluated_at=AT,
        )

    other = _candidate("ETH:LONG", underlying="ETH")
    with pytest.raises(ValueError, match="correlation"):
        resolve_portfolio_conflicts(
            (candidate, other),
            snapshot=_snapshot(),
            policy=_policy(),
            correlations=(
                _corr(
                    candidate.candidate_key,
                    other.candidate_key,
                    "0.40",
                    tradable_at=AT + timedelta(seconds=1),
                ),
            ),
            evaluated_at=AT,
        )


def test_duplicate_candidate_or_correlation_identity_is_rejected() -> None:
    candidate = _candidate("BTC:LONG", underlying="BTC")
    other = _candidate("ETH:LONG", underlying="ETH")

    with pytest.raises(ValueError, match="duplicate candidate"):
        resolve_portfolio_conflicts(
            (candidate, candidate),
            snapshot=_snapshot(),
            policy=_policy(),
            correlations=(),
            evaluated_at=AT,
        )

    correlation = _corr(candidate.candidate_key, other.candidate_key, "0.40")
    with pytest.raises(ValueError, match="duplicate correlation"):
        resolve_portfolio_conflicts(
            (candidate, other),
            snapshot=_snapshot(),
            policy=_policy(),
            correlations=(correlation, correlation),
            evaluated_at=AT,
        )
