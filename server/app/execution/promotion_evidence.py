"""Authoritative, fail-closed promotion-evidence reader and writer (B5 / R6).

Only immutable server facts are evaluated here.  This module deliberately has
no mobile input, credential access, provider call or mode-changing behavior.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.promotion_evidence import (
    PromotionEvidenceDecision,
    PromotionEvidenceSnapshot,
)
from .enums import ExecutionLifecycleMode

if TYPE_CHECKING:
    from .promotion_guard import PromotionDecision, PromotionEvidence


class PromotionEvidenceInputError(ValueError):
    """A server evidence fact is malformed and therefore cannot be trusted."""


class PromotionEvidenceKind(StrEnum):
    TECHNICAL = "TECHNICAL"
    PERFORMANCE = "PERFORMANCE"
    OPERATIONS = "OPERATIONS"


class PromotionVenueCapability(StrEnum):
    """Known capability facts; neither value permits LIVE/mainnet activation."""

    TINVEST_STOP_ENTRY_UNSUPPORTED = "TINVEST_STOP_ENTRY_UNSUPPORTED"
    LIGHTER_TESTNET_ACTIONS_ONLY = "LIGHTER_TESTNET_ACTIONS_ONLY"


def _hash(value: str, field: str) -> str:
    value = value.strip().lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise PromotionEvidenceInputError(f"{field} must be a SHA-256 hex digest")
    return value


def _non_empty(value: str, field: str, maximum: int) -> str:
    value = value.strip()
    if not value or len(value) > maximum:
        raise PromotionEvidenceInputError(f"{field} is required and too long")
    return value


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PromotionEvidenceInputError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class PromotionEvidenceScope:
    strategy_family: str
    strategy_version: str
    venue: str
    source_hash: str
    config_hash: str
    policy_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "strategy_family",
            _non_empty(self.strategy_family, "strategy_family", 32),
        )
        object.__setattr__(
            self,
            "strategy_version",
            _non_empty(self.strategy_version, "strategy_version", 64),
        )
        object.__setattr__(self, "venue", _non_empty(self.venue, "venue", 32).upper())
        object.__setattr__(self, "source_hash", _hash(self.source_hash, "source_hash"))
        object.__setattr__(self, "config_hash", _hash(self.config_hash, "config_hash"))
        object.__setattr__(self, "policy_hash", _hash(self.policy_hash, "policy_hash"))


@dataclass(frozen=True)
class PromotionEvidenceSnapshotInput:
    kind: PromotionEvidenceKind
    scope: PromotionEvidenceScope
    source: str
    evidence_version: int
    observed_at: datetime
    fresh_until: datetime
    sample_size: int
    error_count: int
    gate_passed: bool
    reconciliation_verified: bool
    protection_verified: bool
    kill_switch_verified: bool
    capability: PromotionVenueCapability | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", PromotionEvidenceKind(self.kind))
        object.__setattr__(self, "source", _non_empty(self.source, "source", 64))
        observed_at = _aware(self.observed_at, "observed_at")
        fresh_until = _aware(self.fresh_until, "fresh_until")
        if self.evidence_version < 1:
            raise PromotionEvidenceInputError("evidence_version must be positive")
        if (
            self.sample_size < 0
            or self.error_count < 0
            or self.error_count > self.sample_size
        ):
            raise PromotionEvidenceInputError("error_count must be within sample_size")
        if fresh_until < observed_at:
            raise PromotionEvidenceInputError("fresh_until must not precede observed_at")
        if observed_at > datetime.now(UTC):
            raise PromotionEvidenceInputError("observed_at must not be in the future")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "fresh_until", fresh_until)
        if self.capability is not None:
            object.__setattr__(
                self, "capability", PromotionVenueCapability(self.capability)
            )


@dataclass(frozen=True)
class PromotionEvidenceSnapshotFact:
    snapshot_id: str
    kind: PromotionEvidenceKind
    scope: PromotionEvidenceScope
    source: str
    evidence_version: int
    observed_at: datetime
    fresh_until: datetime
    sample_size: int
    error_count: int
    gate_passed: bool
    reconciliation_verified: bool
    protection_verified: bool
    kill_switch_verified: bool
    capability: PromotionVenueCapability | None = None

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise PromotionEvidenceInputError("snapshot_id is required")
        validated = PromotionEvidenceSnapshotInput(
            kind=self.kind,
            scope=self.scope,
            source=self.source,
            evidence_version=self.evidence_version,
            observed_at=self.observed_at,
            fresh_until=self.fresh_until,
            sample_size=self.sample_size,
            error_count=self.error_count,
            gate_passed=self.gate_passed,
            reconciliation_verified=self.reconciliation_verified,
            protection_verified=self.protection_verified,
            kill_switch_verified=self.kill_switch_verified,
            capability=self.capability,
        )
        object.__setattr__(self, "kind", validated.kind)
        object.__setattr__(self, "source", validated.source)
        object.__setattr__(self, "observed_at", validated.observed_at)
        object.__setattr__(self, "fresh_until", validated.fresh_until)
        object.__setattr__(self, "capability", validated.capability)


def persist_promotion_evidence_snapshot(
    db: Session, evidence: PromotionEvidenceSnapshotInput
) -> PromotionEvidenceSnapshot:
    """Append a trusted server fact; callers cannot mutate a previous one."""

    row = PromotionEvidenceSnapshot(
        kind=evidence.kind.value,
        evidence_version=evidence.evidence_version,
        source=evidence.source,
        strategy_family=evidence.scope.strategy_family,
        strategy_version=evidence.scope.strategy_version,
        venue=evidence.scope.venue,
        source_hash=evidence.scope.source_hash,
        config_hash=evidence.scope.config_hash,
        policy_hash=evidence.scope.policy_hash,
        observed_at=evidence.observed_at,
        fresh_until=evidence.fresh_until,
        sample_size=evidence.sample_size,
        error_count=evidence.error_count,
        gate_passed=evidence.gate_passed,
        reconciliation_verified=evidence.reconciliation_verified,
        protection_verified=evidence.protection_verified,
        kill_switch_verified=evidence.kill_switch_verified,
        capability=(evidence.capability.value if evidence.capability else None),
    )
    db.add(row)
    db.flush()
    return row


def _fact(row: PromotionEvidenceSnapshot) -> PromotionEvidenceSnapshotFact:
    return PromotionEvidenceSnapshotFact(
        snapshot_id=str(row.id),
        kind=PromotionEvidenceKind(row.kind),
        scope=PromotionEvidenceScope(
            strategy_family=row.strategy_family,
            strategy_version=row.strategy_version,
            venue=row.venue,
            source_hash=row.source_hash,
            config_hash=row.config_hash,
            policy_hash=row.policy_hash,
        ),
        source=row.source,
        evidence_version=row.evidence_version,
        observed_at=row.observed_at,
        fresh_until=row.fresh_until,
        sample_size=row.sample_size,
        error_count=row.error_count,
        gate_passed=row.gate_passed,
        reconciliation_verified=row.reconciliation_verified,
        protection_verified=row.protection_verified,
        kill_switch_verified=row.kill_switch_verified,
        capability=(PromotionVenueCapability(row.capability) if row.capability else None),
    )


def _category_ready(
    kind: PromotionEvidenceKind,
    scope: PromotionEvidenceScope,
    facts: tuple[PromotionEvidenceSnapshotFact, ...],
    now: datetime,
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    candidates = tuple(fact for fact in facts if fact.kind == kind)
    label = kind.value.lower()
    if not candidates:
        return False, (f"{label} evidence missing",), ()
    matching = tuple(fact for fact in candidates if fact.scope == scope)
    if not matching:
        return False, (f"{label} evidence provenance mismatch",), ()
    latest = max(matching, key=lambda fact: fact.observed_at)
    snapshot_ids = (latest.snapshot_id,)
    if latest.observed_at > now:
        return False, (f"{label} evidence observed in the future",), snapshot_ids
    if latest.fresh_until < now:
        return False, (f"{label} evidence stale",), snapshot_ids
    if latest.sample_size == 0:
        return False, (f"{label} evidence has no sample",), snapshot_ids
    if not latest.gate_passed:
        return False, (f"{label} evidence gate failed",), snapshot_ids
    required: list[tuple[bool, str]] = []
    if kind in (PromotionEvidenceKind.TECHNICAL, PromotionEvidenceKind.OPERATIONS):
        required.append((latest.reconciliation_verified, "reconciliation"))
    if kind == PromotionEvidenceKind.TECHNICAL:
        required.append((latest.protection_verified, "protection"))
    if kind == PromotionEvidenceKind.OPERATIONS:
        required.append((latest.kill_switch_verified, "kill-switch"))
    for verified, name in required:
        if not verified:
            return False, (f"{label} evidence {name} not verified",), snapshot_ids
    return True, (), snapshot_ids


def _venue_capability_notes(
    scope: PromotionEvidenceScope,
    target: ExecutionLifecycleMode,
    facts: tuple[PromotionEvidenceSnapshotFact, ...],
    now: datetime,
) -> tuple[str, ...]:
    capabilities = {
        fact.capability
        for fact in facts
        if (
            fact.kind == PromotionEvidenceKind.TECHNICAL
            and fact.scope == scope
            and fact.observed_at <= now <= fact.fresh_until
        )
    }
    if scope.venue == "TINVEST":
        if PromotionVenueCapability.TINVEST_STOP_ENTRY_UNSUPPORTED not in capabilities:
            return ("T-Invest stop-entry capability evidence missing",)
        return ("T-Invest stop-entry capability is unsupported",)
    if scope.venue == "LIGHTER":
        if PromotionVenueCapability.LIGHTER_TESTNET_ACTIONS_ONLY not in capabilities:
            return ("Lighter testnet-actions-only capability evidence missing",)
        if target == ExecutionLifecycleMode.SANDBOX:
            return ()
        return ("Lighter testnet actions are not LIVE/mainnet activation evidence",)
    return ()


def evaluate_persisted_promotion_evidence(
    *,
    scope: PromotionEvidenceScope,
    target: ExecutionLifecycleMode,
    facts: Iterable[PromotionEvidenceSnapshotFact],
    now: datetime | None = None,
) -> "PromotionEvidence":
    """Evaluate typed facts only; absence, staleness and mismatch stay blocked."""

    from .promotion_guard import PromotionEvidence

    target = ExecutionLifecycleMode(target)
    evaluated_at = _aware(now or datetime.now(UTC), "now")
    facts_tuple = tuple(facts)
    technical_ok, technical_notes, technical_ids = _category_ready(
        PromotionEvidenceKind.TECHNICAL, scope, facts_tuple, evaluated_at
    )
    performance_ok, performance_notes, performance_ids = _category_ready(
        PromotionEvidenceKind.PERFORMANCE, scope, facts_tuple, evaluated_at
    )
    ops_ok, ops_notes, ops_ids = _category_ready(
        PromotionEvidenceKind.OPERATIONS, scope, facts_tuple, evaluated_at
    )
    capability_notes = _venue_capability_notes(scope, target, facts_tuple, evaluated_at)
    return PromotionEvidence(
        technical_sandbox_ready=technical_ok and not capability_notes,
        adr_gates_passed=(
            technical_ok and performance_ok and ops_ok and not capability_notes
        ),
        performance_gates_passed=performance_ok,
        ops_gates_passed=ops_ok,
        notes=technical_notes + performance_notes + ops_notes + capability_notes,
        snapshot_ids=technical_ids + performance_ids + ops_ids,
    )


def current_persisted_promotion_evidence(
    db: Session,
    *,
    scope: PromotionEvidenceScope,
    target: ExecutionLifecycleMode,
    now: datetime | None = None,
) -> "PromotionEvidence":
    """Load only server-persisted facts near the exact strategy/venue scope."""

    rows = db.execute(
        select(PromotionEvidenceSnapshot).where(
            PromotionEvidenceSnapshot.strategy_family == scope.strategy_family,
            PromotionEvidenceSnapshot.strategy_version == scope.strategy_version,
            PromotionEvidenceSnapshot.venue == scope.venue,
        )
    ).scalars()
    return evaluate_persisted_promotion_evidence(
        scope=scope,
        target=target,
        facts=tuple(_fact(row) for row in rows),
        now=now,
    )


def record_promotion_evidence_decision(
    db: Session,
    *,
    decision: "PromotionDecision",
    scope: PromotionEvidenceScope | None,
    actor: str = "promotion-guard",
) -> str:
    """Persist the non-secret decision correlation before a possible mode event."""

    correlation_id = secrets.token_hex(16)
    db.add(
        PromotionEvidenceDecision(
            correlation_id=correlation_id,
            actor=_non_empty(actor, "actor", 64),
            current_mode=decision.current.value,
            target_mode=decision.target.value,
            allowed=decision.allowed,
            blockers_json=list(decision.blockers),
            evidence_snapshot_ids_json=list(decision.evidence_snapshot_ids),
            strategy_family=(scope.strategy_family if scope else None),
            strategy_version=(scope.strategy_version if scope else None),
            venue=(scope.venue if scope else None),
            source_hash=(scope.source_hash if scope else None),
            config_hash=(scope.config_hash if scope else None),
            policy_hash=(scope.policy_hash if scope else None),
        )
    )
    db.flush()
    return correlation_id


@dataclass(frozen=True)
class PromotionEvidenceCollectionReport:
    """Append-only measurement result; it does not grant a promotion policy."""

    scope: PromotionEvidenceScope
    snapshot_count: int
    technical_samples: int
    performance_samples: int
    operations_samples: int


def collect_promotion_evidence(
    db: Session,
    *,
    scope: PromotionEvidenceScope,
    observed_at: datetime | None = None,
) -> PromotionEvidenceCollectionReport:
    """Materialise server-observed measurement/ops/execution facts for one scope.

    This collector intentionally records only facts already persisted by server
    workers.  It does not accept mobile flags, contact a venue, load a secret,
    or infer an approval policy from a non-empty count.  The resulting facts
    therefore remain fail-closed until a future policy slice establishes the
    required gate semantics.
    """

    observed = _aware(observed_at or datetime.now(UTC), "observed_at")
    if observed > datetime.now(UTC):
        raise PromotionEvidenceInputError("observed_at must not be in the future")
    fresh_until = observed + timedelta(minutes=15)
    # Existing execution, paper and Lighter reconciliation rows lack the full
    # server-owned strategy/venue/environment provenance key.  Projecting any
    # of their global counts into a promotion scope would create false proof,
    # so they deliberately contribute no scoped samples until such facts exist.
    technical_samples = 0
    performance_samples = 0
    operations_samples = 0
    protection_samples = 0
    operation_errors = 0
    inputs = (
        PromotionEvidenceSnapshotInput(
            kind=PromotionEvidenceKind.TECHNICAL,
            scope=scope,
            source="promotion-evidence-collector/v1",
            evidence_version=1,
            observed_at=observed,
            fresh_until=fresh_until,
            sample_size=technical_samples,
            error_count=0,
            # Counts establish provenance, not a promotion policy.
            gate_passed=False,
            reconciliation_verified=technical_samples > 0,
            protection_verified=protection_samples > 0,
            kill_switch_verified=False,
            capability=None,
        ),
        PromotionEvidenceSnapshotInput(
            kind=PromotionEvidenceKind.PERFORMANCE,
            scope=scope,
            source="promotion-evidence-collector/v1",
            evidence_version=1,
            observed_at=observed,
            fresh_until=fresh_until,
            sample_size=performance_samples,
            error_count=0,
            gate_passed=False,
            reconciliation_verified=False,
            protection_verified=False,
            kill_switch_verified=False,
        ),
        PromotionEvidenceSnapshotInput(
            kind=PromotionEvidenceKind.OPERATIONS,
            scope=scope,
            source="promotion-evidence-collector/v1",
            evidence_version=1,
            observed_at=observed,
            fresh_until=fresh_until,
            sample_size=operations_samples,
            error_count=min(operation_errors, operations_samples),
            gate_passed=False,
            reconciliation_verified=technical_samples > 0,
            protection_verified=False,
            kill_switch_verified=False,
        ),
    )
    for item in inputs:
        persist_promotion_evidence_snapshot(db, item)
    return PromotionEvidenceCollectionReport(
        scope=scope,
        snapshot_count=len(inputs),
        technical_samples=technical_samples,
        performance_samples=performance_samples,
        operations_samples=operations_samples,
    )


def collect_registered_promotion_evidence(
    db: Session,
) -> tuple[PromotionEvidenceCollectionReport, ...]:
    """Collect each configured server strategy/venue scope once per scheduler cycle."""

    # Import lazily: this evidence module stays usable by pure evaluator tests
    # and never turns strategy registry metadata into an execution dependency.
    from ..config import get_config
    from ..models.strategies import StrategyVersion

    config_hash = get_config().config_hash
    policy_hash = hashlib.sha256(b"ADR-0001").hexdigest()
    rows = db.execute(select(StrategyVersion)).scalars()
    reports: list[PromotionEvidenceCollectionReport] = []
    for row in rows:
        # A strategy compiled under another config cannot be silently measured
        # as current promotion evidence.
        if row.config_hash != config_hash:
            continue
        for venue in sorted({str(item).upper() for item in row.venue_allowlist if item}):
            scope = PromotionEvidenceScope(
                strategy_family=row.family,
                strategy_version=row.version,
                venue=venue,
                source_hash=hashlib.sha256(
                    f"strategy:{row.family}:{row.version}".encode("utf-8")
                ).hexdigest(),
                config_hash=config_hash,
                policy_hash=policy_hash,
            )
            reports.append(collect_promotion_evidence(db, scope=scope))
    return tuple(reports)


def derive_registered_promotion_scope(
    db: Session,
    *,
    strategy_family: str,
    strategy_version: str,
    venue: str,
) -> PromotionEvidenceScope:
    """Resolve a requested identity to current server-owned provenance hashes."""

    from ..config import get_config
    from ..models.strategies import StrategyVersion

    family = _non_empty(strategy_family, "strategy_family", 32)
    version = _non_empty(strategy_version, "strategy_version", 64)
    venue = _non_empty(venue, "venue", 32).upper()
    row = db.execute(
        select(StrategyVersion).where(
            StrategyVersion.family == family,
            StrategyVersion.version == version,
        )
    ).scalar_one_or_none()
    if row is None:
        raise PromotionEvidenceInputError("strategy/version is not registered")
    config_hash = get_config().config_hash
    if row.config_hash != config_hash:
        raise PromotionEvidenceInputError(
            "strategy config hash does not match current server config"
        )
    if venue not in {str(item).upper() for item in row.venue_allowlist}:
        raise PromotionEvidenceInputError("venue is not allowed for registered strategy")
    return PromotionEvidenceScope(
        strategy_family=family,
        strategy_version=version,
        venue=venue,
        source_hash=hashlib.sha256(
            f"strategy:{family}:{version}".encode("utf-8")
        ).hexdigest(),
        config_hash=config_hash,
        policy_hash=hashlib.sha256(b"ADR-0001").hexdigest(),
    )


__all__ = [
    "PromotionEvidenceInputError",
    "PromotionEvidenceCollectionReport",
    "PromotionEvidenceKind",
    "PromotionEvidenceScope",
    "PromotionEvidenceSnapshotFact",
    "PromotionEvidenceSnapshotInput",
    "PromotionVenueCapability",
    "collect_promotion_evidence",
    "collect_registered_promotion_evidence",
    "derive_registered_promotion_scope",
    "current_persisted_promotion_evidence",
    "evaluate_persisted_promotion_evidence",
    "persist_promotion_evidence_snapshot",
    "record_promotion_evidence_decision",
]
