"""Отчёт прогона сборки пакетов (§6).

«Состава нет» — это два разных ответа, и до сих пор они выглядели одинаково.
Либо конвейер не дошёл до оптимизации (мало данных, короткая общая история),
либо составы посчитаны и ни один не прошёл проверку на истории. На экране в
обоих случаях стояла серая точка «нет», и владелец не мог понять, ждать ему
данных или менять требования.

Хранится последний прогон, а не история: это состояние конвейера, и вопрос к
нему всегда один — что мешает прямо сейчас.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_portfolio_run_report"
down_revision = "0004_idea_evidence_annotations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_runs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True, nullable=False,
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("universe", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("screened", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("common_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("built", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("admitted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "reasons_json", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "dropped_json", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_index(
        "ix_portfolio_runs_started", "portfolio_runs", ["started_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_portfolio_runs_started", table_name="portfolio_runs")
    op.drop_table("portfolio_runs")
