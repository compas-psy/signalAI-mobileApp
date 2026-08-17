"""Scope T-Invest owner capital to the one account the trading token can use.

The read-only token intentionally sees every T-Invest account.  That is useful
for diagnostics, but it is the wrong risk base for FORTS: trading can happen
only on the account exposed by ``tinvest_trade``.  We therefore use the trade
token only to resolve that account id, then read its portfolio with the
read-only token.

On a successful refresh, obsolete T-Invest Account rows are pruned so an old
multi-account snapshot cannot keep inflating Today after the scope changes.
"""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..models import Account
from . import runtime

TINVEST_TRADE_SLOT = "tinvest_trade"
_ORIGINAL_UPSERT = runtime._upsert_accounts


def _single_open_trade_account(db: Session) -> dict:
    secret = runtime.load_secret(db, TINVEST_TRADE_SLOT)
    if not secret:
        raise runtime.CapitalSourceNotConfigured(
            "trade-token не задан: торговый счёт определить нельзя"
        )
    token = secret.get("token", "").strip()
    if not token:
        raise runtime.CapitalSourceNotConfigured(
            "trade-token пуст: торговый счёт определить нельзя"
        )

    raw_accounts = runtime._tinvest_post(
        token,
        "UsersService",
        "GetAccounts",
        {"status": "ACCOUNT_STATUS_OPEN"},
    ).get("accounts")
    rows = raw_accounts if isinstance(raw_accounts, list) else []
    valid = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    ]
    if len(valid) != 1:
        raise runtime.CapitalSourceError(
            "торговый токен должен видеть ровно один торговый счёт; "
            f"получено {len(valid)}"
        )
    return valid[0]


def read_tinvest_trading_account(db: Session) -> tuple[runtime.SourceAccount, ...]:
    read_secret = runtime.load_secret(db, runtime.TINVEST_SLOT)
    if not read_secret:
        raise runtime.CapitalSourceNotConfigured("read-token не задан")
    read_token = read_secret.get("token", "").strip()
    if not read_token:
        raise runtime.CapitalSourceNotConfigured("read-token пуст")

    target = _single_open_trade_account(db)
    account_id = str(target.get("id") or "").strip()
    portfolio = runtime._tinvest_post(
        read_token,
        "OperationsService",
        "GetPortfolio",
        {"accountId": account_id, "currency": "RUB"},
    )
    equity, currency = runtime.parse_tinvest_portfolio_equity(portfolio)
    title = str(target.get("name") or "").strip() or "Торговый счёт"
    return (
        runtime.SourceAccount(
            external_id=account_id,
            title=title,
            currency=currency or "RUB",
            equity=equity,
        ),
    )


def upsert_scoped_accounts(
    db: Session,
    *,
    broker: str,
    circuit: str,
    accounts: tuple[runtime.SourceAccount, ...],
    now,
) -> None:
    _ORIGINAL_UPSERT(
        db,
        broker=broker,
        circuit=circuit,
        accounts=accounts,
        now=now,
    )
    if broker != "tinvest" or not accounts:
        return

    active_ids = tuple(account.external_id for account in accounts)
    db.execute(
        delete(Account).where(
            Account.broker == broker,
            Account.external_id.notin_(active_ids),
        )
    )
    db.flush()


def install() -> None:
    runtime._read_tinvest = read_tinvest_trading_account
    if runtime._upsert_accounts is not upsert_scoped_accounts:
        runtime._upsert_accounts = upsert_scoped_accounts


__all__ = ["install", "read_tinvest_trading_account", "upsert_scoped_accounts"]
