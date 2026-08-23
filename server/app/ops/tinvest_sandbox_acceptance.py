"""Owner-only CLI for provider-confirmed T-Invest Sandbox acceptance.

The command is intentionally narrow and secret-safe. It delegates all broker
I/O to the fixed-host sandbox smoke service and exits successfully only when
the provider confirms at least one executed lot. No token value or provider
error text is printed.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable

from ..db import session_scope
from ..execution.venues.tinvest import TInvestProviderError
from ..execution.venues.tinvest_sandbox_smoke import (
    TInvestSandboxSmokeResult,
    run_tinvest_sandbox_smoke,
)

SmokeRunner = Callable[[str], TInvestSandboxSmokeResult]


def _production_runner(diagnostic_key: str) -> TInvestSandboxSmokeResult:
    with session_scope() as session:
        return run_tinvest_sandbox_smoke(
            session,
            diagnostic_key=diagnostic_key,
        )


def _safe_payload(result: TInvestSandboxSmokeResult) -> dict[str, object]:
    accepted = bool(result.filled and result.executed_lots > 0)
    return {
        "accepted": accepted,
        "symbol": result.symbol,
        "account_suffix": result.account_suffix,
        "provider_order_id": result.provider_order_id,
        "execution_status": result.execution_status,
        "executed_lots": result.executed_lots,
    }


def run_acceptance(
    *,
    diagnostic_key: str,
    smoke_runner: SmokeRunner = _production_runner,
) -> int:
    """Run one idempotent sandbox smoke and print only sanitized evidence."""

    try:
        result = smoke_runner(diagnostic_key)
    except TInvestProviderError as exc:
        print(
            json.dumps(
                {"accepted": False, "error_code": exc.code},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    except Exception:
        # Do not serialize arbitrary exception text: it may contain transport
        # headers or other sensitive values from a future dependency.
        print(
            json.dumps(
                {"accepted": False, "error_code": "INTERNAL_ERROR"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 3

    payload = _safe_payload(result)
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if payload["accepted"] is True else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run provider-confirmed T-Invest Sandbox acceptance smoke."
    )
    parser.add_argument("--diagnostic-key", required=True)
    args = parser.parse_args()
    return run_acceptance(diagnostic_key=args.diagnostic_key)


if __name__ == "__main__":  # pragma: no cover - exercised by VPS workflow
    raise SystemExit(main())
