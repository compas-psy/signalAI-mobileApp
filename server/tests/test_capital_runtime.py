from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.capital import runtime
from app.capital.runtime import parse_bybit_wallet, parse_tinvest_portfolio_equity
from app.models import Account


def test_tinvest_portfolio_equity_reads_money_value_in_rubles():
    payload = {
        "totalAmountPortfolio": {"currency": "rub", "units": "1250000", "nano": 250000000}
    }

    value, currency = parse_tinvest_portfolio_equity(payload)

    assert value == Decimal("1250000.25")
    assert currency == "RUB"


def test_bybit_wallet_keeps_account_equity_in_usd_without_fake_ruble_total():
    payload = {
        "result": {
            "list": [
                {
                    "totalEquity": "4321.50",
                    "totalAvailableBalance": "4000.25",
                }
            ]
        }
    }

    equity, available, currency = parse_bybit_wallet(payload)

    assert equity == Decimal("4321.50")
    assert available == Decimal("4000.25")
    assert currency == "USD"


def test_tinvest_capital_uses_trade_token_to_select_single_account(monkeypatch):
    secrets = {
        "tinvest_invest_read": {"token": "read-token"},
        "tinvest_trade": {"token": "trade-token"},
    }
    calls: list[tuple[str, str, str, dict]] = []

    monkeypatch.setattr(runtime, "load_secret", lambda _db, slot: secrets.get(slot))

    def fake_post(token: str, service: str, method: str, body: dict) -> dict:
        calls.append((token, service, method, body))
        if token == "trade-token" and method == "GetAccounts":
            return {
                "accounts": [
                    {"id": "futures-account", "name": "Фьючерсы", "type": "ACCOUNT_TYPE_TINKOFF"}
                ]
            }
        if token == "read-token" and method == "GetPortfolio":
            assert body["accountId"] == "futures-account"
            return {
                "totalAmountPortfolio": {
                    "currency": "rub",
                    "units": "735000",
                    "nano": 500000000,
                }
            }
        raise AssertionError((token, service, method, body))

    monkeypatch.setattr(runtime, "_tinvest_post", fake_post)

    accounts = runtime._read_tinvest(object())

    assert len(accounts) == 1
    assert accounts[0].external_id == "futures-account"
    assert accounts[0].title == "Фьючерсы"
    assert accounts[0].equity == Decimal("735000.5")
    assert calls == [
        ("trade-token", "UsersService", "GetAccounts", {"status": "ACCOUNT_STATUS_OPEN"}),
        (
            "read-token",
            "OperationsService",
            "GetPortfolio",
            {"accountId": "futures-account", "currency": "RUB"},
        ),
    ]


def test_tinvest_capital_fails_closed_if_trade_token_does_not_identify_one_account(monkeypatch):
    secrets = {
        "tinvest_invest_read": {"token": "read-token"},
        "tinvest_trade": {"token": "trade-token"},
    }
    monkeypatch.setattr(runtime, "load_secret", lambda _db, slot: secrets.get(slot))
    monkeypatch.setattr(
        runtime,
        "_tinvest_post",
        lambda *_args, **_kwargs: {"accounts": [{"id": "one"}, {"id": "two"}]},
    )

    with pytest.raises(runtime.CapitalSourceError, match="ровно один торговый счёт"):
        runtime._read_tinvest(object())


def test_tinvest_success_prunes_old_nontrading_accounts(session):
    moment = datetime(2026, 8, 17, 13, 30, tzinfo=UTC)
    session.add_all(
        [
            Account(
                broker="tinvest",
                external_id="old-brokerage",
                title="Основной",
                currency="RUB",
                circuit="investment",
                equity=Decimal("3000000"),
                synced_at=moment,
            ),
            Account(
                broker="tinvest",
                external_id="futures-account",
                title="Фьючерсы",
                currency="RUB",
                circuit="investment",
                equity=Decimal("700000"),
                synced_at=moment,
            ),
        ]
    )
    session.flush()

    runtime._upsert_accounts(
        session,
        broker="tinvest",
        circuit="investment",
        accounts=(
            runtime.SourceAccount(
                external_id="futures-account",
                title="Фьючерсы",
                currency="RUB",
                equity=Decimal("735000.5"),
            ),
        ),
        now=moment,
    )

    rows = tuple(
        session.execute(
            select(Account).where(Account.broker == "tinvest").order_by(Account.external_id)
        ).scalars()
    )
    assert [(row.external_id, Decimal(row.equity)) for row in rows] == [
        ("futures-account", Decimal("735000.5"))
    ]
