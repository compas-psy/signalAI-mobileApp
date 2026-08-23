from __future__ import annotations

import json

from app.execution.venues.tinvest_sandbox_smoke import TInvestSandboxSmokeResult


def _result(
    *,
    buy_lots: int,
    sell_lots: int,
    flat: bool,
    buy_status: str = "EXECUTION_REPORT_STATUS_FILL",
    sell_status: str = "EXECUTION_REPORT_STATUS_FILL",
) -> TInvestSandboxSmokeResult:
    complete = (
        buy_lots > 0
        and sell_lots == buy_lots
        and flat
        and buy_status == "EXECUTION_REPORT_STATUS_FILL"
        and sell_status == "EXECUTION_REPORT_STATUS_FILL"
    )
    return TInvestSandboxSmokeResult(
        round_trip_complete=complete,
        symbol="SBER",
        account_suffix="123456",
        buy_provider_order_id="provider-buy-1",
        buy_execution_status=buy_status,
        buy_executed_lots=buy_lots,
        sell_provider_order_id="provider-sell-1",
        sell_execution_status=sell_status,
        sell_executed_lots=sell_lots,
        position_flat=flat,
    )


def test_acceptance_exit_zero_only_for_provider_confirmed_round_trip(capsys):
    from app.ops.tinvest_sandbox_acceptance import run_acceptance

    exit_code = run_acceptance(
        diagnostic_key="signalai-acceptance-123456",
        smoke_runner=lambda _key: _result(buy_lots=1, sell_lots=1, flat=True),
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "accepted": True,
        "round_trip_complete": True,
        "symbol": "SBER",
        "account_suffix": "123456",
        "buy_order_id": "provider-buy-1",
        "buy_status": "EXECUTION_REPORT_STATUS_FILL",
        "buy_executed_lots": 1,
        "sell_order_id": "provider-sell-1",
        "sell_status": "EXECUTION_REPORT_STATUS_FILL",
        "sell_executed_lots": 1,
        "position_flat": True,
    }


def test_acceptance_rejects_incomplete_sell_or_residual_position(capsys):
    from app.ops.tinvest_sandbox_acceptance import run_acceptance

    exit_code = run_acceptance(
        diagnostic_key="signalai-acceptance-123456",
        smoke_runner=lambda _key: _result(buy_lots=1, sell_lots=0, flat=False),
    )

    assert exit_code != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "accepted": False,
        "error_code": "ROUND_TRIP_INCOMPLETE",
        "buy_executed_lots": 1,
        "sell_executed_lots": 0,
        "position_flat": False,
    }


def test_acceptance_sanitizes_provider_failure(capsys):
    from app.execution.venues.tinvest import TInvestProviderError
    from app.ops.tinvest_sandbox_acceptance import run_acceptance

    secret = "DO-NOT-PRINT-TOKEN"

    def fail(_key: str):
        raise TInvestProviderError(
            code="CREDENTIAL_MISSING",
            message=f"missing {secret}",
        )

    exit_code = run_acceptance(
        diagnostic_key="signalai-acceptance-123456",
        smoke_runner=fail,
    )

    assert exit_code != 0
    output = capsys.readouterr().out
    assert secret not in output
    assert json.loads(output) == {
        "accepted": False,
        "error_code": "CREDENTIAL_MISSING",
    }
