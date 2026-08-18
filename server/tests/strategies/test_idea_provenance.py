from __future__ import annotations

from datetime import UTC, datetime

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.models import TradeIdea
from app.models.enums import Strategy
from app.strategies.versioning import (
    LEGACY_CONTROL_CONFIG_HASH,
    LEGACY_CONTROL_SOURCE_SHA,
    LEGACY_CONTROL_VERSION,
    LEGACY_RISK_POLICY_VERSION,
    StrategyRole,
    TradingStage,
    manifest_for,
)
from tests.conftest import idea_kwargs


PROVENANCE_COLUMNS = {
    "strategy_family",
    "strategy_version",
    "strategy_role",
    "strategy_config_hash",
    "strategy_code_ref",
    "risk_policy_version",
    "generated_stage",
}


def _assert_legacy_control_provenance(idea: TradeIdea) -> None:
    assert idea.strategy_family == Strategy.TREND_PULLBACK.value
    assert idea.strategy_version == LEGACY_CONTROL_VERSION
    assert idea.strategy_role == StrategyRole.CONTROL.value
    assert idea.strategy_config_hash == LEGACY_CONTROL_CONFIG_HASH
    assert idea.strategy_code_ref == LEGACY_CONTROL_SOURCE_SHA
    assert idea.risk_policy_version == LEGACY_RISK_POLICY_VERSION
    assert idea.generated_stage == TradingStage.PAPER.value


def _revision_lineage(script: ScriptDirectory, revision: str) -> set[str]:
    pending = [revision]
    seen: set[str] = set()
    while pending:
        current_id = pending.pop()
        if current_id in seen:
            continue
        seen.add(current_id)
        current = script.get_revision(current_id)
        assert current is not None
        down = current.down_revision
        if down is None:
            continue
        if isinstance(down, tuple):
            pending.extend(down)
        else:
            pending.append(down)
    return seen


def test_trade_idea_requires_strategy_provenance_columns():
    columns = {column.name: column for column in TradeIdea.__table__.columns}

    assert PROVENANCE_COLUMNS <= set(columns)
    for name in PROVENANCE_COLUMNS:
        assert columns[name].nullable is False


def test_new_legacy_control_idea_can_persist_explicit_manifest_provenance(session, instrument):
    descriptor = manifest_for(Strategy.TREND_PULLBACK)
    moment = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            moment,
            strategy_family=descriptor.family,
            strategy_version=descriptor.version,
            strategy_role=descriptor.role.value,
            strategy_config_hash=descriptor.config_hash,
            strategy_code_ref=descriptor.source_sha,
            risk_policy_version=descriptor.risk_policy_version,
            generated_stage=descriptor.generated_stage.value,
        )
    )

    session.add(idea)
    session.flush()
    session.refresh(idea)

    _assert_legacy_control_provenance(idea)


def test_current_runtime_defaults_to_exact_legacy_control_identity(session, instrument):
    """Metadata defaults must not require a new execution path or gate scanning."""

    moment = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    idea = TradeIdea(**idea_kwargs(instrument.instrument_id, moment))
    session.add(idea)
    session.flush()
    session.refresh(idea)

    _assert_legacy_control_provenance(idea)


def test_migration_schema_has_no_nullable_strategy_provenance(engine):
    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("trade_ideas")}

    assert PROVENANCE_COLUMNS <= set(columns)
    assert all(columns[name]["nullable"] is False for name in PROVENANCE_COLUMNS)


def test_database_head_descends_from_strategy_provenance_revision(engine):
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert "0015_strategy_idea_provenance" in _revision_lineage(script, revision)
