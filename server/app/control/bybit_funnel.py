"""Deterministic Bybit scan-funnel diagnostics for owner Control.

The funnel is observability only. It never decides strategy eligibility and it
never changes thresholds. Production scan writes one terminal fact per Bybit
instrument/scan attempt into the existing append-only DataQualityEvent stream;
Control reads and aggregates those facts later without re-running strategies.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DataQualityEvent

SOURCE = "bybit-scan"
FLAG = "SCAN_TERMINAL"

TERMINAL_STAGES = frozenset(
    {
        "DATA_BLOCKED",
        "LIQUIDITY_BLOCKED",
        "REGIME_REJECTED",
        "SETUP_REJECTED",
        "ADMISSION_REJECTED",
        "PUBLISHED",
        "DUPLICATE",
        "ERROR",
    }
)


@dataclass(frozen=True, slots=True)
class FunnelFact:
    instrument_id: str
    terminal_stage: str
    reason_code: str
    sequence: int = 0

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise ValueError("instrument_id is required")
        if self.terminal_stage not in TERMINAL_STAGES:
            raise ValueError(f"unsupported funnel terminal stage: {self.terminal_stage}")
        if not self.reason_code.strip():
            raise ValueError("reason_code is required")
        if self.sequence < 0:
            raise ValueError("sequence must not be negative")


def _latest_by_instrument(facts: Iterable[FunnelFact]) -> tuple[FunnelFact, ...]:
    latest: dict[str, tuple[int, int, FunnelFact]] = {}
    for order, fact in enumerate(facts):
        previous = latest.get(fact.instrument_id)
        marker = (fact.sequence, order)
        if previous is None or marker >= previous[:2]:
            latest[fact.instrument_id] = (fact.sequence, order, fact)
    return tuple(
        latest[key][2]
        for key in sorted(latest)
    )


def _is_cost_rr_reason(reason: str) -> bool:
    normalized = reason.upper()
    return any(token in normalized for token in ("RR", "EXPECTED_R", "COST"))


def aggregate_funnel(facts: Iterable[FunnelFact]) -> dict[str, object]:
    """Aggregate latest per-instrument terminal facts into cumulative stages."""

    rows = _latest_by_instrument(facts)
    terminal = Counter(row.terminal_stage for row in rows)
    reasons = Counter(row.reason_code for row in rows)

    def not_in(*blocked: str) -> int:
        blocked_set = set(blocked)
        return sum(1 for row in rows if row.terminal_stage not in blocked_set)

    evaluated_stages = {
        "SETUP_REJECTED",
        "ADMISSION_REJECTED",
        "PUBLISHED",
        "DUPLICATE",
    }
    result: dict[str, object] = {
        "universe": len(rows),
        "data_healthy": not_in("DATA_BLOCKED", "ERROR"),
        "liquid": not_in("DATA_BLOCKED", "LIQUIDITY_BLOCKED", "ERROR"),
        "regime_eligible": sum(
            1 for row in rows if row.terminal_stage in evaluated_stages
        ),
        "strategy_evaluated": sum(
            1 for row in rows if row.terminal_stage in evaluated_stages
        ),
        "setup_reject": int(terminal.get("SETUP_REJECTED", 0)),
        "cost_rr_reject": sum(
            1
            for row in rows
            if row.terminal_stage == "ADMISSION_REJECTED"
            and _is_cost_rr_reason(row.reason_code)
        ),
        "published": int(terminal.get("PUBLISHED", 0)),
        "terminal": {
            name: int(count) for name, count in sorted(terminal.items())
        },
        "top_reasons": [
            {"reason": reason, "count": int(count)}
            for reason, count in sorted(
                reasons.items(), key=lambda item: (-item[1], item[0])
            )[:10]
        ],
    }
    return result


def record_funnel_fact(
    session: Session,
    fact: FunnelFact,
    *,
    detail: str = "",
    occurred_at: datetime | None = None,
) -> DataQualityEvent:
    """Append one terminal scan fact to the existing observability journal."""

    row = DataQualityEvent(
        source=SOURCE,
        instrument_id=fact.instrument_id,
        flag=FLAG,
        detail=detail[:512],
        payload_json={
            "terminal_stage": fact.terminal_stage,
            "reason_code": fact.reason_code,
            "sequence": fact.sequence,
        },
    )
    if occurred_at is not None:
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        row.occurred_at = occurred_at
    session.add(row)
    return row


def load_funnel_facts(
    session: Session,
    *,
    start: datetime,
) -> tuple[FunnelFact, ...]:
    """Read recent persisted facts in deterministic event order."""

    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    rows = session.execute(
        select(DataQualityEvent)
        .where(
            DataQualityEvent.source == SOURCE,
            DataQualityEvent.flag == FLAG,
            DataQualityEvent.occurred_at >= start,
        )
        .order_by(DataQualityEvent.occurred_at, DataQualityEvent.id)
    ).scalars().all()

    facts: list[FunnelFact] = []
    for ordinal, row in enumerate(rows):
        payload = row.payload_json or {}
        stage = str(payload.get("terminal_stage") or "")
        reason = str(payload.get("reason_code") or "")
        if stage not in TERMINAL_STAGES or not reason:
            # Old/malformed observability rows cannot poison Control.
            continue
        raw_sequence = payload.get("sequence")
        try:
            sequence = int(raw_sequence) if raw_sequence is not None else ordinal
        except (TypeError, ValueError):
            sequence = ordinal
        facts.append(
            FunnelFact(
                instrument_id=str(row.instrument_id or ""),
                terminal_stage=stage,
                reason_code=reason,
                sequence=max(0, sequence),
            )
        )
    return tuple(facts)


def dashboard_funnel(session: Session, *, start: datetime) -> dict[str, object]:
    return aggregate_funnel(load_funnel_facts(session, start=start))


__all__ = [
    "FLAG",
    "SOURCE",
    "FunnelFact",
    "TERMINAL_STAGES",
    "aggregate_funnel",
    "dashboard_funnel",
    "load_funnel_facts",
    "record_funnel_fact",
]
