"""SAI-043/044/045 authoritative manual-risk preview proof.

SAI-043 calculates and signs the exact owner-visible economics. SAI-044
recalculates the same server-owned state and verifies that the short-lived
signed proof still matches before any immutable override is persisted. SAI-045
adds a deterministic, server-owned isolated-margin leverage/liquidation proof
for risk-increasing crypto-perpetual previews.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ConfigError, EngineConfig, get_config
from ..execution.enums import ExecutionLifecycleMode
from ..execution.mode import get_execution_mode
from ..market.fx import rate_to_rub
from ..models.enums import AssetClass
from ..models.ideas import TradeIdea
from ..models.market import Instrument
from ..models.risk import RiskSnapshot
from .leverage import (
    LeverageLiquidationRejected,
    derive_leverage_liquidation,
    margin_facts_from_metadata,
)
from .manual_override import ManualRiskEnvelope, get_manual_risk_envelope
from .sizing import (
    InstrumentSpec,
    RiskBudget,
    RiskLimits,
    RiskState,
    compute_budget,
    size_position,
)


class ManualRiskPreviewRejected(ValueError):
    """The server cannot produce or verify an authoritative manual-risk preview."""


@dataclass(frozen=True, slots=True)
class ManualRiskPreview:
    idea_id: uuid.UUID
    risk_snapshot_id: uuid.UUID
    preset_id: str
    execution_mode: ExecutionLifecycleMode
    execution_venue: str
    execution_account: str
    allowed: bool
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]
    auto_risk_pct: Decimal
    auto_risk_amount: Decimal
    requested_risk_pct: Decimal
    requested_risk_amount: Decimal
    effective_risk_pct: Decimal
    effective_risk_amount: Decimal
    hard_cap_risk_pct: Decimal
    quantity: Decimal
    notional: Decimal
    resulting_leverage: Decimal | None
    liquidation_distance_ratio: Decimal | None
    total_open_risk_after: Decimal
    cluster_risk_after: Decimal
    worst_case_stop_loss: Decimal
    binding_constraint: str
    issued_at: datetime
    expires_at: datetime
    preview_hash: str
    proof_payload_json: str


def _engine_limits(cfg: EngineConfig) -> RiskLimits:
    return RiskLimits(
        base_risk_per_trade=cfg.decimal("risk.base_risk_per_trade"),
        max_risk_per_trade=cfg.decimal("risk.max_risk_per_trade"),
        max_total_open_risk=cfg.decimal("risk.max_total_open_risk"),
        max_cluster_risk=cfg.decimal("risk.max_cluster_risk"),
        daily_loss_limit=cfg.decimal("risk.daily_loss_limit"),
        weekly_loss_limit=cfg.decimal("risk.weekly_loss_limit"),
        monthly_loss_limit=cfg.decimal("risk.monthly_loss_limit"),
        min_liquidation_distance_ratio=cfg.decimal(
            "risk.min_liquidation_distance_ratio"
        ),
        max_leverage=cfg.decimal("risk.max_crypto_leverage"),
    )


def _bounded_limits(cfg: EngineConfig, envelope: ManualRiskEnvelope) -> RiskLimits:
    base = _engine_limits(cfg)
    return RiskLimits(
        base_risk_per_trade=base.base_risk_per_trade,
        max_risk_per_trade=min(base.max_risk_per_trade, envelope.max_risk_per_trade),
        max_total_open_risk=base.max_total_open_risk,
        max_cluster_risk=base.max_cluster_risk,
        daily_loss_limit=base.daily_loss_limit,
        weekly_loss_limit=base.weekly_loss_limit,
        monthly_loss_limit=base.monthly_loss_limit,
        min_liquidation_distance_ratio=max(
            base.min_liquidation_distance_ratio,
            envelope.min_liquidation_distance_ratio,
        ),
        max_leverage=min(base.max_leverage, envelope.max_leverage),
    )


def _latest_snapshot(db: Session) -> RiskSnapshot:
    rows = db.execute(
        select(RiskSnapshot).order_by(RiskSnapshot.taken_at.desc()).limit(2)
    ).scalars().all()
    if not rows:
        raise ManualRiskPreviewRejected("no server risk snapshot is available")
    if len(rows) > 1 and rows[0].taken_at == rows[1].taken_at:
        raise ManualRiskPreviewRejected(
            "latest server risk snapshot is ambiguous; refresh risk state"
        )
    return rows[0]


def _decimal_json(value: object, *, default: Decimal = Decimal(0)) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _strategy_multiplier(cfg: EngineConfig, idea: TradeIdea) -> Decimal:
    raw = getattr(idea.strategy, "value", idea.strategy)
    key = str(raw).strip().lower()
    return Decimal(str(cfg.get(f"strategies.{key}.risk_multiplier", 1)))


def _risk_state(snapshot: RiskSnapshot, idea: TradeIdea) -> RiskState:
    cluster = str(idea.correlation_cluster or "")
    cluster_risk = _decimal_json((snapshot.cluster_risk_json or {}).get(cluster, 0))
    return RiskState(
        risk_equity=Decimal(snapshot.risk_equity),
        day_pnl_pct=Decimal(snapshot.day_pnl_pct),
        week_pnl_pct=Decimal(snapshot.week_pnl_pct),
        month_pnl_pct=Decimal(snapshot.month_pnl_pct),
        open_risk_pct=Decimal(snapshot.open_risk),
        cluster_risk_pct=cluster_risk,
        current_drawdown=Decimal(snapshot.current_drawdown),
    )


def _instrument_spec(
    db: Session,
    instrument: Instrument,
    *,
    now: datetime,
) -> InstrumentSpec:
    quote_currency = str(instrument.currency or "RUB")
    quote_rate = rate_to_rub(db, quote_currency, now=now)
    asset = getattr(instrument.asset_class, "value", instrument.asset_class)
    is_linear = str(asset) in {
        AssetClass.CRYPTO_SPOT.value,
        AssetClass.CRYPTO_PERPETUAL.value,
    }
    return InstrumentSpec(
        tick_size=Decimal(instrument.tick_size),
        tick_value=Decimal(instrument.tick_value),
        quantity_step=Decimal(instrument.quantity_step),
        min_quantity=Decimal(instrument.min_quantity),
        min_notional=(
            Decimal(instrument.min_notional)
            if instrument.min_notional is not None
            else None
        ),
        contract_multiplier=Decimal(instrument.contract_multiplier),
        is_linear=is_linear,
        quote_currency=quote_currency,
        quote_to_account=quote_rate,
    )


def _execution_scope(
    instrument: Instrument,
    mode: ExecutionLifecycleMode,
) -> tuple[str, str]:
    """Return the server-owned logical execution scope for the preview.

    The instrument master owns the venue. Account is an internal alias, not a
    broker credential/account id; concrete provider credentials remain behind
    the venue adapter. Mode is already server-owned and part of the signed
    proof, so the alias cannot turn a PAPER preview into LIVE execution.
    """

    venue = str(getattr(instrument.venue, "value", instrument.venue)).strip()
    if not venue:
        raise ManualRiskPreviewRejected("idea instrument has no execution venue")
    return venue, f"{mode.value.lower()}-default"


def _preview_signing_key() -> bytes:
    """Derive a domain-separated signing key from server-only secrets.

    The key is dedicated: SIGNALAI_DEVICE_TOKEN is restricted to bootstrap
    pairing and must never become a secondary business-secret dependency.
    """

    dedicated = os.environ.get("SIGNALAI_RISK_PREVIEW_SIGNING_KEY", "").strip()
    if not dedicated:
        raise ManualRiskPreviewRejected("server risk-preview signing secret is not configured")
    source = dedicated.encode("utf-8")
    return hashlib.sha256(b"signalai:risk-preview:v1\x00" + source).digest()


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(Decimal(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _sign_preview(payload: dict[str, object], *, expires_at: datetime) -> str:
    expiry = int(expires_at.timestamp())
    signed = dict(payload)
    signed["expires_at_unix"] = expiry
    canonical = _canonical_json(signed).encode("utf-8")
    signature = hmac.new(_preview_signing_key(), canonical, hashlib.sha256).hexdigest()
    return f"v1.{expiry}.{signature}"


def _normalise_instant(now: datetime | None) -> datetime:
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(UTC)


def verify_manual_risk_preview_token(
    preview: ManualRiskPreview,
    token: str,
    *,
    now: datetime | None = None,
) -> datetime:
    """Verify a signed token against a freshly recalculated manual-risk preview.

    The token carries only version/expiry/HMAC. All economic material is
    recomputed server-side and reconstructed from ``proof_payload_json``. Any
    change to idea provenance, risk snapshot, mode, venue/account, config,
    sizing, caps or the SAI-045 margin-fact fingerprint therefore invalidates
    the old proof instead of trusting client-supplied values.
    """

    if not preview.allowed:
        raise ManualRiskPreviewRejected("current risk state no longer allows override")
    value = token.strip()
    parts = value.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        raise ManualRiskPreviewRejected("manual risk preview token is malformed")
    _, expiry_text, signature = parts
    try:
        expiry_unix = int(expiry_text)
    except ValueError as exc:
        raise ManualRiskPreviewRejected("manual risk preview token is malformed") from exc
    if len(signature) != 64:
        raise ManualRiskPreviewRejected("manual risk preview token is malformed")
    try:
        int(signature, 16)
    except ValueError as exc:
        raise ManualRiskPreviewRejected("manual risk preview token is malformed") from exc

    instant = _normalise_instant(now)
    if expiry_unix <= int(instant.timestamp()):
        raise ManualRiskPreviewRejected("manual risk preview token has expired")
    expires_at = datetime.fromtimestamp(expiry_unix, tz=UTC)
    try:
        payload = json.loads(preview.proof_payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ManualRiskPreviewRejected("manual risk preview proof is invalid") from exc
    if not isinstance(payload, dict):
        raise ManualRiskPreviewRejected("manual risk preview proof is invalid")

    expected = _sign_preview(payload, expires_at=expires_at)
    if not hmac.compare_digest(expected, value):
        raise ManualRiskPreviewRejected(
            "manual risk preview is stale or has an invalid signature; refresh"
        )
    return expires_at


def _effective_budget(
    *,
    ceiling: RiskBudget,
    requested_pct: Decimal,
    risk_equity: Decimal,
) -> RiskBudget:
    effective_pct = min(requested_pct, ceiling.percent)
    if requested_pct <= ceiling.percent:
        binding = "preset_requested"
        label = "Requested preset risk"
    else:
        binding = ceiling.binding
        label = ceiling.binding_label
    return RiskBudget(
        amount=risk_equity * effective_pct,
        percent=effective_pct,
        binding=binding,
        binding_label=label,
        drawdown_multiplier=ceiling.drawdown_multiplier,
        lines=ceiling.lines,
        halted=ceiling.halted,
    )


def preview_manual_risk(
    db: Session,
    *,
    idea_id: uuid.UUID,
    preset_id: str,
    current_mode: ExecutionLifecycleMode,
    now: datetime | None = None,
    cfg: EngineConfig | None = None,
    envelope: ManualRiskEnvelope | None = None,
) -> ManualRiskPreview:
    """Calculate the exact owner-visible B7.3 preview and sign it with TTL."""

    instant = _normalise_instant(now)
    config = cfg or get_config()
    policy = envelope or get_manual_risk_envelope()
    if not policy.enabled:
        raise ManualRiskPreviewRejected("manual risk override is disabled")

    server_mode = get_execution_mode(db).mode
    if current_mode != server_mode:
        raise ManualRiskPreviewRejected(
            f"execution mode changed from {current_mode.value} to {server_mode.value}; refresh"
        )

    preset = preset_id.strip().upper()
    try:
        multiplier = policy.multiplier(preset)
    except ConfigError as exc:
        raise ManualRiskPreviewRejected(str(exc)) from exc

    idea = db.get(TradeIdea, idea_id)
    if idea is None:
        raise ManualRiskPreviewRejected("idea does not exist")
    instrument = db.execute(
        select(Instrument).where(Instrument.instrument_id == idea.instrument_id)
    ).scalar_one_or_none()
    if instrument is None:
        raise ManualRiskPreviewRejected("idea instrument does not exist")
    execution_venue, execution_account = _execution_scope(instrument, server_mode)

    snapshot = _latest_snapshot(db)
    state = _risk_state(snapshot, idea)
    limits = _bounded_limits(config, policy)
    ceiling = compute_budget(
        score=Decimal("100"),
        state=state,
        limits=limits,
        strategy_multiplier=_strategy_multiplier(config, idea),
    )

    auto_pct = Decimal(idea.risk_pct)
    auto_amount = Decimal(idea.risk_amount)
    requested_pct = auto_pct * multiplier
    requested_amount = state.risk_equity * requested_pct
    budget = _effective_budget(
        ceiling=ceiling,
        requested_pct=requested_pct,
        risk_equity=state.risk_equity,
    )
    instrument_spec = _instrument_spec(db, instrument, now=instant)
    sizing = size_position(
        budget=budget,
        entry=Decimal(idea.entry_reference),
        stop=Decimal(idea.stop),
        spec=instrument_spec,
        limits=limits,
    )

    blockers: list[str] = []
    warnings: list[str] = list(sizing.warnings)
    if snapshot.entries_blocked or snapshot.halted:
        blockers.append("RISK_STATE_BLOCKS_ENTRIES")
    if ceiling.halted or ceiling.blocked:
        blockers.append(f"RISK_BUDGET_BLOCKED:{ceiling.binding}")
    if not sizing.tradable:
        blockers.append(f"SIZING_NOT_TRADABLE:{sizing.reason}")

    is_boost = multiplier > Decimal(1)
    if is_boost and (
        budget.percent <= auto_pct or sizing.quantity <= Decimal(idea.quantity)
    ):
        blockers.append("NO_ADDITIONAL_RISK_HEADROOM")

    resulting_leverage: Decimal | None = None
    liquidation_distance_ratio: Decimal | None = None
    margin_proof_hash: str | None = None
    asset = str(getattr(instrument.asset_class, "value", instrument.asset_class))
    if (
        is_boost
        and sizing.tradable
        and asset == AssetClass.CRYPTO_PERPETUAL.value
    ):
        try:
            margin_facts = margin_facts_from_metadata(instrument.metadata_json)
            margin_proof = derive_leverage_liquidation(
                facts=margin_facts,
                venue=execution_venue,
                account=execution_account,
                symbol=instrument.symbol,
                direction=idea.direction,
                entry=Decimal(idea.entry_reference),
                stop=Decimal(idea.stop),
                quantity=sizing.quantity,
                contract_multiplier=Decimal(instrument.contract_multiplier),
                hard_max_leverage=limits.max_leverage,
                min_liquidation_distance_ratio=limits.min_liquidation_distance_ratio,
                now=instant,
            )
            # Reuse the canonical sizing guards as a second independent safety
            # check around the provider-specific derivation. This must preserve
            # the exact already-derived quantity; any disagreement fails closed.
            verified_sizing = size_position(
                budget=budget,
                entry=Decimal(idea.entry_reference),
                stop=Decimal(idea.stop),
                spec=instrument_spec,
                leverage=margin_proof.leverage,
                liquidation_price=margin_proof.liquidation_price,
                limits=limits,
            )
            if not verified_sizing.tradable:
                raise LeverageLiquidationRejected(
                    "SIZING_GUARD_REJECTED",
                    verified_sizing.reason,
                )
            if verified_sizing.quantity != sizing.quantity:
                raise LeverageLiquidationRejected(
                    "SIZING_PROOF_MISMATCH",
                    "margin verification changed the authoritative quantity",
                )
            resulting_leverage = margin_proof.leverage
            liquidation_distance_ratio = margin_proof.liquidation_distance_ratio
            margin_proof_hash = margin_proof.margin_proof_hash
        except LeverageLiquidationRejected as exc:
            blockers.append(f"LEVERAGE_LIQUIDATION_BLOCKED:{exc.code}")

    effective_amount = sizing.risk_amount if sizing.tradable else Decimal(0)
    quantity = sizing.quantity if sizing.tradable else Decimal(0)
    notional = sizing.notional if sizing.tradable else Decimal(0)
    total_open_after = state.open_risk_pct + budget.percent
    cluster_after = state.cluster_risk_pct + budget.percent
    worst_case = effective_amount
    allowed = not blockers

    issued_at = instant
    expires_at = issued_at + timedelta(minutes=policy.ttl_minutes)
    payload = {
        "idea_id": str(idea.id),
        "risk_snapshot_id": str(snapshot.id),
        "preset_id": preset,
        "execution_mode": server_mode.value,
        "execution_venue": execution_venue,
        "execution_account": execution_account,
        "strategy_version": idea.strategy_version,
        "risk_policy_version": idea.risk_policy_version,
        "engine_config_hash": config.config_hash,
        "manual_risk_config_hash": policy.config_hash,
        "auto_risk_pct": _decimal_text(auto_pct),
        "requested_risk_pct": _decimal_text(requested_pct),
        "effective_risk_pct": _decimal_text(budget.percent),
        "effective_quantity": _decimal_text(quantity),
        "effective_risk_amount": _decimal_text(effective_amount),
        "notional": _decimal_text(notional),
        "binding_constraint": budget.binding,
        "total_open_risk_after": _decimal_text(total_open_after),
        "cluster_risk_after": _decimal_text(cluster_after),
        "worst_case_stop_loss": _decimal_text(worst_case),
        "resulting_leverage": _decimal_text(resulting_leverage),
        "liquidation_distance_ratio": _decimal_text(liquidation_distance_ratio),
        "margin_proof_hash": margin_proof_hash,
    }
    proof_payload_json = _canonical_json(payload)
    preview_hash = _sign_preview(payload, expires_at=expires_at) if allowed else ""

    return ManualRiskPreview(
        idea_id=idea.id,
        risk_snapshot_id=snapshot.id,
        preset_id=preset,
        execution_mode=server_mode,
        execution_venue=execution_venue,
        execution_account=execution_account,
        allowed=allowed,
        warnings=tuple(dict.fromkeys(warnings)),
        blockers=tuple(dict.fromkeys(blockers)),
        auto_risk_pct=auto_pct,
        auto_risk_amount=auto_amount,
        requested_risk_pct=requested_pct,
        requested_risk_amount=requested_amount,
        effective_risk_pct=budget.percent,
        effective_risk_amount=effective_amount,
        hard_cap_risk_pct=limits.max_risk_per_trade,
        quantity=quantity,
        notional=notional,
        resulting_leverage=resulting_leverage,
        liquidation_distance_ratio=liquidation_distance_ratio,
        total_open_risk_after=total_open_after,
        cluster_risk_after=cluster_after,
        worst_case_stop_loss=worst_case,
        binding_constraint=budget.binding,
        issued_at=issued_at,
        expires_at=expires_at,
        preview_hash=preview_hash,
        proof_payload_json=proof_payload_json,
    )


__all__ = [
    "ManualRiskPreview",
    "ManualRiskPreviewRejected",
    "preview_manual_risk",
    "verify_manual_risk_preview_token",
]
