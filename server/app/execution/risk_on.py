"""Server-owned preview/confirmation for the manual ``Рискнуть`` action.

The phone selects only execution scope. All money-bearing numbers are rebuilt
from the latest durable RiskSnapshot, current config, instrument specification,
FX and the existing risk-sizing engine. Confirmation repeats the calculation;
a changed or ambiguous server state invalidates the displayed preview.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..market.fx import rate_to_rub
from ..models.enums import AssetClass
from ..models.ideas import TradeIdea
from ..models.market import Instrument
from ..models.risk import RiskSnapshot
from ..risk.sizing import InstrumentSpec, RiskLimits, RiskState, compute_budget, size_position
from .risk_override import (
    RiskOverrideAuthorization,
    RiskOverrideCreation,
    RiskOverrideRequest,
    _preview_hash,
    create_execution_risk_override,
)
from .mode import get_execution_mode


_RISK_ON_REASON = "owner tapped Рискнуть"
_AUTH_REASON = "server-owned bounded RISK_ON preview"


class RiskOnPreviewRejected(ValueError):
    """The server cannot produce an authoritative preview."""


class RiskOnConfirmationRejected(ValueError):
    """The owner confirmation is missing, stale or no longer safe."""


@dataclass(frozen=True)
class RiskOnPreview:
    idea_id: uuid.UUID
    risk_snapshot_id: uuid.UUID
    venue: str
    account: str
    allowed: bool
    blockers: tuple[str, ...]
    base_risk_pct: Decimal
    effective_risk_pct: Decimal
    hard_cap_risk_pct: Decimal
    base_quantity: Decimal
    effective_quantity: Decimal
    effective_risk_amount: Decimal
    effective_leverage: Decimal | None
    hard_cap_leverage: Decimal
    binding_limit: str
    preview_hash: str


def _limits(cfg: EngineConfig) -> RiskLimits:
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


def _latest_snapshot(db: Session) -> RiskSnapshot:
    rows = db.execute(
        select(RiskSnapshot)
        .order_by(RiskSnapshot.taken_at.desc())
        .limit(2)
    ).scalars().all()
    if not rows:
        raise RiskOnPreviewRejected("no server risk snapshot is available")
    if len(rows) > 1 and rows[0].taken_at == rows[1].taken_at:
        raise RiskOnPreviewRejected(
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


def _build(
    db: Session,
    *,
    idea_id: uuid.UUID,
    venue: str,
    account: str,
    now: datetime,
    cfg: EngineConfig,
) -> tuple[RiskOnPreview, RiskOverrideRequest | None, RiskOverrideAuthorization | None]:
    venue = venue.strip()
    account = account.strip()
    if not venue or not account:
        raise RiskOnPreviewRejected("venue and account are required")

    idea = db.get(TradeIdea, idea_id)
    if idea is None:
        raise RiskOnPreviewRejected("idea does not exist")
    instrument = db.execute(
        select(Instrument).where(Instrument.instrument_id == idea.instrument_id)
    ).scalar_one_or_none()
    if instrument is None:
        raise RiskOnPreviewRejected("idea instrument does not exist")
    snapshot = _latest_snapshot(db)
    limits = _limits(cfg)
    strategy_multiplier = _strategy_multiplier(cfg, idea)

    budget = compute_budget(
        score=Decimal("100"),
        state=_risk_state(snapshot, idea),
        limits=limits,
        strategy_multiplier=strategy_multiplier,
    )
    sizing = size_position(
        budget=budget,
        entry=Decimal(idea.entry_reference),
        stop=Decimal(idea.stop),
        spec=_instrument_spec(db, instrument, now=now),
        limits=limits,
    )

    base_risk = Decimal(idea.risk_pct)
    base_quantity = Decimal(idea.quantity)
    blockers: list[str] = []
    if snapshot.entries_blocked or snapshot.halted:
        blockers.append("risk snapshot blocks new entries")
    if budget.halted or budget.blocked:
        blockers.append(f"risk budget is blocked by {budget.binding}")
    if not sizing.tradable:
        blockers.append(sizing.reason or "authoritative sizing is not tradable")
    if budget.percent <= base_risk or sizing.quantity <= base_quantity:
        blockers.append("no additional risk headroom")

    allowed = not blockers
    effective_quantity = sizing.quantity if allowed else Decimal(0)
    effective_risk_amount = sizing.risk_amount if allowed else Decimal(0)
    authorization: RiskOverrideAuthorization | None = None
    request: RiskOverrideRequest | None = None
    preview_hash = ""

    if allowed:
        authorization = RiskOverrideAuthorization(
            allowed=True,
            actor="risk-on-preview-v1",
            reason=_AUTH_REASON,
            hard_cap_risk_pct=limits.max_risk_per_trade,
            hard_cap_leverage=limits.max_leverage,
            detail_json={
                "config_hash": cfg.config_hash,
                "risk_snapshot_id": str(snapshot.id),
                "binding_limit": budget.binding,
                "strategy_multiplier": str(strategy_multiplier),
                "effective_risk_amount": str(sizing.risk_amount),
            },
        )
        request = RiskOverrideRequest(
            idea_id=idea.id,
            risk_snapshot_id=snapshot.id,
            preset="RISK_ON",
            venue=venue,
            account=account,
            effective_risk_pct=budget.percent,
            effective_quantity=sizing.quantity,
            # 3x is displayed as an owner ceiling only. No leverage increase
            # happens without venue-specific margin/liquidation evidence.
            effective_leverage=None,
            idempotency_key="preview-only",
            owner_confirmed=True,
            reason=_RISK_ON_REASON,
        )
        preview_hash = _preview_hash(
            request=request,
            idea=idea,
            mode=get_execution_mode(db).mode,
            authorization=authorization,
        )

    preview = RiskOnPreview(
        idea_id=idea.id,
        risk_snapshot_id=snapshot.id,
        venue=venue,
        account=account,
        allowed=allowed,
        blockers=tuple(dict.fromkeys(blockers)),
        base_risk_pct=base_risk,
        effective_risk_pct=budget.percent,
        hard_cap_risk_pct=limits.max_risk_per_trade,
        base_quantity=base_quantity,
        effective_quantity=effective_quantity,
        effective_risk_amount=effective_risk_amount,
        effective_leverage=None,
        hard_cap_leverage=limits.max_leverage,
        binding_limit=budget.binding,
        preview_hash=preview_hash,
    )
    return preview, request, authorization


def preview_risk_on(
    db: Session,
    *,
    idea_id: uuid.UUID,
    venue: str,
    account: str,
    now: datetime | None = None,
    cfg: EngineConfig | None = None,
) -> RiskOnPreview:
    preview, _, _ = _build(
        db,
        idea_id=idea_id,
        venue=venue,
        account=account,
        now=now or datetime.now(UTC),
        cfg=cfg or get_config(),
    )
    return preview


def confirm_risk_on(
    db: Session,
    *,
    idea_id: uuid.UUID,
    venue: str,
    account: str,
    preview_hash: str,
    idempotency_key: str,
    owner_confirmed: bool,
    now: datetime | None = None,
    cfg: EngineConfig | None = None,
) -> RiskOverrideCreation:
    if not owner_confirmed:
        raise RiskOnConfirmationRejected("explicit owner confirmation is required")
    displayed = preview_hash.strip()
    if not displayed:
        raise RiskOnConfirmationRejected("preview hash is required")
    if not idempotency_key.strip():
        raise RiskOnConfirmationRejected("idempotency key is required")

    try:
        current, request, authorization = _build(
            db,
            idea_id=idea_id,
            venue=venue,
            account=account,
            now=now or datetime.now(UTC),
            cfg=cfg or get_config(),
        )
    except RiskOnPreviewRejected as exc:
        raise RiskOnConfirmationRejected(f"risk preview is stale: {exc}") from exc

    if not current.allowed or request is None or authorization is None:
        raise RiskOnConfirmationRejected(
            "risk preview is stale or no longer allowed: " + "; ".join(current.blockers)
        )
    if current.preview_hash != displayed:
        raise RiskOnConfirmationRejected("risk preview is stale: server state changed")

    confirmed_request = RiskOverrideRequest(
        idea_id=request.idea_id,
        risk_snapshot_id=request.risk_snapshot_id,
        preset=request.preset,
        venue=request.venue,
        account=request.account,
        effective_risk_pct=request.effective_risk_pct,
        effective_quantity=request.effective_quantity,
        effective_leverage=request.effective_leverage,
        idempotency_key=idempotency_key.strip(),
        owner_confirmed=True,
        reason=request.reason,
    )
    return create_execution_risk_override(
        db,
        request=confirmed_request,
        authorization=authorization,
    )


__all__ = [
    "RiskOnConfirmationRejected",
    "RiskOnPreview",
    "RiskOnPreviewRejected",
    "confirm_risk_on",
    "preview_risk_on",
]
