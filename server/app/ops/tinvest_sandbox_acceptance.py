"""VPS-local operator command for provider-confirmed T-Invest Sandbox round trip."""

from __future__ import annotations

import json
import sys
from uuid import uuid4

from ..db import session_scope
from ..execution.venues.tinvest import TInvestProviderError
from ..execution.venues.tinvest_sandbox_readiness import (
    current_tinvest_sandbox_context,
    record_tinvest_sandbox_roundtrip_proof,
    scoped_sandbox_diagnostic_key,
)
from ..execution.venues.tinvest_sandbox_smoke import run_tinvest_sandbox_smoke


def _safe_failure(code: str, message: str) -> int:
    print(json.dumps({"ok": False, "code": code, "message": message}, ensure_ascii=False))
    return 2


def main() -> int:
    client_key = f"vps-roundtrip-{uuid4()}"
    try:
        with session_scope() as db:
            context = current_tinvest_sandbox_context(db)
            diagnostic_key = scoped_sandbox_diagnostic_key(client_key, context)
            result = run_tinvest_sandbox_smoke(db, diagnostic_key=diagnostic_key)
            if not result.round_trip_complete:
                return _safe_failure(
                    "ROUND_TRIP_INCOMPLETE",
                    "provider did not confirm BUY fill, SELL fill and flat position",
                )
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
    except TInvestProviderError as exc:
        return _safe_failure(exc.code, exc.message)
    except Exception:
        return _safe_failure("UNEXPECTED", "sandbox acceptance failed")

    print(
        json.dumps(
            {
                "ok": True,
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
                "readiness_proof_id": proof_id,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
