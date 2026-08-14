"""Read-only reproducible strategy measurement snapshots (issue #40)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...measurement.report import (
    MeasurementDataset,
    StrategyMeasurementRecord,
    build_strategy_measurement_report,
)
from ...models import IdeaOutcome, Instrument, TradeIdea

router = APIRouter(prefix="/measurements", tags=["measurements"])


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _float(value: Decimal | int | float | None) -> float | None:
    return None if value is None else float(value)


def _normalized_deviation(value, *, risk: Decimal) -> float | None:
    if value is None or risk <= 0:
        return None
    return float(Decimal(value) / risk)


def _dataset(raw: object) -> MeasurementDataset | None:
    try:
        return MeasurementDataset(str(raw))
    except ValueError:
        return None


def _measurement_records(
    db: Session,
    *,
    from_time: datetime,
    to_time: datetime,
) -> tuple[list[StrategyMeasurementRecord], int]:
    rows = db.execute(
        select(TradeIdea, IdeaOutcome, Instrument)
        .join(IdeaOutcome, IdeaOutcome.idea_id == TradeIdea.id)
        .join(Instrument, Instrument.instrument_id == TradeIdea.instrument_id)
        .where(
            TradeIdea.signal_time >= from_time,
            TradeIdea.signal_time < to_time,
        )
        .order_by(
            TradeIdea.signal_time,
            TradeIdea.engine_version,
            TradeIdea.id,
        )
    ).all()

    records: list[StrategyMeasurementRecord] = []
    unclassified = 0
    for idea, outcome, instrument in rows:
        detail = outcome.detail_json or {}
        dataset = _dataset(detail.get("measurement_dataset"))
        if dataset is None:
            unclassified += 1
            continue

        if dataset is MeasurementDataset.BACKTEST:
            result_r = outcome.model_r
        else:
            result_r = outcome.actual_r

        risk = abs(Decimal(idea.entry_reference) - Decimal(idea.stop))
        input_id = str(detail.get("measurement_input_id") or idea.id)
        regime = str(detail.get("measurement_regime") or "UNKNOWN")
        venue = str(getattr(instrument.venue, "value", instrument.venue))
        strategy = str(getattr(idea.strategy, "value", idea.strategy))

        records.append(
            StrategyMeasurementRecord(
                input_id=input_id,
                timestamp=_utc(idea.signal_time),
                dataset=dataset,
                variant=idea.engine_version,
                strategy=strategy,
                instrument_id=idea.instrument_id,
                venue=venue,
                regime=regime,
                outcome_r=_float(result_r),
                mfe_r=_float(outcome.mfe_r),
                mae_r=_float(outcome.mae_r),
                entry_deviation_r=_normalized_deviation(
                    outcome.entry_slippage, risk=risk
                ),
                exit_deviation_r=_normalized_deviation(
                    outcome.exit_slippage, risk=risk
                ),
                confidence=_float(idea.confidence),
                operational_failure=bool(detail.get("operational_failure", False)),
                reconciliation_mismatch=bool(
                    detail.get("reconciliation_mismatch", False)
                ),
                label_usable=bool(outcome.label_usable and result_r is not None),
            )
        )

    return records, unclassified


@router.get("/strategies")
def strategy_measurement_snapshot(
    from_time: datetime,
    to_time: datetime,
    champion: str = Query(min_length=1, max_length=64),
    candidate: str = Query(min_length=1, max_length=64),
    min_sample: int = Query(30, ge=1, le=100_000),
    db: Session = Depends(get_db),
) -> dict:
    start = _utc(from_time)
    end = _utc(to_time)
    if start >= end:
        raise HTTPException(
            status_code=422,
            detail="measurement period must satisfy from_time < to_time",
        )

    records, unclassified = _measurement_records(
        db,
        from_time=start,
        to_time=end,
    )
    try:
        return build_strategy_measurement_report(
            records,
            from_time=start,
            to_time=end,
            champion=champion,
            candidate=candidate,
            min_sample=min_sample,
            unclassified_count=unclassified,
        )
    except ValueError as exc:
        # Duplicate input identity means the comparison is not reproducible;
        # fail the snapshot rather than choosing an arbitrary row.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


__all__ = ["router"]
