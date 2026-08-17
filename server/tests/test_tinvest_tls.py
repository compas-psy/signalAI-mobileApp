import hashlib
import ssl

import app.capital.runtime as runtime


OFFICIAL_TBANK_ROOT_DER_SHA256 = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"


def test_tinvest_tls_context_keeps_verification_and_pins_official_root():
    """T-Invest moved to Russian Trusted CA; verification must stay strict."""
    ca_file = runtime.TINVEST_CA_FILE
    assert ca_file.suffix == ".crt"  # public certificate, not secret-bearing PEM material
    assert ca_file.is_file()
    der = ssl.PEM_cert_to_DER_cert(ca_file.read_text(encoding="utf-8"))
    assert hashlib.sha256(der).hexdigest() == OFFICIAL_TBANK_ROOT_DER_SHA256

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
