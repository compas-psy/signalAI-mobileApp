"""Read-only binding of Canary evidence refs to durable server facts.

This module is intentionally non-authorizing.  It never loads a credential,
contacts a provider, changes execution mode or mints an activation proof.  Its
only job is to prove that every opaque ref carried by a Canary policy names one
specific, fresh, append-only server evidence snapshot with the expected
provenance and semantics.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.promotion_evidence import PromotionEvidenceSnapshot
from .promotion_evidence import PromotionEvidenceKind, PromotionVenueCapability

_REQUIRED_KEYS = frozenset(
    {
        "strategy_performance",
        "shadow",
        "testnet",
        "protection_reconciliation",
        "kill_switch_drill",
        "security_scan",
        "operational_health",
    }
)
_ADR_0002_HASH = hashlib.sha256(b"ADR-0002").hexdigest()

_EXPECTED = {
    "strategy_performance": (
        PromotionEvidenceKind.PERFORMANCE,
        "canary-strategy-performance/v1",
    ),
    "shadow": (PromotionEvidenceKind.PERFORMANCE, "canary-shadow/v1"),
    "testnet": (PromotionEvidenceKind.TECHNICAL, "canary-lighter-testnet/v1"),
    "protection_reconciliation": (
        PromotionEvidenceKind.TECHNICAL,
        "canary-protection-reconciliation/v1",
    ),
    "kill_switch_drill": (
        PromotionEvidenceKind.OPERATIONS,
        "canary-kill-switch-drill/v1",
    ),
    "security_scan": (PromotionEvidenceKind.OPERATIONS, "canary-security-scan/v1"),
    "operational_health": (
        PromotionEvidenceKind.OPERATIONS,
        "canary-operational-health/v1",
    ),
}


@dataclass(frozen=True, slots=True)
class CanaryEvidenceBindingResult:
    ready: bool
    blockers: tuple[str, ...]
    snapshot_ids: tuple[str, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "blockers": list(self.blockers),
            "snapshot_ids": list(self.snapshot_ids),
        }


def _code(key: str, suffix: str) -> str:
    return f"CANARY_EVIDENCE_{key.upper()}_{suffix}"


def _scope_matches(
    row: PromotionEvidenceSnapshot,
    *,
    source_hash: str,
    config_hash: str,
    strategy_family: str,
    strategy_version: str,
    venue: str,
) -> bool:
    return (
        row.strategy_family == strategy_family
        and row.strategy_version == strategy_version
        and row.venue.upper() == venue
        and row.source_hash == source_hash
        and row.config_hash == config_hash
        and row.policy_hash == _ADR_0002_HASH
    )


def _semantic_blocker(key: str, row: PromotionEvidenceSnapshot) -> str | None:
    if key == "testnet":
        if row.capability != PromotionVenueCapability.LIGHTER_TESTNET_ACTIONS_ONLY.value:
            return _code(key, "INCOMPLETE")
    elif key == "protection_reconciliation":
        if not row.reconciliation_verified or not row.protection_verified:
            return _code(key, "INCOMPLETE")
    elif key == "kill_switch_drill":
        if not row.kill_switch_verified:
            return _code(key, "INCOMPLETE")
    elif key == "security_scan":
        if row.error_count != 0:
            return _code(key, "INCOMPLETE")
    elif key == "operational_health":
        if not row.reconciliation_verified:
            return _code(key, "INCOMPLETE")
    return None


def evaluate_canary_evidence_bindings(
    db: Session,
    *,
    evidence_refs: Mapping[str, str],
    source_sha: str,
    config_hash: str,
    strategy_family: str,
    strategy_version: str,
    venue: str,
    now: datetime | None = None,
) -> CanaryEvidenceBindingResult:
    """Resolve and validate the seven exact immutable Canary evidence refs.

    Unknown/malformed/reused refs and any missing/stale/mismatched fact fail
    closed.  There is deliberately no fallback to "latest" evidence because a
    policy must remain bound to the exact facts the owner previewed.
    """
    if set(evidence_refs) != _REQUIRED_KEYS:
        return CanaryEvidenceBindingResult(
            ready=False,
            blockers=("CANARY_EVIDENCE_REFS_INCOMPLETE",),
            snapshot_ids=(),
        )

    raw_refs = [str(evidence_refs[key]).strip() for key in sorted(_REQUIRED_KEYS)]
    if len(set(raw_refs)) != len(raw_refs):
        return CanaryEvidenceBindingResult(
            ready=False,
            blockers=("CANARY_EVIDENCE_REF_REUSED",),
            snapshot_ids=(),
        )

    source_sha = str(source_sha).strip().lower()
    config_hash = str(config_hash).strip().lower()
    strategy_family = str(strategy_family).strip()
    strategy_version = str(strategy_version).strip()
    venue = str(venue).strip().upper()
    source_hash = hashlib.sha256(source_sha.encode("utf-8")).hexdigest()
    evaluated_at = now or datetime.now(UTC)
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    evaluated_at = evaluated_at.astimezone(UTC)

    blockers: list[str] = []
    resolved_ids: list[str] = []
    for key in sorted(_REQUIRED_KEYS):
        raw_ref = str(evidence_refs[key]).strip()
        try:
            snapshot_id = uuid.UUID(raw_ref)
        except (ValueError, AttributeError, TypeError):
            blockers.append(_code(key, "REF_INVALID"))
            continue

        row = db.execute(
            select(PromotionEvidenceSnapshot).where(
                PromotionEvidenceSnapshot.id == snapshot_id
            )
        ).scalar_one_or_none()
        if row is None:
            blockers.append(_code(key, "NOT_FOUND"))
            continue
        resolved_ids.append(str(row.id))

        expected_kind, expected_source = _EXPECTED[key]
        if row.kind != expected_kind.value:
            blockers.append(_code(key, "TYPE_MISMATCH"))
            continue
        if row.source != expected_source:
            blockers.append(_code(key, "SOURCE_MISMATCH"))
            continue
        if not _scope_matches(
            row,
            source_hash=source_hash,
            config_hash=config_hash,
            strategy_family=strategy_family,
            strategy_version=strategy_version,
            venue=venue,
        ):
            blockers.append(_code(key, "SCOPE_MISMATCH"))
            continue

        observed_at = row.observed_at
        fresh_until = row.fresh_until
        if (
            observed_at.tzinfo is None
            or observed_at.utcoffset() is None
            or fresh_until.tzinfo is None
            or fresh_until.utcoffset() is None
        ):
            blockers.append(_code(key, "TIME_INVALID"))
            continue
        observed_at = observed_at.astimezone(UTC)
        fresh_until = fresh_until.astimezone(UTC)
        if observed_at > evaluated_at:
            blockers.append(_code(key, "OBSERVED_IN_FUTURE"))
            continue
        if fresh_until < evaluated_at:
            blockers.append(_code(key, "STALE"))
            continue
        if row.sample_size <= 0:
            blockers.append(_code(key, "NO_SAMPLE"))
            continue
        if not row.gate_passed:
            blockers.append(_code(key, "GATE_FAILED"))
            continue
        semantic = _semantic_blocker(key, row)
        if semantic is not None:
            blockers.append(semantic)

    return CanaryEvidenceBindingResult(
        ready=not blockers,
        blockers=tuple(blockers),
        snapshot_ids=tuple(resolved_ids),
    )


__all__ = ["CanaryEvidenceBindingResult", "evaluate_canary_evidence_bindings"]
