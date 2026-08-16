"""Фундаментальный срез инвестиционного контура (§6.2).

Это тот самый четвёртый шаг конвейера, без которого пакет собирать не из
чего. Технический анализ отвечает на вопрос «когда», фундаментальный — «что
вообще держать». Оптимизатор без второго честно найдёт лучший состав из
мусора.

Источник один и тот же — биржа. Ключей ни к каким платным терминалам нет, и
выдумывать оценки аналитиков вместо них нельзя. Зато у ISS без авторизации
есть три ряда, каждый из которых — настоящий фундаментальный факт:

* доходность к погашению и дюрация ОФЗ — прямо в снимке доски;
* фактические дивиденды акции по датам закрытия реестра;
* размер выпуска, из которого вместе с ценой получается капитализация.

Чего нет — того нет, и оно называет себя. У биржевого фонда нет ни
дивидендной доходности, ни капитализации: это не «ноль», а «к нему
неприменимо». Разница та же, что в §15.1 для оценки идеи, и по той же
причине: неприменимое, посчитанное нулём, тянет оценку вниз и выбрасывает
инструмент, к которому вопрос просто не относился.

Зрелые гипотезы исследовательского контура добавляют отдельный, ограниченный
слой к акциям. Они не становятся «ещё одной фундаментальной метрикой» и не
влияют на облигации/фонды: их вклад хранится отдельно и не может обойти
портфельные лимиты, оптимизацию и walk-forward admission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..market import moex
from ..market.http import FetchError
from ..models import Bar, Instrument
from ..models.enums import AssetClass, Timeframe
from .research_evidence import PortfolioResearchEvidence, evidence_for

# Один полностью сильный CONFIRMED thesis уже имеет эффективную силу .75
# (state multiplier .75). Такой зрелый отрицательный факт не должен быть
# просто скидкой к score: автоматическая покупка блокируется, пока thesis
# остаётся актуальным. Более слабый негатив по-прежнему только штрафует score.
NEGATIVE_RESEARCH_BLOCK = Decimal("-0.750000")


class Measure(StrEnum):
    MEASURED = "measured"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class Metric:
    name: str
    label: str
    status: Measure
    value: float | None = None
    text: str = ""

    @property
    def counts(self) -> bool:
        return self.status is Measure.MEASURED


@dataclass(slots=True)
class Fundamental:
    """Фундаментальная карточка бумаги с отдельным research overlay."""

    instrument_id: str
    asset_class: AssetClass
    title: str
    metrics: list[Metric] = field(default_factory=list)
    research: PortfolioResearchEvidence | None = None
    rejected: str = ""

    @property
    def measured(self) -> dict[str, float]:
        return {m.name: m.value for m in self.metrics if m.counts and m.value is not None}

    @property
    def fundamental_score(self) -> float:
        """Оценка только по измеренному фундаменталу, без research overlay."""
        usable = [m for m in self.metrics if m.counts and m.value is not None]
        if not usable:
            return 0.0
        return sum(m.value for m in usable) / len(usable)

    @property
    def score(self) -> float:
        """Screening score: fundamental base plus bounded mature research.

        Делитель фундаментальной части — количество применимых метрик, а не
        всех подряд. Research не маскируется под ещё одну метрику: он
        применяется после расчёта базы и только в пределах собственного
        жёсткого cap.
        """
        base = self.fundamental_score
        if self.asset_class is not AssetClass.EQUITY or self.research is None:
            return base
        return self.research.adjust(base)

    @property
    def evidence_json(self) -> dict:
        if self.asset_class is not AssetClass.EQUITY or self.research is None:
            return {
                "fundamental_score": round(self.fundamental_score, 6),
                "signed_conviction": 0.0,
                "research_adjustment": 0.0,
                "combined_score": round(self.fundamental_score, 6),
                "hypotheses": [],
            }
        return self.research.as_json(fundamental_score=self.fundamental_score)

    @property
    def note(self) -> str:
        parts = [m.text for m in self.metrics if m.text]
        return "; ".join(parts)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _ramp(value: float, bad: float, good: float) -> float:
    """Линейная шкала от «плохо» к «хорошо», зажатая в [0, 1]."""
    if good == bad:
        return 0.5
    return _clamp((value - bad) / (good - bad))


def _median_turnover(
    session: Session,
    instrument_id: str,
    *,
    days: int = 90,
    as_of: datetime | None = None,
) -> float | None:
    moment = as_of or datetime.now(UTC)
    since = moment - timedelta(days=days)
    values = sorted(
        float(v)
        for v in session.execute(
            select(Bar.volume_notional).where(
                Bar.instrument_id == instrument_id,
                Bar.timeframe == Timeframe.D1,
                Bar.is_closed.is_(True),
                Bar.open_time >= since,
                Bar.open_time <= moment,
            )
        ).scalars()
        if v is not None
    )
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _last_close(
    session: Session,
    instrument_id: str,
    *,
    as_of: datetime | None = None,
) -> Decimal | None:
    moment = as_of or datetime.now(UTC)
    return session.execute(
        select(Bar.close)
        .where(
            Bar.instrument_id == instrument_id,
            Bar.timeframe == Timeframe.D1,
            Bar.is_closed.is_(True),
            Bar.open_time <= moment,
        )
        .order_by(Bar.open_time.desc())
        .limit(1)
    ).scalar_one_or_none()


def _last_close_at(
    session: Session,
    instrument_id: str,
    *,
    as_of: datetime | None = None,
) -> datetime | None:
    moment = as_of or datetime.now(UTC)
    return session.execute(
        select(Bar.open_time)
        .where(
            Bar.instrument_id == instrument_id,
            Bar.timeframe == Timeframe.D1,
            Bar.is_closed.is_(True),
            Bar.open_time <= moment,
        )
        .order_by(Bar.open_time.desc())
        .limit(1)
    ).scalar_one_or_none()


def _history_days(
    session: Session,
    instrument_id: str,
    *,
    as_of: datetime | None = None,
) -> int:
    moment = as_of or datetime.now(UTC)
    first = session.execute(
        select(Bar.open_time)
        .where(
            Bar.instrument_id == instrument_id,
            Bar.timeframe == Timeframe.D1,
            Bar.is_closed.is_(True),
            Bar.open_time <= moment,
        )
        .order_by(Bar.open_time)
        .limit(1)
    ).scalar_one_or_none()
    if first is None:
        return 0
    return max(0, (moment - first).days)


# ── Срезы по классам ─────────────────────────────────────────────────────


def _bond_metrics(instrument: Instrument, today: date) -> list[Metric]:
    meta = instrument.metadata_json or {}
    metrics: list[Metric] = []

    raw_yield = meta.get("yield")
    if raw_yield is None:
        metrics.append(
            Metric("yield", "Доходность к погашению", Measure.MISSING,
                   text="биржа не отдала доходность")
        )
    else:
        value = float(raw_yield)
        metrics.append(
            Metric(
                "yield", "Доходность к погашению", Measure.MEASURED,
                value=_ramp(value, 8.0, 20.0),
                text=f"доходность к погашению {value:.1f}%",
            )
        )

    raw_duration = meta.get("duration")
    if raw_duration is None:
        metrics.append(
            Metric("duration", "Дюрация", Measure.MISSING, text="дюрация не пришла")
        )
    else:
        days = float(raw_duration)
        metrics.append(
            Metric(
                "duration", "Дюрация", Measure.MEASURED,
                value=_ramp(days, 2500.0, 200.0),
                text=f"дюрация {days / 365:.1f} г.",
            )
        )

    maturity = meta.get("matdate")
    if maturity:
        try:
            day = date.fromisoformat(str(maturity))
        except ValueError:
            day = None
        if day is not None:
            years = (day - today).days / 365.25
            metrics.append(
                Metric(
                    "maturity", "До погашения", Measure.MEASURED,
                    value=_clamp(1.0 if years >= 0.5 else 0.0),
                    text=f"погашение через {years:.1f} г.",
                )
            )
    metrics.append(
        Metric("dividend_yield", "Дивидендная доходность", Measure.NOT_APPLICABLE)
    )
    metrics.append(Metric("capitalisation", "Капитализация", Measure.NOT_APPLICABLE))
    return metrics


def _equity_metrics(
    session: Session,
    instrument: Instrument,
    *,
    fetch=None,
    as_of: datetime | None = None,
) -> list[Metric]:
    moment = as_of or datetime.now(UTC)
    meta = instrument.metadata_json or {}
    metrics: list[Metric] = []
    price = _last_close(session, instrument.instrument_id, as_of=moment)

    payments: list[tuple[date, Decimal]] = []
    dividends_available = True
    try:
        kwargs = {"fetch": fetch} if fetch else {}
        payments, _ = moex.dividends(instrument.symbol, **kwargs)
    except (FetchError, ValueError):
        dividends_available = False

    if not dividends_available:
        metrics.append(
            Metric(
                "dividend_yield",
                "Дивидендная доходность",
                Measure.MISSING,
                text="источник дивидендов временно недоступен",
            )
        )
    elif price is None or price <= 0:
        metrics.append(
            Metric("dividend_yield", "Дивидендная доходность", Measure.MISSING,
                   text="нет цены — доходность не считается")
        )
    elif not payments:
        metrics.append(
            Metric("dividend_yield", "Дивидендная доходность", Measure.MEASURED,
                   value=0.0, text="дивидендов не платит")
        )
    else:
        horizon = moment.date() - timedelta(days=365)
        recent = sum(float(v) for d, v in payments if horizon <= d <= moment.date())
        yield_pct = recent / float(price) * 100
        metrics.append(
            Metric(
                "dividend_yield", "Дивидендная доходность", Measure.MEASURED,
                value=_ramp(yield_pct, 0.0, 14.0),
                text=(
                    f"дивиденды за год {yield_pct:.1f}%"
                    if recent > 0
                    else "за последний год выплат не было"
                ),
            )
        )

    issue = meta.get("issuesize")
    if issue is None or price is None:
        metrics.append(
            Metric("capitalisation", "Капитализация", Measure.MISSING,
                   text="размер выпуска не пришёл")
        )
    else:
        cap = float(issue) * float(price)
        metrics.append(
            Metric(
                "capitalisation", "Капитализация", Measure.MEASURED,
                value=_ramp(cap, 5e10, 2e12),
                text=f"капитализация {cap / 1e9:.0f} млрд ₽",
            )
        )
    metrics.append(Metric("yield", "Доходность к погашению", Measure.NOT_APPLICABLE))
    metrics.append(Metric("duration", "Дюрация", Measure.NOT_APPLICABLE))
    return metrics


def _fund_metrics(instrument: Instrument) -> list[Metric]:
    """У фонда фундаментала нет — есть его собственное поведение."""
    return [
        Metric("dividend_yield", "Дивидендная доходность", Measure.NOT_APPLICABLE),
        Metric("capitalisation", "Капитализация", Measure.NOT_APPLICABLE),
        Metric("yield", "Доходность к погашению", Measure.NOT_APPLICABLE),
        Metric("duration", "Дюрация", Measure.NOT_APPLICABLE),
    ]


def screen(
    session: Session,
    instruments: list[Instrument],
    *,
    min_history_days: int = 120,
    min_turnover_rub: float = 5_000_000,
    max_price_age_days: int = 7,
    fetch=None,
    as_of: datetime | None = None,
) -> list[Fundamental]:
    """Фундаментальный срез по списку бумаг плюс зрелый research overlay.

    Отбракованные не исчезают: у каждой записана причина. Research получает
    только акции, уже входящие в переданную инвестиционную вселенную; он не
    может сам добавить новый тикер или обойти ликвидность/историю.
    """
    moment = as_of or datetime.now(UTC)
    today = moment.date()
    equity_ids = [
        instrument.instrument_id
        for instrument in instruments
        if instrument.asset_class is AssetClass.EQUITY
    ]
    research = evidence_for(session, equity_ids, as_of=moment)

    result: list[Fundamental] = []
    for instrument in instruments:
        card = Fundamental(
            instrument_id=instrument.instrument_id,
            asset_class=instrument.asset_class,
            title=instrument.title or instrument.symbol,
            research=research.get(instrument.instrument_id),
        )
        if instrument.asset_class in (AssetClass.OFZ, AssetClass.CORPORATE_BOND):
            card.metrics = _bond_metrics(instrument, today)
        elif instrument.asset_class is AssetClass.EQUITY and instrument.instrument_id.startswith(
            "MOEX:EQ:"
        ):
            card.metrics = _equity_metrics(session, instrument, fetch=fetch, as_of=moment)
        else:
            card.metrics = _fund_metrics(instrument)

        history = _history_days(session, instrument.instrument_id, as_of=moment)
        turnover = _median_turnover(session, instrument.instrument_id, as_of=moment)
        last_close_at = _last_close_at(session, instrument.instrument_id, as_of=moment)
        price_age_days = (
            max(0, (moment - last_close_at).days) if last_close_at is not None else None
        )
        if turnover is None:
            card.metrics.append(
                Metric("liquidity", "Оборот", Measure.MISSING, text="оборота нет в базе")
            )
        else:
            card.metrics.append(
                Metric(
                    "liquidity", "Оборот", Measure.MEASURED,
                    value=_ramp(turnover, min_turnover_rub, min_turnover_rub * 20),
                    text=f"оборот {turnover / 1e6:.0f} млн ₽/день",
                )
            )

        if history < min_history_days:
            card.rejected = (
                f"истории {history} дней при пороге {min_history_days} — "
                "в пакет не берётся"
            )
        elif last_close_at is None:
            card.rejected = "закрытой D1-цены нет — актуальность оценки неизвестна"
        elif price_age_days is not None and price_age_days > max_price_age_days:
            card.rejected = (
                f"D1-цена устарела на {price_age_days} дней при пороге "
                f"{max_price_age_days} — в пакет не берётся"
            )
        elif turnover is not None and turnover < min_turnover_rub:
            card.rejected = (
                f"оборот {turnover / 1e6:.1f} млн ₽/день ниже порога "
                f"{min_turnover_rub / 1e6:.0f} млн — выход из позиции будет дорогим"
            )
        elif turnover is None:
            card.rejected = "оборот не измерен — ликвидность неизвестна"
        elif (
            card.asset_class is AssetClass.EQUITY
            and card.research is not None
            and card.research.signed_conviction <= NEGATIVE_RESEARCH_BLOCK
        ):
            card.rejected = (
                "research guardrail: зрелый отрицательный thesis "
                f"{card.research.signed_conviction:.2f} <= {NEGATIVE_RESEARCH_BLOCK:.2f}"
            )
        result.append(card)
    return result
