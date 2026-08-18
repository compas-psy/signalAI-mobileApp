from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.detectors.smc import OrderBlock, StructureEvent, Sweep
from app.features.indicators import Swing
from app.models.enums import (
    Direction,
    LiquidityRegime,
    Strategy,
    TrendRegime,
    VolatilityRegime,
)
from app.strategies import breakout_retest, trend_pullback, wyckoff_reversal
from app.strategies.base import Candidate, Rejection, SetupContext
from tests import test_strategies as legacy


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "control" / "control_cases.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _short_pullback() -> SetupContext:
    ctx = legacy.pullback_context()
    smc = legacy.reading(
        "smc",
        "smc_context",
        {
            "swings": [
                Swing(index=5, confirmed_index=7, price=Decimal(120), is_high=True),
                Swing(index=15, confirmed_index=17, price=Decimal(100), is_high=False),
            ],
            "order_blocks": [
                OrderBlock(
                    index=18,
                    low=Decimal(108),
                    high=Decimal(112),
                    is_bullish=False,
                    body_atr=1.2,
                    volume_z=1.0,
                    reacted=True,
                )
            ],
            "fvg": [],
            "pools": [],
            "sweeps": [
                Sweep(
                    index=20,
                    swing_index=5,
                    level=Decimal(110),
                    extreme=Decimal(113),
                    penetration_atr=0.3,
                    return_bars=0,
                    is_high=True,
                )
            ],
            "events": [StructureEvent("BOS", "down", Decimal(105), 21, 1.5, 15)],
        },
    )
    return replace(
        ctx,
        regime=legacy.regime(trend=TrendRegime.DOWNTREND, score=-4, structure="down"),
        smc=smc,
        price_action=legacy.reading(
            "price_action", "entry_trigger", {"direction": "short"}
        ),
    )


def _scenario(variant: str) -> tuple[object, SetupContext]:
    if variant.startswith("pullback"):
        ctx = legacy.pullback_context()
        if variant == "pullback_crypto":
            return trend_pullback, replace(
                ctx, instrument_id="CRYPTO:BTCUSDT", tick_size=Decimal("0.1")
            )
        if variant == "pullback_short":
            return trend_pullback, _short_pullback()
        if variant == "pullback_no_trigger":
            assert ctx.smc is not None
            smc = legacy.reading(
                "smc",
                "smc_context",
                {**ctx.smc.payload, "sweeps": [], "events": []},
            )
            return trend_pullback, replace(
                ctx, smc=smc, price_action=None, volume_reading=None
            )
        if variant == "pullback_range":
            return trend_pullback, replace(
                ctx, regime=legacy.regime(trend=TrendRegime.RANGE)
            )
        if variant == "pullback_weak_trend":
            return trend_pullback, replace(ctx, regime=legacy.regime(score=2))
        if variant == "pullback_extreme_vol":
            return trend_pullback, replace(
                ctx,
                regime=legacy.regime(volatility=VolatilityRegime.EXTREME),
            )
        if variant == "pullback_untradeable":
            return trend_pullback, replace(
                ctx,
                regime=legacy.regime(liquidity=LiquidityRegime.UNTRADEABLE),
            )
        if variant == "pullback_no_impulse":
            assert ctx.smc is not None
            smc = legacy.reading(
                "smc", "smc_context", {**ctx.smc.payload, "swings": []}
            )
            return trend_pullback, replace(ctx, smc=smc)
        if variant == "pullback_deep":
            deep = [legacy.bar(i, "101", "103", "100", "102") for i in range(50)]
            return trend_pullback, replace(ctx, trigger_bars=deep)
        return trend_pullback, ctx

    if variant.startswith("breakout"):
        ctx = legacy.breakout_context()
        if variant == "breakout_crypto":
            return breakout_retest, replace(
                ctx, instrument_id="CRYPTO:ETHUSDT", tick_size=Decimal("0.1")
            )
        if variant == "breakout_no_range":
            return breakout_retest, replace(ctx, wyckoff=None)
        if variant == "breakout_weak_body":
            weak = list(ctx.setup_bars)
            weak[40] = legacy.bar(40, "110", "116", "109", "111", "500")
            return breakout_retest, replace(ctx, setup_bars=weak, context_bars=weak)
        if variant == "breakout_no_volume":
            return breakout_retest, replace(ctx, volume_reading=None)
        if variant == "breakout_false":
            false = list(ctx.setup_bars)
            false[41] = legacy.bar(41, "115", "116", "104", "105")
            return breakout_retest, replace(ctx, setup_bars=false, context_bars=false)
        return breakout_retest, ctx

    ctx = legacy.reversal_context()
    if variant == "reversal_crypto":
        return wyckoff_reversal, replace(
            ctx, instrument_id="CRYPTO:BTCUSDT", tick_size=Decimal("0.1")
        )
    if variant in {"reversal_no_climax", "reversal_no_spring", "reversal_no_lps"}:
        assert ctx.wyckoff is not None
        drop = {
            "reversal_no_climax": "climax",
            "reversal_no_spring": "spring",
            "reversal_no_lps": "lps",
        }[variant]
        events = [e for e in ctx.wyckoff.payload["events"] if e.kind != drop]
        wyckoff = legacy.reading(
            "wyckoff", "wyckoff_phase", {**ctx.wyckoff.payload, "events": events}
        )
        return wyckoff_reversal, replace(ctx, wyckoff=wyckoff)
    if variant == "reversal_early_choch":
        assert ctx.smc is not None
        smc = legacy.reading(
            "smc",
            "smc_context",
            {
                **ctx.smc.payload,
                "events": [StructureEvent("CHoCH", "up", Decimal(110), 35, 1.2, 30)],
            },
        )
        return wyckoff_reversal, replace(ctx, smc=smc)
    if variant == "reversal_no_prior_move":
        flat = [legacy.bar(i, "105", "107", "103", "106") for i in range(62)]
        return wyckoff_reversal, replace(
            ctx, context_bars=flat, setup_bars=flat, trigger_bars=flat
        )
    return wyckoff_reversal, ctx


def _expanded_bar_set(spec: dict, series_name: str) -> list[tuple]:
    start = legacy.START
    assert start.isoformat() == spec["start"]
    step = timedelta(hours=spec["step_hours"])
    result: list[tuple] = []
    for segment in spec["series"][series_name]:
        values = tuple(segment["ohlcv"])
        for index in range(segment["start_index"], segment["start_index"] + segment["count"]):
            result.append((start + step * index, *values))
    return result


def _actual_bars(ctx: SetupContext, series_name: str) -> list[tuple]:
    bars = {
        "context": ctx.context_bars,
        "setup": ctx.setup_bars,
        "trigger": ctx.trigger_bars,
    }[series_name]
    return [
        (
            bar.open_time,
            str(bar.open),
            str(bar.high),
            str(bar.low),
            str(bar.close),
            str(bar.volume_units) if bar.volume_units is not None else None,
        )
        for bar in bars
    ]


def _assert_expected(result: Candidate | Rejection, expected: dict) -> None:
    assert result.strategy.value == expected["strategy"]
    if expected["kind"] == "candidate":
        assert isinstance(result, Candidate)
        assert result.direction.value == expected["direction"]
        assert result.risk_per_unit > 0
        assert len(result.targets) == 3
        if "risk_multiplier" in expected:
            assert str(result.risk_multiplier) == expected["risk_multiplier"]
        if "trigger_passed" in expected:
            trigger = next(check for check in result.checks if check.name == "triggers")
            assert trigger.passed is expected["trigger_passed"]
        return

    assert isinstance(result, Rejection)
    assert expected["failed_check"] in {check.name for check in result.failed}


def test_control_fixture_catalog_is_large_and_representative():
    fixture = _load_fixture()
    cases = fixture["cases"]

    assert 20 <= len(cases) <= 30
    assert {case["expected"]["strategy"] for case in cases} == {s.value for s in Strategy}
    assert {case["instrument"].split(":", 1)[0] for case in cases} >= {"MOEX", "CRYPTO"}
    assert {case["expected"].get("direction") for case in cases} >= {
        Direction.LONG.value,
        Direction.SHORT.value,
    }
    assert any(case["expected"]["kind"] == "rejection" for case in cases)
    assert any("false_breakout" in case["name"] for case in cases)
    assert any("insufficient" in case["name"] for case in cases)


@pytest.mark.parametrize("case", _load_fixture()["cases"], ids=lambda case: case["name"])
def test_legacy_control_golden(case: dict):
    fixture = _load_fixture()
    module, ctx = _scenario(case["variant"])
    ctx = replace(
        ctx,
        instrument_id=case["instrument"],
        tick_size=Decimal(case["tick_size"]),
    )

    bar_set = fixture["bar_sets"][case["bar_set"]]
    for series_name in ("context", "setup", "trigger"):
        assert _actual_bars(ctx, series_name) == _expanded_bar_set(bar_set, series_name)

    result = module.build(ctx)
    _assert_expected(result, case["expected"])
