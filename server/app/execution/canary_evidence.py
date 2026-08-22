"""Trusted server-side registry and read-only verifier for Canary evidence refs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.canary_evidence import CanaryEvidenceReference

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_CATEGORIES = (
    "strategy_performance",
    "shadow",
    "testnet",
    "protection_reconciliation",
    "kill_switch_drill",
    "security_scan",
    "operational_health",
)


class CanaryEvidenceInputError(ValueError):
    pass


class CanaryEvidenceCategory(StrEnum):
    STRATEGY_PERFORMANCE = "strategy_performance"
    SHADOW = "shadow"
    TESTNET = "testnet"
    PROTECTION_RECONCILIATION = "protection_reconciliation"
    KILL_SWITCH_DRILL = "kill_switch_drill"
    SECURITY_SCAN = "security_scan"
    OPERATIONAL_HEALTH = "operational_health"


class CanaryEvidenceVerdict(StrEnum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


def _bounded(value: object, field: str, maximum: int) -> str:
    text = str(value).strip()
    if not text or len(text) > maximum or any(c in text for c in "\r\n\x00"):
        raise CanaryEvidenceInputError(f"{field} is invalid")
    return text


def _hash64(value: object, field: str) -> str:
    text = str(value).strip().lower()
    if _HEX64.fullmatch(text) is None:
        raise CanaryEvidenceInputError(f"{field} must be a SHA-256 hex digest")
    return text


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CanaryEvidenceInputError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class CanaryEvidenceScope:
    source_sha: str
    engine_config_hash: str
    strategy_family: str
    strategy_version: str
    venue: str

    def __post_init__(self) -> None:
        source_sha = str(self.source_sha).strip().lower()
        if _HEX40.fullmatch(source_sha) is None:
            raise CanaryEvidenceInputError("source_sha must be an exact lowercase Git SHA")
        object.__setattr__(self, "source_sha", source_sha)
        object.__setattr__(
            self,
            "engine_config_hash",
            _hash64(self.engine_config_hash, "engine_config_hash"),
        )
        object.__setattr__(
            self,
            "strategy_family",
            _bounded(self.strategy_family, "strategy_family", 64),
        )
        object.__setattr__(
            self,
            "strategy_version",
            _bounded(self.strategy_version, "strategy_version", 64),
        )
        object.__setattr__(self, "venue", _bounded(self.venue, "venue", 32).upper())


@dataclass(frozen=True, slots=True)
class CanaryEvidenceReferenceInput:
    category: CanaryEvidenceCategory | str
    evidence_ref: str
    scope: CanaryEvidenceScope
    source: str
    artifact_sha256: str
    verdict: CanaryEvidenceVerdict | str
    observed_at: datetime
    fresh_until: datetime

    def __post_init__(self) -> None:
        try:
            category = CanaryEvidenceCategory(self.category)
        except ValueError as exc:
            raise CanaryEvidenceInputError("category is invalid") from exc
        try:
            verdict = CanaryEvidenceVerdict(self.verdict)
        except ValueError as exc:
            raise CanaryEvidenceInputError("verdict is invalid") from exc
        observed_at = _aware(self.observed_at, "observed_at")
        fresh_until = _aware(self.fresh_until, "fresh_until")
        if observed_at > datetime.now(UTC):
            raise CanaryEvidenceInputError("observed_at must not be in the future")
        if fresh_until < observed_at:
            raise CanaryEvidenceInputError("fresh_until must not precede observed_at")
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "verdict", verdict)
        object.__setattr__(self, "evidence_ref", _bounded(self.evidence_ref, "evidence_ref", 128))
        object.__setattr__(self, "source", _bounded(self.source, "source", 64))
        object.__setattr__(self, "artifact_sha256", _hash64(self.artifact_sha256, "artifact_sha256"))
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "fresh_until", fresh_until)


@dataclass(frozen=True, slots=True)
class CanaryEvidenceBindingResult:
    complete: bool
    blockers: tuple[str, ...]
    evidence_refs: tuple[str, ...]


def persist_canary_evidence_reference(
    db: Session,
    evidence: CanaryEvidenceReferenceInput,
) -> CanaryEvidenceReference:
    """Append one server-observed evidence metadata fact; raw artifacts stay elsewhere."""
    row = CanaryEvidenceReference(
        category=evidence.category.value,
        evidence_ref=evidence.evidence_ref,
        source=evidence.source,
        artifact_sha256=evidence.artifact_sha256,
        verdict=evidence.verdict.value,
        source_sha=evidence.scope.source_sha,
        engine_config_hash=evidence.scope.engine_config_hash,
        strategy_family=evidence.scope.strategy_family,
        strategy_version=evidence.scope.strategy_version,
        venue=evidence.scope.venue,
        observed_at=evidence.observed_at,
        fresh_until=evidence.fresh_until,
    )
    db.add(row)
    db.flush()
    return row


def _scope_matches(row: CanaryEvidenceReference, scope: CanaryEvidenceScope) -> bool:
    return (
        row.source_sha == scope.source_sha
        and row.engine_config_hash == scope.engine_config_hash
        and row.strategy_family == scope.strategy_family
        and row.strategy_version == scope.strategy_version
        and row.venue == scope.venue
    )


def verify_canary_evidence_refs(
    db: Session,
    *,
    evidence_refs: Mapping[str, str],
    scope: CanaryEvidenceScope,
    now: datetime | None = None,
) -> CanaryEvidenceBindingResult:
    """Resolve every exact policy ref to one immutable trusted metadata row."""
    evaluated_at = _aware(now or datetime.now(UTC), "now")
    if set(evidence_refs) != set(_REQUIRED_CATEGORIES):
        return CanaryEvidenceBindingResult(
            complete=False,
            blockers=("CANARY_EVIDENCE_REF_SET_INVALID",),
            evidence_refs=(),
        )

    blockers: list[str] = []
    resolved: list[str] = []
    for category in _REQUIRED_CATEGORIES:
        ref = str(evidence_refs[category]).strip()
        row = db.execute(
            select(CanaryEvidenceReference).where(
                CanaryEvidenceReference.evidence_ref == ref
            )
        ).scalar_one_or_none()
        if row is None:
            blockers.append(f"CANARY_EVIDENCE_MISSING:{category}")
            continue
        resolved.append(ref)
        if row.category != category:
            blockers.append(f"CANARY_EVIDENCE_CATEGORY_MISMATCH:{category}")
            continue
        if not _scope_matches(row, scope):
            blockers.append(f"CANARY_EVIDENCE_SCOPE_MISMATCH:{category}")
            continue
        if row.observed_at > evaluated_at:
            blockers.append(f"CANARY_EVIDENCE_FUTURE:{category}")
            continue
        if row.fresh_until < evaluated_at:
            blockers.append(f"CANARY_EVIDENCE_STALE:{category}")
            continue
        if row.verdict != CanaryEvidenceVerdict.VERIFIED.value:
            blockers.append(f"CANARY_EVIDENCE_FAILED:{category}")
            continue

    return CanaryEvidenceBindingResult(
        complete=not blockers,
        blockers=tuple(blockers),
        evidence_refs=tuple(resolved),
    )


__all__ = [
    "CanaryEvidenceBindingResult",
    "CanaryEvidenceCategory",
    "CanaryEvidenceInputError",
    "CanaryEvidenceReferenceInput",
    "CanaryEvidenceScope",
    "CanaryEvidenceVerdict",
    "persist_canary_evidence_reference",
    "verify_canary_evidence_refs",
]
