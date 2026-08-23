from __future__ import annotations

import json

from app.execution.venues.tinvest_sandbox_smoke import TInvestSandboxSmokeResult


def _result(*, filled: bool, executed_lots: int) -> TInvestSandboxSmokeResult:
    return TInvestSandboxSmokeResult(
        filled=filled,
        symbol="SBER",
        account_suffix="123456",
        provider_order_id="provider-order-1",
        execution_status="EXECUTION_REPORT_STATUS_FILL" if filled else "NEW",
        executed_lots=executed_lots,
    )


def test_acceptance_exit_zero_only_for_provider_confirmed_fill(capsys):
    from app.ops.tinvest_sandbox_acceptance import run_acceptance

    exit_code = run_acceptance(
        diagnostic_key="signalai-acceptance-123456",
        smoke_runner=lambda _key: _result(filled=True, executed_lots=1),
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "accepted": True,
        "symbol": "SBER",
        "account_suffix": "123456",
        "provider_order_id": "provider-order-1",
        "execution_status": "EXECUTION_REPORT_STATUS_FILL",
        "executed_lots": 1,
    }


def test_acceptance_rejects_zero_fill_even_if_provider_returned_order(capsys):
    from app.ops.tinvest_sandbox_acceptance import run_acceptance

    exit_code = run_acceptance(
        diagnostic_key="signalai-acceptance-123456",
        smoke_runner=lambda _key: _result(filled=False, executed_lots=0),
    )

    assert exit_code != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is False
    assert payload["executed_lots"] == 0


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
