"""Отбор вселенной §5.

Проверяется главное свойство §5: вселенная — это **результат фильтров**, а не
список. Поэтому здесь нет теста «SiU6 попал в выдачу»; есть тесты «серия у
экспирации не берётся», «без следующей серии роллировать некуда», «без истории
допуск не даётся». Инструмент, прошедший их, попадёт сам.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import get_config
from app.market import crypto, moex, universe
from app.market.http import FetchReport
from app.models import Bar, DataQualityEvent, Instrument
from app.models.enums import AssetClass, Timeframe, Venue

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)
TODAY = NOW.date()


def report(url="t"):
    return FetchReport(url=url, status=200, elapsed_ms=1, bytes_read=1, ok=True)


BOARD = {
    "securities": {
        "columns": ["SECID", "SHORTNAME", "LASTTRADEDATE", "MINSTEP", "STEPPRICE", "DECIMALS"],
        "data": [
            ["SiU6", "Si-9.26", "2026-09-17", "1", "1", 0],
            ["SiZ6", "Si-12.26", "2026-12-17", "1", "1", 0],
            ["BRQ6", "BR-8.26", "2026-07-31", "0.01", "7.5", 2],
        ],
    },
    "marketdata": {
        "columns": ["SECID", "LAST", "VALTODAY", "OPENPOSITION", "UPDATETIME", "BID", "OFFER"],
        "data": [
            ["SiU6", "90123", "15000000000", "412000", "18:45", "90122", "90124"],
            ["SiZ6", "91000", "2000000000", "90000", "18:45", "90998", "91002"],
            ["BRQ6", "70.5", "900000000", "12000", "18:45", "70.49", "70.51"],
        ],
    },
}


def board_fetch(url):
    return BOARD, report(url)


def forts_row(sec_id, expiry, *, turnover="1000000", bid=None, ask=None):
    return moex.FortsRow(
        sec_id=sec_id, short_name=sec_id, last=Decimal(100),
        turnover=Decimal(turnover), open_interest=Decimal(1000),
        min_step=Decimal(1), step_price=Decimal(1), decimals=0,
        last_trade_date=expiry, updated_at=None,
        bid=Decimal(bid) if bid else None, ask=Decimal(ask) if ask else None,
    )


# ─── Корень контракта ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sec_id,root",
    [
        ("SiU6", "Si"),
        ("BRV6", "BR"),
        ("RIZ5", "RI"),
        ("GAZRH6", "GAZR"),
        ("MXM6", "MX"),
    ],
)
def test_root_is_parsed_from_the_end(sec_id, root):
    """Разбор идёт с конца: у ``SiU6`` первые буквы — ``SiU``, а корень ``Si``.

    Ошибка здесь не косметическая: каждая серия стала бы отдельным корнем, и
    следующая серия для роллирования не нашлась бы никогда.
    """
    assert moex.root_of(sec_id) == root


@pytest.mark.parametrize("sec_id", ["Si-9.26", "", "SiU", "123"])
def test_unparseable_code_is_not_guessed(sec_id):
    """Непонятный код — None, а не выдуманный корень."""
    assert moex.root_of(sec_id) is None


# ─── Кандидаты §5.2 ───────────────────────────────────────────────────────


def test_expiring_series_is_not_a_candidate():
    """До экспирации пять дней — сетап тут ни при чём, это риск контракта."""
    rows = [forts_row("SiU6", TODAY + timedelta(days=3))]
    assert universe.futures_candidates(rows, TODAY, min_days_to_expiry=5) == []


def test_nearest_tradable_series_wins_over_far_one():
    rows = [
        forts_row("SiZ6", TODAY + timedelta(days=140)),
        forts_row("SiU6", TODAY + timedelta(days=50)),
    ]
    (candidate,) = universe.futures_candidates(rows, TODAY, min_days_to_expiry=5)
    assert candidate.near.sec_id == "SiU6"
    assert candidate.next_series.sec_id == "SiZ6"
    assert candidate.can_roll


def test_roll_target_is_seen_even_if_it_would_fail_alone():
    """Для роллирования важно, что серия существует, а не её сегодняшний оборот."""
    rows = [
        forts_row("SiU6", TODAY + timedelta(days=50)),
        forts_row("SiZ6", TODAY + timedelta(days=140), turnover="0"),
    ]
    (candidate,) = universe.futures_candidates(rows, TODAY, min_days_to_expiry=5)
    assert candidate.can_roll


def test_lonely_series_cannot_be_rolled():
    rows = [forts_row("SiU6", TODAY + timedelta(days=50))]
    (candidate,) = universe.futures_candidates(rows, TODAY, min_days_to_expiry=5)
    assert not candidate.can_roll


def test_dead_series_is_dropped_while_the_board_trades():
    """На торгующейся доске нулевой оборот — свойство контракта."""
    rows = [
        forts_row("SiU6", TODAY + timedelta(days=50), turnover="0"),
        forts_row("BRV6", TODAY + timedelta(days=50), turnover="500000000"),
    ]
    roots = {c.root for c in universe.futures_candidates(rows, TODAY, min_days_to_expiry=5)}
    assert roots == {"BR"}


def test_closed_market_does_not_empty_the_universe():
    """Ночью оборот за день ноль у всех — это про время суток, а не про рынок.

    Без этого различия ночной прогон вычистил бы вселенную целиком: загрузка
    встала бы до утра, а в истории осталась бы дыра без объяснения.
    """
    rows = [
        forts_row("SiU6", TODAY + timedelta(days=50), turnover="0"),
        forts_row("BRV6", TODAY + timedelta(days=50), turnover="0"),
    ]
    roots = {
        c.root for c in universe.futures_candidates(
            rows, TODAY, min_days_to_expiry=5,
            min_snapshot_turnover=Decimal("10000000"),
        )
    }
    assert roots == {"SI", "BR"}


def test_prefilter_drops_contracts_trading_on_pennies():
    """Предварительный отсев — чтобы не тянуть историю по мёртвым контрактам."""
    rows = [
        forts_row("SiU6", TODAY + timedelta(days=50), turnover="500000000"),
        forts_row("UTV6", TODAY + timedelta(days=50), turnover="1200"),
    ]
    roots = {
        c.root for c in universe.futures_candidates(
            rows, TODAY, min_days_to_expiry=5,
            min_snapshot_turnover=Decimal("10000000"),
        )
    }
    assert roots == {"SI"}


# ─── Синхронизация справочника ────────────────────────────────────────────


def test_sync_creates_candidates_from_the_whole_board(session):
    kept = universe.sync_futures(session, now=NOW, fetch=board_fetch)
    symbols = {i.symbol for i in kept}
    # BRQ6 истекает 31 июля — ближе пяти дней, в кандидаты не идёт.
    assert symbols == {"SiU6"}
    (item,) = kept
    assert item.next_contract == "MOEX:FUT:SiZ6"
    assert item.correlation_cluster == "rub_fx"


def test_candidate_is_not_tradable_until_history_confirms_it(session):
    """Снимок доски не доказывает ликвидность — её доказывает история."""
    (item,) = universe.sync_futures(session, now=NOW, fetch=board_fetch)
    assert item.in_universe is True
    assert item.is_tradable is False


def test_root_without_liquid_series_leaves_a_trace(session):
    """Молчаливо пропасть корень не может — иначе непонятно, почему его нет."""
    universe.sync_futures(session, now=NOW, fetch=board_fetch)
    events = session.execute(select(DataQualityEvent)).scalars().all()
    assert any("корень BR" in e.detail for e in events)


def test_specification_is_overwritten_not_kept(session):
    """Шаг цены меняется при пересмотре параметров контракта.

    По нему считается размер позиции — устаревшая спецификация это тихая
    ошибка в деньгах.
    """
    session.add(
        Instrument(
            instrument_id="MOEX:FUT:SiU6", venue=Venue.MOEX,
            asset_class=AssetClass.FUTURES, symbol="SiU6",
            tick_size=Decimal(99), tick_value=Decimal(99),
        )
    )
    session.flush()
    universe.sync_futures(session, now=NOW, fetch=board_fetch)
    updated = session.execute(
        select(Instrument).where(Instrument.instrument_id == "MOEX:FUT:SiU6")
    ).scalar_one()
    assert updated.tick_size == Decimal(1)


def test_rolled_out_series_leaves_universe_but_stays_in_catalogue(session):
    """По старой серии есть история и, возможно, закрытые сделки."""
    old = Instrument(
        instrument_id="MOEX:FUT:SiM6", venue=Venue.MOEX,
        asset_class=AssetClass.FUTURES, symbol="SiM6",
        tick_size=Decimal(1), tick_value=Decimal(1), in_universe=True,
    )
    session.add(old)
    session.flush()
    universe.sync_futures(session, now=NOW, fetch=board_fetch)
    session.refresh(old)
    assert old.in_universe is False
    assert session.get(Instrument, old.id) is not None


# ─── Допуск §5.2 по истории ───────────────────────────────────────────────


def _futures(session, **over) -> Instrument:
    data = dict(
        instrument_id="MOEX:FUT:SiU6", venue=Venue.MOEX,
        asset_class=AssetClass.FUTURES, symbol="SiU6",
        tick_size=Decimal(1), tick_value=Decimal(1),
        expiry=TODAY + timedelta(days=50),
        next_contract="MOEX:FUT:SiZ6", in_universe=True,
    )
    data.update(over)
    item = Instrument(**data)
    session.add(item)
    session.flush()
    return item


def _fill_history(
    session,
    instrument_id: str,
    *,
    daily: int = 30,
    hourly: int = 300,
    notional: str = "20000000000",
    open_interest: str = "6000000",
):
    for i in range(daily):
        session.add(
            Bar(
                instrument_id=instrument_id, timeframe=Timeframe.D1,
                open_time=NOW - timedelta(days=i),
                open=Decimal(100), high=Decimal(101), low=Decimal(99),
                close=Decimal(100),
                volume_notional=Decimal(notional),
                open_interest=Decimal(open_interest),
                is_closed=True, source="test",
            )
        )
    for i in range(hourly):
        session.add(
            Bar(
                instrument_id=instrument_id, timeframe=Timeframe.H1,
                open_time=NOW - timedelta(hours=i),
                open=Decimal(100), high=Decimal(101), low=Decimal(99),
                close=Decimal(100), is_closed=True, source="test",
            )
        )
    session.flush()


def test_admission_passes_on_liquid_history(session):
    item = _futures(session)
    _fill_history(session, item.instrument_id)
    verdict = universe.admit_futures(
        session, item, now=NOW, spread_snapshot=Decimal("0.0001")
    )
    assert verdict.admitted, verdict.reasons
    assert verdict.measured["closed_hourly_bars"] == "300"


def test_admission_fails_without_history(session):
    """Пустая история — «не измерено», и это отказ, а не молчаливое «ок»."""
    item = _futures(session)
    verdict = universe.admit_futures(session, item, now=NOW)
    assert not verdict.admitted
    assert any("не измерен" in r for r in verdict.reasons)


def test_thin_turnover_is_rejected_with_a_number(session):
    item = _futures(session)
    _fill_history(session, item.instrument_id, notional="1000000")
    verdict = universe.admit_futures(session, item, now=NOW)
    assert not verdict.admitted
    assert any("оборот" in r for r in verdict.reasons)


def test_too_few_hourly_bars_is_rejected(session):
    item = _futures(session)
    _fill_history(session, item.instrument_id, hourly=100)
    verdict = universe.admit_futures(session, item, now=NOW)
    assert not verdict.admitted
    assert any("часовых баров 100" in r for r in verdict.reasons)


def test_missing_roll_target_blocks_admission(session):
    """§5.2 требует доступности следующего контракта — без неё позицию некуда переносить."""
    item = _futures(session, next_contract=None)
    _fill_history(session, item.instrument_id)
    verdict = universe.admit_futures(
        session, item, now=NOW, spread_snapshot=Decimal("0.0001")
    )
    assert not verdict.admitted
    assert any("роллировать некуда" in r for r in verdict.reasons)


def test_wide_spread_is_rejected_and_named_a_snapshot(session):
    item = _futures(session)
    _fill_history(session, item.instrument_id)
    verdict = universe.admit_futures(
        session, item, now=NOW, spread_snapshot=Decimal("0.01")
    )
    assert not verdict.admitted
    assert any("снимок, а не медиана" in r for r in verdict.reasons)


def test_unmeasured_spread_does_not_silently_pass_as_zero(session):
    """У ISS нет истории котировок. Отсутствие меры должно быть сказано вслух."""
    item = _futures(session)
    _fill_history(session, item.instrument_id)
    verdict = universe.admit_futures(session, item, now=NOW)
    assert verdict.admitted
    assert any("спред не измерен" in r for r in verdict.reasons)
    assert "relative_spread_snapshot" not in verdict.measured


def test_open_interest_is_converted_to_money(session):
    """OI приходит в контрактах, а порог §5.2 — в рублях.

    Стоимость шага у Si равна шагу, поэтому рубль на пункт — единица, и
    500 000 контрактов по цене 100 дают 50 млн ₽: ниже порога в 500 млн.
    """
    item = _futures(session)
    _fill_history(session, item.instrument_id, open_interest="500000")
    verdict = universe.admit_futures(session, item, now=NOW)
    assert not verdict.admitted
    assert any("медианный OI" in r for r in verdict.reasons)


def test_review_writes_the_reason_onto_the_instrument(session):
    item = _futures(session)
    verdict_report = universe.review_universe(session, now=NOW)
    session.refresh(item)
    assert verdict_report.checked == 1
    assert verdict_report.admitted == 0
    assert item.is_tradable is False
    assert "не допущен §5" in item.universe_note


# ─── Crypto §5.3 ──────────────────────────────────────────────────────────


def ticker(symbol, turnover, *, oi="1000", bid="100", ask="100.01"):
    return crypto.Ticker(
        symbol=symbol, last=Decimal(100), turnover_24h=Decimal(turnover),
        open_interest=Decimal(oi) if oi else None, funding_rate=Decimal("0.0001"),
        next_funding_time=None,
        bid=Decimal(bid) if bid else None, ask=Decimal(ask) if ask else None,
    )


def spec(symbol, *, status="Trading", contract_type="LinearPerpetual"):
    return crypto.InstrumentInfo(
        symbol=symbol, status=status, contract_type=contract_type,
        base_coin=symbol.removesuffix("USDT"), quote_coin="USDT",
        tick_size=Decimal("0.1"), qty_step=Decimal("0.001"),
        min_order_qty=Decimal("0.001"), launch_time=None, delivery_time=None,
    )


def test_mandatory_pair_does_not_compete_for_places():
    """§5.3: BTC и ETH обязательны при доступности, а не «если попадут в топ»."""
    tickers = [
        ticker("SOLUSDT", "9000000000"),
        ticker("BTCUSDT", "1"),
        ticker("ETHUSDT", "1"),
    ]
    specs = {t.symbol: spec(t.symbol) for t in tickers}
    chosen = universe.crypto_candidates(
        tickers, specs, min_turnover=Decimal("100000000"), max_active=2
    )
    assert [c.symbol for c in chosen] == ["BTCUSDT", "ETHUSDT"]


def test_delisted_contract_is_not_taken():
    tickers = [ticker("BTCUSDT", "9000000000"), ticker("XXXUSDT", "9000000000")]
    specs = {
        "BTCUSDT": spec("BTCUSDT"),
        "XXXUSDT": spec("XXXUSDT", status="Delivering"),
    }
    chosen = universe.crypto_candidates(
        tickers, specs, min_turnover=Decimal("1"), max_active=12
    )
    assert [c.symbol for c in chosen] == ["BTCUSDT"]


def test_contract_without_specification_is_not_taken():
    """Нет шага объёма — нет размера позиции. Торговать вслепую нельзя."""
    tickers = [ticker("BTCUSDT", "9000000000"), ticker("NEWUSDT", "9000000000")]
    chosen = universe.crypto_candidates(
        tickers, {"BTCUSDT": spec("BTCUSDT")},
        min_turnover=Decimal("1"), max_active=12,
    )
    assert [c.symbol for c in chosen] == ["BTCUSDT"]


def test_active_universe_is_capped():
    tickers = [ticker(f"C{i}USDT", str(10_000_000_000 - i)) for i in range(20)]
    specs = {t.symbol: spec(t.symbol) for t in tickers}
    chosen = universe.crypto_candidates(
        tickers, specs, min_turnover=Decimal("1"), max_active=12
    )
    assert len(chosen) == 12


def test_crypto_admission_requires_a_year_of_history(session):
    item = Instrument(
        instrument_id="CRYPTO:PERP:BTCUSDT", venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL, symbol="BTCUSDT",
        tick_size=Decimal("0.1"), tick_value=Decimal("0.1"), in_universe=True,
    )
    session.add(item)
    session.flush()
    for i in range(0, 200, 2):
        session.add(
            Bar(
                instrument_id=item.instrument_id, timeframe=Timeframe.D1,
                open_time=NOW - timedelta(days=i),
                open=Decimal(100), high=Decimal(101), low=Decimal(99),
                close=Decimal(100), volume_notional=Decimal("9" * 12),
                is_closed=True, source="test",
            )
        )
    session.flush()
    verdict = universe.admit_crypto(
        session, item, ticker("BTCUSDT", "9000000000"), now=NOW
    )
    assert not verdict.admitted
    assert any("истории 198 дней" in r for r in verdict.reasons)


# ─── Предел активной вселенной §5.3 ───────────────────────────────────────


def test_candidate_pool_is_wider_than_the_active_limit():
    """Предел §5.3 — про допущенные, а не про претендентов.

    На живой бирже у Bybit в linear торгуются токенизированные акции и золото
    (SOXL, MU, SKHYNIX, XAU). По суточному обороту они обходили половину
    криптовалют, занимали все двенадцать мест и вытесняли настоящую крипту —
    а потом всё равно отсеивались за нехваткой годовой истории.
    """
    tickers = [ticker(f"C{i}USDT", str(10_000_000_000 - i)) for i in range(50)]
    specs = {t.symbol: spec(t.symbol) for t in tickers}
    pool = universe.crypto_candidate_pool(
        tickers, specs, min_turnover=Decimal("1"), max_active=12
    )
    assert len(pool) == 36


def test_active_crypto_universe_is_capped_after_admission(session):
    """Допущенных больше предела — лишние выбывают с названной причиной."""
    instruments = []
    for i in range(15):
        item = Instrument(
            instrument_id=f"CRYPTO:PERP:C{i}USDT", venue=Venue.CRYPTO,
            asset_class=AssetClass.CRYPTO_PERPETUAL, symbol=f"C{i}USDT",
            tick_size=Decimal("0.1"), tick_value=Decimal("0.1"), in_universe=True,
        )
        session.add(item)
        instruments.append(item)
    session.flush()

    report = universe.AdmissionReport()
    for i, item in enumerate(instruments):
        report.verdicts[item.instrument_id] = universe.Verdict(
            admitted=True,
            measured={"median_daily_notional_usdt": str(1000 + i)},
        )
        item.is_tradable = True

    universe._cap_active_crypto(session, instruments, report, cfg=get_config())
    tradable = [i.symbol for i in instruments if i.is_tradable]
    assert len(tradable) == 12
    # Отсечены самые тонкие по обороту, а не случайные.
    assert set(tradable).isdisjoint({"C0USDT", "C1USDT", "C2USDT"})
    dropped = report.verdicts["CRYPTO:PERP:C0USDT"]
    assert any("предел" in r for r in dropped.reasons)


def test_mandatory_pair_keeps_its_place_when_capping(session):
    """BTC и ETH обязательны по §5.3 — предел их не выталкивает."""
    instruments = []
    for symbol in ["BTCUSDT", "ETHUSDT", *[f"C{i}USDT" for i in range(13)]]:
        item = Instrument(
            instrument_id=f"CRYPTO:PERP:{symbol}", venue=Venue.CRYPTO,
            asset_class=AssetClass.CRYPTO_PERPETUAL, symbol=symbol,
            tick_size=Decimal("0.1"), tick_value=Decimal("0.1"), in_universe=True,
        )
        session.add(item)
        instruments.append(item)
    session.flush()

    report = universe.AdmissionReport()
    for i, item in enumerate(instruments):
        # У обязательных оборот наименьший — проверяем, что это не важно.
        turnover = "1" if item.symbol in universe.MANDATORY_CRYPTO else str(1000 + i)
        report.verdicts[item.instrument_id] = universe.Verdict(
            admitted=True, measured={"median_daily_notional_usdt": turnover},
        )
        item.is_tradable = True

    universe._cap_active_crypto(session, instruments, report, cfg=get_config())
    tradable = {i.symbol for i in instruments if i.is_tradable}
    assert {"BTCUSDT", "ETHUSDT"} <= tradable
    assert len(tradable) == 12
