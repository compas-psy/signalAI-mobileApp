"""Transparent paired Bybit-vs-Lighter shadow execution scorecard (SAI-074).

This module is measurement-only.  It compares explicit paired shadow facts and
may make evidence eligible for the next testnet slice.  It has no provider I/O,
no execution-mode mutation and no venue-selection behavior.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

_ZERO = Decimal("0")
_ONE = Decimal("1")
_VENUES = ("BYBIT", "LIGHTER")
_STATUSES = {"EVALUATED", "UNAVAILABLE"}
_RECONCILIATION = {"EXACT", "AMBIGUOUS", "CONSUMED_UNKNOWN", "UNAVAILABLE"}


class VenueShadowStatus(StrEnum):
    PASS_EVIDENCE = "PASS_EVIDENCE"
    FAIL_EVIDENCE = "FAIL_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True, slots=True)
class VenueShadowObservation:
    opportunity_key: str
    venue: str
    market_snapshot_hash: str
    status: str
    total_cost_bps: Decimal | None
    ack_latency_ms: Decimal | None
    fill_slippage_bps: Decimal | None
    protection_latency_ms: Decimal | None
    reconciliation_outcome: str
    duplicate_execution_incident: bool = False
    unprotected_execution_incident: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.opportunity_key, str) or not self.opportunity_key.strip():
            raise ValueError("opportunity_key must be non-blank")
        if self.venue not in _VENUES:
            raise ValueError("venue must be BYBIT or LIGHTER")
        _require_sha256("market_snapshot_hash", self.market_snapshot_hash)
        if self.status not in _STATUSES:
            raise ValueError("status must be EVALUATED or UNAVAILABLE")
        if self.reconciliation_outcome not in _RECONCILIATION:
            raise ValueError("unsupported reconciliation_outcome")
        for field in (
            "duplicate_execution_incident",
            "unprotected_execution_incident",
        ):
            if not isinstance(getattr(self, field), bool):
                raise ValueError(f"{field} must be bool")
        for field in (
            "total_cost_bps",
            "ack_latency_ms",
            "fill_slippage_bps",
            "protection_latency_ms",
        ):
            _require_optional_decimal(field, getattr(self, field))
        for field in ("total_cost_bps", "ack_latency_ms", "protection_latency_ms"):
            value = getattr(self, field)
            if value is not None and value < _ZERO:
                raise ValueError(f"{field} cannot be negative")
        if self.status == "UNAVAILABLE":
            if any(
                getattr(self, field) is not None
                for field in (
                    "total_cost_bps",
                    "ack_latency_ms",
                    "fill_slippage_bps",
                    "protection_latency_ms",
                )
            ):
                raise ValueError("UNAVAILABLE observation cannot carry quantitative metrics")
            if self.reconciliation_outcome != "UNAVAILABLE":
                raise ValueError("UNAVAILABLE observation requires UNAVAILABLE reconciliation")
            if self.duplicate_execution_incident or self.unprotected_execution_incident:
                raise ValueError("UNAVAILABLE observation cannot claim execution incidents")
        elif self.reconciliation_outcome == "UNAVAILABLE":
            raise ValueError("EVALUATED observation cannot use UNAVAILABLE reconciliation")


@dataclass(frozen=True, slots=True)
class VenueShadowScorecardPolicy:
    min_paired_opportunities: int
    min_metric_pairs: int
    max_lighter_cost_delta_bps: Decimal
    max_lighter_ack_latency_delta_ms: Decimal
    max_lighter_fill_slippage_delta_bps: Decimal
    max_lighter_protection_latency_delta_ms: Decimal
    max_lighter_ambiguity_rate_delta: Decimal
    max_lighter_unavailable_rate: Decimal

    def __post_init__(self) -> None:
        for field in ("min_paired_opportunities", "min_metric_pairs"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be a positive integer")
        for field in (
            "max_lighter_cost_delta_bps",
            "max_lighter_ack_latency_delta_ms",
            "max_lighter_fill_slippage_delta_bps",
            "max_lighter_protection_latency_delta_ms",
            "max_lighter_ambiguity_rate_delta",
            "max_lighter_unavailable_rate",
        ):
            value = getattr(self, field)
            _require_decimal(field, value)
            if value < _ZERO:
                raise ValueError(f"{field} cannot be negative")
        if self.max_lighter_ambiguity_rate_delta > _ONE:
            raise ValueError("max_lighter_ambiguity_rate_delta must be <= 1")
        if self.max_lighter_unavailable_rate > _ONE:
            raise ValueError("max_lighter_unavailable_rate must be <= 1")


@dataclass(frozen=True, slots=True)
class VenueMetricSummary:
    total_cost_bps: Decimal | None
    ack_latency_ms: Decimal | None
    fill_slippage_bps: Decimal | None
    protection_latency_ms: Decimal | None
    ambiguity_rate: Decimal | None
    unavailable_rate: Decimal
    duplicate_execution_incidents: int
    unprotected_execution_incidents: int


@dataclass(frozen=True, slots=True)
class VenueMetricDelta:
    total_cost_bps: Decimal | None
    ack_latency_ms: Decimal | None
    fill_slippage_bps: Decimal | None
    protection_latency_ms: Decimal | None
    ambiguity_rate: Decimal | None


@dataclass(frozen=True, slots=True)
class VenueMetricPairCounts:
    total_cost_bps: int
    ack_latency_ms: int
    fill_slippage_bps: int
    protection_latency_ms: int
    ambiguity_rate: int


@dataclass(frozen=True, slots=True)
class VenueShadowScorecardResult:
    status: VenueShadowStatus
    reasons: tuple[str, ...]
    paired_opportunities: int
    bybit: VenueMetricSummary
    lighter: VenueMetricSummary
    lighter_minus_bybit: VenueMetricDelta
    metric_pairs: VenueMetricPairCounts
    weighted_score: None
    eligible_for_testnet: bool


def evaluate_venue_shadow_scorecard(
    observations: Iterable[VenueShadowObservation],
    *,
    policy: VenueShadowScorecardPolicy,
) -> VenueShadowScorecardResult:
    if not isinstance(policy, VenueShadowScorecardPolicy):
        raise ValueError("policy must be VenueShadowScorecardPolicy")
    rows = tuple(observations)
    if any(not isinstance(row, VenueShadowObservation) for row in rows):
        raise ValueError("observations must contain VenueShadowObservation values")

    pairs = _pair_rows(rows)
    bybit_rows = tuple(pair[0] for pair in pairs)
    lighter_rows = tuple(pair[1] for pair in pairs)
    paired_count = len(pairs)

    bybit = _venue_summary(bybit_rows, paired_count)
    lighter = _venue_summary(lighter_rows, paired_count)
    deltas, pair_counts = _paired_deltas(pairs)

    fail_reasons: list[str] = []
    insufficient_reasons: list[str] = []

    if paired_count < policy.min_paired_opportunities:
        insufficient_reasons.append("PAIRED_OPPORTUNITY_SAMPLE_INSUFFICIENT")

    if lighter.duplicate_execution_incidents:
        fail_reasons.append("LIGHTER_DUPLICATE_EXECUTION_INCIDENT")
    if lighter.unprotected_execution_incidents:
        fail_reasons.append("LIGHTER_UNPROTECTED_EXECUTION_INCIDENT")
    if lighter.unavailable_rate > policy.max_lighter_unavailable_rate:
        fail_reasons.append("LIGHTER_UNAVAILABLE_RATE_EXCEEDED")

    dimensions = (
        (
            "TOTAL_COST_SAMPLE_INSUFFICIENT",
            pair_counts.total_cost_bps,
            deltas.total_cost_bps,
            policy.max_lighter_cost_delta_bps,
            "LIGHTER_COST_DELTA_EXCEEDED",
        ),
        (
            "ACK_LATENCY_SAMPLE_INSUFFICIENT",
            pair_counts.ack_latency_ms,
            deltas.ack_latency_ms,
            policy.max_lighter_ack_latency_delta_ms,
            "LIGHTER_ACK_LATENCY_DELTA_EXCEEDED",
        ),
        (
            "FILL_SLIPPAGE_SAMPLE_INSUFFICIENT",
            pair_counts.fill_slippage_bps,
            deltas.fill_slippage_bps,
            policy.max_lighter_fill_slippage_delta_bps,
            "LIGHTER_FILL_SLIPPAGE_DELTA_EXCEEDED",
        ),
        (
            "PROTECTION_LATENCY_SAMPLE_INSUFFICIENT",
            pair_counts.protection_latency_ms,
            deltas.protection_latency_ms,
            policy.max_lighter_protection_latency_delta_ms,
            "LIGHTER_PROTECTION_LATENCY_DELTA_EXCEEDED",
        ),
        (
            "RECONCILIATION_SAMPLE_INSUFFICIENT",
            pair_counts.ambiguity_rate,
            deltas.ambiguity_rate,
            policy.max_lighter_ambiguity_rate_delta,
            "LIGHTER_AMBIGUITY_DELTA_EXCEEDED",
        ),
    )
    for insufficient_code, sample, delta, threshold, fail_code in dimensions:
        if sample < policy.min_metric_pairs or delta is None:
            insufficient_reasons.append(insufficient_code)
        elif delta > threshold:
            fail_reasons.append(fail_code)

    if fail_reasons:
        status = VenueShadowStatus.FAIL_EVIDENCE
        reasons = tuple(dict.fromkeys(fail_reasons + insufficient_reasons))
    elif insufficient_reasons:
        status = VenueShadowStatus.INSUFFICIENT_EVIDENCE
        reasons = tuple(dict.fromkeys(insufficient_reasons))
    else:
        status = VenueShadowStatus.PASS_EVIDENCE
        reasons = ("ALL_COMPONENT_GATES_PASS",)

    return VenueShadowScorecardResult(
        status=status,
        reasons=reasons,
        paired_opportunities=paired_count,
        bybit=bybit,
        lighter=lighter,
        lighter_minus_bybit=deltas,
        metric_pairs=pair_counts,
        weighted_score=None,
        eligible_for_testnet=status is VenueShadowStatus.PASS_EVIDENCE,
    )


def _pair_rows(
    rows: tuple[VenueShadowObservation, ...],
) -> tuple[tuple[VenueShadowObservation, VenueShadowObservation], ...]:
    grouped: dict[str, list[VenueShadowObservation]] = defaultdict(list)
    for row in rows:
        grouped[row.opportunity_key].append(row)

    pairs: list[tuple[VenueShadowObservation, VenueShadowObservation]] = []
    for opportunity_key in sorted(grouped):
        group = grouped[opportunity_key]
        by_venue = {venue: [row for row in group if row.venue == venue] for venue in _VENUES}
        if any(len(by_venue[venue]) != 1 for venue in _VENUES) or len(group) != 2:
            raise ValueError(
                f"opportunity {opportunity_key!r} requires exactly one BYBIT and one LIGHTER observation"
            )
        bybit = by_venue["BYBIT"][0]
        lighter = by_venue["LIGHTER"][0]
        if bybit.market_snapshot_hash != lighter.market_snapshot_hash:
            raise ValueError(
                f"opportunity {opportunity_key!r} must use the same market snapshot"
            )
        pairs.append((bybit, lighter))
    return tuple(pairs)


def _venue_summary(
    rows: tuple[VenueShadowObservation, ...],
    paired_count: int,
) -> VenueMetricSummary:
    evaluated = tuple(row for row in rows if row.status == "EVALUATED")
    return VenueMetricSummary(
        total_cost_bps=_mean(row.total_cost_bps for row in evaluated),
        ack_latency_ms=_mean(row.ack_latency_ms for row in evaluated),
        fill_slippage_bps=_mean(row.fill_slippage_bps for row in evaluated),
        protection_latency_ms=_mean(row.protection_latency_ms for row in evaluated),
        ambiguity_rate=_ambiguity_rate(evaluated),
        unavailable_rate=(
            Decimal(sum(row.status == "UNAVAILABLE" for row in rows)) / Decimal(paired_count)
            if paired_count
            else _ZERO
        ),
        duplicate_execution_incidents=sum(row.duplicate_execution_incident for row in rows),
        unprotected_execution_incidents=sum(row.unprotected_execution_incident for row in rows),
    )


def _paired_deltas(
    pairs: tuple[tuple[VenueShadowObservation, VenueShadowObservation], ...],
) -> tuple[VenueMetricDelta, VenueMetricPairCounts]:
    metric_names = (
        "total_cost_bps",
        "ack_latency_ms",
        "fill_slippage_bps",
        "protection_latency_ms",
    )
    deltas: dict[str, Decimal | None] = {}
    counts: dict[str, int] = {}
    for name in metric_names:
        values = [
            getattr(lighter, name) - getattr(bybit, name)
            for bybit, lighter in pairs
            if bybit.status == "EVALUATED"
            and lighter.status == "EVALUATED"
            and getattr(bybit, name) is not None
            and getattr(lighter, name) is not None
        ]
        counts[name] = len(values)
        deltas[name] = _mean(values)

    ambiguity_values = [
        _ambiguity_value(lighter) - _ambiguity_value(bybit)
        for bybit, lighter in pairs
        if bybit.status == "EVALUATED" and lighter.status == "EVALUATED"
    ]
    counts["ambiguity_rate"] = len(ambiguity_values)
    ambiguity_delta = _mean(ambiguity_values)

    return (
        VenueMetricDelta(
            total_cost_bps=deltas["total_cost_bps"],
            ack_latency_ms=deltas["ack_latency_ms"],
            fill_slippage_bps=deltas["fill_slippage_bps"],
            protection_latency_ms=deltas["protection_latency_ms"],
            ambiguity_rate=ambiguity_delta,
        ),
        VenueMetricPairCounts(
            total_cost_bps=counts["total_cost_bps"],
            ack_latency_ms=counts["ack_latency_ms"],
            fill_slippage_bps=counts["fill_slippage_bps"],
            protection_latency_ms=counts["protection_latency_ms"],
            ambiguity_rate=counts["ambiguity_rate"],
        ),
    )


def _ambiguity_value(row: VenueShadowObservation) -> Decimal:
    return _ZERO if row.reconciliation_outcome == "EXACT" else _ONE


def _ambiguity_rate(rows: tuple[VenueShadowObservation, ...]) -> Decimal | None:
    if not rows:
        return None
    return sum((_ambiguity_value(row) for row in rows), start=_ZERO) / Decimal(len(rows))


def _mean(values: Iterable[Decimal | None]) -> Decimal | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present, start=_ZERO) / Decimal(len(present))


def _require_sha256(field: str, value: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field} must be a 64-character SHA-256 identity")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be a hexadecimal SHA-256 identity") from exc


def _require_decimal(field: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")


def _require_optional_decimal(field: str, value: Decimal | None) -> None:
    if value is not None:
        _require_decimal(field, value)


__all__ = [
    "VenueMetricDelta",
    "VenueMetricPairCounts",
    "VenueMetricSummary",
    "VenueShadowObservation",
    "VenueShadowScorecardPolicy",
    "VenueShadowScorecardResult",
    "VenueShadowStatus",
    "evaluate_venue_shadow_scorecard",
]
