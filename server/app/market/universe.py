"""Отбор вселенной (engine-ТЗ §5).

ТЗ не даёт списка инструментов — оно даёт фильтры. Для FORTS исторические
свечи ISS иногда содержат нулевой `value`/неполный ряд OI при полностью живой
доске. Recovery не снимает пороги ликвидности: когда историческая медиана
неизмерима или равна нулю, второй проход может использовать свежий снимок
доски, но явно помечает источник измерения как snapshot.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..models import Bar, DataQualityEvent, Instrument
from ..models.enums import AssetClass, QualityFlag, Timeframe, Venue
from . import crypto, moex
from .fx import record_rates
from .http import FetchError

MANDATORY_CRYPTO = ("BTCUSDT", "ETHUSDT")
FORTS_SNAPSHOT_MAX_AGE = timedelta(days=4)


@dataclass
class Verdict:
    admitted: bool
    reasons: list[str] = field(default_factory=list)
    measured: dict[str, str] = field(default_factory=dict)

    @property
    def note(self) -> str:
        head = "допущен §5" if self.admitted else "не допущен §5"
        detail = "; ".join(self.reasons)
        return f"{head}: {detail}"[:256] if detail else head


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return Decimal(statistics.median(sorted(values)))


def _daily_notionals(
    session: Session, instrument_id: str, *, days: int, now: datetime
) -> list[Decimal]:
    since = now - timedelta(days=days)
    rows = session.execute(
        select(Bar.volume_notional).where(
            Bar.instrument_id == instrument_id,
            Bar.timeframe == Timeframe.D1,
            Bar.is_closed.is_(True),
            Bar.open_time >= since,
        )
    ).scalars()
    return [v for v in rows if v is not None]


def _daily_oi_notionals(
    session: Session, instrument: Instrument, *, days: int, now: datetime
) -> list[Decimal]:
    since = now - timedelta(days=days)
    rows = session.execute(
        select(Bar.open_interest, Bar.close).where(
            Bar.instrument_id == instrument.instrument_id,
            Bar.timeframe == Timeframe.D1,
            Bar.is_closed.is_(True),
            Bar.open_time >= since,
        )
    ).all()
    if not instrument.tick_size or instrument.tick_size <= 0:
        return []
    rubles_per_point = instrument.tick_value / instrument.tick_size
    return [
        oi * close * rubles_per_point
        for oi, close in rows
        if oi is not None and close is not None
    ]


def _closed_hourly_bars(session: Session, instrument_id: str) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(Bar).where(
                Bar.instrument_id == instrument_id,
                Bar.timeframe == Timeframe.H1,
                Bar.is_closed.is_(True),
            )
        ).scalar_one()
    )


def _daily_bar_span_days(session: Session, instrument_id: str) -> int:
    row = session.execute(
        select(func.min(Bar.open_time), func.max(Bar.open_time)).where(
            Bar.instrument_id == instrument_id,
            Bar.timeframe == Timeframe.D1,
            Bar.is_closed.is_(True),
        )
    ).one()
    if row[0] is None or row[1] is None:
        return 0
    return (row[1] - row[0]).days


def _decimal_meta(meta: dict, key: str) -> Decimal | None:
    raw = meta.get(key)
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except Exception:
        return None


def _fresh_forts_snapshot(instrument: Instrument, *, now: datetime) -> dict:
    """Вернуть snapshot только пока он достаточно свежий для допуска.

    Четыре дня покрывают обычные выходные, но не позволяют старому снимку
    пережить неделю недоступности ISS и продолжать объявлять контракт живым.
    """
    meta = instrument.metadata_json or {}
    raw_at = meta.get("snapshot_at")
    if not raw_at:
        return {}
    try:
        at = datetime.fromisoformat(str(raw_at))
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        else:
            at = at.astimezone(UTC)
    except (TypeError, ValueError):
        return {}
    if now - at > FORTS_SNAPSHOT_MAX_AGE:
        return {}
    return meta


def _snapshot_oi_notional(instrument: Instrument, meta: dict) -> Decimal | None:
    oi = _decimal_meta(meta, "snapshot_open_interest")
    last = _decimal_meta(meta, "snapshot_last")
    if oi is None or oi <= 0 or last is None or last <= 0:
        return None
    if not instrument.tick_size or instrument.tick_size <= 0:
        return None
    rubles_per_point = instrument.tick_value / instrument.tick_size
    value = oi * last * rubles_per_point
    return value if value > 0 else None


# ─── Фьючерсы (§5.2) ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class FuturesCandidate:
    root: str
    near: moex.FortsRow
    next_series: moex.FortsRow | None

    @property
    def can_roll(self) -> bool:
        return self.next_series is not None


def futures_candidates(
    rows: list[moex.FortsRow],
    today: date,
    *,
    min_days_to_expiry: int,
    min_snapshot_turnover: Decimal = Decimal(0),
) -> list[FuturesCandidate]:
    board_trades = any((r.turnover or Decimal(0)) > 0 for r in rows)
    floor = min_snapshot_turnover if board_trades else Decimal(0)

    result: list[FuturesCandidate] = []
    for root, series in moex.series_by_root(rows).items():
        tradable = [
            r for r in series
            if r.last_trade_date is not None
            and (r.last_trade_date - today).days > min_days_to_expiry
            and (r.turnover or Decimal(0)) >= floor
            and (not board_trades or (r.turnover or Decimal(0)) > 0)
        ]
        if not tradable:
            continue
        near = tradable[0]
        later = [
            r for r in series
            if r.last_trade_date is not None
            and near.last_trade_date is not None
            and r.last_trade_date > near.last_trade_date
        ]
        result.append(FuturesCandidate(root, near, later[0] if later else None))
    return result


def admit_futures(
    session: Session,
    instrument: Instrument,
    *,
    cfg: EngineConfig | None = None,
    now: datetime | None = None,
    spread_snapshot: Decimal | None = None,
) -> Verdict:
    """Проверить FORTS, сохраняя строгие пороги при дырявом ISS history."""
    config = cfg or get_config()
    moment = now or datetime.now(UTC)
    verdict = Verdict(admitted=True)

    min_notional = config.decimal("universe.futures.min_median_daily_notional_rub")
    min_oi = config.decimal("universe.futures.min_median_oi_notional_rub")
    max_spread = config.decimal("universe.futures.max_median_relative_spread")
    min_bars = int(config.get("universe.futures.min_closed_hourly_bars"))
    min_days = int(config.get("universe.futures.min_days_to_expiry"))
    snapshot = _fresh_forts_snapshot(instrument, now=moment)

    history_notional = _median(
        _daily_notionals(session, instrument.instrument_id, days=30, now=moment)
    )
    snapshot_notional = _decimal_meta(snapshot, "snapshot_turnover_rub")
    if history_notional is not None and history_notional > 0:
        notional = history_notional
        verdict.measured["median_daily_notional_rub"] = str(notional)
        verdict.measured["turnover_source"] = "history_30d_median"
    elif snapshot_notional is not None and snapshot_notional > 0:
        notional = snapshot_notional
        verdict.measured["daily_notional_rub"] = str(notional)
        verdict.measured["turnover_source"] = "fresh_board_snapshot"
        verdict.reasons.append("оборот ISS history нулевой/пустой — использован свежий снимок доски")
    else:
        notional = None
        verdict.admitted = False
        verdict.reasons.append("оборот не измерен: history и свежий снимок доски пусты")

    if notional is not None and notional < min_notional:
        verdict.admitted = False
        verdict.reasons.append(
            f"оборот {notional:.0f} ₽ ниже порога {min_notional:.0f} ₽"
        )

    history_oi = _median(
        _daily_oi_notionals(session, instrument, days=30, now=moment)
    )
    snapshot_oi = _snapshot_oi_notional(instrument, snapshot)
    if history_oi is not None and history_oi > 0:
        oi = history_oi
        verdict.measured["median_oi_notional_rub"] = str(oi)
        verdict.measured["oi_source"] = "history_30d_median"
    elif snapshot_oi is not None:
        oi = snapshot_oi
        verdict.measured["oi_notional_rub"] = str(oi)
        verdict.measured["oi_source"] = "fresh_board_snapshot"
        verdict.reasons.append("ряд OI ISS пустой/нулевой — использован свежий снимок доски")
    else:
        oi = None
        verdict.admitted = False
        verdict.reasons.append("открытый интерес не измерен: history и свежий снимок пусты")

    if oi is not None and oi < min_oi:
        verdict.admitted = False
        verdict.reasons.append(
            f"OI {oi:.0f} ₽ ниже порога {min_oi:.0f} ₽"
        )

    bars = _closed_hourly_bars(session, instrument.instrument_id)
    verdict.measured["closed_hourly_bars"] = str(bars)
    if bars < min_bars:
        verdict.admitted = False
        verdict.reasons.append(f"закрытых часовых баров {bars} из {min_bars}")

    if instrument.expiry is None:
        verdict.admitted = False
        verdict.reasons.append("дата исполнения неизвестна")
    else:
        left = (instrument.expiry - moment.date()).days
        verdict.measured["days_to_expiry"] = str(left)
        if left <= min_days:
            verdict.admitted = False
            verdict.reasons.append(f"до экспирации {left} дней при пороге {min_days}")

    if instrument.next_contract is None:
        verdict.admitted = False
        verdict.reasons.append("следующая серия недоступна — роллировать некуда")

    if spread_snapshot is None:
        spread_snapshot = _decimal_meta(snapshot, "spread_snapshot")
    if spread_snapshot is None:
        verdict.reasons.append("спред не измерен: ISS не отдаёт историю котировок")
    else:
        verdict.measured["relative_spread_snapshot"] = str(spread_snapshot)
        if spread_snapshot > max_spread:
            verdict.admitted = False
            verdict.reasons.append(
                f"спред {spread_snapshot:.4%} шире порога {max_spread:.4%} "
                "(снимок, не медиана 30d)"
            )

    return verdict


def sync_futures(
    session: Session, *, now: datetime | None = None, fetch=None, cfg=None
) -> list[Instrument]:
    config = cfg or get_config()
    moment = now or datetime.now(UTC)
    kwargs = {"fetch": fetch} if fetch else {}
    min_days = int(config.get("universe.futures.min_days_to_expiry"))

    rows, _ = moex.forts_board(**kwargs)
    record_rates(session, rows, now=moment)
    prefilter = config.decimal("universe.futures.min_median_daily_notional_rub") / 10
    candidates = futures_candidates(
        rows, moment.date(),
        min_days_to_expiry=min_days,
        min_snapshot_turnover=prefilter,
    )
    if not candidates:
        _record_empty_board(session, len(rows))
        return []

    passed = {c.root for c in candidates}
    for root in moex.series_by_root(rows):
        if root not in passed:
            session.add(
                DataQualityEvent(
                    source="moex",
                    flag=QualityFlag.LOW_LIQUIDITY.value,
                    detail=(
                        f"корень {root}: нет ликвидной серии дальше "
                        f"{min_days} дней до экспирации"
                    )[:512],
                )
            )

    kept: list[Instrument] = []
    for candidate in candidates:
        series = candidate.near
        instrument_id = f"MOEX:FUT:{series.sec_id}"
        existing = session.execute(
            select(Instrument).where(Instrument.instrument_id == instrument_id)
        ).scalar_one_or_none()
        if existing is None:
            existing = Instrument(
                instrument_id=instrument_id,
                venue=Venue.MOEX,
                asset_class=AssetClass.FUTURES,
                symbol=series.sec_id,
                currency="RUB",
            )
            session.add(existing)

        existing.title = series.short_name
        existing.tick_size = series.min_step or Decimal(1)
        existing.tick_value = series.step_price or Decimal(1)
        existing.expiry = series.last_trade_date
        existing.next_contract = (
            f"MOEX:FUT:{candidate.next_series.sec_id}" if candidate.can_roll else None
        )
        existing.correlation_cluster = cluster_for(candidate.root)
        existing.in_universe = True
        existing.is_tradable = False
        existing.universe_note = (
            f"кандидат §5.2: ближняя серия корня {candidate.root}, "
            f"экспирация {series.last_trade_date}"
            + ("" if candidate.can_roll else ", следующей серии нет")
        )
        old = existing.metadata_json or {}
        spread = series.relative_spread

        # Ночной/выходной snapshot может отдавать нули. Он не должен стирать
        # последний измеренный торговый снимок: freshness TTL выше не даст ему
        # жить дольше четырёх дней.
        turnover = series.turnover if (series.turnover or Decimal(0)) > 0 else _decimal_meta(old, "snapshot_turnover_rub")
        open_interest = series.open_interest if (series.open_interest or Decimal(0)) > 0 else _decimal_meta(old, "snapshot_open_interest")
        last = series.last if (series.last or Decimal(0)) > 0 else _decimal_meta(old, "snapshot_last")
        snapshot_changed = any(
            value is not None and value > 0
            for value in (series.turnover, series.open_interest, series.last)
        )
        existing.metadata_json = {
            **old,
            "root": candidate.root,
            "spread_snapshot": str(spread) if spread is not None else old.get("spread_snapshot"),
            "snapshot_turnover_rub": str(turnover) if turnover is not None else None,
            "snapshot_open_interest": str(open_interest) if open_interest is not None else None,
            "snapshot_last": str(last) if last is not None else None,
            "snapshot_at": moment.isoformat() if snapshot_changed else old.get("snapshot_at"),
        }
        kept.append(existing)

    _retire_missing(session, kept, Venue.MOEX, AssetClass.FUTURES)
    session.flush()
    return kept


def _record_empty_board(session: Session, rows_seen: int) -> None:
    session.add(
        DataQualityEvent(
            source="moex",
            flag=QualityFlag.SOURCE_CONFLICT.value,
            detail=(
                f"доска вернула {rows_seen} строк и ни одного кандидата — "
                "вселенная оставлена как была"
            )[:512],
        )
    )
    session.flush()


def _retire_missing(
    session: Session,
    kept: list[Instrument],
    venue: Venue,
    asset_class: AssetClass,
) -> None:
    live = {i.instrument_id for i in kept}
    rows = session.execute(
        select(Instrument).where(
            Instrument.venue == venue,
            Instrument.asset_class == asset_class,
            Instrument.in_universe.is_(True),
        )
    ).scalars()
    for row in rows:
        if row.instrument_id not in live:
            row.in_universe = False
            row.is_tradable = False
            row.universe_note = "выбыл из снимка биржи: смена серии либо экспирация"


# ─── Crypto (§5.3) ────────────────────────────────────────────────────────


def crypto_candidates(
    tickers: list[crypto.Ticker],
    specs: dict[str, crypto.InstrumentInfo],
    *,
    min_turnover: Decimal,
    max_active: int,
) -> list[crypto.Ticker]:
    by_symbol = {t.symbol: t for t in tickers}

    def usable(symbol: str) -> bool:
        spec = specs.get(symbol)
        return spec is not None and spec.is_trading and spec.is_perpetual

    chosen: list[crypto.Ticker] = []
    for symbol in MANDATORY_CRYPTO:
        ticker = by_symbol.get(symbol)
        if ticker is not None and usable(symbol):
            chosen.append(ticker)

    rest = [
        t for t in tickers
        if t.symbol not in {c.symbol for c in chosen}
        and usable(t.symbol)
        and t.symbol.endswith("USDT")
        and (t.turnover_24h or Decimal(0)) >= min_turnover
    ]
    rest.sort(key=lambda t: t.turnover_24h or Decimal(0), reverse=True)
    return [*chosen, *rest[: max(0, max_active - len(chosen))]]


def crypto_candidate_pool(
    tickers: list[crypto.Ticker],
    specs: dict[str, crypto.InstrumentInfo],
    *,
    min_turnover: Decimal,
    max_active: int,
    depth: int = 3,
) -> list[crypto.Ticker]:
    return crypto_candidates(
        tickers, specs,
        min_turnover=min_turnover,
        max_active=max_active * depth,
    )


def admit_crypto(
    session: Session,
    instrument: Instrument,
    ticker: crypto.Ticker | None,
    *,
    cfg: EngineConfig | None = None,
    now: datetime | None = None,
) -> Verdict:
    config = cfg or get_config()
    moment = now or datetime.now(UTC)
    verdict = Verdict(admitted=True)

    min_notional = config.decimal("universe.crypto.min_median_daily_notional_usdt")
    min_history = int(config.get("universe.crypto.min_history_days"))

    notional = _median(_daily_notionals(session, instrument.instrument_id, days=30, now=moment))
    if notional is None:
        verdict.admitted = False
        verdict.reasons.append("оборот не измерен: дневных баров нет")
    else:
        verdict.measured["median_daily_notional_usdt"] = str(notional)
        if notional < min_notional:
            verdict.admitted = False
            verdict.reasons.append(
                f"медианный оборот {notional:.0f} USDT ниже порога {min_notional:.0f}"
            )

    span = _daily_bar_span_days(session, instrument.instrument_id)
    verdict.measured["history_days"] = str(span)
    if span < min_history:
        verdict.admitted = False
        verdict.reasons.append(f"истории {span} дней из {min_history}")

    if ticker is None:
        verdict.admitted = False
        verdict.reasons.append("инструмента нет в снимке рынка")
    else:
        spread = ticker.relative_spread
        if spread is None:
            verdict.reasons.append("спред не измерен: биржа не отдала котировки")
        else:
            verdict.measured["relative_spread"] = f"{spread:.6f}"
        if ticker.open_interest is None:
            verdict.admitted = False
            verdict.reasons.append("открытый интерес недоступен")
        else:
            verdict.measured["open_interest"] = str(ticker.open_interest)

    return verdict


def sync_crypto(
    session: Session, *, now: datetime | None = None, fetch=None, cfg=None
) -> list[Instrument]:
    config = cfg or get_config()
    kwargs = {"fetch": fetch} if fetch else {}
    max_active = int(config.get("universe.crypto.max_active"))
    min_turnover = config.decimal("universe.crypto.min_median_daily_notional_usdt")

    snapshot, _ = crypto.tickers(**kwargs)
    specs_list, _ = crypto.instruments_info(**kwargs)
    specs = {s.symbol: s for s in specs_list}

    chosen = crypto_candidate_pool(
        snapshot, specs, min_turnover=min_turnover, max_active=max_active
    )

    kept: list[Instrument] = []
    for ticker in chosen:
        spec = specs[ticker.symbol]
        instrument_id = f"CRYPTO:PERP:{ticker.symbol}"
        existing = session.execute(
            select(Instrument).where(Instrument.instrument_id == instrument_id)
        ).scalar_one_or_none()
        if existing is None:
            existing = Instrument(
                instrument_id=instrument_id,
                venue=Venue.CRYPTO,
                asset_class=AssetClass.CRYPTO_PERPETUAL,
                symbol=ticker.symbol,
                currency=spec.quote_coin or "USDT",
            )
            session.add(existing)

        existing.title = f"{spec.base_coin}/{spec.quote_coin} бессрочный"
        existing.tick_size = spec.tick_size or Decimal("0.01")
        existing.tick_value = spec.tick_size or Decimal("0.01")
        existing.quantity_step = spec.qty_step or Decimal(1)
        existing.min_quantity = spec.min_order_qty or spec.qty_step or Decimal(1)
        existing.expiry = None
        existing.correlation_cluster = "crypto"
        existing.in_universe = True
        existing.is_tradable = False
        existing.universe_note = (
            "обязательный §5.3" if ticker.symbol in MANDATORY_CRYPTO
            else f"кандидат §5.3 по обороту {ticker.turnover_24h or 0:.0f} USDT за сутки"
        )
        kept.append(existing)

    _retire_missing(session, kept, Venue.CRYPTO, AssetClass.CRYPTO_PERPETUAL)
    session.flush()
    return kept


# ─── Второй проход ────────────────────────────────────────────────────────


@dataclass
class AdmissionReport:
    checked: int = 0
    admitted: int = 0
    verdicts: dict[str, Verdict] = field(default_factory=dict)


def review_universe(
    session: Session, *, now: datetime | None = None, fetch=None, cfg=None
) -> AdmissionReport:
    config = cfg or get_config()
    moment = now or datetime.now(UTC)
    report = AdmissionReport()

    snapshot: dict[str, crypto.Ticker] = {}
    instruments = list(
        session.execute(
            select(Instrument).where(Instrument.in_universe.is_(True))
        ).scalars()
    )
    if any(i.venue is Venue.CRYPTO for i in instruments):
        try:
            rows, _ = crypto.tickers(**({"fetch": fetch} if fetch else {}))
            snapshot = {t.symbol: t for t in rows}
        except (FetchError, ValueError):
            snapshot = {}

    for instrument in instruments:
        report.checked += 1
        if instrument.venue is Venue.CRYPTO:
            verdict = admit_crypto(
                session, instrument, snapshot.get(instrument.symbol),
                cfg=config, now=moment,
            )
        else:
            raw = (instrument.metadata_json or {}).get("spread_snapshot")
            verdict = admit_futures(
                session, instrument, cfg=config, now=moment,
                spread_snapshot=Decimal(raw) if raw else None,
            )
        instrument.is_tradable = verdict.admitted
        instrument.universe_note = verdict.note
        instrument.metadata_json = {
            **(instrument.metadata_json or {}),
            "admission": verdict.measured,
        }
        report.verdicts[instrument.instrument_id] = verdict

    _cap_active_crypto(session, instruments, report, cfg=config)

    report.admitted = sum(1 for v in report.verdicts.values() if v.admitted)
    session.flush()
    return report


def _cap_active_crypto(
    session: Session,
    instruments: list[Instrument],
    report: AdmissionReport,
    *,
    cfg: EngineConfig,
) -> None:
    max_active = int(cfg.get("universe.crypto.max_active"))
    admitted = [
        i for i in instruments
        if i.venue is Venue.CRYPTO
        and report.verdicts.get(i.instrument_id, Verdict(False)).admitted
    ]
    if len(admitted) <= max_active:
        return

    def rank(item: Instrument) -> tuple[int, Decimal]:
        mandatory = 1 if item.symbol in MANDATORY_CRYPTO else 0
        measured = report.verdicts[item.instrument_id].measured
        turnover = Decimal(measured.get("median_daily_notional_usdt", "0"))
        return (mandatory, turnover)

    admitted.sort(key=rank, reverse=True)
    for item in admitted[max_active:]:
        verdict = report.verdicts[item.instrument_id]
        verdict.admitted = False
        verdict.reasons.append(
            f"за пределом активной вселенной §5.3 ({max_active} инструментов)"
        )
        item.is_tradable = False
        item.universe_note = verdict.note


_CLUSTERS = {
    "SI": "rub_fx", "CNY": "rub_fx", "ED": "rub_fx", "EU": "rub_fx",
    "RI": "ru_equity_index", "MX": "ru_equity_index", "MM": "ru_equity_index",
    "SBRF": "ru_equity", "GAZR": "ru_equity", "LKOH": "ru_equity",
    "VTBR": "ru_equity", "GMKN": "ru_equity", "ROSN": "ru_equity",
    "BR": "energy", "NG": "energy",
    "GOLD": "metals", "SILV": "metals", "PLT": "metals", "PLD": "metals",
}


def cluster_for(root: str) -> str:
    return _CLUSTERS.get(root.upper(), f"root_{root.upper()}")
