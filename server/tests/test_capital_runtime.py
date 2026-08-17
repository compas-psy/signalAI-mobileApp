from decimal import Decimal

from app.capital.runtime import parse_bybit_wallet, parse_tinvest_portfolio_equity


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
