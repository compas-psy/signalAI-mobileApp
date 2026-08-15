"""Offline runtime adapter for persisted Rosstat producer prices -> SPREAD.

The deterministic SPREAD engine works in quarters: four periods are one year
and twelve periods are its calibration floor. Rosstat producer-price rows are
monthly, so this module is the deliberately strict boundary between the two.
It never forward-fills a leg, never substitutes zero, and never exposes an
observation to the engine before the observation's persisted ``tradable_at``.

Production issuer baskets intentionally remain empty until a green live-source
probe validates exact current OKPD2/OKEI series and their economic exposure.
Tests inject explicit fixture baskets; runtime code must never infer one from a
human-readable product label.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ResearchObservation
from ..models.enums import EvidenceRole, ResearchDirection
from .adapters import rosstat_prices
from .engines import spread
from .fusion import Falsifier, SignalInput
from .issuers import REGISTRY
from .market_context import for_hypothesis
from .pipeline import run as run_pipeline
from .scoring import EvidenceItem

Side = Literal["product", "input"]


@dataclass(frozen=True, slots=True)
class SpreadLegConfig:
    observation_type: str
    unit: str
    coefficient: Decimal
    rationale: str
    side: Side

    def __post_init__(self) -> None:
        if not self.observation_type:
            raise ValueError("SPREAD leg requires observation_type")
        if not self.unit:
            raise ValueError("SPREAD leg requires unit")
        if self.coefficient <= 0:
            raise ValueError("SPREAD leg coefficient must be positive")
        if not self.rationale.strip():
            raise ValueError("SPREAD leg coefficient requires rationale")
        if self.side not in ("product", "input"):
            raise ValueError("SPREAD leg side must be product or input")


@dataclass(frozen=True, slots=True)
class SpreadBasket:
    basket_id: str
    issuers: tuple[str, ...]
    legs: tuple[SpreadLegConfig, ...]
    revenue_coverage: float
    cost_coverage: float
    calibrated: bool = True
    contract_lag_periods: int = 0
    hedged: bool = False
    vertically_integrated: bool = False

    def __post_init__(self) -> None:
        if not self.basket_id.strip():
            raise ValueError("SPREAD basket requires basket_id")
        if not self.legs:
            raise ValueError("SPREAD basket requires at least one leg")
        types = [leg.observation_type for leg in self.legs]
        if len(types) != len(set(types)):
            raise ValueError("SPREAD basket observation types must be unique")
        if not any(leg.side == "product" for leg in self.legs):
            raise ValueError("SPREAD basket requires a product leg")
        if not any(leg.side == "input" for leg in self.legs):
            raise ValueError("SPREAD basket requires an input leg")
        if not 0 <= self.revenue_coverage <= 1:
            raise ValueError("revenue_coverage must be between 0 and 1")
        if not 0 <= self.cost_coverage <= 1:
            raise ValueError("cost_coverage must be between 0 and 1")
        if self.contract_lag_periods < 0:
            raise ValueError("contract_lag_periods cannot be negative")


@dataclass
class SpreadPreparation:
    periods: list[spread.Period] = field(default_factory=list)
    observation_ids: tuple[str, ...] = ()
    lineage_roots: tuple[str, ...] = ()
    reason_codes: list[str] = field(default_factory=list)


@dataclass
class SpreadRunReport:
    baskets: int = 0
    complete_quarters: int = 0
    signals: int = 0
    hypotheses: int = 0
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"SPREAD корзин {self.baskets}",
            f"полных кварталов {self.complete_quarters}",
            f"сигналов {self.signals}",
            f"гипотез {self.hypotheses}",
        ]
        if self.skipped:
            parts.append(f"пропущено {len(self.skipped)}: {self.skipped[0]}")
        return ", ".join(parts)


# Deliberately empty until the post-green live Rosstat probe validates exact
# machine series, coefficient rationale and issuer exposure.
PRODUCTION_BASKETS: tuple[SpreadBasket, ...] = ()


def _add_reason(reasons: list[str], code: str) -> None:
    if code not in reasons:
        reasons.append(code)


def _quarter(year: int, month: int) -> tuple[int, int]:
    return year, (month - 1) // 3 + 1


def _quarter_months(quarter: int) -> tuple[int, int, int]:
    first = (quarter - 1) * 3 + 1
    return first, first + 1, first + 2


def prepare_periods(
    session: Session,
    basket: SpreadBasket,
    *,
    as_of: datetime,
) -> SpreadPreparation:
    """Build complete calendar-quarter averages visible at ``as_of``.

    A quarter is all-or-nothing. Every configured leg must have exactly one
    visible numeric observation with the configured unit in all three calendar
    months. This protects the engine from accidental monthly semantics and from
    optimistic partial baskets.
    """
    required = {leg.observation_type: leg for leg in basket.legs}
    rows = list(
        session.execute(
            select(ResearchObservation)
            .where(
                ResearchObservation.source_id == rosstat_prices.SOURCE_ID,
                ResearchObservation.entity_id == "RU",
                ResearchObservation.observation_type.in_(tuple(required)),
            )
            .order_by(
                ResearchObservation.period_start.asc(),
                ResearchObservation.observation_type.asc(),
                ResearchObservation.id.asc(),
            )
        ).scalars()
    )

    reasons: list[str] = []
    usable: dict[tuple[int, int, str], list[ResearchObservation]] = defaultdict(list)
    visible_types: set[str] = set()
    quarter_keys: set[tuple[int, int]] = set()

    for row in rows:
        if row.tradable_at is None or row.tradable_at > as_of:
            _add_reason(reasons, "spread_observation_not_yet_tradable")
            continue
        leg = required[row.observation_type]
        visible_types.add(row.observation_type)
        if row.unit != leg.unit:
            _add_reason(reasons, "spread_unit_mismatch")
            continue
        if row.value_numeric is None:
            _add_reason(reasons, "spread_missing_numeric_value")
            continue
        period = row.period_start or row.period_end
        if period is None:
            _add_reason(reasons, "spread_missing_period")
            continue
        year, quarter = _quarter(period.year, period.month)
        quarter_keys.add((year, quarter))
        usable[(period.year, period.month, row.observation_type)].append(row)

    if set(required) - visible_types:
        _add_reason(reasons, "spread_missing_series")

    periods: list[spread.Period] = []
    used_ids: list[str] = []
    roots: set[str] = set()
    for year, quarter in sorted(quarter_keys):
        months = _quarter_months(quarter)
        product_legs: list[spread.Leg] = []
        input_legs: list[spread.Leg] = []
        quarter_rows: list[ResearchObservation] = []
        complete = True

        for config in basket.legs:
            monthly: list[ResearchObservation] = []
            for month in months:
                candidates = usable.get((year, month, config.observation_type), [])
                if len(candidates) != 1:
                    if len(candidates) > 1:
                        _add_reason(reasons, "spread_duplicate_month")
                    complete = False
                    break
                monthly.append(candidates[0])
            if not complete:
                break

            average = sum(
                (Decimal(row.value_numeric) for row in monthly), Decimal(0)
            ) / Decimal(3)
            leg = spread.Leg(
                metric=config.observation_type,
                price=average,
                coefficient=config.coefficient,
            )
            if config.side == "product":
                product_legs.append(leg)
            else:
                input_legs.append(leg)
            quarter_rows.extend(monthly)

        if not complete:
            _add_reason(reasons, "spread_incomplete_quarter")
            continue

        periods.append(
            spread.Period(
                period=f"{year}-Q{quarter}",
                products=tuple(product_legs),
                inputs=tuple(input_legs),
            )
        )
        used_ids.extend(str(row.id) for row in quarter_rows)
        roots.update(row.lineage_root_id for row in quarter_rows if row.lineage_root_id)

    return SpreadPreparation(
        periods=periods,
        observation_ids=tuple(used_ids),
        lineage_roots=tuple(sorted(roots)),
        reason_codes=reasons,
    )


def _issuer(secid: str):
    return next((item for item in REGISTRY if item.secid == secid), None)


def _evidence(prepared: SpreadPreparation, confidence: Decimal) -> tuple[EvidenceItem, ...]:
    quality = max(0.0, min(1.0, float(confidence)))
    return tuple(
        EvidenceItem(
            lineage_root_id=root,
            data_type="producer_price_spread",
            role=EvidenceRole.SUPPORT,
            source_quality=0.95,
            freshness=1.0,
            data_quality=quality,
        )
        for root in prepared.lineage_roots
    )


def _direction(value: str) -> ResearchDirection | None:
    if value == "positive":
        return ResearchDirection.POSITIVE
    if value == "negative":
        return ResearchDirection.NEGATIVE
    return None


def run_spread(
    session: Session,
    *,
    baskets: tuple[SpreadBasket, ...] | None = None,
    now: datetime | None = None,
) -> SpreadRunReport:
    """Evaluate configured baskets and hand valid signals to common fusion.

    Passing ``baskets`` is mainly for deterministic tests and future explicit
    manual runs. Normal production calls use ``PRODUCTION_BASKETS``, which is
    intentionally empty until a separate live-source validation change.
    """
    moment = now or datetime.now(UTC)
    configured = PRODUCTION_BASKETS if baskets is None else baskets
    report = SpreadRunReport(baskets=len(configured))
    if not configured:
        report.skipped.append("SPREAD: production baskets not configured")
        return report

    for basket in configured:
        prepared = prepare_periods(session, basket, as_of=moment)
        report.complete_quarters += len(prepared.periods)

        if basket.calibrated and len(prepared.periods) < spread.MIN_QUARTERS:
            codes = ",".join(prepared.reason_codes) or "spread_uncalibrated_proxy"
            report.skipped.append(
                f"{basket.basket_id}: spread_uncalibrated_proxy "
                f"({len(prepared.periods)}/{spread.MIN_QUARTERS} complete quarters; {codes})"
            )
            continue

        result = spread.evaluate(
            prepared.periods,
            revenue_coverage=basket.revenue_coverage,
            cost_coverage=basket.cost_coverage,
            calibrated=basket.calibrated,
            contract_lag_periods=basket.contract_lag_periods,
            hedged=basket.hedged,
            vertically_integrated=basket.vertically_integrated,
        )
        research_direction = _direction(result.direction)
        if not result.applicable or research_direction is None:
            codes = ",".join((*prepared.reason_codes, *result.reason_codes))
            report.skipped.append(
                f"{basket.basket_id}: {codes or 'spread_no_signal'} — {result.detail}"
            )
            continue
        if not basket.issuers:
            report.skipped.append(f"{basket.basket_id}: spread_no_issuer_exposure")
            continue

        signals: list[SignalInput] = []
        issuer_confidence: dict[str, Decimal] = {}
        for secid in basket.issuers:
            issuer = _issuer(secid)
            if issuer is None:
                report.skipped.append(f"{basket.basket_id}: unknown issuer {secid}")
                continue
            issuer_confidence[secid] = issuer.confidence
            signals.append(
                SignalInput(
                    strategy_key=spread.STRATEGY_KEY,
                    entity_id=secid,
                    instrument_id=f"MOEX:EQ:{secid}",
                    direction=research_direction,
                    strength=result.strength,
                    target_kpi_family="operating_margin",
                    causal_driver=f"producer_price_spread:{basket.basket_id}",
                    window_from_days=90 * basket.contract_lag_periods,
                    window_to_days=365 + 90 * basket.contract_lag_periods,
                    evidence=_evidence(prepared, result.confidence),
                    reason_codes=tuple((*prepared.reason_codes, *result.reason_codes)),
                    detail=f"{basket.basket_id}: {result.detail}",
                )
            )

        report.signals += len(signals)
        if not signals:
            continue

        def resolve(bucket: list[SignalInput]) -> dict:
            head = bucket[0]
            confidence = issuer_confidence[head.entity_id]
            market = for_hypothesis(
                session,
                instrument_id=head.instrument_id,
                direction=head.direction,
            )
            return {
                # One official dataset is evidence for an early candidate,
                # never enough by itself for the 3-2-1 confirmation gate.
                "confirmations": 1,
                "entity_confidence": confidence,
                "effect_size": float(abs(head.strength)),
                "exposure_confidence": min(
                    float(confidence), float(result.confidence)
                ),
                "falsifiers": [
                    Falsifier(
                        description=(
                            "producer-price spread returns to its year-ago "
                            "baseline for two consecutive quarters"
                        ),
                        metric_or_event="producer_price_spread",
                        operator="<=",
                        threshold=0.0,
                        check_frequency="P3M",
                    )
                ],
                "market_context": market.score,
                "market_context_state": market.state,
                "market_context_detail": market.detail,
            }

        outcome = run_pipeline(session, signals, resolve=resolve, now=moment)
        report.hypotheses += outcome.created + outcome.updated

    return report


__all__ = [
    "PRODUCTION_BASKETS",
    "SpreadBasket",
    "SpreadLegConfig",
    "SpreadPreparation",
    "SpreadRunReport",
    "prepare_periods",
    "run_spread",
]
