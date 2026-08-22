"""Controlled operator path for one evidence-gated Lighter testnet smoke (SAI-077).

The operator never trusts a precomputed eligibility flag. It parses raw paired
Bybit/Lighter shadow observations, recomputes SAI-074, checks freshness before
secret/provider I/O, loads only the isolated testnet trade slot, constructs one
SAI-076 transport, and runs the exact SAI-075 create/cancel path. Every outcome
stored here is sanitized and LIVE remains physically ineligible.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ...experiments.venue_shadow_scorecard_v1 import (
    VenueShadowObservation,
    VenueShadowScorecardPolicy,
    VenueShadowScorecardResult,
    evaluate_venue_shadow_scorecard,
)
from ...models.lighter_execution import LighterTestnetSmokeEvidence
from .lighter_auth import (
    LIGHTER_TESTNET_TRADE_SLOT,
    LighterServerCredentials,
    load_lighter_server_credentials,
)
from .lighter_facts import LighterMarketFact, parse_lighter_market_fact
from .lighter_sdk_transport import build_lighter_testnet_transport
from .lighter_testnet_verification import (
    LighterTestnetAdmissionStatus,
    LighterTestnetSmokeError,
    run_lighter_testnet_cancel_recovery,
    run_lighter_testnet_create_cancel_smoke,
    verify_lighter_testnet_admission,
)

_MAX_ARTIFACT_BYTES = 1024 * 1024
_MAX_EVIDENCE_AGE = timedelta(hours=24)
_MAX_FUTURE_SKEW = timedelta(minutes=5)
_TOP_LEVEL_FIELDS = {"observed_at", "policy", "observations", "market", "order"}
_POLICY_FIELDS = {
    "min_paired_opportunities",
    "min_metric_pairs",
    "max_lighter_cost_delta_bps",
    "max_lighter_ack_latency_delta_ms",
    "max_lighter_fill_slippage_delta_bps",
    "max_lighter_protection_latency_delta_ms",
    "max_lighter_ambiguity_rate_delta",
    "max_lighter_unavailable_rate",
}
_ORDER_FIELDS = {"client_order_id", "quantity", "price", "is_ask"}
_TERMINAL_SUCCESS = {"SUCCESS", "RECOVERY_SUCCESS"}

CredentialLoader = Callable[[Session, str], LighterServerCredentials | None]
TransportFactory = Callable[[LighterServerCredentials], Any]


class LighterTestnetOperatorError(ValueError):
    """Fail-closed artifact/operator contract error without provider secrets."""


class LighterTestnetOperatorStatus(StrEnum):
    BLOCKED = "BLOCKED"
    SUCCESS = "SUCCESS"
    CREATE_FAILED = "CREATE_FAILED"
    CANCEL_FAILED = "CANCEL_FAILED"
    RECOVERY_SUCCESS = "RECOVERY_SUCCESS"


@dataclass(frozen=True, slots=True)
class LighterTestnetSmokeArtifact:
    source_sha256: str
    run_key: str
    observed_at: datetime
    shadow_result: VenueShadowScorecardResult
    market: LighterMarketFact
    client_order_id: str
    quantity: Decimal
    price: Decimal
    is_ask: bool


@dataclass(frozen=True, slots=True)
class LighterTestnetOperatorResult:
    run_key: str
    status: LighterTestnetOperatorStatus
    reason_code: str | None
    create_tx_hash: str | None
    cancel_tx_hash: str | None

    @property
    def eligible_for_live(self) -> bool:
        return False


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LighterTestnetOperatorError(f"{field} must be an object")
    return value


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LighterTestnetOperatorError("observed_at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LighterTestnetOperatorError("observed_at must be an ISO timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LighterTestnetOperatorError("observed_at must be timezone-aware")
    return parsed.astimezone(UTC)


def _decimal(value: object, *, field: str, positive: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise LighterTestnetOperatorError(f"{field} must be a finite decimal") from None
    if not parsed.is_finite() or (positive and parsed <= 0):
        qualifier = "positive finite" if positive else "finite"
        raise LighterTestnetOperatorError(f"{field} must be a {qualifier} decimal")
    return parsed


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LighterTestnetOperatorError(f"{field} must be a positive integer")
    return value


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    try:
        rendered = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise LighterTestnetOperatorError("artifact must contain canonical JSON values") from None
    return rendered.encode("utf-8")


def _parse_policy(raw: object) -> VenueShadowScorecardPolicy:
    policy = _mapping(raw, field="policy")
    if set(policy) != _POLICY_FIELDS:
        raise LighterTestnetOperatorError("policy fields do not match contract")
    return VenueShadowScorecardPolicy(
        min_paired_opportunities=_positive_int(
            policy["min_paired_opportunities"], field="min_paired_opportunities"
        ),
        min_metric_pairs=_positive_int(policy["min_metric_pairs"], field="min_metric_pairs"),
        max_lighter_cost_delta_bps=_decimal(
            policy["max_lighter_cost_delta_bps"], field="max_lighter_cost_delta_bps"
        ),
        max_lighter_ack_latency_delta_ms=_decimal(
            policy["max_lighter_ack_latency_delta_ms"],
            field="max_lighter_ack_latency_delta_ms",
        ),
        max_lighter_fill_slippage_delta_bps=_decimal(
            policy["max_lighter_fill_slippage_delta_bps"],
            field="max_lighter_fill_slippage_delta_bps",
        ),
        max_lighter_protection_latency_delta_ms=_decimal(
            policy["max_lighter_protection_latency_delta_ms"],
            field="max_lighter_protection_latency_delta_ms",
        ),
        max_lighter_ambiguity_rate_delta=_decimal(
            policy["max_lighter_ambiguity_rate_delta"],
            field="max_lighter_ambiguity_rate_delta",
        ),
        max_lighter_unavailable_rate=_decimal(
            policy["max_lighter_unavailable_rate"], field="max_lighter_unavailable_rate"
        ),
    )


def _parse_observations(raw: object) -> tuple[VenueShadowObservation, ...]:
    if not isinstance(raw, list) or not raw:
        raise LighterTestnetOperatorError("observations must be a non-empty list")
    rows: list[VenueShadowObservation] = []
    for index, item in enumerate(raw):
        row = _mapping(item, field=f"observations[{index}]")
        try:
            rows.append(
                VenueShadowObservation(
                    opportunity_key=str(row["opportunity_key"]),
                    venue=str(row["venue"]),
                    market_snapshot_hash=str(row["market_snapshot_hash"]),
                    status=str(row["status"]),
                    total_cost_bps=(
                        None
                        if row.get("total_cost_bps") is None
                        else _decimal(row["total_cost_bps"], field="total_cost_bps")
                    ),
                    ack_latency_ms=(
                        None
                        if row.get("ack_latency_ms") is None
                        else _decimal(row["ack_latency_ms"], field="ack_latency_ms")
                    ),
                    fill_slippage_bps=(
                        None
                        if row.get("fill_slippage_bps") is None
                        else _decimal(row["fill_slippage_bps"], field="fill_slippage_bps")
                    ),
                    protection_latency_ms=(
                        None
                        if row.get("protection_latency_ms") is None
                        else _decimal(
                            row["protection_latency_ms"], field="protection_latency_ms"
                        )
                    ),
                    reconciliation_outcome=str(row["reconciliation_outcome"]),
                    duplicate_execution_incident=row.get(
                        "duplicate_execution_incident", False
                    ),
                    unprotected_execution_incident=row.get(
                        "unprotected_execution_incident", False
                    ),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LighterTestnetOperatorError(
                f"invalid observations[{index}] contract"
            ) from exc
    return tuple(rows)


def parse_lighter_testnet_smoke_artifact(raw: bytes) -> LighterTestnetSmokeArtifact:
    """Parse raw immutable evidence and recompute SAI-074 locally."""

    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_ARTIFACT_BYTES:
        raise LighterTestnetOperatorError("artifact must be 1..1048576 bytes")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LighterTestnetOperatorError("artifact must be UTF-8 JSON") from None
    payload = _mapping(payload, field="artifact")
    if set(payload) != _TOP_LEVEL_FIELDS:
        raise LighterTestnetOperatorError("artifact fields do not match contract")

    observed_at = _aware_datetime(payload["observed_at"])
    policy = _parse_policy(payload["policy"])
    observations = _parse_observations(payload["observations"])
    shadow_result = evaluate_venue_shadow_scorecard(observations, policy=policy)

    market_payload = _mapping(payload["market"], field="market")
    try:
        market = parse_lighter_market_fact(market_payload, observed_at=observed_at)
    except (TypeError, ValueError) as exc:
        raise LighterTestnetOperatorError("invalid market contract") from exc

    order = _mapping(payload["order"], field="order")
    if set(order) != _ORDER_FIELDS:
        raise LighterTestnetOperatorError("order fields do not match contract")
    client_order_id = order["client_order_id"]
    if (
        not isinstance(client_order_id, str)
        or not client_order_id.strip()
        or len(client_order_id) > 96
    ):
        raise LighterTestnetOperatorError("client_order_id must be 1..96 characters")
    if not isinstance(order["is_ask"], bool):
        raise LighterTestnetOperatorError("is_ask must be boolean")
    quantity = _decimal(order["quantity"], field="quantity", positive=True)
    price = _decimal(order["price"], field="price", positive=True)

    canonical = _canonical_json(payload)
    source_sha256 = hashlib.sha256(canonical).hexdigest()
    run_payload = (
        f"{source_sha256}|{market.market_id}|{client_order_id.strip()}|"
        f"{quantity}|{price}|{int(order['is_ask'])}"
    )
    run_key = hashlib.sha256(run_payload.encode("utf-8")).hexdigest()
    return LighterTestnetSmokeArtifact(
        source_sha256=source_sha256,
        run_key=run_key,
        observed_at=observed_at,
        shadow_result=shadow_result,
        market=market,
        client_order_id=client_order_id.strip(),
        quantity=quantity,
        price=price,
        is_ask=order["is_ask"],
    )


def _lock_run(db: Session, run_key: str) -> None:
    digest = bytes.fromhex(run_key)
    lock_key = int.from_bytes(digest[:8], "big", signed=False) & ((1 << 63) - 1)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key or 1})


def _latest(db: Session, run_key: str) -> LighterTestnetSmokeEvidence | None:
    return db.scalar(
        select(LighterTestnetSmokeEvidence)
        .where(LighterTestnetSmokeEvidence.run_key == run_key)
        .order_by(
            LighterTestnetSmokeEvidence.observed_at.desc(),
            LighterTestnetSmokeEvidence.created_at.desc(),
        )
        .limit(1)
    )


def _result_from_row(row: LighterTestnetSmokeEvidence) -> LighterTestnetOperatorResult:
    return LighterTestnetOperatorResult(
        run_key=row.run_key,
        status=LighterTestnetOperatorStatus(row.event_type),
        reason_code=row.reason_code,
        create_tx_hash=row.create_tx_hash,
        cancel_tx_hash=row.cancel_tx_hash,
    )


def _evidence_key(
    *,
    artifact: LighterTestnetSmokeArtifact,
    event_type: str,
    reason_code: str | None,
    create_tx_hash: str | None,
    cancel_tx_hash: str | None,
    account_index: int | None,
    api_key_index: int | None,
    observed_at: datetime,
) -> str:
    safe = {
        "run_key": artifact.run_key,
        "source_sha256": artifact.source_sha256,
        "event_type": event_type,
        "reason_code": reason_code,
        "create_tx_hash": create_tx_hash,
        "cancel_tx_hash": cancel_tx_hash,
        "account_index": account_index,
        "api_key_index": api_key_index,
        "observed_at": observed_at.astimezone(UTC).isoformat(),
    }
    return hashlib.sha256(_canonical_json(safe)).hexdigest()


def _persist(
    db: Session,
    *,
    artifact: LighterTestnetSmokeArtifact,
    status: LighterTestnetOperatorStatus,
    reason_code: str | None,
    create_tx_hash: str | None,
    cancel_tx_hash: str | None,
    credentials: LighterServerCredentials | None,
    observed_at: datetime,
) -> LighterTestnetOperatorResult:
    account_index = credentials.account_index if credentials is not None else None
    api_key_index = credentials.api_key_index if credentials is not None else None
    evidence_key = _evidence_key(
        artifact=artifact,
        event_type=status.value,
        reason_code=reason_code,
        create_tx_hash=create_tx_hash,
        cancel_tx_hash=cancel_tx_hash,
        account_index=account_index,
        api_key_index=api_key_index,
        observed_at=observed_at,
    )
    existing = db.scalar(
        select(LighterTestnetSmokeEvidence).where(
            LighterTestnetSmokeEvidence.evidence_key == evidence_key
        )
    )
    if existing is None:
        db.add(
            LighterTestnetSmokeEvidence(
                evidence_key=evidence_key,
                run_key=artifact.run_key,
                source_sha256=artifact.source_sha256,
                event_type=status.value,
                reason_code=reason_code,
                scorecard_status=artifact.shadow_result.status.value,
                scorecard_reasons=list(artifact.shadow_result.reasons),
                scorecard_observed_at=artifact.observed_at,
                account_index=account_index,
                api_key_index=api_key_index,
                market_index=artifact.market.market_id,
                symbol=artifact.market.symbol,
                client_order_id=artifact.client_order_id,
                create_tx_hash=create_tx_hash,
                cancel_tx_hash=cancel_tx_hash,
                eligible_for_live=False,
                observed_at=observed_at,
            )
        )
        db.flush()
    return LighterTestnetOperatorResult(
        run_key=artifact.run_key,
        status=status,
        reason_code=reason_code,
        create_tx_hash=create_tx_hash,
        cancel_tx_hash=cancel_tx_hash,
    )


def _normalized_now(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise LighterTestnetOperatorError("now must be timezone-aware")
    return now.astimezone(UTC)


def execute_lighter_testnet_smoke_artifact(
    db: Session,
    *,
    raw_artifact: bytes,
    session_factory,
    now: datetime,
    credential_loader: CredentialLoader = load_lighter_server_credentials,
    transport_factory: TransportFactory = build_lighter_testnet_transport,
) -> LighterTestnetOperatorResult:
    """Execute or safely resume one deterministic testnet smoke run."""

    artifact = parse_lighter_testnet_smoke_artifact(raw_artifact)
    moment = _normalized_now(now)
    _lock_run(db, artifact.run_key)
    latest = _latest(db, artifact.run_key)

    if latest is not None and latest.event_type in _TERMINAL_SUCCESS:
        return _result_from_row(latest)
    if latest is not None and latest.event_type == LighterTestnetOperatorStatus.CREATE_FAILED:
        # CREATE failure can be ambiguous at the provider boundary. Never submit
        # CREATE again automatically until reconciliation/manual evidence exists.
        return _result_from_row(latest)

    recovering_cancel = (
        latest is not None
        and latest.event_type == LighterTestnetOperatorStatus.CANCEL_FAILED
        and isinstance(latest.create_tx_hash, str)
        and bool(latest.create_tx_hash.strip())
    )

    if not recovering_cancel:
        if artifact.observed_at > moment + _MAX_FUTURE_SKEW:
            return _persist(
                db,
                artifact=artifact,
                status=LighterTestnetOperatorStatus.BLOCKED,
                reason_code="SHADOW_EVIDENCE_FUTURE",
                create_tx_hash=None,
                cancel_tx_hash=None,
                credentials=None,
                observed_at=moment,
            )
        if moment - artifact.observed_at > _MAX_EVIDENCE_AGE:
            return _persist(
                db,
                artifact=artifact,
                status=LighterTestnetOperatorStatus.BLOCKED,
                reason_code="SHADOW_EVIDENCE_STALE",
                create_tx_hash=None,
                cancel_tx_hash=None,
                credentials=None,
                observed_at=moment,
            )
        if not artifact.shadow_result.eligible_for_testnet:
            return _persist(
                db,
                artifact=artifact,
                status=LighterTestnetOperatorStatus.BLOCKED,
                reason_code="SHADOW_GATE_NOT_ELIGIBLE",
                create_tx_hash=None,
                cancel_tx_hash=None,
                credentials=None,
                observed_at=moment,
            )

    try:
        credentials = credential_loader(db, LIGHTER_TESTNET_TRADE_SLOT)
    except Exception:
        credentials = None
    if not isinstance(credentials, LighterServerCredentials):
        return _persist(
            db,
            artifact=artifact,
            status=LighterTestnetOperatorStatus.BLOCKED,
            reason_code="TESTNET_TRADE_CREDENTIALS_REQUIRED",
            create_tx_hash=(latest.create_tx_hash if recovering_cancel else None),
            cancel_tx_hash=None,
            credentials=None,
            observed_at=moment,
        )

    try:
        transport = transport_factory(credentials)
    except Exception:
        return _persist(
            db,
            artifact=artifact,
            status=LighterTestnetOperatorStatus.BLOCKED,
            reason_code="TESTNET_TRANSPORT_CONSTRUCTION_FAILED",
            create_tx_hash=(latest.create_tx_hash if recovering_cancel else None),
            cancel_tx_hash=None,
            credentials=credentials,
            observed_at=moment,
        )

    try:
        admission = verify_lighter_testnet_admission(
            credentials=credentials,
            shadow_result=artifact.shadow_result,
            transport=transport,
            observed_at=moment,
        )
        if admission.status is not LighterTestnetAdmissionStatus.READY:
            reason = admission.reasons[0] if admission.reasons else "TESTNET_ADMISSION_BLOCKED"
            return _persist(
                db,
                artifact=artifact,
                status=LighterTestnetOperatorStatus.BLOCKED,
                reason_code=reason,
                create_tx_hash=(latest.create_tx_hash if recovering_cancel else None),
                cancel_tx_hash=None,
                credentials=credentials,
                observed_at=moment,
            )

        if recovering_cancel:
            assert latest is not None
            assert latest.create_tx_hash is not None
            try:
                smoke = run_lighter_testnet_cancel_recovery(
                    admission=admission,
                    session_factory=session_factory,
                    transport=transport,
                    market=artifact.market,
                    client_order_id=artifact.client_order_id,
                    create_tx_hash=latest.create_tx_hash,
                )
            except Exception:
                return _persist(
                    db,
                    artifact=artifact,
                    status=LighterTestnetOperatorStatus.CANCEL_FAILED,
                    reason_code="CANCEL_FAILED",
                    create_tx_hash=latest.create_tx_hash,
                    cancel_tx_hash=None,
                    credentials=credentials,
                    observed_at=moment,
                )
            return _persist(
                db,
                artifact=artifact,
                status=LighterTestnetOperatorStatus.RECOVERY_SUCCESS,
                reason_code=None,
                create_tx_hash=smoke.create_tx_hash,
                cancel_tx_hash=smoke.cancel_tx_hash,
                credentials=credentials,
                observed_at=moment,
            )

        try:
            smoke = run_lighter_testnet_create_cancel_smoke(
                admission=admission,
                session_factory=session_factory,
                transport=transport,
                market=artifact.market,
                client_order_id=artifact.client_order_id,
                quantity=artifact.quantity,
                price=artifact.price,
                is_ask=artifact.is_ask,
            )
        except LighterTestnetSmokeError as exc:
            if exc.stage == "CANCEL" and exc.create_tx_hash:
                return _persist(
                    db,
                    artifact=artifact,
                    status=LighterTestnetOperatorStatus.CANCEL_FAILED,
                    reason_code="CANCEL_FAILED",
                    create_tx_hash=exc.create_tx_hash,
                    cancel_tx_hash=None,
                    credentials=credentials,
                    observed_at=moment,
                )
            return _persist(
                db,
                artifact=artifact,
                status=LighterTestnetOperatorStatus.CREATE_FAILED,
                reason_code="CREATE_FAILED",
                create_tx_hash=None,
                cancel_tx_hash=None,
                credentials=credentials,
                observed_at=moment,
            )
        except Exception:
            # Unknown failure around CREATE is treated as potentially ambiguous
            # and therefore never automatically retried.
            return _persist(
                db,
                artifact=artifact,
                status=LighterTestnetOperatorStatus.CREATE_FAILED,
                reason_code="CREATE_FAILED",
                create_tx_hash=None,
                cancel_tx_hash=None,
                credentials=credentials,
                observed_at=moment,
            )

        return _persist(
            db,
            artifact=artifact,
            status=LighterTestnetOperatorStatus.SUCCESS,
            reason_code=None,
            create_tx_hash=smoke.create_tx_hash,
            cancel_tx_hash=smoke.cancel_tx_hash,
            credentials=credentials,
            observed_at=moment,
        )
    finally:
        close = getattr(transport, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


__all__ = [
    "LighterTestnetOperatorError",
    "LighterTestnetOperatorResult",
    "LighterTestnetOperatorStatus",
    "LighterTestnetSmokeArtifact",
    "execute_lighter_testnet_smoke_artifact",
    "parse_lighter_testnet_smoke_artifact",
]
