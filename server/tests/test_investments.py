"""Инвестиционная вселенная: доски биржи, классы и бюджет первой загрузки.

Проверяется то, из-за чего портфель было не собрать: попадают ли бумаги в
справочник, правильно ли считается стоимость шага у облигации (ошибка здесь
в сто раз меняет размер позиции), и не съедает ли первое наполнение весь
проход планировщика.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.market import moex
from app.market.ingest import ingest_universe
from app.market.investments import BOARDS, investment_universe, sync_investments
from app.models import Bar, DataQualityEvent, Instrument
from app.models.enums import AssetClass, Timeframe, Venue


def _board(rows: dict[str, list[dict]]):
    """Заглушка ISS: отдаёт заранее заданные доски и пустые свечи."""

    def fetch(url: str):
        report = moex.FetchReport(
            url=url, status=200, elapsed_ms=1, bytes_read=1, ok=True
        )
        for key, payload in rows.items():
            if key in url:
                if "start=" in url and "start=0" not in url:
                    return {"securities": [], "marketdata": []}, report
                return payload, report
        return {"candles": []}, report

    return fetch


SHARES = {
    "securities": {
        "columns": ["SECID", "SHORTNAME", "MINSTEP", "DECIMALS", "LOTSIZE",
                    "ISSUESIZE", "SECTYPE"],
        "data": [
            ["SBER", "Сбербанк", 0.01, 2, 10, 21586948000, "1"],
            ["THIN", "Неликвид", 0.01, 2, 1, 1000000, "1"],
        ],
    },
    "marketdata": {
        "columns": ["SECID", "LAST", "VALTODAY"],
        "data": [["SBER", 312.4, 9_800_000_000], ["THIN", 5.1, 120_000]],
    },
}

BONDS = {
    "securities": {
        "columns": ["SECID", "SHORTNAME", "MINSTEP", "DECIMALS", "LOTSIZE",
                    "MATDATE", "COUPONPERCENT", "COUPONVALUE", "FACEVALUE",
                    "SECTYPE"],
        "data": [
            ["SU26238RMFS4", "ОФЗ 26238", 0.01, 2, 1, "2041-05-15", 7.1,
             35.4, 1000, "3"],
        ],
    },
    "marketdata": {
        "columns": ["SECID", "LAST", "VALTODAY", "YIELD", "DURATION"],
        "data": [["SU26238RMFS4", 54.2, 400_000_000, 14.83, 3900]],
    },
}

EMPTY = {"securities": {"columns": [], "data": []},
         "marketdata": {"columns": [], "data": []}}


def test_доски_биржи_наполняют_справочник(session):
    kept = sync_investments(
        session,
        fetch=_board({
            "markets/shares/boards/TQBR": SHARES,
            "markets/shares/boards/TQTF": EMPTY,
            "markets/bonds/boards/TQOB": BONDS,
        }),
    )
    session.flush()
    assert "MOEX:EQ:SBER" in kept
    assert "MOEX:BOND:SU26238RMFS4" in kept
    # Неликвид отсеян порогом оборота, а не попал «на всякий случай».
    assert "MOEX:EQ:THIN" not in kept

    universe = {i.instrument_id: i for i in investment_universe(session)}
    assert universe["MOEX:EQ:SBER"].asset_class is AssetClass.EQUITY
    assert universe["MOEX:EQ:SBER"].lot_size == 10


def test_у_облигации_шаг_стоит_доли_номинала(session):
    sync_investments(
        session,
        fetch=_board({
            "markets/shares/boards/TQBR": EMPTY,
            "markets/shares/boards/TQTF": EMPTY,
            "markets/bonds/boards/TQOB": BONDS,
        }),
    )
    session.flush()
    bond = session.execute(
        select(Instrument).where(Instrument.instrument_id == "MOEX:BOND:SU26238RMFS4")
    ).scalar_one()
    # Цена облигации — проценты номинала. Шаг 0,01% от 1000 ₽ стоит 10 копеек;
    # принять его за 1 копейку значит ошибиться в размере позиции в сто раз.
    assert bond.tick_size == Decimal("0.01")
    assert bond.tick_value == Decimal("0.10000000")
    # Фундаментальные поля приезжают вместе с бумагой, а не отдельным запросом.
    assert bond.metadata_json["yield"] == "14.83"
    assert bond.metadata_json["matdate"] == "2041-05-15"
    assert bond.metadata_json["board"] == "TQOB"


def test_первая_загрузка_ограничена_бюджетом(session):
    """Наполнение вселенной не должно отнимать у владельца выдачу дня.

    Планировщик последователен: пока идёт загрузка, скан идей не работает.
    Семьдесят новых бумаг по шесть лет дневок — это тысяча запросов подряд.
    """
    now = datetime.now(UTC)
    for i in range(8):
        session.add(
            Instrument(
                instrument_id=f"MOEX:EQ:N{i}",
                venue=Venue.MOEX,
                asset_class=AssetClass.EQUITY,
                symbol=f"N{i}",
                title=f"Бумага {i}",
                tick_size=Decimal("0.01"),
                tick_value=Decimal("0.01"),
                lot_size=1,
                quantity_step=Decimal("1"),
                min_quantity=Decimal("1"),
                in_universe=True,
                metadata_json={"market": "shares", "board": "TQBR"},
            )
        )
    session.flush()
    # У двух бумаг история уже есть — они догружаются хвостом и в бюджет
    # первой загрузки не входят.
    for i in (0, 1):
        session.add(
            Bar(
                instrument_id=f"MOEX:EQ:N{i}",
                timeframe=Timeframe.D1,
                open_time=now - timedelta(days=2),
                open=Decimal("100"), high=Decimal("101"),
                low=Decimal("99"), close=Decimal("100"),
                is_closed=True, source="test", quality_flags=[],
            )
        )
    session.flush()

    touched: set[str] = set()

    def fetch(url: str):
        for part in url.split("/"):
            if part.startswith("N") and part[1:].isdigit():
                touched.add(part)
        report = moex.FetchReport(
            url=url, status=200, elapsed_ms=1, bytes_read=1, ok=True
        )
        return {"candles": []}, report

    ingest_universe(session, fetch=fetch, first_load_budget=3)
    # Две с историей плюс три новых — не больше.
    assert touched == {"N0", "N1", "N2", "N3", "N4"}


FUNDS = {
    "securities": {
        "columns": ["SECID", "SHORTNAME", "MINSTEP", "DECIMALS", "LOTSIZE",
                    "ISSUESIZE", "SECTYPE"],
        "data": [
            ["LQDT", "Ликвидность", 0.0001, 4, 1, 100000000, "ETF"],
            ["SBGB", "Фонд ОФЗ", 0.01, 2, 1, 5000000, "ETF"],
        ],
    },
    "marketdata": {
        "columns": ["SECID", "LAST", "VALTODAY"],
        # Оборот фонда на порядок меньше, чем у голубой фишки: 8 и 4 млн ₽.
        "data": [["LQDT", 1.62, 8_000_000], ["SBGB", 11.4, 4_000_000]],
    },
}


def test_фонды_не_отсекаются_порогом_для_акций(session):
    """У биржевых фондов оборот меньше по построению, а не по неликвидности.

    Общий порог в 20 млн ₽ отсекал их все: во вселенной не оказалось ни
    одного фонда, а значит ни денежного рынка, ни облигационных. Без них
    консервативный профиль не собирается никогда — потолок на акции не даёт
    набрать 100%.
    """
    kept = sync_investments(
        session,
        fetch=_board({
            "markets/shares/boards/TQBR": EMPTY,
            "markets/shares/boards/TQTF": FUNDS,
            "markets/bonds/boards/TQOB": EMPTY,
        }),
    )
    session.flush()
    assert "MOEX:ETF:LQDT" in kept
    assert "MOEX:ETF:SBGB" in kept


def test_пустая_доска_записывает_причину(session):
    """Класс активов не может пропадать беззвучно.

    Доска фондов не доезжала, во вселенной не было ни одного фонда, и экран
    сообщал лишь «в отборе только акции и ОФЗ» — про доску не знал никто.
    """
    sync_investments(
        session,
        fetch=_board({
            "markets/shares/boards/TQBR": EMPTY,
            "markets/shares/boards/TQTF": EMPTY,
            "markets/bonds/boards/TQOB": EMPTY,
        }),
    )
    session.flush()
    notes = [
        e.detail
        for e in session.query(DataQualityEvent).filter_by(flag="BOARD_EMPTY")
    ]
    # По событию на каждую доску, и в каждом — чем именно она пуста.
    assert len(notes) == len(BOARDS)
    assert any(n.startswith("TQTF:") for n in notes)
    assert all("не прошла отбор" in n or "не ответила" in n for n in notes)
