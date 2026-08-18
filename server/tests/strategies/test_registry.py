from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError

import pytest

from app.models.enums import Strategy
from app.strategies.registry import StrategyDescriptor, StrategyRegistry
from app.strategies.versioning import (
    LEGACY_CONTROL_CONFIG_HASH,
    LEGACY_CONTROL_VERSION,
    StrategyRole,
    TradingStage,
)


def _candidate(version: str = "candidate_v2") -> StrategyDescriptor:
    return StrategyDescriptor(
        family=Strategy.TREND_PULLBACK.value,
        version=version,
        role=StrategyRole.CANDIDATE,
        enabled_stages=frozenset(
            {TradingStage.BACKTEST, TradingStage.OOS, TradingStage.SHADOW}
        ),
        config_hash="2" * 64,
    )


def test_legacy_control_is_seeded_and_available_for_runtime_and_replay(session):
    registry = StrategyRegistry(session)

    descriptor = registry.get(Strategy.TREND_PULLBACK.value, LEGACY_CONTROL_VERSION)

    assert descriptor == StrategyDescriptor(
        family=Strategy.TREND_PULLBACK.value,
        version=LEGACY_CONTROL_VERSION,
        role=StrategyRole.CONTROL,
        enabled_stages=frozenset(
            {
                TradingStage.BACKTEST,
                TradingStage.OOS,
                TradingStage.SHADOW,
                TradingStage.PAPER,
                TradingStage.SANDBOX,
            }
        ),
        config_hash=LEGACY_CONTROL_CONFIG_HASH,
    )
    assert descriptor in registry.active_for(
        stage=TradingStage.PAPER,
        venue="MOEX",
        instrument="MOEX:FUT:SIU6",
    )
    assert registry.for_replay(
        Strategy.TREND_PULLBACK.value, LEGACY_CONTROL_VERSION
    ) == descriptor


def test_control_and_candidate_register_independently_and_version_is_unique(session):
    registry = StrategyRegistry(session)
    candidate = _candidate()

    registry.register(
        candidate,
        actor="system",
        reason="SAI-003 test candidate",
        venues=frozenset({"MOEX"}),
        instrument_prefixes=frozenset({"MOEX:FUT:"}),
    )

    assert registry.get(candidate.family, candidate.version) == candidate
    assert registry.get(candidate.family, LEGACY_CONTROL_VERSION).role is StrategyRole.CONTROL
    assert candidate in registry.active_for(
        stage=TradingStage.SHADOW,
        venue="MOEX",
        instrument="MOEX:FUT:RIU6",
    )
    assert candidate not in registry.active_for(
        stage=TradingStage.SHADOW,
        venue="CRYPTO",
        instrument="CRYPTO:BTCUSDT",
    )

    with pytest.raises(ValueError, match="already registered"):
        registry.register(candidate, actor="system", reason="duplicate")


def test_candidate_cannot_become_champion_without_audited_decision(session, monkeypatch):
    registry = StrategyRegistry(session)
    candidate = _candidate()
    registry.register(candidate, actor="system", reason="new challenger")

    monkeypatch.setenv("SIGNALAI_STRATEGY_ROLE", "CHAMPION")
    assert registry.get(candidate.family, candidate.version).role is StrategyRole.CANDIDATE

    with pytest.raises(ValueError, match="decision_ref"):
        registry.record_promotion(
            candidate.family,
            candidate.version,
            to_role=StrategyRole.CHAMPION,
            actor="owner",
            decision_ref="",
            reason="env flags are not decisions",
        )

    promoted = registry.record_promotion(
        candidate.family,
        candidate.version,
        to_role=StrategyRole.CHAMPION,
        actor="owner",
        decision_ref="decision:test:001",
        reason="explicit audited decision",
    )

    assert promoted.role is StrategyRole.CHAMPION
    history = registry.history(candidate.family, candidate.version)
    assert [event.sequence for event in history] == [1, 2]
    assert history[-1].decision_ref == "decision:test:001"
    assert history[-1].from_role == StrategyRole.CANDIDATE.value
    assert history[-1].to_role == StrategyRole.CHAMPION.value


def test_legacy_control_identity_cannot_be_promoted_retired_or_deleted(session):
    registry = StrategyRegistry(session)

    with pytest.raises(ValueError, match="legacy control"):
        registry.record_promotion(
            Strategy.TREND_PULLBACK.value,
            LEGACY_CONTROL_VERSION,
            to_role=StrategyRole.RETIRED,
            actor="owner",
            decision_ref="decision:test:retire-control",
            reason="must remain the control",
        )

    version_row = registry.version_row(
        Strategy.TREND_PULLBACK.value, LEGACY_CONTROL_VERSION
    )
    with pytest.raises(DBAPIError):
        session.execute(
            delete(type(version_row)).where(type(version_row).id == version_row.id)
        )


def test_ui_visibility_is_audited_but_does_not_disable_control_or_replay(session):
    registry = StrategyRegistry(session)
    family = Strategy.TREND_PULLBACK.value

    hidden = registry.set_ui_visibility(
        family,
        LEGACY_CONTROL_VERSION,
        visible=False,
        actor="owner",
        reason="hide control cards while keeping counterfactual measurement",
    )

    assert hidden.ui_visible is False
    descriptor = registry.get(family, LEGACY_CONTROL_VERSION)
    assert descriptor in registry.active_for(
        stage=TradingStage.PAPER,
        venue="MOEX",
        instrument="MOEX:FUT:SIU6",
    )
    assert registry.for_replay(family, LEGACY_CONTROL_VERSION) == descriptor
    assert descriptor not in registry.visible_for_ui(
        stage=TradingStage.PAPER,
        venue="MOEX",
        instrument="MOEX:FUT:SIU6",
    )


def test_promotion_history_is_append_only_at_database_level(session):
    registry = StrategyRegistry(session)
    candidate = _candidate("candidate_append_only")
    registry.register(candidate, actor="system", reason="append-only proof")
    registry.record_promotion(
        candidate.family,
        candidate.version,
        to_role=StrategyRole.CHAMPION,
        actor="owner",
        decision_ref="decision:test:append-only",
        reason="audited promotion",
    )
    event = registry.history(candidate.family, candidate.version)[-1]

    event_type = type(event)
    with pytest.raises(DBAPIError):
        session.execute(
            update(event_type)
            .where(event_type.id == event.id)
            .values(reason="rewrite history")
        )


def test_registry_history_has_database_backing(session):
    registry = StrategyRegistry(session)
    candidate = _candidate("candidate_db_backed")
    registry.register(candidate, actor="system", reason="database backing")

    version_row = registry.version_row(candidate.family, candidate.version)
    event_type = type(registry.history(candidate.family, candidate.version)[0])
    rows = session.execute(
        select(event_type).where(event_type.strategy_version_id == version_row.id)
    ).scalars().all()

    assert len(rows) == 1
    assert rows[0].sequence == 1
