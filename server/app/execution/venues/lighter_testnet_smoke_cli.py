"""Owner/operator CLI for the SAI-077 Lighter testnet smoke.

Input is the raw shadow-evidence artifact on stdin. Output is deliberately small
and sanitized so GitHub Actions logs can contain it safely.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from ...db import get_session_factory, session_scope
from .lighter_testnet_operator import (
    LighterTestnetOperatorError,
    LighterTestnetOperatorStatus,
    execute_lighter_testnet_smoke_artifact,
)

_SUCCESS = {
    LighterTestnetOperatorStatus.SUCCESS,
    LighterTestnetOperatorStatus.RECOVERY_SUCCESS,
}


def _print_result(*, status: str, reason_code: str | None, run_key: str | None = None,
                  create_tx_hash: str | None = None,
                  cancel_tx_hash: str | None = None) -> None:
    print(
        json.dumps(
            {
                "run_key": run_key,
                "status": status,
                "reason_code": reason_code,
                "create_tx_hash": create_tx_hash,
                "cancel_tx_hash": cancel_tx_hash,
                "eligible_for_live": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def main() -> int:
    raw = sys.stdin.buffer.read()
    factory = get_session_factory()
    try:
        with session_scope() as db:
            result = execute_lighter_testnet_smoke_artifact(
                db,
                raw_artifact=raw,
                session_factory=factory,
                now=datetime.now(UTC),
            )
    except LighterTestnetOperatorError:
        _print_result(status="ERROR", reason_code="ARTIFACT_INVALID")
        return 2
    except Exception:
        _print_result(status="ERROR", reason_code="OPERATOR_FAILED")
        return 3

    _print_result(
        run_key=result.run_key,
        status=result.status.value,
        reason_code=result.reason_code,
        create_tx_hash=result.create_tx_hash,
        cancel_tx_hash=result.cancel_tx_hash,
    )
    return 0 if result.status in _SUCCESS else 2


if __name__ == "__main__":
    raise SystemExit(main())
