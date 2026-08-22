import pytest

from datetime import UTC, datetime

from app.device_enrollment import (
    bootstrap_pairing_session_config,
    issue_device_token,
    normalize_device_metadata,
    token_verifier,
)
from app.risk.manual_preview import ManualRiskPreviewRejected, _preview_signing_key


def test_issued_device_token_is_high_entropy_and_only_verifier_is_storable():
    token = issue_device_token()

    assert len(token) >= 43
    assert token_verifier(token) != token
    assert len(token_verifier(token)) == 64


def test_device_token_verifier_rejects_short_or_noncanonical_bearers():
    with pytest.raises(ValueError, match="malformed"):
        token_verifier("a" * 32)
    with pytest.raises(ValueError, match="malformed"):
        token_verifier("a" * 43 + "=")


def test_device_metadata_is_allowlisted_and_bounded():
    metadata = normalize_device_metadata(
        {"label": "Owner phone", "platform": "android", "app_version": "1.0.0"}
    )

    assert metadata == {
        "label": "Owner phone",
        "platform": "android",
        "app_version": "1.0.0",
    }
    with pytest.raises(ValueError, match="metadata"):
        normalize_device_metadata({"label": "x" * 65})
    with pytest.raises(ValueError, match="metadata"):
        normalize_device_metadata({"unexpected": "field"})


def test_bootstrap_secret_cannot_be_reused_as_a_business_signing_secret(monkeypatch):
    monkeypatch.setenv("SIGNALAI_DEVICE_TOKEN", "bootstrap-only-secret")
    monkeypatch.delenv("SIGNALAI_RISK_PREVIEW_SIGNING_KEY", raising=False)

    with pytest.raises(ManualRiskPreviewRejected, match="signing secret"):
        _preview_signing_key()


def test_bootstrap_pairing_needs_an_independent_unexpired_bounded_session(monkeypatch):
    """Removing the session check would let the static bootstrap mint forever."""
    moment = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setenv(
        "SIGNALAI_DEVICE_PAIRING_SESSION_ID",
        "s" * 43,
    )
    monkeypatch.setenv(
        "SIGNALAI_DEVICE_PAIRING_EXPIRES_AT",
        "2026-08-21T12:05:00Z",
    )
    monkeypatch.setenv("SIGNALAI_DEVICE_PAIRING_MAX_USES", "1")

    session = bootstrap_pairing_session_config(now=moment)

    assert session.session_id == "s" * 43
    assert session.max_uses == 1
    assert session.expires_at == datetime(2026, 8, 21, 12, 5, tzinfo=UTC)
    assert session.verifier != session.session_id


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({}, "session"),
        (
            {
                "SIGNALAI_DEVICE_PAIRING_SESSION_ID": "s" * 43,
                "SIGNALAI_DEVICE_PAIRING_EXPIRES_AT": "2026-08-21T11:59:59Z",
            },
            "expired",
        ),
        (
            {
                "SIGNALAI_DEVICE_PAIRING_SESSION_ID": "s" * 43,
                "SIGNALAI_DEVICE_PAIRING_EXPIRES_AT": "2026-08-21T12:05:00Z",
                "SIGNALAI_DEVICE_PAIRING_MAX_USES": "0",
            },
            "uses",
        ),
    ],
)
def test_bootstrap_pairing_session_configuration_fails_closed(monkeypatch, environment, message):
    for key in (
        "SIGNALAI_DEVICE_PAIRING_SESSION_ID",
        "SIGNALAI_DEVICE_PAIRING_EXPIRES_AT",
        "SIGNALAI_DEVICE_PAIRING_MAX_USES",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match=message):
        bootstrap_pairing_session_config(
            now=datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
        )
