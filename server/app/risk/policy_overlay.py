"""Connect persisted optimizer champion to future RiskPolicy snapshots.

Никакого второго sizing engine здесь нет. Overlay может менять только exit
geometry (shares/trailing/time stop). ``risk_multiplier`` всегда остаётся у
confidence profile и дополнительно запрещён в optimizer config validator.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..config import get_config

_BASE_PROFILE = None
_INSTALLED = False


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def _profile_with_champion(cfg, mode: str) -> dict[str, Any]:
    from . import engine_v2
    from .optimizer import champion_profile_override

    base = dict(_BASE_PROFILE(cfg, mode))
    ctx = engine_v2._CTX.get()  # internal by design: same single runtime context
    if ctx is None:
        return base
    overlay = champion_profile_override(ctx.session, mode, cfg=cfg)
    if not overlay:
        return base

    # Exit-only allowlist. Risk sizing cannot be promoted by this optimizer.
    allowed = {
        "tp_shares",
        "runner_enabled",
        "runner_activation_r",
        "atr_multiplier",
        "min_trail_r",
        "mfe_giveback",
        "tp1_stop_lock_r",
        "tp2_stop_lock_r",
        "time_stop_hours",
    }
    for key in allowed:
        if key in overlay:
            base[key] = overlay[key]
    return base


def _trade_profile_with_candidates(trade) -> dict[str, Any] | None:
    """Recover exact signed exit profile after scheduler restart."""
    shares = [_d(v) for v in (trade.tp_shares or [])]
    if len(shares) < 3:
        return None
    total = sum(shares, Decimal(0))
    if total <= 0:
        return None
    normalized = [v / total for v in shares]
    cfg = get_config()

    profiles: list[tuple[str, dict[str, Any]]] = []
    for mode in ("defensive", "balanced", "conviction"):
        profiles.append((mode, dict(cfg.get(f"risk.management.profiles.{mode}"))))
    for candidate in cfg.get("risk.management.optimizer.candidates"):
        cid = str(candidate.get("id", ""))
        for mode, profile in (candidate.get("profiles") or {}).items():
            enriched = dict(profile)
            enriched["candidate_id"] = cid
            profiles.append((str(mode), enriched))

    for mode, profile in profiles:
        expected = [_d(v) for v in profile.get("tp_shares", [])]
        if len(expected) != len(normalized):
            continue
        e_total = sum(expected, Decimal(0))
        if e_total <= 0:
            continue
        expected = [v / e_total for v in expected]
        if all(abs(a - b) <= Decimal("0.00000001") for a, b in zip(expected, normalized, strict=True)):
            profile["mode"] = mode
            # Optimizer candidates inherit risk-independent fields from the
            # base mode only if they were intentionally omitted.
            base = dict(cfg.get(f"risk.management.profiles.{mode}"))
            base.update(profile)
            return base
    return None


def install() -> None:
    global _BASE_PROFILE, _INSTALLED
    if _INSTALLED:
        return
    from . import dynamic_exit, engine_v2

    _BASE_PROFILE = engine_v2._profile
    engine_v2._profile = _profile_with_champion
    dynamic_exit._profile_for = _trade_profile_with_candidates
    _INSTALLED = True


__all__ = ["install"]
