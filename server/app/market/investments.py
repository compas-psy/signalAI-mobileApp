"""Инвестиционная вселенная: акции, фонды, ОФЗ (engine-ТЗ §5.1, §6).

До этого движок знал только срочный рынок и крипту — то, из чего собираются
сделки. Портфель собирается из другого: бумаг, которые держат месяцами.
Их не было вовсе, и поэтому «Пакеты» не могли посчитаться в принципе.

Списка бумаг здесь нет — как и во вселенной срочного рынка. Есть доски
биржи и пороги ликвидности. Что прошло — то и попало.

Про класс актива отдельно. У биржевого фонда в справочнике ISS не написано,
чем он наполнен: «ETF» — это форма, а не содержание. Классифицировать по
названию («ЛИКВИДНОСТЬ значит денежный рынок») — ровно тот хардкод, который
устаревает молча. Поэтому класс фонда определяется **измерением**: денежный
рынок опознаётся почти нулевыми колебаниями и отсутствием просадок,
облигационный — низкими, акционный — рыночными. Пока истории мало, класс
помечен предварительным, и это написано в примечании инструмента.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..models import Bar, DataQualityEvent, Instrument
from ..models.enums import AssetClass, Timeframe, Venue
from . import moex
from .http import FetchError

# Доски фондового рынка, с которых собирается инвестиционный контур.
#
# Фондов две доски, и это не перестраховка. TQTF — «Т+: ETF», доска
# иностранных ETF: после 2022 года торги по ним стоят, и живых бумаг там
# может не остаться вовсе. Российские биржевые ПИФы — LQDT, фонды облигаций
# и денежного рынка, то есть ровно те инструменты, которые дают доход на
# падающем рынке, — это **паи**, и живут они на TQIF.
#
# Одной доски TQTF не хватало: класс «фонд» существовал в коде, в
# ограничениях оптимизатора и в защите от выбрасывания, но во вселенную не
# попадала ни одна бумага, и все эти механизмы работали вхолостую. Экран
# сообщал «TQTF: ни одна бумага не прошла отбор: строк 0» — и это была
# правда про доску, а не про биржу.
#
# Класс здесь предварительный. Настоящий измеряется по истории самого фонда
# (см. classify_funds): денежный рынок отличается от облигационного
# волатильностью и отсутствием просадок, а не доской и не названием.
BOARDS = (
    ("shares", "TQBR", AssetClass.EQUITY, "EQ"),
    ("shares", "TQTF", AssetClass.BOND_FUND, "ETF"),
    ("shares", "TQIF", AssetClass.BOND_FUND, "ETF"),
    ("bonds", "TQOB", AssetClass.OFZ, "BOND"),
)


def _limits(cfg: EngineConfig) -> dict[str, int]:
    section = cfg.section("universe").get("investments", {})
    return {
        "min_history_days": int(section.get("min_history_days", 120)),
        "max_equities": int(section.get("max_equities", 40)),
        "max_funds": int(section.get("max_funds", 15)),
        "max_bonds": int(section.get("max_bonds", 20)),
        "min_turnover_rub": int(section.get("min_daily_turnover_rub", 20_000_000)),
        "min_bond_turnover_rub": int(section.get("min_bond_turnover_rub", 5_000_000)),
        "min_fund_turnover_rub": int(section.get("min_fund_turnover_rub", 3_000_000)),
    }


def _note(session: Session, board: str, detail: str) -> None:
    """Записать, почему доска не дала бумаг.

    Событие качества данных, а не лог: лог живёт до перезапуска контейнера, а
    вопрос «почему во вселенной нет фондов» задаётся дни спустя.
    """
    session.add(
        DataQualityEvent(
            source="moex",
            instrument_id=None,
            flag="BOARD_EMPTY",
            detail=f"{board}: {detail}"[:512],
        )
    )


def _tick(row: moex.BoardRow) -> Decimal:
    """Шаг цены. Нет в справочнике — выводится из числа знаков.

    Инструмент без шага цены нельзя ни округлить, ни посчитать по нему
    размер позиции, поэтому пустое место здесь недопустимо. Знаки после
    запятой биржа отдаёт всегда.
    """
    if row.min_step and row.min_step > 0:
        return row.min_step
    decimals = row.decimals if row.decimals is not None else 2
    return Decimal(1).scaleb(-max(0, min(8, decimals)))


def _tick_value(row: moex.BoardRow, asset_class: AssetClass, tick: Decimal) -> Decimal:
    """Сколько рублей стоит один шаг цены на одну бумагу.

    У акций и фондов цена — это рубли, и шаг стоит сам себя. У облигации
    цена — проценты номинала, и шаг стоит номинал × шаг / 100. Перепутать
    одно с другим значит ошибиться в размере позиции в сто раз.
    """
    if asset_class is AssetClass.OFZ:
        face = row.number("FACEVALUE") or Decimal(1000)
        return (face * tick / Decimal(100)).quantize(Decimal("0.00000001"))
    return tick


def _extras(row: moex.BoardRow, market: str, board: str) -> dict:
    """Фундаментальные поля, которые отдала биржа. Только измеренное."""
    data: dict[str, object] = {"market": market, "board": board}
    for key in ("YIELD", "DURATION", "COUPONPERCENT", "FACEVALUE", "ISSUESIZE"):
        value = row.number(key)
        if value is not None:
            data[key.lower()] = str(value)
    maturity = row.extra.get("MATDATE")
    if maturity:
        data["matdate"] = str(maturity)
    sec_type = row.extra.get("SECTYPE")
    if sec_type:
        data["sectype"] = str(sec_type)
    return data


def _issuer_id(
    row: moex.BoardRow,
    existing: Instrument | None,
    asset_class: AssetClass,
    *,
    fetch=None,
) -> str | None:
    """Эмитент только из явного идентификатора MOEX; имена не сопоставляем."""
    previous = (existing.metadata_json or {}).get("issuer_id") if existing else None
    if isinstance(previous, str) and previous:
        return previous
    if asset_class is not AssetClass.EQUITY:
        return None
    kwargs = {"fetch": fetch} if fetch else {}
    try:
        issuer_id, _ = moex.security_issuer_id(row.sec_id, **kwargs)
    except FetchError:
        return None
    return issuer_id


def sync_investments(session: Session, *, fetch=None) -> list[str]:
    """Обновить справочник инвестиционных бумаг по доскам биржи.

    Отказ одной доски не отменяет остальные: недоступность рынка облигаций
    не должна лишать владельца акций. Каждая доска отбирается по обороту и
    ограничивается сверху — вселенная должна помещаться в загрузку, иначе
    один проход упрётся в лимиты биржи и не завершится никогда.
    """
    cfg = get_config()
    limits = _limits(cfg)
    kwargs = {"fetch": fetch} if fetch else {}
    now = datetime.now(UTC)
    kept: list[str] = []

    for market, board, provisional, prefix in BOARDS:
        try:
            rows, _ = moex.stock_board(market, board, **kwargs)
        except (FetchError, ValueError) as exc:
            _note(
                session, board,
                f"доска не ответила: {type(exc).__name__}: {exc}"[:400],
            )
            continue

        floor = Decimal(
            {
                AssetClass.OFZ: limits["min_bond_turnover_rub"],
                AssetClass.BOND_FUND: limits["min_fund_turnover_rub"],
            }.get(provisional, limits["min_turnover_rub"])
        )
        cap = {
            AssetClass.EQUITY: limits["max_equities"],
            AssetClass.BOND_FUND: limits["max_funds"],
            AssetClass.OFZ: limits["max_bonds"],
        }[provisional]

        liquid = [r for r in rows if r.turnover and r.turnover >= floor and r.last]
        liquid.sort(key=lambda r: r.turnover or Decimal(0), reverse=True)
        if not liquid:
            with_price = sum(1 for r in rows if r.last)
            best = max((r.turnover or Decimal(0) for r in rows), default=Decimal(0))
            _note(
                session, board,
                f"ни одна бумага не прошла отбор: строк {len(rows)}, "
                f"с ценой {with_price}, наибольший оборот {best:.0f} ₽ "
                f"при пороге {floor:.0f} ₽",
            )
        for row in liquid[:cap]:
            instrument_id = f"MOEX:{prefix}:{row.sec_id}"
            tick = _tick(row)
            existing = session.execute(
                select(Instrument).where(Instrument.instrument_id == instrument_id)
            ).scalar_one_or_none()
            metadata = _extras(row, market, board)
            issuer_id = _issuer_id(row, existing, provisional, fetch=fetch)
            if issuer_id:
                metadata["issuer_id"] = issuer_id
            payload = {
                "venue": Venue.MOEX,
                "symbol": row.sec_id,
                "title": row.short_name,
                "currency": "RUB",
                "tick_size": tick,
                "tick_value": _tick_value(row, provisional, tick),
                "lot_size": max(1, row.lot_size or 1),
                "quantity_step": Decimal(1),
                "min_quantity": Decimal(1),
                "contract_multiplier": Decimal(1),
                "is_tradable": True,
                "in_universe": True,
                "metadata_json": metadata,
                "updated_at": now,
            }
            if existing is None:
                session.add(
                    Instrument(
                        instrument_id=instrument_id,
                        asset_class=provisional,
                        universe_note=(
                            f"инвестиционный контур §6: доска {board}, "
                            f"оборот {row.turnover:.0f} ₽; класс предварительный"
                            if provisional is AssetClass.BOND_FUND
                            else f"инвестиционный контур §6: доска {board}, "
                            f"оборот {row.turnover:.0f} ₽"
                        ),
                        **payload,
                    )
                )
            else:
                for key, value in payload.items():
                    setattr(existing, key, value)
            kept.append(instrument_id)
    return kept


MONEY_MARKET_VOL = 0.015
MONEY_MARKET_DRAWDOWN = 0.003
BOND_FUND_VOL = 0.06


def classify_funds(session: Session, *, min_days: int = 120) -> dict[str, str]:
    """Уточнить класс биржевых фондов по их собственной истории."""
    funds = list(
        session.execute(
            select(Instrument).where(
                Instrument.venue == Venue.MOEX,
                Instrument.instrument_id.like("MOEX:ETF:%"),
            )
        ).scalars()
    )
    result: dict[str, str] = {}
    for fund in funds:
        closes = [
            float(c)
            for c in session.execute(
                select(Bar.close)
                .where(
                    Bar.instrument_id == fund.instrument_id,
                    Bar.timeframe == Timeframe.D1,
                    Bar.is_closed.is_(True),
                )
                .order_by(Bar.open_time)
            ).scalars()
            if c is not None
        ]
        if len(closes) < min_days:
            result[fund.instrument_id] = "истории мало — класс остался предварительным"
            continue
        prices = np.asarray(closes, dtype=float)
        if np.any(prices <= 0):
            result[fund.instrument_id] = "ряд цен испорчен — класс не уточнён"
            continue
        returns = np.diff(np.log(prices))
        vol = float(returns.std(ddof=1) * np.sqrt(252)) if returns.size > 1 else 0.0
        equity = np.exp(np.cumsum(returns))
        drawdown = float((1 - equity / np.maximum.accumulate(equity)).max())

        if vol <= MONEY_MARKET_VOL and drawdown <= MONEY_MARKET_DRAWDOWN:
            decided = AssetClass.MONEY_MARKET
            why = f"колебания {vol:.2%}, просадок нет — денежный рынок"
        elif vol <= BOND_FUND_VOL:
            decided = AssetClass.BOND_FUND
            why = f"колебания {vol:.2%}, просадка {drawdown:.1%} — облигационный"
        else:
            decided = AssetClass.EQUITY
            why = f"колебания {vol:.1%}, просадка {drawdown:.1%} — акционный"

        fund.asset_class = decided
        fund.universe_note = f"класс измерен по истории: {why}"[:256]
        result[fund.instrument_id] = why
    return result


INVESTMENT_CLASSES = (
    AssetClass.MONEY_MARKET,
    AssetClass.BOND_FUND,
    AssetClass.OFZ,
    AssetClass.CORPORATE_BOND,
    AssetClass.EQUITY,
    AssetClass.GOLD,
)


def investment_universe(session: Session) -> list[Instrument]:
    """Бумаги инвестиционного контура, допущенные во вселенную."""
    return list(
        session.execute(
            select(Instrument).where(
                Instrument.in_universe.is_(True),
                Instrument.asset_class.in_([c.value for c in INVESTMENT_CLASSES]),
            )
        ).scalars()
    )
