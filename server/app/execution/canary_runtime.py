"""Server-owned runtime facts used by Canary readiness and owner activation.

The phone is never allowed to supply source/config/paper-only proof. Missing
release provenance is represented as an empty source SHA so the existing
preflight fails closed with DEPLOYED_SOURCE_SHA_UNKNOWN.
"""
from __future__ import annotations

import os

from ..config import get_config
from .canary_preflight import CanaryRuntimeContext


def current_canary_runtime_context() -> CanaryRuntimeContext:
    cfg = get_config()
    return CanaryRuntimeContext(
        source_sha=os.environ.get("SIGNALAI_SOURCE_SHA", "").strip().lower(),
        config_hash=cfg.config_hash,
        paper_only=bool(cfg.get("risk.paper_only")),
    )


__all__ = ["current_canary_runtime_context"]
