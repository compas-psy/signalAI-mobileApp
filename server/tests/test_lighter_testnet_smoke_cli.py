from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace


def _request():
    return {
        "schema": "signalai.lighter.testnet-smoke-request.v1",
        "client_order_id": "sai077-cli-1",
        "quantity": "0.01",
        "price": "4000",
        "is_ask": False,
        "market": {
            "market_id": 0,
            "symbol": "ETH",
            "status": "active",
            "min_base_amount": "0.001",
            "min_quote_amount": "5",
            "size_decimals": 4,
            "price_decimals": 2,
            "quote_decimals": 2,
            "maker_fee_pct": "0.0001",
            "taker_fee_pct": "0.0004",
            "liquidation_fee_pct": "0.005",
            "order_quote_limit": "1000000",
            "multiplier": "1",
            "observed_at": "2026-08-22T02:00:00+00:00",
        },
    }


def test_cli_request_parser_builds_market_without_any_credential_field(tmp_path):
    from app.lighter_testnet_smoke_cli import load_smoke_request

    path = tmp_path / "request.json"
    path.write_text(json.dumps(_request()), encoding="utf-8")
    request = load_smoke_request(path)
    assert request.client_order_id == "sai077-cli-1"
    assert request.quantity == Decimal("0.01")
    assert request.market.market_id == 0
    assert request.market.observed_at == datetime(2026, 8, 22, 2, 0, tzinfo=UTC)


def test_cli_rejects_credential_like_fields(tmp_path):
    from app.lighter_testnet_smoke_cli import LighterSmokeCliError, load_smoke_request

    for field in ("api_private_key", "api_key", "secret", "account_index"):
        request = _request()
        request[field] = "must-not-enter-cli"
        path = tmp_path / f"{field}.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        try:
            load_smoke_request(path)
        except LighterSmokeCliError as exc:
            assert "request fields" in str(exc)
        else:
            raise AssertionError(f"credential-like field {field} was accepted")


def test_cli_prints_only_redacted_evidence_summary(monkeypatch, tmp_path, capsys):
    import app.lighter_testnet_smoke_cli as cli

    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request()), encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "run_lighter_testnet_operator_smoke",
        lambda **kwargs: SimpleNamespace(
            status="SUCCESS",
            evidence_sha256="f" * 64,
            create_tx_hash="0xcreate",
            cancel_tx_hash="0xcancel",
            eligible_for_live=False,
        ),
    )
    monkeypatch.setattr(cli, "get_session_factory", lambda: lambda: SimpleNamespace(close=lambda: None))

    code = cli.main([
        "--shadow-evidence", str(tmp_path / "shadow.json"),
        "--request", str(request_path),
        "--result-evidence", str(tmp_path / "result.json"),
    ])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output == {
        "status": "SUCCESS",
        "evidence_sha256": "f" * 64,
        "create_tx_hash": "0xcreate",
        "cancel_tx_hash": "0xcancel",
        "eligible_for_live": False,
    }


def test_cli_maps_cancel_failed_to_recoverable_exit_code(monkeypatch, tmp_path, capsys):
    import app.lighter_testnet_smoke_cli as cli

    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request()), encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "run_lighter_testnet_operator_smoke",
        lambda **kwargs: SimpleNamespace(
            status="CANCEL_FAILED",
            evidence_sha256="e" * 64,
            create_tx_hash="0xcreate",
            cancel_tx_hash=None,
            eligible_for_live=False,
        ),
    )
    monkeypatch.setattr(cli, "get_session_factory", lambda: lambda: SimpleNamespace(close=lambda: None))
    code = cli.main([
        "--shadow-evidence", str(tmp_path / "shadow.json"),
        "--request", str(request_path),
        "--result-evidence", str(tmp_path / "result.json"),
    ])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "CANCEL_FAILED"
