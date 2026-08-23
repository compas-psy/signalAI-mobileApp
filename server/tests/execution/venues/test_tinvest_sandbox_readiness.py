from __future__ import annotations

from app.integration_secrets import BY_SLOT, save_secret
from app.execution.venues.tinvest_sandbox_readiness import (
    current_tinvest_sandbox_context,
    current_tinvest_sandbox_readiness,
    record_tinvest_sandbox_roundtrip_proof,
    scoped_sandbox_diagnostic_key,
)


def _configure_sandbox_token(session, token: str) -> None:
    save_secret(
        session,
        BY_SLOT["tinvest_sandbox_trade"],
        {"token": token},
        actor="test",
    )
    session.commit()


def test_roundtrip_proof_is_bound_to_exact_source_and_credential_generation(
    session, monkeypatch
):
    monkeypatch.setenv("SIGNALAI_SOURCE_SHA", "a" * 40)
    _configure_sandbox_token(session, "sandbox-token-generation-one")

    context = current_tinvest_sandbox_context(session)
    assert context.source_sha == "a" * 40
    assert current_tinvest_sandbox_readiness(session).ready is False

    proof_id = record_tinvest_sandbox_roundtrip_proof(
        session,
        context=context,
        symbol="LQDT",
        account_suffix="123456",
        buy_order_id="buy-order-safe",
        buy_status="EXECUTION_REPORT_STATUS_FILL",
        buy_executed_lots=1,
        sell_order_id="sell-order-safe",
        sell_status="EXECUTION_REPORT_STATUS_FILL",
        sell_executed_lots=1,
        position_flat=True,
    )
    session.commit()

    ready = current_tinvest_sandbox_readiness(session)
    assert ready.ready is True
    assert ready.proof_id == proof_id

    monkeypatch.setenv("SIGNALAI_SOURCE_SHA", "b" * 40)
    changed_release = current_tinvest_sandbox_readiness(session)
    assert changed_release.ready is False
    assert "current release has no provider-confirmed sandbox round trip" in changed_release.notes

    monkeypatch.setenv("SIGNALAI_SOURCE_SHA", "a" * 40)
    _configure_sandbox_token(session, "sandbox-token-generation-two")
    rotated_credential = current_tinvest_sandbox_readiness(session)
    assert rotated_credential.ready is False
    assert "current sandbox credential has no provider-confirmed round trip" in rotated_credential.notes


def test_scoped_provider_identity_changes_with_release_or_credential(session, monkeypatch):
    monkeypatch.setenv("SIGNALAI_SOURCE_SHA", "c" * 40)
    _configure_sandbox_token(session, "sandbox-token-generation-a")
    first = current_tinvest_sandbox_context(session)
    first_id = scoped_sandbox_diagnostic_key("mobile-sandbox-roundtrip-v1", first)

    # Exact same source + credential produces the same provider identities on retry.
    assert scoped_sandbox_diagnostic_key("mobile-sandbox-roundtrip-v1", first) == first_id

    monkeypatch.setenv("SIGNALAI_SOURCE_SHA", "d" * 40)
    second = current_tinvest_sandbox_context(session)
    assert scoped_sandbox_diagnostic_key("mobile-sandbox-roundtrip-v1", second) != first_id


def test_readiness_is_fail_closed_without_source_or_credential(session, monkeypatch):
    monkeypatch.delenv("SIGNALAI_SOURCE_SHA", raising=False)
    readiness = current_tinvest_sandbox_readiness(session)
    assert readiness.ready is False
    assert readiness.proof_id is None
