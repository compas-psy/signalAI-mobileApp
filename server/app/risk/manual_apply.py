"""SAI-044 apply path for a signed, freshly rechecked manual-risk preview.

This service is the only bridge from the read-only SAI-043 preview to the
immutable SAI-042 execution-risk override. It deliberately accepts no client
risk, quantity, leverage or notional. Those values are recalculated from
server state immediately before persistence.

Apply remains intentionally PAPER-only in this slice. SAI-045 now supplies the
deterministic leverage/liquidation proof for crypto risk increases, but widening
manual overrides into money-bearing SANDBOX/CANARY/LIVE modes remains a later
provider/promotion/acceptance concern and is not enabled here.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from ..execution.enums import ExecutionLifecycleMode
from ..execution.risk_override import (
    ExecutionRiskOverrideRejected,
    RiskOverrideAuthorization,
    RiskOverrideRequest,
    create_execution_risk_override,
)
from ..models.execution import ExecutionRiskOverride
from .manual_override import get_manual_risk_envelope
from .manual_preview import (
    ManualRiskPreviewRejected,
    preview_manual_risk,
    verify_manual_risk_preview_token,
)


class ManualRiskOverrideApplyRejected(ValueError):
    """A signed owner preview cannot be converted into a durable override."""


@dataclass(frozen=True, slots=True)
class ManualRiskOverrideApplication:
    override: ExecutionRiskOverride
    created: bool


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(Decimal(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _utc(now: datetime | None) -> datetime:
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(UTC)


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def apply_manual_risk_override(
    db: Session,
    *,
    idea_id: uuid.UUID,
    preset_id: str,
    current_mode: ExecutionLifecycleMode,
    preview_hash: str,
    owner_confirmed: bool,
    idempotency_key: str,
    reason: str,
    now: datetime | None = None,
) -> ManualRiskOverrideApplication:
    """Recheck the SAI-043 proof and persist exactly one bounded override."""

    if not owner_confirmed:
        raise ManualRiskOverrideApplyRejected("explicit owner confirmation is required")
    if not idempotency_key.strip():
        raise ManualRiskOverrideApplyRejected("idempotency key is required")
    if not reason.strip():
        raise ManualRiskOverrideApplyRejected("owner reason is required")
    if not preview_hash.strip():
        raise ManualRiskOverrideApplyRejected("signed preview hash is required")

    instant = _utc(now)
    try:
        fresh = preview_manual_risk(
            db,
            idea_id=idea_id,
            preset_id=preset_id,
            current_mode=current_mode,
            now=instant,
        )
    except ManualRiskPreviewRejected as exc:
        raise ManualRiskOverrideApplyRejected(str(exc)) from exc

    if not fresh.allowed:
        detail = ", ".join(fresh.blockers) or "current risk state blocks override"
        raise ManualRiskOverrideApplyRejected(detail)
    if fresh.execution_mode is not ExecutionLifecycleMode.PAPER:
        raise ManualRiskOverrideApplyRejected(
            "manual risk override apply is PAPER-only until later "
            "provider/promotion/acceptance slices enable money-bearing modes"
        )
    if fresh.preset_id == "AUTO":
        raise ManualRiskOverrideApplyRejected(
            "AUTO is not a risk-increasing override; choose a BOOST preset"
        )
    if fresh.effective_risk_pct <= fresh.auto_risk_pct:
        raise ManualRiskOverrideApplyRejected(
            "current limits leave no additional risk headroom"
        )

    try:
        preview_expires_at = verify_manual_risk_preview_token(
            fresh,
            preview_hash,
            now=instant,
        )
    except ManualRiskPreviewRejected as exc:
        raise ManualRiskOverrideApplyRejected(str(exc)) from exc

    token_digest = hashlib.sha256(preview_hash.strip().encode("utf-8")).hexdigest()
    policy = get_manual_risk_envelope()
    authorization = RiskOverrideAuthorization(
        allowed=True,
        actor="manual-risk-policy",
        reason="signed SAI-043 preview verified after fresh server recheck",
        hard_cap_risk_pct=fresh.hard_cap_risk_pct,
        hard_cap_leverage=policy.max_leverage,
        preview_proof_hash=token_digest,
        detail_json={
            "manual_preview_token_sha256": token_digest,
            "manual_preview_expires_at": _iso_z(preview_expires_at),
            "binding_constraint": fresh.binding_constraint,
            "effective_risk_amount": _decimal_text(fresh.effective_risk_amount),
            "notional": _decimal_text(fresh.notional),
            "total_open_risk_after": _decimal_text(fresh.total_open_risk_after),
            "cluster_risk_after": _decimal_text(fresh.cluster_risk_after),
            "worst_case_stop_loss": _decimal_text(fresh.worst_case_stop_loss),
            "warnings": list(fresh.warnings),
        },
    )
    request = RiskOverrideRequest(
        idea_id=fresh.idea_id,
        risk_snapshot_id=fresh.risk_snapshot_id,
        preset=fresh.preset_id,
        venue=fresh.execution_venue,
        account=fresh.execution_account,
        effective_risk_pct=fresh.effective_risk_pct,
        effective_quantity=fresh.quantity,
        effective_leverage=fresh.resulting_leverage,
        idempotency_key=idempotency_key.strip(),
        owner_confirmed=True,
        reason=reason.strip(),
    )
    try:
        creation = create_execution_risk_override(
            db,
            request=request,
            authorization=authorization,
        )
    except ExecutionRiskOverrideRejected as exc:
        raise ManualRiskOverrideApplyRejected(str(exc)) from exc

    return ManualRiskOverrideApplication(
        override=creation.override,
        created=creation.created,
    )


__all__ = [
    "ManualRiskOverrideApplication",
    "ManualRiskOverrideApplyRejected",
    "apply_manual_risk_override",
]
