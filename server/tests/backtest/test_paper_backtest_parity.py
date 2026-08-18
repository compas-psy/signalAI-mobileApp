from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.backtest.parity import load_parity_fixture, run_parity_case


FIXTURE = Path(__file__).parent / "fixtures" / "paper_backtest_parity.json"


@pytest.mark.parametrize("case", load_parity_fixture(FIXTURE).cases, ids=lambda case: case.name)
def test_paper_and_golden_backtest_are_semantically_identical(case):
    result = run_parity_case(case)

    assert result.paper.execution_kinds == result.golden.execution_kinds
    assert result.paper.execution_kinds == case.expected_execution
    assert result.paper.entry_time == result.golden.entry_time
    assert result.paper.close_time == result.golden.close_time
    assert result.paper.targets_hit == result.golden.targets_hit
    assert result.paper.current_stop == result.golden.current_stop
    assert result.paper.gross_r == result.golden.gross_r
    assert result.paper.cost_r == result.golden.cost_r
    assert result.paper.net_r == result.golden.net_r
    assert result.paper.close_reason == result.golden.close_reason
    assert result.paper.close_reason == case.expected_close_reason


def test_parity_fixture_is_machine_readable_and_names_are_unique():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    names = [case["name"] for case in payload["cases"]]

    assert len(names) >= 4
    assert len(names) == len(set(names))
    assert payload["funding_interval_hours"] > 0
    assert set(payload["cost_model"]) == {
        "maker_fee_bps",
        "taker_fee_bps",
        "entry_slippage_bps",
        "exit_slippage_bps",
        "funding_bps_per_interval",
        "spread_bps",
    }
