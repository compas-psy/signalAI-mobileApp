from decimal import Decimal

from app.config import EngineConfig
from app.pipeline import scan as scan_module


def test_scan_admission_thresholds_include_configured_watch_probability():
    cfg = EngineConfig(
        data={
            "ideas": {
                "active_probability_min": 0.54,
                "active_expected_r_min": 0.20,
                "min_rr_tp2": 2.0,
                "min_confidence": 0.50,
                "watch_probability_min": 0.47,
            }
        },
        config_hash="test",
        source="test",
    )

    factory = getattr(scan_module, "_admission_thresholds", None)
    assert factory is not None, "scan must build admission thresholds from config"

    thresholds = factory(cfg)
    assert thresholds.active_probability_min == Decimal("0.54")
    assert thresholds.active_expected_r_min == Decimal("0.2")
    assert thresholds.min_rr_tp2 == Decimal("2.0")
    assert thresholds.min_confidence == Decimal("0.5")
    assert thresholds.watch_probability_min == Decimal("0.47")
