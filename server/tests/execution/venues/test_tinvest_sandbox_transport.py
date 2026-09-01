from __future__ import annotations

import io
import json
import urllib.error

import pytest

from app.integration_secrets import BY_SLOT, save_secret
from app.execution.venues.tinvest import TInvestProviderError
from app.execution.venues.tinvest_transport import (
    TINVEST_SANDBOX_REST_BASE,
    TInvestSandboxHttpTransport,
    build_tinvest_sandbox_transport,
)


class _Response:
    def __init__(self, body: dict[str, object], status: int = 200) -> None:
        self._payload = json.dumps(body).encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit: int = -1) -> bytes:
        return self._payload if limit < 0 else self._payload[:limit]


class _CapturingOpener:
    def __init__(self, response: _Response | Exception) -> None:
        self.response = response
        self.requests = []
        self.timeouts: list[float] = []

    def __call__(self, request, *, timeout: float):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_transport_is_fixed_to_official_sandbox_rest_and_uses_bearer_json():
    assert TINVEST_SANDBOX_REST_BASE == "https://sandbox-invest-public-api.tbank.ru/rest"
    opener = _CapturingOpener(_Response({"accounts": []}))
    token = "sandbox-private-bearer"
    transport = TInvestSandboxHttpTransport(token=token, opener=opener)

    result = transport.call("SandboxService", "GetSandboxAccounts", {})

    assert result == {"accounts": []}
    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert request.full_url == (
        "https://sandbox-invest-public-api.tbank.ru/rest/"
        "tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxAccounts"
    )
    assert request.get_header("Authorization") == f"Bearer {token}"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {}
    assert opener.timeouts == [pytest.approx(15.0)]
    assert token not in repr(transport)


def test_transport_rejects_path_injection_before_network():
    opener = _CapturingOpener(_Response({}))
    transport = TInvestSandboxHttpTransport(token="secret-token", opener=opener)

    for service, method in [
        ("../OrdersService", "GetOrders"),
        ("SandboxService", "../PostOrder"),
        ("OrdersService?host=live", "GetOrders"),
        ("SandboxService", "Post/Order"),
    ]:
        with pytest.raises(TInvestProviderError) as captured:
            transport.call(service, method, {})
        assert captured.value.code == "INVALID_REQUEST"
    assert opener.requests == []


def test_transport_sanitizes_http_and_network_failures_without_token_or_raw_body():
    token = "do-not-leak-this-token"
    raw_provider_body = b'{"code":16,"message":"provider says account secret detail"}'
    http_error = urllib.error.HTTPError(
        url="https://sandbox-invest-public-api.tbank.ru/rest/redacted",
        code=401,
        msg="Unauthorized",
        hdrs=None,
        fp=io.BytesIO(raw_provider_body),
    )
    transport = TInvestSandboxHttpTransport(
        token=token,
        opener=_CapturingOpener(http_error),
    )

    with pytest.raises(TInvestProviderError) as captured:
        transport.call("SandboxService", "GetSandboxAccounts", {})
    public = str(captured.value)
    assert captured.value.code == "HTTP_401"
    assert token not in public
    assert "account secret detail" not in public
    assert len(public) < 200

    network = TInvestSandboxHttpTransport(
        token=token,
        opener=_CapturingOpener(urllib.error.URLError("proxy contains secret detail")),
    )
    with pytest.raises(TInvestProviderError) as network_error:
        network.call("SandboxService", "GetSandboxAccounts", {})
    assert network_error.value.code == "TRANSPORT"
    assert token not in str(network_error.value)
    assert "proxy contains secret detail" not in str(network_error.value)


def test_transport_preserves_rpc_not_found_even_when_rest_uses_http_400():
    token = "do-not-leak-this-token"
    http_error = urllib.error.HTTPError(
        url="https://sandbox-invest-public-api.tbank.ru/rest/redacted",
        code=400,
        msg="Bad Request",
        hdrs=None,
        fp=io.BytesIO(
            json.dumps(
                {
                    "code": 5,
                    "message": "50005 order not found; private provider context",
                    "details": [],
                }
            ).encode("utf-8")
        ),
    )
    transport = TInvestSandboxHttpTransport(
        token=token,
        opener=_CapturingOpener(http_error),
    )

    with pytest.raises(TInvestProviderError) as captured:
        transport.call("SandboxService", "GetSandboxOrderState", {})

    assert captured.value.code == "NOT_FOUND"
    assert captured.value.is_not_found is True
    assert token not in str(captured.value)
    assert "private provider context" not in str(captured.value)


def test_factory_loads_only_server_sandbox_slot_and_fails_before_network(session):
    with pytest.raises(TInvestProviderError) as missing:
        build_tinvest_sandbox_transport(session)
    assert missing.value.code == "CREDENTIAL_MISSING"

    token = "server-owned-sandbox-token"
    save_secret(
        session,
        BY_SLOT["tinvest_sandbox_trade"],
        {"token": token},
        actor="test",
    )
    transport = build_tinvest_sandbox_transport(session, opener=_CapturingOpener(_Response({})))
    assert isinstance(transport, TInvestSandboxHttpTransport)
    assert token not in repr(transport)


def test_transport_rejects_empty_token_at_construction():
    with pytest.raises(TInvestProviderError) as captured:
        TInvestSandboxHttpTransport(token="   ")
    assert captured.value.code == "CREDENTIAL_INVALID"
