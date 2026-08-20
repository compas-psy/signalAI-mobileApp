"""Idempotent persistence for append-only Shadow measurement observations."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models import ShadowObservation
from .runtime_v1 import ShadowCandidateObservation


def persist_shadow_observations(
    session: Session,
    observations: Sequence[ShadowCandidateObservation],
) -> tuple[ShadowObservation, ...]:
    """Persist immutable observations; replay of the same identity is a no-op.

    ``ON CONFLICT DO NOTHING`` makes retries/concurrent scheduler attempts safe.
    The row is selected afterwards so callers receive the canonical persisted
    object whether this invocation inserted it or merely replayed it.
    """

    if not isinstance(session, Session):
        raise ValueError("session must be a SQLAlchemy Session")
    values = list(observations)
    if any(not isinstance(item, ShadowCandidateObservation) for item in values):
        raise ValueError("observations must contain ShadowCandidateObservation values")

    keys = [item.observation_key for item in values]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate Shadow observation key in persistence batch")

    result: list[ShadowObservation] = []
    for item in values:
        statement = (
            insert(ShadowObservation)
            .values(
                observation_key=item.observation_key,
                opportunity_key=item.opportunity_key,
                stage=item.stage,
                instrument_id=item.instrument_id,
                venue=item.venue,
                strategy_family=item.strategy_family,
                strategy_version=item.strategy_version,
                signal_emitted=item.signal_emitted,
                direction=None if item.direction is None else item.direction.value,
                raw_edge_score=item.raw_edge_score,
                entry_reference=item.entry_reference,
                data_quality_state=(
                    None
                    if item.data_quality_state is None
                    else item.data_quality_state.value
                ),
                evaluated_at=item.evaluated_at,
                market_snapshot_hash=item.market_snapshot_hash,
                cost_model_hash=item.cost_model_hash,
            )
            .on_conflict_do_nothing(index_elements=["observation_key"])
        )
        session.execute(statement)
        row = session.execute(
            select(ShadowObservation).where(
                ShadowObservation.observation_key == item.observation_key
            )
        ).scalar_one()
        result.append(row)

    return tuple(result)


__all__ = ["persist_shadow_observations"]
