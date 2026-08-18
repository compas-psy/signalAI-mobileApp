"""Fail-open Ollama model shedding for Resource Autopilot.

This is deliberately the narrowest resource side effect in SignalAI. It reads
only the SAI-018 advisory disposition for ``OLLAMA_EXPLAINABILITY`` and may ask
the configured Ollama server to unload that model. Trading, risk, position
protection, exits, reconciliation, scanner and signal state are not imported or
modified here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from urllib.request import Request, urlopen

from .backpressure import BackpressurePlan, WorkloadDisposition, WorkloadKind


class OllamaShedStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNLOADED = "UNLOADED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class OllamaShedResult:
    status: OllamaShedStatus
    attempted: bool
    model: str | None = None
    detail: str = ""


OllamaTransport = Callable[[str, dict, float], None]


def _native_ollama_base(base: str) -> str:
    cleaned = base.strip().rstrip("/")
    if cleaned.endswith("/v1"):
        cleaned = cleaned[:-3].rstrip("/")
    return cleaned


def _default_transport(url: str, payload: dict, timeout: float) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - trusted configured URL
        response.read(1)


def shed_ollama_for_plan(
    plan: BackpressurePlan,
    *,
    transport: OllamaTransport = _default_transport,
    timeout: float = 2.0,
) -> OllamaShedResult:
    """Unload the configured explainability model only when the plan says SHED.

    All operational failures are converted to a structured result. This keeps
    Ollama availability outside the trading-availability dependency chain.
    """

    disposition = plan.workloads.get(WorkloadKind.OLLAMA_EXPLAINABILITY)
    if disposition is not WorkloadDisposition.SHED:
        return OllamaShedResult(
            status=OllamaShedStatus.NOT_REQUIRED,
            attempted=False,
            detail="ollama explainability workload is not marked SHED",
        )

    base = os.environ.get("SIGNALAI_LLM_BASE_URL", "").strip()
    model = os.environ.get("SIGNALAI_LLM_MODEL", "qwen3.5:4b").strip()
    native_base = _native_ollama_base(base)
    if not native_base or not model:
        return OllamaShedResult(
            status=OllamaShedStatus.NOT_CONFIGURED,
            attempted=False,
            model=model or None,
            detail="Ollama base URL or configured model is missing",
        )
    if timeout <= 0:
        return OllamaShedResult(
            status=OllamaShedStatus.FAILED,
            attempted=False,
            model=model,
            detail="ValueError: timeout must be positive",
        )

    url = f"{native_base}/api/generate"
    payload = {
        "model": model,
        "prompt": "",
        "stream": False,
        "keep_alive": 0,
    }
    try:
        transport(url, payload, timeout)
    except Exception as exc:  # fail-open: explainability must not affect trading
        return OllamaShedResult(
            status=OllamaShedStatus.FAILED,
            attempted=True,
            model=model,
            detail=f"{type(exc).__name__}: {exc}",
        )

    return OllamaShedResult(
        status=OllamaShedStatus.UNLOADED,
        attempted=True,
        model=model,
        detail="configured explainability model unload requested",
    )


__all__ = [
    "OllamaShedResult",
    "OllamaShedStatus",
    "shed_ollama_for_plan",
]
