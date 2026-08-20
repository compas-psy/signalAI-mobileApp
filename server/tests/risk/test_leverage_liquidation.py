from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.enums import Direction
from app.risk.leverage import (
    LeverageLiquidationRejected,
    LeverageTier,
    LinearIsolatedMarginFacts,
    derive_leverage_liquidation,
)


NOW = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)


def _facts(
    *,
    available_margin: str = "333",
    exposure_before: str = "0",
    leverage_step: str = "0.1",
    expires_at: datetime | None = None,
    margin_mode: str = "ISOLATED",
    tiers: tuple[LeverageTier, ...] | None = None,
) -> LinearIsolatedMarginFacts:
    return LinearIsolatedMarginFacts(
        source="bybit-v5-risk-limit",
        source_ref="/v5/market/risk-limit",
        observed_at=NOW,
        expires_at=expires_at or NOW + timedelta(minutes=5),
        venue="CRYPTO",
        account="paper-default",
        symbol="BTCUSDT",
        margin_mode=margin_mode,
        available_margin=Decimal(available_margin),
        exposure_before=Decimal(exposure_before),
        leverage_step=Decimal(leverage_step),
        tiers=tiers
        or (
            LeverageTier(
                tier_id=1,
                risk_limit_value=Decimal("100000"),
                initial_margin_rate=Decimal("0.01"),
                maintenance_margin_rate=Decimal("0.005"),
                max_leverage=Decimal("100"),
                maintenance_margin_deduction=Decimal("0"),
            ),
        ),
    )


def test_sai_045_uses_minimum_required_leverage_rounded_up_to_venue_step():
    facts = _facts()

    proof = derive_leverage_liquidation(
        facts=facts,
        venue="CRYPTO",
        account="paper-default",
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entry=Decimal("100"),
        stop=Decimal("95"),
        quantity=Decimal("10"),
        contract_multiplier=Decimal("1"),
        hard_max_leverage=Decimal("10"),
        min_liquidation_distance_ratio=Decimal("2.5"),
        now=NOW,
    )

    assert proof.leverage == Decimal("3.1")
    assert proof.required_leverage > Decimal("3")
    assert proof.required_leverage < Decimal("3.1")
    assert proof.tier_id == 1
    expected_liq = (
        Decimal("1000") - (Decimal("1000") / Decimal("3.1"))
    ) / (Decimal("10") * (Decimal("1") - Decimal("0.005")))
    assert proof.liquidation_price == expected_liq
    assert proof.liquidation_distance_ratio == (
        abs(Decimal("100") - expected_liq) / Decimal("5")
    )
    assert proof.liquidation_distance_ratio > Decimal("2.5")
    assert proof.initial_margin == Decimal("1000") / Decimal("3.1")
    assert proof.maintenance_margin == Decimal("5")
    assert len(proof.margin_proof_hash) == 64


def test_sai_045_selects_tier_from_existing_plus_new_exposure_and_handles_short():
    facts = _facts(
        available_margin="200",
        exposure_before="500",
        leverage_step="0.5",
        tiers=(
            LeverageTier(
                tier_id=1,
                risk_limit_value=Decimal("1000"),
                initial_margin_rate=Decimal("0.2"),
                maintenance_margin_rate=Decimal("0.005"),
                max_leverage=Decimal("5"),
                maintenance_margin_deduction=Decimal("0"),
            ),
            LeverageTier(
                tier_id=2,
                risk_limit_value=Decimal("2000"),
                initial_margin_rate=Decimal("0.2"),
                maintenance_margin_rate=Decimal("0.01"),
                max_leverage=Decimal("5"),
                maintenance_margin_deduction=Decimal("5"),
            ),
        ),
    )

    proof = derive_leverage_liquidation(
        facts=facts,
        venue="CRYPTO",
        account="paper-default",
        symbol="BTCUSDT",
        direction=Direction.SHORT,
        entry=Decimal("100"),
        stop=Decimal("105"),
        quantity=Decimal("8"),
        contract_multiplier=Decimal("1"),
        hard_max_leverage=Decimal("5"),
        min_liquidation_distance_ratio=Decimal("2.5"),
        now=NOW,
    )

    assert proof.position_notional == Decimal("800")
    assert proof.total_exposure == Decimal("1300")
    assert proof.tier_id == 2
    assert proof.leverage == Decimal("4")
    expected_liq = (
        Decimal("800") + (Decimal("800") / Decimal("4")) + Decimal("5")
    ) / (Decimal("8") * (Decimal("1") + Decimal("0.01")))
    assert proof.liquidation_price == expected_liq
    assert proof.liquidation_price > Decimal("100")


def test_sai_045_rejects_required_leverage_above_policy_or_tier_cap():
    facts = _facts(available_margin="100")

    with pytest.raises(LeverageLiquidationRejected) as exc_info:
        derive_leverage_liquidation(
            facts=facts,
            venue="CRYPTO",
            account="paper-default",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            entry=Decimal("100"),
            stop=Decimal("95"),
            quantity=Decimal("10"),
            contract_multiplier=Decimal("1"),
            hard_max_leverage=Decimal("3"),
            min_liquidation_distance_ratio=Decimal("2.5"),
            now=NOW,
        )

    assert exc_info.value.code == "REQUIRED_LEVERAGE_EXCEEDS_CAP"


def test_sai_045_rejects_liquidation_too_close_to_stop():
    facts = _facts(available_margin="20", leverage_step="1")

    with pytest.raises(LeverageLiquidationRejected) as exc_info:
        derive_leverage_liquidation(
            facts=facts,
            venue="CRYPTO",
            account="paper-default",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            entry=Decimal("100"),
            stop=Decimal("99.3"),
            quantity=Decimal("10"),
            contract_multiplier=Decimal("1"),
            hard_max_leverage=Decimal("100"),
            min_liquidation_distance_ratio=Decimal("2.5"),
            now=NOW,
        )

    assert exc_info.value.code == "LIQUIDATION_BUFFER_TOO_SMALL"


@pytest.mark.parametrize(
    ("facts", "venue", "account", "symbol", "expected_code"),
    [
        (
            _facts(expires_at=NOW - timedelta(seconds=1)),
            "CRYPTO",
            "paper-default",
            "BTCUSDT",
            "MARGIN_FACTS_EXPIRED",
        ),
        (
            _facts(margin_mode="CROSS"),
            "CRYPTO",
            "paper-default",
            "BTCUSDT",
            "UNSUPPORTED_MARGIN_MODE",
        ),
        (
            _facts(),
            "CRYPTO",
            "other-account",
            "BTCUSDT",
            "MARGIN_SCOPE_MISMATCH",
        ),
        (
            _facts(),
            "CRYPTO",
            "paper-default",
            "ETHUSDT",
            "MARGIN_SCOPE_MISMATCH",
        ),
    ],
)
def test_sai_045_margin_facts_are_fresh_isolated_and_scope_bound(
    facts,
    venue,
    account,
    symbol,
    expected_code,
):
    with pytest.raises(LeverageLiquidationRejected) as exc_info:
        derive_leverage_liquidation(
            facts=facts,
            venue=venue,
            account=account,
            symbol=symbol,
            direction=Direction.LONG,
            entry=Decimal("100"),
            stop=Decimal("95"),
            quantity=Decimal("10"),
            contract_multiplier=Decimal("1"),
            hard_max_leverage=Decimal("10"),
            min_liquidation_distance_ratio=Decimal("2.5"),
            now=NOW,
        )

    assert exc_info.value.code == expected_code


def test_sai_045_rejects_position_that_has_no_declared_risk_tier():
    facts = _facts(
        exposure_before="900",
        tiers=(
            LeverageTier(
                tier_id=1,
                risk_limit_value=Decimal("1000"),
                initial_margin_rate=Decimal("0.1"),
                maintenance_margin_rate=Decimal("0.01"),
                max_leverage=Decimal("10"),
                maintenance_margin_deduction=Decimal("0"),
            ),
        ),
    )

    with pytest.raises(LeverageLiquidationRejected) as exc_info:
        derive_leverage_liquidation(
            facts=facts,
            venue="CRYPTO",
            account="paper-default",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            entry=Decimal("100"),
            stop=Decimal("95"),
            quantity=Decimal("10"),
            contract_multiplier=Decimal("1"),
            hard_max_leverage=Decimal("10"),
            min_liquidation_distance_ratio=Decimal("2.5"),
            now=NOW,
        )

    assert exc_info.value.code == "NO_RISK_TIER_FOR_EXPOSURE"
