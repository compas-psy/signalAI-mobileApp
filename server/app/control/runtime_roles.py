"""Owner-facing strategy role semantics for Control.

The strategy registry is a governance/measurement system today.  Production
TradeIdea publication still comes from the legacy scanner suite, so a registry
CHAMPION must never be presented as the live generator until that wiring
actually exists.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import StrategyVersion
from ..strategy_identity import LEGACY_CONTROL_VERSION

LIVE_STRATEGY_FAMILIES = (
    "TREND_PULLBACK",
    "BREAKOUT_RETEST",
    "WYCKOFF_REVERSAL",
)


def registry_role_map(session: Session) -> dict[str, str]:
    """Return the latest persisted governance role by strategy version."""

    rows = session.execute(
        select(StrategyVersion).order_by(StrategyVersion.family, StrategyVersion.version)
    ).scalars().all()
    roles: dict[str, str] = {}
    for row in rows:
        if not row.events:
            continue
        roles[row.version] = row.events[-1].to_role
    return roles


def compose_runtime_roles(
    competition: Mapping[str, object] | None,
    *,
    registry_roles: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Describe what publishes ideas versus what is only being measured.

    Missing registry history does not promote a candidate by implication.  It
    remains a challenger/shadow measurement until an audited governance event
    says otherwise, and even a CHAMPION remains shadow-only while the current
    production scanner is explicitly decoupled from StrategyRegistry.
    """

    payload = competition or {}
    control_version = str(payload.get("control_version") or LEGACY_CONTROL_VERSION)
    candidates_raw = payload.get("candidates") or []
    candidate_versions: list[str] = []
    for item in candidates_raw if isinstance(candidates_raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        version = item.get("version")
        if version is None:
            continue
        text = str(version).strip()
        if text and text not in candidate_versions:
            candidate_versions.append(text)

    roles = {str(key): str(value) for key, value in (registry_roles or {}).items()}
    champion = next(
        (version for version in candidate_versions if roles.get(version) == "CHAMPION"),
        None,
    )
    challengers = [
        version
        for version in candidate_versions
        if version != champion and roles.get(version) != "RETIRED"
    ]
    shadow_only = [
        version for version in candidate_versions if roles.get(version) != "RETIRED"
    ]

    return {
        "live_generator": {
            "version": control_version,
            "publishes_trade_ideas": True,
            "strategy_families": list(LIVE_STRATEGY_FAMILIES),
        },
        "champion": champion,
        "challengers": challengers,
        "shadow_only": shadow_only,
        "governance_controls_runtime": False,
        "explanation": (
            "StrategyRegistry roles are governance/measurement state; current "
            "TradeIdea publication remains on the legacy production scanner."
        ),
    }


__all__ = [
    "LIVE_STRATEGY_FAMILIES",
    "compose_runtime_roles",
    "registry_role_map",
]
