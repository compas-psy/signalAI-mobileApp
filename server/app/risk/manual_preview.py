"""SAI-043 authoritative manual-risk preview.

This module is deliberately read-only: it calculates and signs the exact
owner-visible economics but does not persist an override or move money.
SAI-044 will consume the signed preview token and add the write path.
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
    """The server cannot produce an authoritative manual-risk preview."""


@dataclass(frozen=True, slots=True)
class ManualRiskPreview:
    idea_id: uuid.UUID
    risk_snapshot_id: uuid.UUID
    preset_id: str
    execution_mode: ExecutionLifecycleMode
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
    is_linear = str(asset) == AssetClass.CRYPTO.value
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


def _preview_signing_key() -> bytes:
    """Derive a domain-separated signing key from server-only secrets.

    A dedicated key wins when configured. Otherwise we derive a separate HMAC
    key from SIGNALAI_DEVICE_TOKEN, which is already mandatory for every
    business API request. The raw device token is never used as the MAC key.
    """

    dedicated = os.environ.get("SIGNALAI_RISK_PREVIEW_SIGNING_KEY", "").strip()
    if dedicated:
        source = dedicated.encode("utf-8")
    else:
        device = os.environ.get("SIGNALAI_DEVICE_TOKEN", "").strip()
        if not device:
            raise ManualRiskPreviewRejected(
                "server risk-preview signing secret is not configured"
            )
        source = device.encode("utf-8")
    return hashlib.sha256(b"signalai:risk-preview:v1\x00" + source).digest()


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(Decimal(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _sign_preview(payload: dict[str, object], *, expires_at: datetime) -> str:
    expiry = int(expires_at.timestamp())
    signed = dict(payload)
    signed["expires_at_unix"] = expiry
    canonical = json.dumps(
        signed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    signature = hmac.new(_preview_signing_key(), canonical, hashlib.sha256).hexdigest()
    return f"v1.{expiry}.{signature}"


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

    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
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
    sizing = size_position(
        budget=budget,
        entry=Decimal(idea.entry_reference),
        stop=Decimal(idea.stop),
        spec=_instrument_spec(db, instrument, now=instant),
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

    # SAI-045 owns venue-tier margin/leverage/liquidation derivation. SAI-043
    # exposes the required fields now but never invents them. An override cannot
    # become LIVE through this read-only endpoint, and the later apply path must
    # recalculate these fields before any money-moving intent is accepted.
    resulting_leverage: Decimal | None = None
    liquidation_distance_ratio: Decimal | None = None
    warnings.append("LEVERAGE_LIQUIDATION_DERIVATION_PENDING_SAI_045")

    effective_amount = sizing.risk_amount if sizing.tradable else Decimal(0)
    quantity = sizing.quantity if sizing.tradable else Decimal(0)
    notional = sizing.notional if sizing.tradable else Decimal(0)
    total_open_after = state.open_risk_pct + budget.percent
    cluster_after = state.cluster_risk_pct + budget.percent
    worst_case = effective_amount
    allowed = not blockers

    issued_at = instant.astimezone(UTC)
    expires_at = issued_at + timedelta(minutes=policy.ttl_minutes)
    payload = {
        "idea_id": str(idea.id),
        "risk_snapshot_id": str(snapshot.id),
        "preset_id": preset,
        "execution_mode": server_mode.value,
        "strategy_version": idea.strategy_version,
        "risk_policy_version": idea.risk_policy_version,
        "engine_config_hash": config.config_hash,
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
        "resulting_leverage": None,
        "liquidation_distance_ratio": None,
    }
    preview_hash = _sign_preview(payload, expires_at=expires_at) if allowed else ""

    return ManualRiskPreview(
        idea_id=idea.id,
        risk_snapshot_id=snapshot.id,
        preset_id=preset,
        execution_mode=server_mode,
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
    )


__all__ = [
    "ManualRiskPreview",
    "ManualRiskPreviewRejected",
    "preview_manual_risk",
]
