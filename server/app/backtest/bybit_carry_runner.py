"""Persistence adapter for the Bybit carry OOS replay.

The replay itself stays pure. This module resolves one immutable dataset
snapshot, converts its mark/index/funding streams into PIT carry facts, runs the
metric-specific replay, and persists evidence without populating directional-R
fields.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..datasets.snapshots import DatasetSnapshotResolver, FilesystemSnapshotStore, ResolvedDataset
from ..market.derivatives import CryptoCarryMarketFacts, FundingObservation
from ..models import BacktestRun
from ..version import ENGINE_VERSION
from .bybit_carry_backtest import CarryReplayGate, replay_carry_oos
from .bybit_dataset import DATA_READY

_STRATEGY = "crypto_carry_v1"


def _label(snapshot_id: str) -> str:
    # Share the canonical entry-backtest identity so the generic R-multiple
    # suite sees specialized carry evidence and never writes a fake directional
    # blocker for the same strategy/snapshot.
    return f"bybit-entry-backtest:{_STRATEGY}:{snapshot_id}"


def _period(dataset: ResolvedDataset) -> tuple[date, date]:
    watermark = dataset.source_watermark
    raw_start = watermark.get("period_start")
    raw_end = watermark.get("period_end")
    if raw_start is not None and raw_end is not None:
        return _aware(raw_start).date(), _aware(raw_end).date()
    dates = [row.tradable_at.date() for row in dataset.rows]
    if not dates:
        day = dataset.tradable_at.date()
        return day, day
    return min(dates), max(dates)


def _aware(value: object) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("carry dataset timestamps must be timezone-aware")
    return result


def resolve_carry_facts(dataset: ResolvedDataset) -> tuple[CryptoCarryMarketFacts, ...]:
    """Build deterministic PIT facts from immutable mark/index/funding rows.

    A funding interval is inferred only after two settled prints are already
    known. The first print is therefore not an evaluable fact: using the next
    future settlement to infer its interval would introduce look-ahead.
    """

    symbol = str(dataset.source_watermark.get("symbol") or dataset.dataset_name)
    streams: dict[str, list] = {
        "mark_price": [],
        "index_price": [],
        "funding": [],
    }
    for row in dataset.rows:
        stream = str(row.values.get("stream") or "")
        if stream in streams:
            streams[stream].append(row)

    marks = sorted(streams["mark_price"], key=lambda row: row.tradable_at)
    indexes = sorted(streams["index_price"], key=lambda row: row.tradable_at)
    fundings = sorted(streams["funding"], key=lambda row: row.tradable_at)
    if not marks or not indexes or len(fundings) < 2:
        return ()

    funding_history: list[FundingObservation] = []
    for row in fundings:
        rate = row.values.get("funding_rate")
        if rate is None:
            continue
        funding_history.append(
            FundingObservation(
                rate=Decimal(str(rate)),
                settled_at=row.tradable_at,
                tradable_at=row.tradable_at,
                source="bybit_snapshot",
            )
        )
    if len(funding_history) < 2:
        return ()

    facts: list[CryptoCarryMarketFacts] = []
    index_cursor = 0
    funding_cursor = 0
    latest_index = None
    latest_funding: list[FundingObservation] = []

    for mark in marks:
        while (
            index_cursor < len(indexes)
            and indexes[index_cursor].tradable_at <= mark.tradable_at
        ):
            latest_index = indexes[index_cursor]
            index_cursor += 1
        while (
            funding_cursor < len(funding_history)
            and funding_history[funding_cursor].tradable_at <= mark.tradable_at
        ):
            latest_funding.append(funding_history[funding_cursor])
            funding_cursor += 1
        if latest_index is None or len(latest_funding) < 2:
            continue

        previous = latest_funding[-2]
        latest = latest_funding[-1]
        interval_minutes = int(
            (latest.settled_at - previous.settled_at).total_seconds() // 60
        )
        if interval_minutes <= 0:
            continue

        mark_price = mark.values.get("close")
        index_price = latest_index.values.get("close")
        if mark_price is None or index_price is None:
            continue
        facts.append(
            CryptoCarryMarketFacts(
                instrument_id=symbol,
                mark_price=Decimal(str(mark_price)),
                index_price=Decimal(str(index_price)),
                current_funding_rate=latest.rate,
                funding_interval_minutes=interval_minutes,
                funding_history=tuple(latest_funding),
                observed_at=max(
                    mark.tradable_at,
                    latest_index.tradable_at,
                    latest.tradable_at,
                ),
                tradable_at=mark.tradable_at,
                source="bybit_snapshot",
            )
        )

    return tuple(facts)


def run_pending_bybit_carry_backtest(
    session: Session,
    *,
    store: FilesystemSnapshotStore,
    snapshot_id: str,
    cfg: EngineConfig | None = None,
    gate: CarryReplayGate | None = None,
) -> BacktestRun | None:
    """Create specialized carry evidence once per immutable snapshot."""

    existing = session.execute(
        select(BacktestRun.id).where(BacktestRun.label == _label(snapshot_id)).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return None
    return run_bybit_carry_backtest(
        session,
        store=store,
        snapshot_id=snapshot_id,
        cfg=cfg,
        gate=gate,
    )


def run_bybit_carry_backtest(
    session: Session,
    *,
    store: FilesystemSnapshotStore,
    snapshot_id: str,
    cfg: EngineConfig | None = None,
    gate: CarryReplayGate | None = None,
) -> BacktestRun:
    """Run and persist CARRY_BPS OOS evidence for one immutable snapshot."""

    config = cfg or get_config()
    dataset = DatasetSnapshotResolver(session, store=store).resolve_snapshot_id(snapshot_id)
    period_from, period_to = _period(dataset)
    symbol = str(dataset.source_watermark.get("symbol") or "")

    if not dataset.dataset_name.startswith("bybit:"):
        raise ValueError("Bybit carry runner requires a bybit:* dataset snapshot")

    if str(dataset.source_watermark.get("readiness") or "") != DATA_READY:
        return _blocked(
            session,
            dataset=dataset,
            period_from=period_from,
            period_to=period_to,
            symbol=symbol,
            cfg=config,
            reason="DATA_BLOCKED",
        )

    facts = resolve_carry_facts(dataset)
    if not facts:
        return _blocked(
            session,
            dataset=dataset,
            period_from=period_from,
            period_to=period_to,
            symbol=symbol,
            cfg=config,
            reason="CARRY_FACTS_UNAVAILABLE",
        )

    raw = config.get("shadow.crypto_carry")
    report = replay_carry_oos(
        facts=facts,
        execution_cost_bps=Decimal(str(raw["execution_cost_bps"])),
        hedge_carry_bps_per_interval=Decimal(
            str(raw["hedge_carry_bps_per_interval"])
        ),
        funding_uncertainty_bps_per_interval=Decimal(
            str(raw["funding_uncertainty_bps_per_interval"])
        ),
        gate=gate or CarryReplayGate(),
    )
    profit_factor = report.profit_factor if report.profit_factor.is_finite() else None
    payload = {
        "metric_space": report.metric_space,
        "dataset": dataset.audit,
        "oos": {
            "observations": len(report.outcomes),
            "expectancy_bps": str(report.expectancy_bps),
            "profit_factor": str(report.profit_factor),
            "top5_contribution": str(report.top5_contribution),
            "gate_passed": report.gate_passed,
            "gate_reasons": list(report.gate_reasons),
        },
        "cost_model": {
            "execution_cost_bps": str(raw["execution_cost_bps"]),
            "hedge_carry_bps_per_interval": str(
                raw["hedge_carry_bps_per_interval"]
            ),
            "funding_uncertainty_bps_per_interval": str(
                raw["funding_uncertainty_bps_per_interval"]
            ),
            "funding_uncertainty_used_in_realized_outcome": False,
        },
    }
    run = BacktestRun(
        label=_label(dataset.snapshot_id),
        strategy=_STRATEGY,
        period_from=period_from,
        period_to=period_to,
        config_hash=config.config_hash,
        engine_version=ENGINE_VERSION,
        universe_json=["CRYPTO", "BYBIT", symbol] if symbol else ["CRYPTO", "BYBIT"],
        trades=len(report.outcomes),
        net_return=None,
        profit_factor=profit_factor,
        expectancy_r=None,
        max_drawdown=None,
        sharpe=None,
        sortino=None,
        calmar=None,
        brier_score=None,
        pbo=None,
        top5_contribution=report.top5_contribution,
        report_json=payload,
        gate_passed=report.gate_passed,
        gate_detail_json={
            "metric_space": "CARRY_BPS",
            "expectancy_bps": str(report.expectancy_bps),
            "profit_factor": str(report.profit_factor),
            "reasons": list(report.gate_reasons),
        },
    )
    session.add(run)
    return run


def _blocked(
    session: Session,
    *,
    dataset: ResolvedDataset,
    period_from: date,
    period_to: date,
    symbol: str,
    cfg: EngineConfig,
    reason: str,
) -> BacktestRun:
    run = BacktestRun(
        label=_label(dataset.snapshot_id),
        strategy=_STRATEGY,
        period_from=period_from,
        period_to=period_to,
        config_hash=cfg.config_hash,
        engine_version=ENGINE_VERSION,
        universe_json=["CRYPTO", "BYBIT", symbol] if symbol else ["CRYPTO", "BYBIT"],
        trades=0,
        net_return=None,
        profit_factor=None,
        expectancy_r=None,
        max_drawdown=None,
        sharpe=None,
        sortino=None,
        calmar=None,
        brier_score=None,
        pbo=None,
        top5_contribution=None,
        report_json={
            "metric_space": "CARRY_BPS",
            "reason": reason,
            "dataset": dataset.audit,
        },
        gate_passed=False,
        gate_detail_json={"metric_space": "CARRY_BPS", "reason": reason},
    )
    session.add(run)
    return run


__all__ = [
    "resolve_carry_facts",
    "run_bybit_carry_backtest",
    "run_pending_bybit_carry_backtest",
]
