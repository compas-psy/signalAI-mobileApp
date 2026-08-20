from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.enums import Direction
from app.strategies.relative_strength_v1 import (
    RelativeStrengthObservation,
    rank_relative_strength,
)


EVALUATED_AT = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)


def _obs(
    instrument_id: str,
    momentum: str,
    quality: str,
    *,
    direction: Direction = Direction.LONG,
    cluster: str | None = None,
    tradable_at: datetime = EVALUATED_AT,
) -> RelativeStrengthObservation:
    return RelativeStrengthObservation(
        instrument_id=instrument_id,
        direction=direction,
        normalized_momentum=Decimal(momentum),
        quality_score=Decimal(quality),
        correlation_cluster=cluster,
        observed_at=EVALUATED_AT,
        tradable_at=tradable_at,
        source="fixture-cross-section",
    )


def test_ranker_prefers_stronger_clean_candidate_and_exposes_parts() -> None:
    ranked = rank_relative_strength(
        [
            _obs("CRYPTO:BTCUSDT", "1.8", "0.95"),
            _obs("CRYPTO:ETHUSDT", "0.7", "0.90"),
            _obs("CRYPTO:DOGEUSDT", "-0.2", "0.80"),
        ],
        evaluated_at=EVALUATED_AT,
    )

    assert [item.instrument_id for item in ranked] == [
        "CRYPTO:BTCUSDT",
        "CRYPTO:ETHUSDT",
        "CRYPTO:DOGEUSDT",
    ]
    assert all(Decimal("0") <= item.ranking_boost <= Decimal("1") for item in ranked)
    assert ranked[0].momentum_percentile == Decimal("1")
    assert ranked[-1].momentum_percentile == Decimal("0")
    assert ranked[0].quality_score == Decimal("0.95")
    assert ranked[0].crowding_factor == Decimal("1")
    assert ranked[0].tradable_at == EVALUATED_AT
    assert "momentum" in ranked[0].explanation
    assert "quality" in ranked[0].explanation


def test_ranker_is_direction_aware_for_short_candidates() -> None:
    ranked = rank_relative_strength(
        [
            _obs("CRYPTO:STRONGDOWN", "-2.0", "0.90", direction=Direction.SHORT),
            _obs("CRYPTO:WEAKDOWN", "-0.4", "0.90", direction=Direction.SHORT),
        ],
        evaluated_at=EVALUATED_AT,
    )

    assert ranked[0].instrument_id == "CRYPTO:STRONGDOWN"
    assert ranked[0].direction is Direction.SHORT
    assert ranked[0].directional_momentum == Decimal("2.0")
    assert ranked[1].directional_momentum == Decimal("0.4")


def test_cluster_crowding_is_soft_penalty_not_hard_drop() -> None:
    ranked = rank_relative_strength(
        [
            _obs("MOEX:FUT:BRV6", "1.5", "0.9", cluster="oil"),
            _obs("MOEX:EQ:LKOH", "1.4", "0.9", cluster="oil"),
            _obs("MOEX:FUT:SIU6", "1.0", "0.9", cluster="fx"),
        ],
        evaluated_at=EVALUATED_AT,
    )
    by_id = {item.instrument_id: item for item in ranked}

    assert len(ranked) == 3
    assert by_id["MOEX:FUT:BRV6"].crowding_factor < Decimal("1")
    assert by_id["MOEX:EQ:LKOH"].crowding_factor < Decimal("1")
    assert by_id["MOEX:FUT:SIU6"].crowding_factor == Decimal("1")
    assert by_id["MOEX:FUT:BRV6"].ranking_boost > Decimal("0")
    assert by_id["MOEX:EQ:LKOH"].ranking_boost > Decimal("0")


def test_ranker_does_not_emit_trade_entry_contract() -> None:
    ranked = rank_relative_strength(
        [_obs("CRYPTO:BTCUSDT", "1.0", "0.9")],
        evaluated_at=EVALUATED_AT,
    )

    assert len(ranked) == 1
    item = ranked[0]
    assert not hasattr(item, "entry_hypothesis")
    assert not hasattr(item, "stop")
    assert not hasattr(item, "targets")
    assert not hasattr(item, "order_intent")


def test_future_not_yet_tradable_observation_is_excluded_no_lookahead() -> None:
    ranked = rank_relative_strength(
        [
            _obs("CRYPTO:BTCUSDT", "1.0", "0.9"),
            _obs(
                "CRYPTO:FUTURE",
                "100.0",
                "1.0",
                tradable_at=EVALUATED_AT + timedelta(minutes=1),
            ),
        ],
        evaluated_at=EVALUATED_AT,
    )

    assert [item.instrument_id for item in ranked] == ["CRYPTO:BTCUSDT"]
    assert ranked[0].momentum_percentile == Decimal("0.5")


def test_ranker_uses_midrank_for_ties_and_deterministic_instrument_id_order() -> None:
    ranked = rank_relative_strength(
        [
            _obs("CRYPTO:B", "1.0", "0.8"),
            _obs("CRYPTO:A", "1.0", "0.8"),
            _obs("CRYPTO:C", "0.0", "0.8"),
        ],
        evaluated_at=EVALUATED_AT,
    )

    tied = [item for item in ranked if item.instrument_id in {"CRYPTO:A", "CRYPTO:B"}]
    assert tied[0].momentum_percentile == tied[1].momentum_percentile == Decimal("0.75")
    assert [item.instrument_id for item in tied] == ["CRYPTO:A", "CRYPTO:B"]


def test_quality_score_must_be_normalized_probability_like_value() -> None:
    with pytest.raises(ValueError, match="quality_score"):
        _obs("CRYPTO:BTCUSDT", "1.0", "1.01")


def test_ranker_rejects_duplicate_instrument_direction_observations() -> None:
    duplicate = _obs("CRYPTO:BTCUSDT", "1.0", "0.9")
    with pytest.raises(ValueError, match="duplicate"):
        rank_relative_strength([duplicate, duplicate], evaluated_at=EVALUATED_AT)
