import hashlib
import ssl

import app.capital.runtime as runtime


OFFICIAL_TBANK_ROOT_PEM_SHA256 = "e4370c9b6b540f063ba1829222d2d6041cbb0bfc5d001ee6bbb97620914594dc"


def test_tinvest_tls_context_keeps_verification_and_pins_official_root():
    """T-Invest moved to Russian Trusted CA; verification must stay strict."""
    ca_file = runtime.TINVEST_CA_FILE
    assert ca_file.is_file()
    assert hashlib.sha256(ca_file.read_bytes()).hexdigest() == OFFICIAL_TBANK_ROOT_PEM_SHA256

    context = runtime._tinvest_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_tinvest_requests_use_scoped_ca_context(monkeypatch):
    seen = {}

    def fake_request(url, *, method="GET", headers=None, body=None, ssl_context=None):
        seen["url"] = url
        seen["ssl_context"] = ssl_context
        return {"accounts": []}

    monkeypatch.setattr(runtime, "_request_json", fake_request)

    runtime._tinvest_post("token", "UsersService", "GetAccounts", {})

    assert seen["url"].startswith("https://invest-public-api.tbank.ru/rest/")
    assert isinstance(seen["ssl_context"], ssl.SSLContext)
    assert seen["ssl_context"].verify_mode == ssl.CERT_REQUIRED
    assert seen["ssl_context"].check_hostname is True
