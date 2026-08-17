"""Server-owned read model for owner capital.

Only read credentials are used here.  A source refresh is atomic from the
owner's point of view: a failed broker request never replaces the previous
successful Account rows with zeroes.  The API therefore has an explicit
fresh/stale/unavailable state instead of making an outage look like lost
capital.

T-Invest Sandbox is intentionally not part of this module.  Its token remains
on the Android device and is used only to mirror confirmed FORTS paper plans.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..integration_secrets import load_secret
from ..models import Account, DataQualityEvent

TINVEST_SLOT = "tinvest_invest_read"
BYBIT_SLOT = "bybit_read"
TINVEST_SOURCE = "capital_tinvest"
BYBIT_SOURCE = "capital_bybit"
TINVEST_CA_FILE = Path(__file__).with_name("RussianTrustedRootCA.pem")

_FRESH_FOR = timedelta(minutes=10)
_TIMEOUT_SECONDS = 20


class CapitalSourceError(RuntimeError):
    pass


class CapitalSourceNotConfigured(CapitalSourceError):
    pass


@dataclass(frozen=True, slots=True)
class SourceAccount:
    external_id: str
    title: str
    currency: str
    equity: Decimal
    free_margin: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SourceState:
    source: str
    title: str
    status: str
    accounts: tuple[SourceAccount, ...]
    synced_at: datetime | None
    note: str = ""

    @property
    def equity_by_currency(self) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for account in self.accounts:
            result[account.currency] = result.get(account.currency, Decimal(0)) + account.equity
        return result


@dataclass(frozen=True, slots=True)
class CapitalState:
    generated_at: datetime
    sources: tuple[SourceState, ...]

    @property
    def incomplete(self) -> bool:
        return any(source.status != "fresh" for source in self.sources)


def _decimal(raw, default: Decimal = Decimal(0)) -> Decimal:
    try:
        if raw in (None, ""):
            return default
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _quotation(raw) -> tuple[Decimal, str]:
    if not isinstance(raw, dict):
        return Decimal(0), "RUB"
    units = _decimal(raw.get("units"))
    nano = _decimal(raw.get("nano"))
    currency = str(raw.get("currency") or "rub").upper()
    return units + nano / Decimal(1_000_000_000), currency


def parse_tinvest_portfolio_equity(payload: dict) -> tuple[Decimal, str]:
    """Read the broker's total portfolio amount without reconstructing it."""
    value, currency = _quotation(payload.get("totalAmountPortfolio"))
    return value, currency


def parse_bybit_wallet(payload: dict) -> tuple[Decimal, Decimal, str]:
    """Bybit account-wide totals are USD-valued; keep that unit explicit."""
    result = payload.get("result") if isinstance(payload, dict) else None
    rows = result.get("list") if isinstance(result, dict) else None
    row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    return (
        _decimal(row.get("totalEquity")),
        _decimal(row.get("totalAvailableBalance")),
        "USD",
    )


def _request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urlopen(  # noqa: S310 - fixed broker hosts
            request, timeout=_TIMEOUT_SECONDS, context=ssl_context
        ) as response:
            decoded = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        raise CapitalSourceError(f"HTTP {exc.code}") from exc
    except URLError as exc:
        reason = exc.reason
        detail = type(reason).__name__ if isinstance(reason, BaseException) else type(exc).__name__
        raise CapitalSourceError(detail) from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise CapitalSourceError(type(exc).__name__) from exc
    if not isinstance(decoded, dict):
        raise CapitalSourceError("неожиданный формат ответа")
    return decoded


def _tinvest_ssl_context() -> ssl.SSLContext:
    """Default public CAs plus T-Bank's official Russian Trusted Root.

    The extra root is scoped to T-Invest requests only; TLS verification and
    hostname checks remain enabled.  The PEM is the file embedded in the
    official T-Bank Python SDK 1.49.2.
    """
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(TINVEST_CA_FILE))
    return context


def _tinvest_post(token: str, service: str, method: str, body: dict) -> dict:
    base = "https://invest-public-api.tbank.ru/rest"
    namespace = "tinkoff.public.invest.api.contract.v1"
    return _request_json(
        f"{base}/{namespace}.{service}/{method}",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        body=body,
        ssl_context=_tinvest_ssl_context(),
    )


def _read_tinvest(db: Session) -> tuple[SourceAccount, ...]:
    secret = load_secret(db, TINVEST_SLOT)
    if not secret:
        raise CapitalSourceNotConfigured("read-token не задан")
    token = secret.get("token", "").strip()
    if not token:
        raise CapitalSourceNotConfigured("read-token пуст")

    raw_accounts = _tinvest_post(
        token,
        "UsersService",
        "GetAccounts",
        {"status": "ACCOUNT_STATUS_OPEN"},
    ).get("accounts")
    rows = raw_accounts if isinstance(raw_accounts, list) else []
    accounts: list[SourceAccount] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        account_id = str(row.get("id") or "").strip()
        if not account_id:
            continue
        portfolio = _tinvest_post(
            token,
            "OperationsService",
            "GetPortfolio",
            {"accountId": account_id, "currency": "RUB"},
        )
        equity, currency = parse_tinvest_portfolio_equity(portfolio)
        title = str(row.get("name") or "").strip() or (
            "ИИС" if "IIS" in str(row.get("type") or "") else "Брокерский счёт"
        )
        accounts.append(
            SourceAccount(
                external_id=account_id,
                title=title,
                currency=currency or "RUB",
                equity=equity,
            )
        )
    return tuple(accounts)


def _read_bybit(db: Session) -> tuple[SourceAccount, ...]:
    secret = load_secret(db, BYBIT_SLOT)
    if not secret:
        raise CapitalSourceNotConfigured("read API key не задан")
    api_key = secret.get("api_key", "").strip()
    api_secret = secret.get("api_secret", "").strip()
    if not api_key or not api_secret:
        raise CapitalSourceNotConfigured("read API key/secret неполны")

    recv_window = "5000"
    timestamp = str(int(time.time() * 1000))
    query = urlencode({"accountType": "UNIFIED"})
    payload = f"{timestamp}{api_key}{recv_window}{query}"
    signature = hmac.new(
        api_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    response = _request_json(
        f"https://api.bybit.com/v5/account/wallet-balance?{query}",
        headers={
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": signature,
        },
    )
    if int(response.get("retCode") or 0) != 0:
        raise CapitalSourceError(str(response.get("retMsg") or "Bybit отказал"))
    equity, available, currency = parse_bybit_wallet(response)
    return (
        SourceAccount(
            external_id="UNIFIED",
            title="Bybit Unified",
            currency=currency,
            equity=equity,
            free_margin=available,
        ),
    )


def _upsert_accounts(
    db: Session,
    *,
    broker: str,
    circuit: str,
    accounts: tuple[SourceAccount, ...],
    now: datetime,
) -> None:
    for incoming in accounts:
        row = db.execute(
            select(Account).where(
                Account.broker == broker,
                Account.external_id == incoming.external_id,
            )
        ).scalar_one_or_none()
        if row is None:
            row = Account(
                broker=broker,
                external_id=incoming.external_id,
                title=incoming.title,
                currency=incoming.currency,
                circuit=circuit,
                equity=incoming.equity,
                free_margin=incoming.free_margin,
                synced_at=now,
            )
            db.add(row)
        else:
            row.title = incoming.title
            row.currency = incoming.currency
            row.equity = incoming.equity
            row.free_margin = incoming.free_margin
            row.synced_at = now
    db.flush()


def _event(db: Session, source: str, flag: str, detail: str, payload: dict | None = None) -> None:
    db.add(
        DataQualityEvent(
            source=source,
            flag=flag,
            detail=detail[:512],
            payload_json=payload or {},
        )
    )
    db.flush()


def _refresh_one(
    db: Session,
    *,
    source: str,
    broker: str,
    circuit: str,
    read,
    now: datetime,
) -> str:
    try:
        accounts = read(db)
        _upsert_accounts(
            db,
            broker=broker,
            circuit=circuit,
            accounts=accounts,
            now=now,
        )
        _event(
            db,
            source,
            "CAPITAL_REFRESH_OK",
            f"счетов {len(accounts)}",
            {"accounts": len(accounts)},
        )
        return f"{broker}: счетов {len(accounts)}"
    except CapitalSourceNotConfigured as exc:
        _event(db, source, "CAPITAL_NOT_CONFIGURED", str(exc))
        return f"{broker}: не настроен"
    except Exception as exc:  # fail source-local; keep last-known-good rows
        _event(db, source, "CAPITAL_REFRESH_FAILED", f"{type(exc).__name__}: {exc}")
        return f"{broker}: stale ({type(exc).__name__})"


def refresh(db: Session, *, now: datetime | None = None) -> str:
    """Refresh both brokers independently and keep last-known-good on errors."""
    moment = now or datetime.now(UTC)
    notes = [
        _refresh_one(
            db,
            source=TINVEST_SOURCE,
            broker="tinvest",
            circuit="investment",
            read=_read_tinvest,
            now=moment,
        ),
        _refresh_one(
            db,
            source=BYBIT_SOURCE,
            broker="bybit",
            circuit="risk",
            read=_read_bybit,
            now=moment,
        ),
    ]
    return " · ".join(notes)


def _latest_event(db: Session, source: str) -> DataQualityEvent | None:
    return db.execute(
        select(DataQualityEvent)
        .where(DataQualityEvent.source == source)
        .order_by(DataQualityEvent.occurred_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _source_state(
    db: Session,
    *,
    broker: str,
    source: str,
    title: str,
    now: datetime,
) -> SourceState:
    rows = tuple(
        db.execute(
            select(Account)
            .where(Account.broker == broker)
            .order_by(Account.title, Account.external_id)
        ).scalars()
    )
    accounts = tuple(
        SourceAccount(
            external_id=row.external_id,
            title=row.title,
            currency=row.currency,
            equity=Decimal(row.equity),
            free_margin=None if row.free_margin is None else Decimal(row.free_margin),
        )
        for row in rows
    )
    synced_at = max((row.synced_at for row in rows), default=None)
    event = _latest_event(db, source)
    flag = event.flag if event is not None else ""
    note = event.detail if event is not None else "снимок ещё не получен"

    if flag == "CAPITAL_NOT_CONFIGURED":
        status = "not_configured"
    elif flag == "CAPITAL_REFRESH_FAILED":
        status = "stale" if accounts else "unavailable"
    elif not accounts or synced_at is None:
        status = "unavailable"
    elif now - synced_at > _FRESH_FOR:
        status = "stale"
        note = f"последний успешный снимок {synced_at.isoformat()}"
    else:
        status = "fresh"
        if flag == "CAPITAL_REFRESH_OK":
            note = "сверено сервером"

    return SourceState(
        source=broker,
        title=title,
        status=status,
        accounts=accounts,
        synced_at=synced_at,
        note=note,
    )


def state(db: Session, *, now: datetime | None = None) -> CapitalState:
    moment = now or datetime.now(UTC)
    return CapitalState(
        generated_at=moment,
        sources=(
            _source_state(
                db,
                broker="tinvest",
                source=TINVEST_SOURCE,
                title="Т‑Инвестиции",
                now=moment,
            ),
            _source_state(
                db,
                broker="bybit",
                source=BYBIT_SOURCE,
                title="Bybit",
                now=moment,
            ),
        ),
    )


__all__ = [
    "CapitalState",
    "SourceAccount",
    "SourceState",
    "parse_bybit_wallet",
    "parse_tinvest_portfolio_equity",
    "refresh",
    "state",
]
