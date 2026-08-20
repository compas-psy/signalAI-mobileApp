"""Immutable open-trade management policy snapshots (SAI-049)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from .base import Base, UuidPk, utcnow_column
from .execution import ExecutionIntent


class ExecutionManagementPolicySnapshot(UuidPk, Base):
    """Frozen policy facts that govern one already-protected position.

    An optimizer/config release may change future trades, but this row is
    append-only and keyed one-to-one to the execution intent so an opened
    position keeps the exact strategy/risk/exit/venue contract it started with.
    """

    __tablename__ = "execution_management_policy_snapshots"

    intent_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("execution_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("risk_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    risk_override_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("execution_risk_overrides.id", ondelete="RESTRICT"),
        nullable=True,
    )
    risk_policy_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    manual_override_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    exit_profile_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    venue_rules_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        UniqueConstraint(
            "intent_id",
            name="uq_execution_management_policy_snapshots_intent",
        ),
        Index(
            "ix_execution_management_policy_snapshots_created",
            "created_at",
        ),
    )


def _state_text(intent: ExecutionIntent) -> str:
    return str(getattr(intent.state, "value", intent.state)).upper()


@event.listens_for(Session, "before_commit")
def _freeze_protected_management_policy_before_commit(session: Session) -> None:
    """Make the PROTECTED transaction the management-policy freeze boundary.

    The execution core commits immediately after provider-confirmed protection
    and before entering MANAGING. Enforcing the snapshot at the Session commit
    boundary keeps the snapshot durable in the same transaction as PROTECTED,
    even if a future caller reaches that state without going through one
    particular orchestration helper.
    """

    if session.info.get("_sai049_management_policy_freeze"):
        return

    intents: dict[uuid.UUID, ExecutionIntent] = {}
    for obj in (*session.identity_map.values(), *session.new):
        if (
            isinstance(obj, ExecutionIntent)
            and obj.id is not None
            and _state_text(obj) == "PROTECTED"
        ):
            intents[obj.id] = obj

    if not intents:
        return

    session.info["_sai049_management_policy_freeze"] = True
    try:
        from ..execution.management_policy import freeze_execution_management_policy

        for intent_id in intents:
            existing = session.execute(
                select(ExecutionManagementPolicySnapshot.id).where(
                    ExecutionManagementPolicySnapshot.intent_id == intent_id
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            freeze_execution_management_policy(session, intent_id=intent_id)
    finally:
        session.info.pop("_sai049_management_policy_freeze", None)


__all__ = ["ExecutionManagementPolicySnapshot"]
