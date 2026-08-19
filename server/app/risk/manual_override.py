"""Bounded manual-risk presets from B7.1 / SAI-042.

The owner override is deliberately a separate policy layer from strategy and
exit optimizers. This loader fails closed if its envelope is wider than the
engine's current production hard caps.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml

from ..config import ConfigError, EngineConfig, get_config

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "manual_risk_override.yaml"


@dataclass(frozen=True, slots=True)
class ManualRiskEnvelope:
    enabled: bool
    presets: dict[str, Decimal]
    max_risk_per_trade: Decimal
    max_leverage: Decimal
    min_liquidation_distance_ratio: Decimal
    ttl_minutes: int

    def multiplier(self, preset_id: str) -> Decimal:
        key = preset_id.strip().upper()
        try:
            return self.presets[key]
        except KeyError as exc:
            raise ConfigError(f"unknown manual risk preset: {preset_id!r}") from exc


def _decimal(raw: object, *, label: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except Exception as exc:  # yaml scalar -> Decimal conversion boundary
        raise ConfigError(f"{label} must be decimal") from exc
    if not value.is_finite():
        raise ConfigError(f"{label} must be finite")
    return value


def load_manual_risk_envelope(
    path: str | Path | None = None,
    *,
    engine_config: EngineConfig | None = None,
) -> ManualRiskEnvelope:
    source = Path(path) if path is not None else CONFIG_PATH
    if not source.is_file():
        raise ConfigError(f"manual risk config not found: {source}")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"manual risk config is invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("manual risk config must be a mapping")

    presets_raw = raw.get("presets")
    if not isinstance(presets_raw, dict):
        raise ConfigError("manual risk presets must be a mapping")
    expected = {"AUTO", "BOOST_1", "BOOST_2"}
    if set(presets_raw) != expected:
        raise ConfigError("manual risk presets must be exactly AUTO/BOOST_1/BOOST_2")

    presets: dict[str, Decimal] = {}
    for key, value in presets_raw.items():
        if not isinstance(value, dict):
            raise ConfigError(f"manual risk preset {key} must be a mapping")
        multiplier = _decimal(value.get("multiplier"), label=f"presets.{key}.multiplier")
        if multiplier < Decimal(1):
            raise ConfigError(f"presets.{key}.multiplier cannot reduce AUTO risk")
        presets[str(key)] = multiplier
    if presets["AUTO"] != Decimal(1):
        raise ConfigError("AUTO preset multiplier must be exactly 1")
    if not (presets["AUTO"] < presets["BOOST_1"] < presets["BOOST_2"]):
        raise ConfigError("BOOST preset multipliers must be strictly increasing")

    max_risk = _decimal(raw.get("max_risk_per_trade"), label="max_risk_per_trade")
    max_leverage = _decimal(raw.get("max_leverage"), label="max_leverage")
    min_liq = _decimal(
        raw.get("min_liquidation_distance_ratio"),
        label="min_liquidation_distance_ratio",
    )
    ttl_minutes = int(raw.get("ttl_minutes", 0))
    if max_risk <= 0 or max_leverage <= 0 or min_liq <= 0:
        raise ConfigError("manual risk hard limits must be positive")
    if ttl_minutes < 1 or ttl_minutes > 30:
        raise ConfigError("manual risk preview ttl_minutes must be in [1, 30]")

    cfg = engine_config or get_config()
    if max_risk > cfg.decimal("risk.max_risk_per_trade"):
        raise ConfigError("manual risk envelope widens max_risk_per_trade")
    if max_leverage > cfg.decimal("risk.max_crypto_leverage"):
        raise ConfigError("manual risk envelope widens max leverage")
    if min_liq < cfg.decimal("risk.min_liquidation_distance_ratio"):
        raise ConfigError("manual risk envelope weakens liquidation distance")

    return ManualRiskEnvelope(
        enabled=bool(raw.get("enabled", False)),
        presets=presets,
        max_risk_per_trade=max_risk,
        max_leverage=max_leverage,
        min_liquidation_distance_ratio=min_liq,
        ttl_minutes=ttl_minutes,
    )


@lru_cache(maxsize=1)
def get_manual_risk_envelope() -> ManualRiskEnvelope:
    return load_manual_risk_envelope()


__all__ = [
    "ManualRiskEnvelope",
    "get_manual_risk_envelope",
    "load_manual_risk_envelope",
]
