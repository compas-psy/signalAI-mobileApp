from __future__ import annotations

from app.control.bybit_funnel import FunnelFact, aggregate_funnel


def test_bybit_funnel_counts_reached_stages_and_terminal_reasons() -> None:
    facts = (
        FunnelFact("CRYPTO:PERP:BTCUSDT", "PUBLISHED", "ACTIVE"),
        FunnelFact("CRYPTO:PERP:ETHUSDT", "LIQUIDITY_BLOCKED", "LIQUIDITY_UNTRADEABLE"),
        FunnelFact("CRYPTO:PERP:SOLUSDT", "DATA_BLOCKED", "DATA_H1_INSUFFICIENT"),
        FunnelFact("CRYPTO:PERP:XRPUSDT", "SETUP_REJECTED", "NO_VALID_SETUP"),
        FunnelFact("CRYPTO:PERP:DOGEUSDT", "ADMISSION_REJECTED", "RR"),
        FunnelFact("CRYPTO:PERP:ADAUSDT", "REGIME_REJECTED", "EXTREME_VOLATILITY"),
    )

    funnel = aggregate_funnel(facts)

    assert funnel["universe"] == 6
    assert funnel["data_healthy"] == 5
    assert funnel["liquid"] == 4
    assert funnel["regime_eligible"] == 3
    assert funnel["strategy_evaluated"] == 3
    assert funnel["setup_reject"] == 1
    assert funnel["cost_rr_reject"] == 1
    assert funnel["published"] == 1
    assert funnel["terminal"] == {
        "ADMISSION_REJECTED": 1,
        "DATA_BLOCKED": 1,
        "LIQUIDITY_BLOCKED": 1,
        "PUBLISHED": 1,
        "REGIME_REJECTED": 1,
        "SETUP_REJECTED": 1,
    }
    assert funnel["top_reasons"][0]["count"] == 1
    assert {item["reason"] for item in funnel["top_reasons"]} >= {
        "DATA_H1_INSUFFICIENT",
        "LIQUIDITY_UNTRADEABLE",
        "NO_VALID_SETUP",
        "RR",
    }


def test_funnel_deduplicates_same_instrument_to_latest_fact() -> None:
    facts = (
        FunnelFact("CRYPTO:PERP:BTCUSDT", "DATA_BLOCKED", "DATA_H1_INSUFFICIENT", sequence=1),
        FunnelFact("CRYPTO:PERP:BTCUSDT", "PUBLISHED", "ACTIVE", sequence=2),
    )

    funnel = aggregate_funnel(facts)

    assert funnel["universe"] == 1
    assert funnel["published"] == 1
    assert funnel["terminal"] == {"PUBLISHED": 1}
