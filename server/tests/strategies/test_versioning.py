from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.models.enums import Strategy
from app.strategies.versioning import (
    LEGACY_CONTROL_MANIFEST,
    LEGACY_CONTROL_VERSION,
    StrategyRole,
    TradingStage,
    manifest_for,
)


CONTROL_SOURCE_SHA = "74de570dcaf90900ece5c8e8c6c5f558ca4f49d7"
CONTROL_CONFIG_HASH = "110d5b5d29560e762f2ee15528bd03ed6ae30b0e6a652b94a40b40eeabd51ada"
CONTROL_BLOBS = {
    "server/app/strategies/base.py": "4496b94d7ae806ace1ec7bb298c795bd9a0045c7",
    "server/app/strategies/breakout_retest.py": "f357ba5b351b63d7531592964fe9cd44fc120289",
    "server/app/strategies/trend_pullback.py": "69be92ff5b79b3ff8b788bff631c9052fd890ba1",
    "server/app/strategies/wyckoff_reversal.py": "c28c15ff2a9056a40996eaa80dac5fd1dbcb52ba",
}


def test_legacy_control_manifest_is_pinned_to_exact_source_snapshot():
    manifest = LEGACY_CONTROL_MANIFEST

    assert manifest.version == LEGACY_CONTROL_VERSION == "legacy_control_v1"
    assert manifest.role is StrategyRole.CONTROL
    assert manifest.source_sha == CONTROL_SOURCE_SHA
    assert manifest.config_hash == CONTROL_CONFIG_HASH
    assert dict(manifest.source_blobs) == CONTROL_BLOBS
    assert manifest.generated_stage is TradingStage.PAPER
    assert manifest.risk_policy_version


def test_legacy_control_manifest_covers_all_current_strategy_families():
    assert set(LEGACY_CONTROL_MANIFEST.families) == {
        Strategy.TREND_PULLBACK.value,
        Strategy.BREAKOUT_RETEST.value,
        Strategy.WYCKOFF_REVERSAL.value,
    }


@pytest.mark.parametrize("strategy", tuple(Strategy))
def test_manifest_for_current_strategy_returns_same_control_snapshot(strategy: Strategy):
    descriptor = manifest_for(strategy)

    assert descriptor.family == strategy.value
    assert descriptor.version == LEGACY_CONTROL_VERSION
    assert descriptor.role is StrategyRole.CONTROL
    assert descriptor.source_sha == CONTROL_SOURCE_SHA
    assert descriptor.config_hash == CONTROL_CONFIG_HASH


def test_control_manifest_is_frozen():
    with pytest.raises(FrozenInstanceError):
        LEGACY_CONTROL_MANIFEST.version = "mutable"  # type: ignore[misc]


def test_unknown_strategy_family_fails_closed():
    with pytest.raises(KeyError):
        manifest_for("NOT_A_STRATEGY")
