from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import ConfigError, get_config
from app.risk.manual_override import (
    get_manual_risk_envelope,
    load_manual_risk_envelope,
)


def _config_text(
    *,
    enabled: str = "true",
    max_risk: str = "0.0075",
    max_leverage: str = "3.0",
    min_liq: str = "2.5",
    ttl: str = "5",
) -> str:
    return f"""enabled: {enabled}
presets:
  AUTO: {{multiplier: 1.00}}
  BOOST_1: {{multiplier: 1.25}}
  BOOST_2: {{multiplier: 1.50}}
max_risk_per_trade: {max_risk}
max_leverage: {max_leverage}
min_liquidation_distance_ratio: {min_liq}
ttl_minutes: {ttl}
"""


def test_default_manual_risk_envelope_is_named_bounded_and_short_lived():
    envelope = get_manual_risk_envelope()

    assert envelope.enabled is True
    assert envelope.presets == {
        "AUTO": Decimal("1.00"),
        "BOOST_1": Decimal("1.25"),
        "BOOST_2": Decimal("1.50"),
    }
    assert envelope.max_risk_per_trade == Decimal("0.0075")
    assert envelope.max_leverage == Decimal("3.0")
    assert envelope.min_liquidation_distance_ratio == Decimal("2.5")
    assert envelope.ttl_minutes == 5
    assert len(envelope.config_hash) == 64
    int(envelope.config_hash, 16)


@pytest.mark.parametrize(
    ("max_risk", "max_leverage", "min_liq", "message"),
    [
        ("0.02", "3.0", "2.5", "widens max_risk_per_trade"),
        ("0.0075", "9.0", "2.5", "widens max leverage"),
        ("0.0075", "3.0", "1.0", "weakens liquidation distance"),
    ],
)
def test_manual_risk_config_fails_closed_when_it_widens_engine_caps(
    tmp_path,
    max_risk,
    max_leverage,
    min_liq,
    message,
):
    path = tmp_path / "manual-risk.yaml"
    path.write_text(
        _config_text(
            max_risk=max_risk,
            max_leverage=max_leverage,
            min_liq=min_liq,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=message):
        load_manual_risk_envelope(path, engine_config=get_config())


@pytest.mark.parametrize(
    ("enabled", "ttl", "message"),
    [
        ('"false"', "5", "enabled must be boolean"),
        ("true", '"tomorrow"', "ttl_minutes must be integer"),
    ],
)
def test_manual_risk_config_rejects_ambiguous_scalar_types(
    tmp_path,
    enabled,
    ttl,
    message,
):
    path = tmp_path / "manual-risk.yaml"
    path.write_text(_config_text(enabled=enabled, ttl=ttl), encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_manual_risk_envelope(path, engine_config=get_config())


def test_manual_risk_policy_hash_changes_when_policy_changes(tmp_path):
    first = tmp_path / "manual-risk-a.yaml"
    second = tmp_path / "manual-risk-b.yaml"
    first.write_text(_config_text(ttl="5"), encoding="utf-8")
    second.write_text(_config_text(ttl="6"), encoding="utf-8")

    a = load_manual_risk_envelope(first, engine_config=get_config())
    b = load_manual_risk_envelope(second, engine_config=get_config())

    assert a.config_hash != b.config_hash
