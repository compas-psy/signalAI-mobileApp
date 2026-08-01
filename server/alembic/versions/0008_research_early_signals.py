"""Исследовательский контур ранних сигналов (ТЗ Early Signals §5, §8, §12, §16).

Шесть таблиц, и ни одна из них не про сделку. Идея — это план входа со
стопом и целями; гипотеза — утверждение об экономике эмитента на год-два,
которое проверяется наблюдениями и опровергается фактами. Разные горизонты,
разные критерии истинности, разные последствия ошибки — поэтому разные
таблицы, а не флаг в существующих.

Реестр источников появляется первым и не случайно: ТЗ §2.1 запрещает
сетевой вызов без записи о правовом режиме. Таблица разрешений хранит и
отказы — журнал, в котором есть только успехи, не отвечает на вопрос,
пытались ли обойти запрет.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_research_early_signals"
down_revision = "0007_fx_rates"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "research_sources",
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("owner", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("base_url", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("access_method", sa.String(length=16), nullable=False),
        sa.Column("auth_type", sa.String(length=24), nullable=False, server_default="none"),
        sa.Column("license_status", sa.String(length=24), nullable=False),
        sa.Column(
            "usage_scope", sa.String(length=64), nullable=False,
            server_default="internal_research",
        ),
        sa.Column("allowed_operations", JSONB, nullable=False, server_default="{}"),
        sa.Column("terms_url", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("terms_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terms_review_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_frequency", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("max_staleness", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("source_timezone", sa.String(length=48), nullable=False, server_default="UTC"),
        sa.Column("rate_limit_policy", JSONB, nullable=False, server_default="{}"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("schema_version", sa.String(length=16), nullable=False, server_default="1.0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("source_id", name="pk_research_sources"),
    )

    op.create_table(
        "research_permits",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("requested_operations", JSONB, nullable=False, server_default="[]"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_research_permits"),
    )
    op.create_index("ix_research_permits_source", "research_permits", ["source_id", "issued_at"])

    op.create_table(
        "research_observations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("observation_type", sa.String(length=48), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("tradable_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "publication_time_uncertain", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("lineage_root_id", sa.String(length=64), nullable=False),
        sa.Column("source_locator", JSONB, nullable=False, server_default="{}"),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("value_numeric", sa.Numeric(38, 12), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("unit", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("scale", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fact_label", sa.String(length=24), nullable=False, server_default="fact"),
        sa.Column("quality_flags", JSONB, nullable=False, server_default="[]"),
        sa.Column("revision_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_research_observations"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["research_sources.source_id"],
            name="fk_research_observations_source_id_research_sources",
        ),
        sa.CheckConstraint("scale > 0", name="ck_research_observations_scale_positive"),
    )
    op.create_index(
        "ix_research_obs_entity", "research_observations", ["entity_id", "tradable_at"]
    )
    op.create_index(
        "ix_research_obs_type", "research_observations", ["observation_type", "tradable_at"]
    )

    op.create_table(
        "research_signals",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("strategy_key", sa.String(length=32), nullable=False),
        sa.Column("strategy_version", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("instrument_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "detected_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("strength", sa.Numeric(12, 8), nullable=False),
        sa.Column("signed_strength", sa.Numeric(12, 8), nullable=False),
        sa.Column("evidence_confidence", sa.Numeric(12, 8), nullable=False),
        sa.Column("economic_relevance", sa.Numeric(12, 8), nullable=False),
        sa.Column("freshness", sa.Numeric(12, 8), nullable=False),
        sa.Column("risk_penalty", sa.Numeric(12, 8), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("components", JSONB, nullable=False, server_default="[]"),
        sa.Column("reason_codes", JSONB, nullable=False, server_default="[]"),
        sa.Column("evidence_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id", name="pk_research_signals"),
    )
    op.create_index(
        "ix_research_signals_lookup",
        "research_signals",
        ["strategy_key", "entity_id", "as_of"],
    )

    op.create_table(
        "research_hypotheses",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=24), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("instrument_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("causal_path_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("target_kpis_json", JSONB, nullable=False, server_default="[]"),
        sa.Column("expected_lag", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("evidence_score", sa.Numeric(12, 8), nullable=False, server_default="0"),
        sa.Column("economic_score", sa.Numeric(12, 8), nullable=False, server_default="0"),
        sa.Column("market_context_score", sa.Numeric(12, 8), nullable=True),
        sa.Column(
            "market_context_state", sa.String(length=32), nullable=False,
            server_default="unknown",
        ),
        sa.Column("research_priority", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("three_two_one_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("investability_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("fact_summary_json", JSONB, nullable=False, server_default="[]"),
        sa.Column("alternative_explanations_json", JSONB, nullable=False, server_default="[]"),
        sa.Column("risk_flags", JSONB, nullable=False, server_default="[]"),
        sa.Column("falsifiers_json", JSONB, nullable=False, server_default="[]"),
        sa.Column("missing_evidence_json", JSONB, nullable=False, server_default="[]"),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("config_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("engine_version", sa.String(length=32), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id", name="pk_research_hypotheses"),
        sa.UniqueConstraint(
            "fingerprint", "version", name="uq_research_hypotheses_fingerprint"
        ),
    )
    op.create_index(
        "ix_research_hypotheses_state",
        "research_hypotheses",
        ["state", "research_priority"],
    )

    op.create_table(
        "research_hypothesis_evidence",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("hypothesis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("signal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("lineage_root_id", sa.String(length=64), nullable=False),
        sa.Column(
            "independence_group", sa.String(length=48), nullable=False, server_default=""
        ),
        sa.Column("claim_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_locator", JSONB, nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Numeric(12, 8), nullable=False, server_default="1"),
        sa.Column("weight", sa.Numeric(12, 8), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_research_hypothesis_evidence"),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"], ["research_hypotheses.id"],
            name="fk_hypothesis_evidence_hypothesis",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_research_evidence_hypothesis",
        "research_hypothesis_evidence",
        ["hypothesis_id", "role"],
    )


def downgrade() -> None:
    op.drop_table("research_hypothesis_evidence")
    op.drop_table("research_hypotheses")
    op.drop_table("research_signals")
    op.drop_table("research_observations")
    op.drop_table("research_permits")
    op.drop_table("research_sources")
