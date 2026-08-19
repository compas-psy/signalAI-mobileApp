from __future__ import annotations

from decimal import Decimal

import pytest

from app.execution.risk_on import RiskOnConfirmationRejected, confirm_risk_on, preview_risk_on
from app.models import ExecutionRiskOverride, RiskSnapshot, TradeIdea
from tests.conftest import idea_kwargs


def _seed_idea(session, instrument, now, **overrides):
    idea = TradeIdea(**idea_kwargs(instrument.instrument_id, now, status="TRIGGERED", quality_status="ACTIVE", risk_pct=Decimal("0.005"), risk_amount=Decimal("500"), quantity=Decimal("1"), **overrides))
    session.add(idea)
    session.flush()
    return idea


def _snapshot(session, *, equity="200000", **overrides):
    values = dict(risk_equity=Decimal(equity), open_risk=Decimal("0"), day_pnl_pct=Decimal("0"), week_pnl_pct=Decimal("0"), month_pnl_pct=Decimal("0"), current_drawdown=Decimal("0"), drawdown_multiplier=Decimal("1"), cluster_risk_json={"rub_fx": "0"})
    values.update(overrides)
    row = RiskSnapshot(**values)
    session.add(row)
    session.flush()
    return row


def test_preview_reuses_authoritative_sizing_and_owner_hard_cap(session, instrument, now):
    idea = _seed_idea(session, instrument, now)
    risk = _snapshot(session)
    preview = preview_risk_on(session, idea_id=idea.id, venue="TINVEST", account="sandbox-main", now=now)
    assert preview.allowed is True
    assert preview.risk_snapshot_id == risk.id
    assert preview.base_risk_pct == Decimal("0.005")
    assert preview.effective_risk_pct == Decimal("0.0075")
    assert preview.hard_cap_risk_pct == Decimal("0.0075")
    assert preview.base_quantity == Decimal("1")
    assert preview.effective_quantity == Decimal("2")
    assert preview.effective_risk_amount == Decimal("1400")
    assert preview.binding_limit == "score"
    assert preview.effective_leverage is None
    assert preview.hard_cap_leverage == Decimal("3.0")
    assert preview.blockers == ()
    assert len(preview.preview_hash) == 64


def test_preview_never_bypasses_portfolio_or_strategy_safety(session, instrument, now):
    idea = _seed_idea(session, instrument, now, strategy="WYCKOFF_REVERSAL")
    _snapshot(session, equity="400000", open_risk=Decimal("0.019"), cluster_risk_json={"rub_fx": "0.009"})
    preview = preview_risk_on(session, idea_id=idea.id, venue="TINVEST", account="sandbox-main", now=now)
    assert preview.allowed is False
    assert preview.effective_risk_pct == Decimal("0.001")
    assert preview.effective_quantity == Decimal("0")
    assert "no additional risk headroom" in preview.blockers


def test_preview_fails_closed_when_latest_risk_snapshot_blocks_entries(session, instrument, now):
    idea = _seed_idea(session, instrument, now)
    _snapshot(session, entries_blocked=True)
    preview = preview_risk_on(session, idea_id=idea.id, venue="TINVEST", account="sandbox-main", now=now)
    assert preview.allowed is False
    assert "risk snapshot blocks new entries" in preview.blockers
    assert preview.effective_quantity == Decimal("0")


def test_confirm_recalculates_preview_and_rejects_stale_risk_state(session, instrument, now):
    idea = _seed_idea(session, instrument, now)
    _snapshot(session)
    shown = preview_risk_on(session, idea_id=idea.id, venue="TINVEST", account="sandbox-main", now=now)
    assert shown.allowed is True
    _snapshot(session, equity="200000", open_risk=Decimal("0.019"))
    with pytest.raises(RiskOnConfirmationRejected, match="stale"):
        confirm_risk_on(session, idea_id=idea.id, venue="TINVEST", account="sandbox-main", preview_hash=shown.preview_hash, idempotency_key="risk-on-confirm-1", owner_confirmed=True, now=now)
    assert session.query(ExecutionRiskOverride).count() == 0


def test_confirm_creates_exact_immutable_override_and_is_idempotent(session, instrument, now):
    idea = _seed_idea(session, instrument, now)
    _snapshot(session)
    shown = preview_risk_on(session, idea_id=idea.id, venue="TINVEST", account="sandbox-main", now=now)
    first = confirm_risk_on(session, idea_id=idea.id, venue="TINVEST", account="sandbox-main", preview_hash=shown.preview_hash, idempotency_key="risk-on-confirm-1", owner_confirmed=True, now=now)
    second = confirm_risk_on(session, idea_id=idea.id, venue="TINVEST", account="sandbox-main", preview_hash=shown.preview_hash, idempotency_key="risk-on-confirm-1", owner_confirmed=True, now=now)
    assert first.override.id == second.override.id
    assert first.created is True
    assert second.created is False
    assert first.override.preview_hash == shown.preview_hash
    assert first.override.effective_risk_pct == shown.effective_risk_pct
    assert first.override.effective_quantity == shown.effective_quantity
    assert first.override.hard_cap_risk_pct == Decimal("0.0075")
    assert first.override.effective_leverage is None


def test_confirm_requires_explicit_owner_confirmation(session, instrument, now):
    idea = _seed_idea(session, instrument, now)
    _snapshot(session)
    shown = preview_risk_on(session, idea_id=idea.id, venue="TINVEST", account="sandbox-main", now=now)
    with pytest.raises(RiskOnConfirmationRejected, match="owner confirmation"):
        confirm_risk_on(session, idea_id=idea.id, venue="TINVEST", account="sandbox-main", preview_hash=shown.preview_hash, idempotency_key="risk-on-confirm-1", owner_confirmed=False, now=now)
