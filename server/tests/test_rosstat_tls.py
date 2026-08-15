from __future__ import annotations

import hashlib
import ssl

from app.research import collector
from app.research.adapters import rosstat_prices


ROOT_SHA256 = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"
SUB_SHA256 = "2155785036c900dbb5f1bb2a1569c80c55595bd6bf94867a29bbddbc7d88a3f2"


def _ca_fingerprints(context: ssl.SSLContext) -> set[str]:
    return {
        hashlib.sha256(certificate).hexdigest()
        for certificate in context.get_ca_certs(binary_form=True)
    }


def test_rosstat_tls_context_keeps_hostname_and_certificate_verification_enabled():
    context = rosstat_prices.tls_context()

    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    fingerprints = _ca_fingerprints(context)
    assert ROOT_SHA256 in fingerprints
    assert SUB_SHA256 in fingerprints


def test_tls_context_is_source_scoped_to_official_https_hosts():
    assert isinstance(
        rosstat_prices.tls_context_for("https://rosstat.gov.ru/statistics/price"),
        ssl.SSLContext,
    )
    assert isinstance(
        rosstat_prices.tls_context_for(
            "https://www.rosstat.gov.ru/storage/mediabank/file.xlsx"
        ),
        ssl.SSLContext,
    )
    assert rosstat_prices.tls_context_for("http://rosstat.gov.ru/statistics/price") is None
    assert rosstat_prices.tls_context_for("https://example.org/file.xlsx") is None


def test_collector_passes_custom_context_only_to_rosstat(monkeypatch):
    contexts: list[object | None] = []

    class Response:
        status = 200
        headers = {"Content-Type": "text/html"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, *_args):
            return b"ok"

    def fake_urlopen(request, **kwargs):
        contexts.append(kwargs.get("context"))
        return Response()

    monkeypatch.setattr(collector.urllib.request, "urlopen", fake_urlopen)

    assert collector._text("https://rosstat.gov.ru/statistics/price") == "ok"
    assert collector._text("https://www.cbr.ru/hd_base/") == "ok"

    assert isinstance(contexts[0], ssl.SSLContext)
    assert contexts[1] is None
