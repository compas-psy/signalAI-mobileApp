"""Immutable strategy provenance for champion/challenger measurement.

This module deliberately does not alter strategy mathematics or runtime
eligibility.  ``legacy_control_v1`` is a measurement identity only: the
existing strategy suite remains enabled and operational for scanning, signal
generation, notification, and the paper lifecycle exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..models.enums import Strategy


LEGACY_CONTROL_VERSION = "legacy_control_v1"
LEGACY_CONTROL_SOURCE_SHA = "74de570dcaf90900ece5c8e8c6c5f558ca4f49d7"
LEGACY_CONTROL_CONFIG_HASH = (
    "110d5b5d29560e762f2ee15528bd03ed6ae30b0e6a652b94a40b40eeabd51ada"
)
LEGACY_RISK_POLICY_VERSION = "legacy_risk_policy@74de570dcaf9"


class StrategyRole(StrEnum):
    CONTROL = "CONTROL"
    CANDIDATE = "CANDIDATE"
    CHAMPION = "CHAMPION"
    RETIRED = "RETIRED"


class TradingStage(StrEnum):
    BACKTEST = "BACKTEST"
    OOS = "OOS"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    SANDBOX = "SANDBOX"
    CANARY_LIVE = "CANARY_LIVE"
    LIVE = "LIVE"


@dataclass(frozen=True, slots=True)
class ControlManifest:
    """Pinned source/config identity; never a runtime enable/disable switch."""

    version: str
    role: StrategyRole
    source_sha: str
    config_hash: str
    risk_policy_version: str
    generated_stage: TradingStage
    families: tuple[str, ...]
    source_blobs: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class StrategyDescriptor:
    """Per-family provenance; role/stage are metadata, not execution gates."""

    family: str
    version: str
    role: StrategyRole
    source_sha: str
    config_hash: str
    risk_policy_version: str
    generated_stage: TradingStage


LEGACY_CONTROL_MANIFEST = ControlManifest(
    version=LEGACY_CONTROL_VERSION,
    role=StrategyRole.CONTROL,
    source_sha=LEGACY_CONTROL_SOURCE_SHA,
    config_hash=LEGACY_CONTROL_CONFIG_HASH,
    risk_policy_version=LEGACY_RISK_POLICY_VERSION,
    generated_stage=TradingStage.PAPER,
    families=tuple(strategy.value for strategy in Strategy),
    source_blobs=(
        (
            "server/app/strategies/base.py",
            "4496b94d7ae806ace1ec7bb298c795bd9a0045c7",
        ),
        (
            "server/app/strategies/breakout_retest.py",
            "f357ba5b351b63d7531592964fe9cd44fc120289",
        ),
        (
            "server/app/strategies/trend_pullback.py",
            "69be92ff5b79b3ff8b788bff631c9052fd890ba1",
        ),
        (
            "server/app/strategies/wyckoff_reversal.py",
            "c28c15ff2a9056a40996eaa80dac5fd1dbcb52ba",
        ),
    ),
)


_DESCRIPTORS: dict[str, StrategyDescriptor] = {
    family: StrategyDescriptor(
        family=family,
        version=LEGACY_CONTROL_MANIFEST.version,
        role=LEGACY_CONTROL_MANIFEST.role,
        source_sha=LEGACY_CONTROL_MANIFEST.source_sha,
        config_hash=LEGACY_CONTROL_MANIFEST.config_hash,
        risk_policy_version=LEGACY_CONTROL_MANIFEST.risk_policy_version,
        generated_stage=LEGACY_CONTROL_MANIFEST.generated_stage,
    )
    for family in LEGACY_CONTROL_MANIFEST.families
}


def manifest_for(strategy: Strategy | str) -> StrategyDescriptor:
    """Return exact control provenance for a current family; unknown fails closed."""

    family = strategy.value if isinstance(strategy, Strategy) else strategy
    try:
        return _DESCRIPTORS[family]
    except KeyError:
        raise KeyError(f"unknown strategy family: {family}") from None
