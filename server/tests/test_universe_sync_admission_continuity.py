from datetime import UTC, datetime
from decimal import Decimal

from app.market import crypto, review_resilience, universe
from app.models import Instrument
from app.models.enums import AssetClass, Venue


NOW = datetime(2026, 8, 24, 18, tzinfo=UTC)


BOARD = {
    "securities": {
        "columns": [
            "SECID",
            "SHORTNAME",
            "LASTTRADEDATE",
            "MINSTEP",
            "STEPPRICE",
            "DECIMALS",
        ],
        "data": [
            ["SiU6", "Si-9.26", "2026-09-17", "1", "1", 0],
            ["SiZ6", "Si-12.26", "2026-12-17", "1", "1", 0],
        ],
    },
    "marketdata": {
        "columns": [
            "SECID",
            "LAST",
            "VALTODAY",
            "OPENPOSITION",
            "UPDATETIME",
            "BID",
            "OFFER",
        ],
        "data": [
            ["SiU6", "90123", "15000000000", "412000", "18:45", "90122", "90124"],
            ["SiZ6", "91000", "2000000000", "90000", "18:45", "90998", "91002"],
        ],
    },
}


def _board_fetch(_url):
    return BOARD, None


def _ticker(symbol: str = "BTCUSDT") -> crypto.Ticker:
    return crypto.Ticker(
        symbol=symbol,
        last=Decimal("65000"),
        turnover_24h=Decimal("9000000000"),
        open_interest=Decimal("1000000000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=None,
        bid=Decimal("64999"),
        ask=Decimal("65001"),
    )


def _spec(symbol: str = "BTCUSDT") -> crypto.InstrumentInfo:
    return crypto.InstrumentInfo(
        symbol=symbol,
        status="Trading",
        contract_type="LinearPerpetual",
        base_coin=symbol.removesuffix("USDT"),
        quote_coin="USDT",
        tick_size=Decimal("0.1"),
        qty_step=Decimal("0.001"),
        min_order_qty=Decimal("0.001"),
        launch_time=None,
        delivery_time=None,
    )


def test_futures_metadata_sync_preserves_last_admitted_verdict(session):
    row = Instrument(
        instrument_id="MOEX:FUT:SiU6",
        venue=Venue.MOEX,
        asset_class=AssetClass.FUTURES,
        symbol="SiU6",
        tick_size=Decimal("1"),
        tick_value=Decimal("1"),
        in_universe=True,
        is_tradable=True,
        metadata_json={
            "root": "SI",
            "admission": {"median_daily_notional_rub": "20000000000"},
        },
    )
    session.add(row)
    session.flush()

    review_resilience.sync_futures_core_seeded(
        session, now=NOW, fetch=_board_fetch
    )
    session.refresh(row)

    assert row.in_universe is True
    assert row.is_tradable is True


def test_new_futures_candidate_stays_blocked_until_first_review(session):
    (row,) = review_resilience.sync_futures_core_seeded(
        session, now=NOW, fetch=_board_fetch
    )

    assert row.in_universe is True
    assert row.is_tradable is False


def test_futures_contract_missing_from_new_snapshot_is_blocked_immediately(session):
    old = Instrument(
        instrument_id="MOEX:FUT:SiM6",
        venue=Venue.MOEX,
        asset_class=AssetClass.FUTURES,
        symbol="SiM6",
        tick_size=Decimal("1"),
        tick_value=Decimal("1"),
        in_universe=True,
        is_tradable=True,
        metadata_json={"root": "SI"},
    )
    session.add(old)
    session.flush()

    review_resilience.sync_futures_core_seeded(
        session, now=NOW, fetch=_board_fetch
    )
    session.refresh(old)

    assert old.in_universe is False
    assert old.is_tradable is False


def test_crypto_metadata_sync_preserves_last_admitted_verdict(session, monkeypatch):
    row = Instrument(
        instrument_id="CRYPTO:PERP:BTCUSDT",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        symbol="BTCUSDT",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        in_universe=True,
        is_tradable=True,
    )
    session.add(row)
    session.flush()

    monkeypatch.setattr(universe.crypto, "tickers", lambda **_kwargs: ([_ticker()], None))
    monkeypatch.setattr(
        universe.crypto,
        "instruments_info",
        lambda **_kwargs: ([_spec()], None),
    )

    review_resilience.sync_crypto_admission_continuous(session, now=NOW)
    session.refresh(row)

    assert row.in_universe is True
    assert row.is_tradable is True


def test_new_crypto_candidate_stays_blocked_until_first_review(session, monkeypatch):
    monkeypatch.setattr(universe.crypto, "tickers", lambda **_kwargs: ([_ticker()], None))
    monkeypatch.setattr(
        universe.crypto,
        "instruments_info",
        lambda **_kwargs: ([_spec()], None),
    )

    (row,) = review_resilience.sync_crypto_admission_continuous(session, now=NOW)

    assert row.in_universe is True
    assert row.is_tradable is False
