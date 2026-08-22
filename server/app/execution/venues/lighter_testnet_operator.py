"""Evidence-backed, testnet-only Lighter operator boundary (SAI-077)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ...experiments.venue_shadow_scorecard_v1 import (
    VenueShadowObservation,
    VenueShadowScorecardPolicy,
    VenueShadowScorecardResult,
    evaluate_venue_shadow_scorecard,
)
from .lighter_actions import SessionFactory
from .lighter_auth import (
    LIGHTER_TESTNET_TRADE_SLOT,
    LighterServerCredentials,
    load_lighter_server_credentials,
)
from .lighter_facts import LighterMarketFact
from .lighter_sdk_transport import build_lighter_testnet_transport
from .lighter_testnet_verification import (
    LighterTestnetSmokeError,
    run_lighter_testnet_cancel_recovery,
    run_lighter_testnet_create_cancel_smoke,
    verify_lighter_testnet_admission,
)

_SHADOW_SCHEMA = "signalai.lighter.shadow-evidence.v1"
_RESULT_SCHEMA = "signalai.lighter.testnet-smoke-evidence.v1"
CredentialLoader = Callable[[Session, str], LighterServerCredentials | None]
TransportFactory = Callable[[LighterServerCredentials], Any]


class LighterTestnetOperatorError(RuntimeError):
    """Sanitized fail-closed operator error."""


@dataclass(frozen=True, slots=True)
class LighterShadowEvidence:
    path: Path
    sha256: str
    generated_at: datetime
    scorecard: VenueShadowScorecardResult


@dataclass(frozen=True, slots=True)
class LighterTestnetSmokeEvidence:
    status: str
    observed_at: datetime
    shadow_evidence_sha256: str
    client_order_id: str
    create_tx_hash: str | None
    cancel_tx_hash: str | None
    account_index: int
    api_key_index: int
    base_url: str
    chain_id: int
    evidence_sha256: str

    @property
    def eligible_for_live(self) -> bool:
        return False


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LighterTestnetOperatorError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _time(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LighterTestnetOperatorError(f"{field} must be an ISO-8601 timestamp")
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")), field)
    except ValueError:
        raise LighterTestnetOperatorError(f"{field} must be an ISO-8601 timestamp") from None


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise LighterTestnetOperatorError(f"{field} must be a finite decimal") from None
    if not result.is_finite():
        raise LighterTestnetOperatorError(f"{field} must be a finite decimal")
    return result


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LighterTestnetOperatorError(f"{field} must be an object")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LighterTestnetOperatorError(f"{field} must be non-blank")
    return value.strip()


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LighterTestnetOperatorError(f"{field} must be a positive integer")
    return value


def _parse_policy(raw: object) -> VenueShadowScorecardPolicy:
    data = _mapping(raw, "policy")
    try:
        return VenueShadowScorecardPolicy(
            min_paired_opportunities=_positive_int(data.get("min_paired_opportunities"), "policy.min_paired_opportunities"),
            min_metric_pairs=_positive_int(data.get("min_metric_pairs"), "policy.min_metric_pairs"),
            max_lighter_cost_delta_bps=_decimal(data.get("max_lighter_cost_delta_bps"), "policy.max_lighter_cost_delta_bps"),
            max_lighter_ack_latency_delta_ms=_decimal(data.get("max_lighter_ack_latency_delta_ms"), "policy.max_lighter_ack_latency_delta_ms"),
            max_lighter_fill_slippage_delta_bps=_decimal(data.get("max_lighter_fill_slippage_delta_bps"), "policy.max_lighter_fill_slippage_delta_bps"),
            max_lighter_protection_latency_delta_ms=_decimal(data.get("max_lighter_protection_latency_delta_ms"), "policy.max_lighter_protection_latency_delta_ms"),
            max_lighter_ambiguity_rate_delta=_decimal(data.get("max_lighter_ambiguity_rate_delta"), "policy.max_lighter_ambiguity_rate_delta"),
            max_lighter_unavailable_rate=_decimal(data.get("max_lighter_unavailable_rate"), "policy.max_lighter_unavailable_rate"),
        )
    except ValueError as exc:
        raise LighterTestnetOperatorError("shadow evidence policy is invalid") from exc


def _parse_observation(raw: object, index: int) -> VenueShadowObservation:
    data = _mapping(raw, f"observations[{index}]")
    prefix = f"observations[{index}]"
    try:
        return VenueShadowObservation(
            opportunity_key=_text(data.get("opportunity_key"), f"{prefix}.opportunity_key"),
            venue=_text(data.get("venue"), f"{prefix}.venue"),
            market_snapshot_hash=_text(data.get("market_snapshot_hash"), f"{prefix}.market_snapshot_hash"),
            status=_text(data.get("status"), f"{prefix}.status"),
            total_cost_bps=None if data.get("total_cost_bps") is None else _decimal(data.get("total_cost_bps"), f"{prefix}.total_cost_bps"),
            ack_latency_ms=None if data.get("ack_latency_ms") is None else _decimal(data.get("ack_latency_ms"), f"{prefix}.ack_latency_ms"),
            fill_slippage_bps=None if data.get("fill_slippage_bps") is None else _decimal(data.get("fill_slippage_bps"), f"{prefix}.fill_slippage_bps"),
            protection_latency_ms=None if data.get("protection_latency_ms") is None else _decimal(data.get("protection_latency_ms"), f"{prefix}.protection_latency_ms"),
            reconciliation_outcome=_text(data.get("reconciliation_outcome"), f"{prefix}.reconciliation_outcome"),
            duplicate_execution_incident=data.get("duplicate_execution_incident", False),
            unprotected_execution_incident=data.get("unprotected_execution_incident", False),
        )
    except ValueError as exc:
        raise LighterTestnetOperatorError(f"shadow evidence {prefix} is invalid") from exc


def load_lighter_shadow_evidence(path: str | Path) -> LighterShadowEvidence:
    evidence_path = Path(path)
    try:
        raw = evidence_path.read_bytes()
        document = _mapping(json.loads(raw), "shadow evidence")
    except (OSError, json.JSONDecodeError):
        raise LighterTestnetOperatorError("shadow evidence cannot be loaded") from None
    if document.get("schema") != _SHADOW_SCHEMA:
        raise LighterTestnetOperatorError("shadow evidence schema is unsupported")
    rows = document.get("observations")
    if not isinstance(rows, list) or not rows:
        raise LighterTestnetOperatorError("shadow evidence observations are required")
    observations = tuple(_parse_observation(row, i) for i, row in enumerate(rows))
    try:
        scorecard = evaluate_venue_shadow_scorecard(observations, policy=_parse_policy(document.get("policy")))
    except ValueError as exc:
        raise LighterTestnetOperatorError("shadow evidence cannot be evaluated") from exc
    return LighterShadowEvidence(
        path=evidence_path,
        sha256=hashlib.sha256(raw).hexdigest(),
        generated_at=_time(document.get("generated_at"), "generated_at"),
        scorecard=scorecard,
    )


def _canonical(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _payload(*, status: str, observed_at: datetime, shadow_sha: str, client_order_id: str, create_tx_hash: str | None, cancel_tx_hash: str | None, credentials: LighterServerCredentials, transport: Any) -> dict[str, Any]:
    return {
        "schema": _RESULT_SCHEMA,
        "status": status,
        "observed_at": observed_at.isoformat(),
        "shadow_evidence_sha256": shadow_sha,
        "client_order_id": client_order_id,
        "create_tx_hash": create_tx_hash,
        "cancel_tx_hash": cancel_tx_hash,
        "account_index": credentials.account_index,
        "api_key_index": credentials.api_key_index,
        "base_url": str(transport.base_url),
        "chain_id": int(transport.chain_id),
        "eligible_for_live": False,
    }


def _write(path: str | Path, payload: dict[str, Any]) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    envelope = {**payload, "evidence_sha256": digest}
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", prefix=f".{target.name}.", dir=target.parent, delete=False) as handle:
            temporary = handle.name
            os.fchmod(handle.fileno(), 0o600)
            handle.write(_canonical(envelope))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise LighterTestnetOperatorError("testnet smoke evidence cannot be persisted") from None
    return digest


def _result(payload: dict[str, Any], digest: str) -> LighterTestnetSmokeEvidence:
    return LighterTestnetSmokeEvidence(
        status=str(payload["status"]), observed_at=_time(payload["observed_at"], "observed_at"),
        shadow_evidence_sha256=str(payload["shadow_evidence_sha256"]), client_order_id=str(payload["client_order_id"]),
        create_tx_hash=payload.get("create_tx_hash"), cancel_tx_hash=payload.get("cancel_tx_hash"),
        account_index=int(payload["account_index"]), api_key_index=int(payload["api_key_index"]),
        base_url=str(payload["base_url"]), chain_id=int(payload["chain_id"]), evidence_sha256=digest,
    )


def _safe_close(transport: Any) -> None:
    try:
        close = getattr(transport, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _credentials(db: Session, loader: CredentialLoader) -> LighterServerCredentials:
    try:
        result = loader(db, LIGHTER_TESTNET_TRADE_SLOT)
    except Exception:
        raise LighterTestnetOperatorError("testnet trade credential load failed") from None
    if not isinstance(result, LighterServerCredentials):
        raise LighterTestnetOperatorError("testnet trade credential is not configured")
    if result.environment != "testnet" or result.purpose != "trade":
        raise LighterTestnetOperatorError("testnet trade credential scope is invalid")
    return result


def _context(db: Session, shadow: LighterShadowEvidence, observed_at: datetime, loader: CredentialLoader, factory: TransportFactory):
    if not shadow.scorecard.eligible_for_testnet:
        raise LighterTestnetOperatorError("shadow evidence is not eligible for testnet")
    moment = _utc(observed_at, "observed_at")
    credentials = _credentials(db, loader)
    try:
        transport = factory(credentials)
    except Exception:
        raise LighterTestnetOperatorError("testnet transport construction failed") from None
    admission = verify_lighter_testnet_admission(credentials=credentials, shadow_result=shadow.scorecard, transport=transport, observed_at=moment)
    if not admission.eligible_for_order_smoke:
        _safe_close(transport)
        reason = admission.reasons[0] if admission.reasons else "UNKNOWN"
        raise LighterTestnetOperatorError(f"testnet admission blocked: {reason}")
    return credentials, transport, admission, moment


def _persist(path, status, moment, shadow, client_order_id, create_tx_hash, cancel_tx_hash, credentials, transport):
    payload = _payload(status=status, observed_at=moment, shadow_sha=shadow.sha256, client_order_id=client_order_id, create_tx_hash=create_tx_hash, cancel_tx_hash=cancel_tx_hash, credentials=credentials, transport=transport)
    return _result(payload, _write(path, payload))


def run_lighter_testnet_operator_smoke(*, db: Session, session_factory: SessionFactory, shadow_evidence_path: str | Path, result_evidence_path: str | Path, market: LighterMarketFact, client_order_id: str, quantity: Decimal, price: Decimal, is_ask: bool, observed_at: datetime, credential_loader: CredentialLoader = load_lighter_server_credentials, transport_factory: TransportFactory = build_lighter_testnet_transport) -> LighterTestnetSmokeEvidence:
    shadow = load_lighter_shadow_evidence(shadow_evidence_path)
    credentials, transport, admission, moment = _context(db, shadow, observed_at, credential_loader, transport_factory)
    try:
        try:
            smoke = run_lighter_testnet_create_cancel_smoke(admission=admission, session_factory=session_factory, transport=transport, market=market, client_order_id=client_order_id, quantity=quantity, price=price, is_ask=is_ask)
            return _persist(result_evidence_path, "SUCCESS", moment, shadow, smoke.client_order_id, smoke.create_tx_hash, smoke.cancel_tx_hash, credentials, transport)
        except LighterTestnetSmokeError as exc:
            status = "CANCEL_FAILED" if exc.stage == "CANCEL" else "CREATE_FAILED"
            return _persist(result_evidence_path, status, moment, shadow, exc.client_order_id, exc.create_tx_hash, None, credentials, transport)
    finally:
        _safe_close(transport)


def _load_previous(path: str | Path) -> dict[str, Any]:
    try:
        document = _mapping(json.loads(Path(path).read_text(encoding="utf-8")), "prior smoke evidence")
    except (OSError, json.JSONDecodeError):
        raise LighterTestnetOperatorError("prior smoke evidence cannot be loaded") from None
    if document.get("schema") != _RESULT_SCHEMA:
        raise LighterTestnetOperatorError("prior smoke evidence schema is unsupported")
    stored = document.get("evidence_sha256")
    unsigned = dict(document)
    unsigned.pop("evidence_sha256", None)
    expected = hashlib.sha256(_canonical(unsigned)).hexdigest()
    if not isinstance(stored, str) or not hmac.compare_digest(stored, expected):
        raise LighterTestnetOperatorError("prior smoke evidence integrity check failed")
    if document.get("status") != "CANCEL_FAILED":
        raise LighterTestnetOperatorError("prior smoke evidence is not cancel-recoverable")
    if not isinstance(document.get("create_tx_hash"), str) or not document["create_tx_hash"].strip():
        raise LighterTestnetOperatorError("prior smoke evidence lacks create transaction hash")
    if not isinstance(document.get("client_order_id"), str) or not document["client_order_id"].strip():
        raise LighterTestnetOperatorError("prior smoke evidence lacks client order id")
    return document


def recover_lighter_testnet_operator_cancel(*, db: Session, session_factory: SessionFactory, shadow_evidence_path: str | Path, result_evidence_path: str | Path, market: LighterMarketFact, observed_at: datetime, credential_loader: CredentialLoader = load_lighter_server_credentials, transport_factory: TransportFactory = build_lighter_testnet_transport) -> LighterTestnetSmokeEvidence:
    shadow = load_lighter_shadow_evidence(shadow_evidence_path)
    previous = _load_previous(result_evidence_path)
    if previous.get("shadow_evidence_sha256") != shadow.sha256:
        raise LighterTestnetOperatorError("shadow evidence changed since failed smoke")
    credentials, transport, admission, moment = _context(db, shadow, observed_at, credential_loader, transport_factory)
    try:
        if previous.get("account_index") != credentials.account_index or previous.get("api_key_index") != credentials.api_key_index or previous.get("base_url") != getattr(transport, "base_url", None) or previous.get("chain_id") != getattr(transport, "chain_id", None):
            raise LighterTestnetOperatorError("prior smoke evidence scope does not match recovery scope")
        try:
            smoke = run_lighter_testnet_cancel_recovery(admission=admission, session_factory=session_factory, transport=transport, market=market, client_order_id=previous["client_order_id"], create_tx_hash=previous["create_tx_hash"])
            return _persist(result_evidence_path, "SUCCESS", moment, shadow, smoke.client_order_id, smoke.create_tx_hash, smoke.cancel_tx_hash, credentials, transport)
        except LighterTestnetSmokeError as exc:
            return _persist(result_evidence_path, "CANCEL_FAILED", moment, shadow, exc.client_order_id, exc.create_tx_hash, None, credentials, transport)
    finally:
        _safe_close(transport)


__all__ = ["LIGHTER_TESTNET_TRADE_SLOT", "LighterShadowEvidence", "LighterTestnetOperatorError", "LighterTestnetSmokeEvidence", "load_lighter_shadow_evidence", "recover_lighter_testnet_operator_cancel", "run_lighter_testnet_operator_smoke"]
