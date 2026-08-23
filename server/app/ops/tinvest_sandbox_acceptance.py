"""VPS-local operator command for provider-confirmed T-Invest Sandbox round trip."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from ..db import session_scope
from ..execution.enums import ExecutionLifecycleMode
from ..execution.promotion_guard import (
    current_server_promotion_evidence,
    evaluate_promotion,
)
from ..execution.venues.tinvest import TInvestProviderError
from ..execution.venues.tinvest_sandbox_readiness import (
    current_tinvest_sandbox_context,
    record_tinvest_sandbox_roundtrip_proof,
    scoped_sandbox_diagnostic_key,
)
from ..execution.venues.tinvest_sandbox_smoke import (
    TInvestSandboxSmokeResult,
    run_tinvest_sandbox_smoke,
)

_VPS_ACCEPTANCE_KEY = "vps-sandbox-roundtrip-v1"


class _AcceptanceBlocked(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _safe_failure(code: str) -> int:
    # Exception text is deliberately not emitted: even though the transport
    # bounds its own errors, an unexpected upstream/library message must never
    # become a path for broker credentials or response bodies into CI.
    _emit({"accepted": False, "error_code": code})
    return 2


def _accepted_payload(result: TInvestSandboxSmokeResult) -> dict[str, object]:
    return {
        "accepted": True,
        "round_trip_complete": True,
        "symbol": result.symbol,
        "account_suffix": result.account_suffix,
        "buy_order_id": result.buy_provider_order_id,
        "buy_status": result.buy_execution_status,
        "buy_executed_lots": result.buy_executed_lots,
        "sell_order_id": result.sell_provider_order_id,
        "sell_status": result.sell_execution_status,
        "sell_executed_lots": result.sell_executed_lots,
        "position_flat": result.position_flat,
    }


def run_acceptance(
    *,
    diagnostic_key: str,
    smoke_runner: Callable[[str], TInvestSandboxSmokeResult],
) -> int:
    """Classify one injected provider run without exposing exception text.

    This small seam keeps the VPS acceptance contract unit-testable. Production
    ``main`` performs the same classification and additionally persists the
    release+credential-bound readiness proof in the production database.
    """

    try:
        result = smoke_runner(diagnostic_key)
    except TInvestProviderError as exc:
        return _safe_failure(exc.code)
    except Exception:
        return _safe_failure("UNEXPECTED")

    if not result.round_trip_complete:
        _emit(
            {
                "accepted": False,
                "error_code": "ROUND_TRIP_INCOMPLETE",
                "buy_executed_lots": result.buy_executed_lots,
                "sell_executed_lots": result.sell_executed_lots,
                "position_flat": result.position_flat,
            }
        )
        return 2

    _emit(_accepted_payload(result))
    return 0


def main() -> int:
    try:
        with session_scope() as db:
            context = current_tinvest_sandbox_context(db)
            diagnostic_key = scoped_sandbox_diagnostic_key(_VPS_ACCEPTANCE_KEY, context)
            result = run_tinvest_sandbox_smoke(db, diagnostic_key=diagnostic_key)
            if not result.round_trip_complete:
                raise _AcceptanceBlocked("ROUND_TRIP_INCOMPLETE")

            proof_id = record_tinvest_sandbox_roundtrip_proof(
                db,
                context=context,
                symbol=result.symbol,
                account_suffix=result.account_suffix,
                buy_order_id=result.buy_provider_order_id,
                buy_status=result.buy_execution_status,
                buy_executed_lots=result.buy_executed_lots,
                sell_order_id=result.sell_provider_order_id,
                sell_status=result.sell_execution_status,
                sell_executed_lots=result.sell_executed_lots,
                position_flat=result.position_flat,
            )

            # Verify the actual production promotion evidence path, without
            # changing execution mode. This proves that the exact persisted
            # release+credential proof is sufficient for PAPER -> SANDBOX and
            # that no mobile-supplied readiness boolean is involved.
            evidence = current_server_promotion_evidence(
                db,
                current=ExecutionLifecycleMode.PAPER,
                target=ExecutionLifecycleMode.SANDBOX,
            )
            decision = evaluate_promotion(
                current=ExecutionLifecycleMode.PAPER,
                target=ExecutionLifecycleMode.SANDBOX,
                evidence=evidence,
            )
            if not decision.allowed:
                raise _AcceptanceBlocked("PROMOTION_GUARD_BLOCKED")
    except _AcceptanceBlocked as exc:
        return _safe_failure(exc.code)
    except TInvestProviderError as exc:
        return _safe_failure(exc.code)
    except Exception:
        return _safe_failure("UNEXPECTED")

    payload = _accepted_payload(result)
    payload["readiness_proof_id"] = proof_id
    payload["promotion_ready"] = True
    _emit(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
