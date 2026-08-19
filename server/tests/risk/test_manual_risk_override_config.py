from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import ConfigError, get_config
from app.risk.manual_override import (
    get_manual_risk_envelope,
    load_manual_risk_envelope,
)


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
        f"""enabled: true
presets:
  AUTO: {{multiplier: 1.00}}
  BOOST_1: {{multiplier: 1.25}}
  BOOST_2: {{multiplier: 1.50}}
max_risk_per_trade: {max_risk}
max_leverage: {max_leverage}
min_liquidation_distance_ratio: {min_liq}
ttl_minutes: 5
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=message):
        load_manual_risk_envelope(path, engine_config=get_config())
