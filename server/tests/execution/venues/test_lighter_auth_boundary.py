from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_db
from app.integration_secrets import load_secret
from app.main import app
from tests.conftest import DEVICE_HEADERS


CREDENTIAL_FIELDS = {"account_index", "api_key_index", "api_private_key"}


def _client(session):
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app, headers=DEVICE_HEADERS)


def test_lighter_slots_are_explicit_separate_and_write_only(session) -> None:
    with _client(session) as client:
        response = client.get("/api/v1/integrations")
        assert response.status_code == 200
        by_slot = {item["slot"]: item for item in response.json()}

        assert set(("lighter_read", "lighter_testnet_trade", "lighter_trade")) <= set(by_slot)
        assert by_slot["lighter_read"]["venue"] == "LIGHTER"
        assert by_slot["lighter_read"]["environment"] == "live"
        assert by_slot["lighter_testnet_trade"]["environment"] == "testnet"
        assert by_slot["lighter_trade"]["environment"] == "live"
        assert set(by_slot["lighter_read"]["fields"]) == CREDENTIAL_FIELDS
        assert set(by_slot["lighter_testnet_trade"]["fields"]) == CREDENTIAL_FIELDS
        assert set(by_slot["lighter_trade"]["fields"]) == CREDENTIAL_FIELDS
        assert by_slot["lighter_testnet_trade"]["required"] is False

        rendered = response.text.lower()
        assert "eth_private_key" not in rendered
        assert "auth_token" not in rendered
        assert "nonce" not in rendered
        assert "client_order_id" not in rendered


def test_lighter_api_private_key_is_encrypted_and_never_echoed(session) -> None:
    private_key = "0x" + "a1" * 32
    payload = {
        "account_index": "42",
        "api_key_index": "253",
        "api_private_key": private_key,
    }

    with _client(session) as client:
        response = client.put(
            "/api/v1/integrations/lighter_read",
            json={"values": payload},
        )
        assert response.status_code == 200
        assert response.json()["configured"] is True
        assert private_key not in response.text

        listed = client.get("/api/v1/integrations")
        assert private_key not in listed.text

    raw = session.execute(
        text(
            "SELECT encrypted_payload FROM signalai_integration_secrets "
            "WHERE slot = 'lighter_read'"
        )
    ).scalar_one()
    assert private_key.encode() not in bytes(raw)
    assert load_secret(session, "lighter_read") == payload


def test_lighter_runtime_credentials_normalize_indices_and_private_key(session) -> None:
    from app.execution.venues.lighter_auth import (
        LIGHTER_READ_SLOT,
        load_lighter_server_credentials,
    )
    from app.integration_secrets import BY_SLOT, save_secret

    private_key = "0x" + "b2" * 32
    save_secret(
        session,
        BY_SLOT[LIGHTER_READ_SLOT],
        {
            "account_index": "7",
            "api_key_index": "253",
            "api_private_key": private_key,
        },
    )

    credentials = load_lighter_server_credentials(session, LIGHTER_READ_SLOT)
    assert credentials is not None
    assert credentials.account_index == 7
    assert credentials.api_key_index == 253
    assert credentials.api_private_key == "b2" * 32
    assert credentials.environment == "live"
    assert credentials.purpose == "read"
    assert "0x" + credentials.api_private_key not in repr(credentials)
    assert credentials.api_private_key not in repr(credentials)


def test_lighter_secret_validation_fails_closed_on_invalid_indices_or_key(session) -> None:
    cases = (
        ({"account_index": "-1", "api_key_index": "1", "api_private_key": "ab" * 32}, "account_index"),
        ({"account_index": "1", "api_key_index": "254", "api_private_key": "ab" * 32}, "api_key_index"),
        ({"account_index": "1", "api_key_index": "1.5", "api_private_key": "ab" * 32}, "api_key_index"),
        ({"account_index": "1", "api_key_index": "1", "api_private_key": "not-hex"}, "api_private_key"),
    )

    with _client(session) as client:
        for values, expected in cases:
            response = client.put(
                "/api/v1/integrations/lighter_trade",
                json={"values": values},
            )
            assert response.status_code == 422
            assert expected in response.json()["detail"]

    assert load_secret(session, "lighter_trade") is None


def test_lighter_auth_boundary_has_no_signing_nonce_or_order_behavior(session) -> None:
    from app.execution.venues import lighter_auth

    forbidden = (
        "SignerClient",
        "create_auth_token",
        "next_nonce",
        "submit",
        "create_order",
        "cancel_order",
        "withdraw",
    )
    for name in forbidden:
        assert not hasattr(lighter_auth, name)

    source = open(lighter_auth.__file__, encoding="utf-8").read().lower()
    assert "import lighter" not in source
    assert "eth_private_key" not in source
    assert "withdraw" not in source
