"""Сборка пакетов капитала (§6.1–§6.5).

Порядок здесь — это и есть ответ на вопрос «почему пакет такой».

1. **Вселенная.** Бумаги инвестиционного контура, допущенные по ликвидности.
2. **Фундаментальный срез.** Что вообще имеет смысл держать: доходность к
   погашению и дюрация у облигаций, дивиденды и капитализация у акций,
   измеренный класс у фондов.
3. **Отбор.** Лучшие по срезу внутри каждого класса — кандидаты. Оптимизатор
   не должен выбирать из всего рынка: на трёхстах бумагах он найдёт триста
   мнимых закономерностей.
4. **Оптимизация состава.** Ресэмплинг Michaud на блочном бутстрэпе: сто с
   лишним выборок, в каждой свой оптимум, наружу — усреднённый состав.
   Ограничения — потолки по классам профиля и потолок на бумагу.
5. **Проверка на истории.** Скользящее окно: веса считаются по прошлому,
   измеряются на следующем куске, которого оптимизатор не видел.
6. **Допуск.** Не прошедший проверку пакет не показывается — с причиной.

Ни одного зашитого веса, ни одного зашитого тикера. Состав целиком выводится
из рядов цен и того, что биржа сказала о бумагах.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_config
from ..market.investments import investment_universe
from ..models import (
    Bar,
    Instrument,
    PortfolioModel,
    PortfolioRun,
    PortfolioWeight,
)
from ..models.enums import AssetClass, PackageSize, RiskProfile, Timeframe
from . import fundamentals as fund
from .stats import (
    Constraints,
    project_with_groups,
    ReturnPanel,
    build_panel,
    optimise_to_volatility,
    performance,
    resampled_weights,
    shrunk_covariance,
    annualise_cov,
    annualise_mean,
)
from .walkforward import judge, walk_forward

# ── Профили ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Profile:
    """Профиль риска — это целевые колебания и потолки по классам (§6.1).

    Профиль не задаёт ни одной бумаги. Он говорит, сколько колебаний
    владелец согласен переносить и чего в портфеле не должно быть больше
    определённой доли. Всё остальное решает оптимизатор на данных.
    """

    target_volatility: float
    drawdown_limit: float
    caps: dict[AssetClass, float]
    floors: dict[AssetClass, float] = field(default_factory=dict)
    title: str = ""


PROFILES: dict[RiskProfile, Profile] = {
    RiskProfile.CONSERVATIVE: Profile(
        target_volatility=0.06,
        drawdown_limit=0.08,
        caps={
            AssetClass.EQUITY: 0.25,
            AssetClass.CRYPTO_SPOT: 0.0,
            AssetClass.CRYPTO_PERPETUAL: 0.0,
        },
        title="сохранить и обогнать вклад",
    ),
    RiskProfile.OPTIMAL: Profile(
        target_volatility=0.12,
        drawdown_limit=0.18,
        caps={
            AssetClass.EQUITY: 0.60,
            AssetClass.CRYPTO_SPOT: 0.05,
            AssetClass.CRYPTO_PERPETUAL: 0.05,
        },
        title="расти с понятным риском",
    ),
    RiskProfile.AGGRESSIVE: Profile(
        target_volatility=0.20,
        drawdown_limit=0.30,
        caps={
            AssetClass.EQUITY: 0.85,
            AssetClass.CRYPTO_SPOT: 0.05,
            AssetClass.CRYPTO_PERPETUAL: 0.05,
        },
        title="максимум потенциала",
    ),
}

# Горизонт меняет не состав, а окно оценки. Годовой пакет обязан отражать
# текущий режим рынка; пятилетний — пережить смену режимов, поэтому смотрит
# на всю доступную историю.
HORIZON_WINDOW = {1: 504, 5: 0}  # торговых дней; 0 — вся история

ROLE_BY_CLASS = {
    AssetClass.MONEY_MARKET: "денежная подушка",
    AssetClass.BOND_FUND: "процентный доход",
    AssetClass.OFZ: "процентный доход",
    AssetClass.CORPORATE_BOND: "процентный доход",
    AssetClass.EQUITY: "рост капитала",
    AssetClass.GOLD: "защита от обвала",
    AssetClass.CRYPTO_SPOT: "асимметричный риск",
    AssetClass.CRYPTO_PERPETUAL: "асимметричный риск",
}


# ── Результат сборки ─────────────────────────────────────────────────────


@dataclass(slots=True)
class Position:
    instrument_id: str
    title: str
    asset_class: AssetClass
    weight: float
    stability: float
    role: str
    thesis: str
    kill: str
    score: float
    expected_return: float


@dataclass(slots=True)
class Package:
    profile: RiskProfile
    package: PackageSize
    horizon_years: int
    admitted: bool
    reason: str
    # Состав годен, но рискованнее, чем обещает профиль. Это не повод его
    # прятать: числа риска показываются рядом, решение за владельцем.
    meets_target: bool = True
    warnings: list[str] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    expected_low: float = 0.0
    expected_high: float = 0.0
    volatility: float = 0.0
    drawdown: float = 0.0
    cvar_95: float = 0.0
    rationale: str = ""
    stress: dict = field(default_factory=dict)


@dataclass(slots=True)
class BuildReport:
    """Что произошло за прогон — на этом строится экран прогресса."""

    universe: int = 0
    screened: int = 0
    candidates: int = 0
    common_days: int = 0
    packages: list[Package] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""

    @property
    def admitted(self) -> int:
        return sum(1 for p in self.packages if p.admitted)


# ── Данные ───────────────────────────────────────────────────────────────


def daily_closes(session: Session, instrument_ids: list[str]) -> dict[str, list]:
    series: dict[str, list] = {}
    for instrument_id in instrument_ids:
        rows = session.execute(
            select(Bar.open_time, Bar.close)
            .where(
                Bar.instrument_id == instrument_id,
                Bar.timeframe == Timeframe.D1,
                Bar.is_closed.is_(True),
            )
            .order_by(Bar.open_time)
        ).all()
        points = [(t.date(), float(c)) for t, c in rows if c is not None]
        if len(points) >= 2:
            series[instrument_id] = points
    return series


def _panel_with_window(
    series: dict[str, list],
    *,
    needed: int,
    min_width: int,
    target: int | None = None,
    classes: dict[str, AssetClass] | None = None,
) -> tuple[ReturnPanel, list[tuple[str, str]]]:
    """Панель, у которой хватает общей истории на скользящую проверку.

    Панель строится по датам, **общим** для всех бумаг, — иначе ковариация
    считалась бы по разным периодам для разных пар. Отсюда неприятное
    свойство: одна недавно допущенная бумага обрезает окно всем остальным.
    Именно это и произошло на боевом сервере: 54 бумаги с историей, а общее
    окно короче, чем нужно одному циклу проверки (два года обучения плюс
    квартал измерения). Проверка не проводилась ни разу, значит ни один
    состав не мог быть допущен — экран честно показывал «нет» на шаге
    оптимизации, и причина этого «нет» была не видна.

    Здесь короткие ряды **отбрасываются**, а не укорачивают всех: бумага без
    длинной истории просто не участвует в этом пересчёте. Отброшенные едут в
    отчёт с причиной — «пропала из состава» не должно быть неотличимо от
    «сломалась загрузка».
    """
    working = dict(series)
    dropped: list[tuple[str, str]] = []
    panel = build_panel(working)
    # Цель — не минимально допустимое окно, а осмысленное. Останавливаться на
    # первом же успехе значило получить три окна проверки: девять месяцев вне
    # обучения, по которым вердикт «состав теряет 3.1% годовых» говорит
    # больше о конкретном квартале, чем о составе. Пока отбрасывание коротких
    # рядов удлиняет общее окно и бумаг остаётся достаточно — продолжаем.
    goal = max(needed, target or needed)
    by_class = classes or {}
    while panel.periods < goal and len(working) > min_width:
        # Уходит самый короткий ряд — он и ограничивает пересечение. Но не
        # любой: класс актива, от которого осталась пара бумаг, беречь
        # важнее длины окна. Вырезав все облигации и денежный рынок, мы
        # оставим консервативный профиль без единого допустимого состава —
        # его потолок на акции просто не даст набрать 100%.
        shortest = _next_to_drop(working, by_class)
        if shortest is None:
            break
        length = len(working[shortest])
        candidate = dict(working)
        del candidate[shortest]
        grown = build_panel(candidate)
        # Если удаление не удлиняет окно, дальше резать бессмысленно: длину
        # держит не этот ряд, и мы просто обедняем отбор.
        if panel.periods >= needed and grown.periods <= panel.periods:
            break
        working, panel = candidate, grown
        dropped.append(
            (
                shortest,
                f"истории {length} дней — общее окно короче {goal} дней, "
                "нужных для осмысленной проверки; в этот пересчёт не взят",
            )
        )
    return panel, dropped


def _next_to_drop(
    working: dict[str, list], by_class: dict[str, AssetClass]
) -> str | None:
    """Какой ряд убрать следующим: самый короткий из тех, кого не жалко.

    «Не жалко» — значит его класс представлен ещё как минимум двумя другими
    бумагами. Класс, доживший до пары представителей, не режется: без него
    часть профилей перестаёт быть выполнимой в принципе.
    """
    counts: dict[AssetClass, int] = {}
    for key in working:
        asset_class = by_class.get(key)
        if asset_class is not None:
            counts[asset_class] = counts.get(asset_class, 0) + 1

    def length(key: str) -> int:
        return len(working[key])

    spare = [
        key
        for key in working
        if by_class.get(key) is None or counts.get(by_class[key], 0) > 2
    ]
    pool = spare or list(working)
    if not pool:
        return None
    return min(pool, key=length)


# ── Ограничения ──────────────────────────────────────────────────────────


def _constraints(
    classes: list[AssetClass], profile: Profile, *, positions: int
) -> Constraints:
    """Границы весов: потолок на бумагу и потолки по классам.

    Потолок на бумагу выведен из размера пакета, а не назначен числом: в
    пакете на три позиции 20% — это мало, в пакете на двенадцать — это уже
    концентрация. Берётся полторы «равные доли», но не больше трети.
    """
    n = len(classes)
    per_name = min(0.34, max(0.12, 1.5 / max(1, positions)))
    lo = np.zeros(n)
    hi = np.full(n, per_name)
    for i, asset_class in enumerate(classes):
        cap = profile.caps.get(asset_class)
        if cap is not None and cap < per_name:
            hi[i] = cap

    groups: list[tuple[np.ndarray, float]] = []
    for asset_class, cap in profile.caps.items():
        mask = np.array([c is asset_class for c in classes])
        if mask.any():
            groups.append((mask, float(cap)))
    if hi.sum() < 1.0:
        # Потолки не дают набрать 100% — расширяем потолок на бумагу до
        # выполнимого. Молча вернуть недобранный портфель нельзя: сумма
        # весов обязана быть единицей (UX-ТЗ §7.1).
        hi = np.minimum(1.0, hi * (1.05 / max(hi.sum(), 1e-9)))
    return Constraints(lo=lo, hi=hi, groups=tuple(groups))


# ── Сборка одного пакета ─────────────────────────────────────────────────


def _reachable(hi: np.ndarray, groups: tuple[tuple[np.ndarray, float], ...]) -> float:
    """Максимальная сумма весов при этих потолках.

    Классы в ``groups`` не пересекаются (каждый вес принадлежит одному
    классу), поэтому верхняя граница считается сложением: по классу — его
    потолок либо сумма потолков бумаг, что меньше; всё остальное — как есть.
    """
    taken = np.zeros(hi.size, dtype=bool)
    total = 0.0
    for mask, cap in groups:
        total += min(cap, float(hi[mask].sum()))
        taken |= mask
    total += float(hi[~taken].sum())
    return total


def _trim(
    weights: np.ndarray, count: tuple[int, int], constraints: Constraints
) -> np.ndarray:
    """Свести состав к разрешённому числу позиций, не нарушив ограничений.

    Хвост из десяти позиций по 0.4% — не диверсификация, а комиссия: такие
    доли не переживают ни одного ребаланса. Но вычеркнуть лишнее и поделить
    остаток на сумму нельзя: нормировка раздувает оставшиеся веса и легко
    выносит класс за его потолок — консервативный профиль так набирал 31%
    акций при разрешённых 25%. Поэтому после отбора состав **проецируется**
    обратно на множество ограничений.

    Если оставленных бумаг не хватает, чтобы набрать 100% под потолками
    классов, набор расширяется следующими по весу — пока не станет
    выполнимым. Недобранный портфель — не портфель.
    """
    low, high = count
    order = list(np.argsort(-weights))
    keep = [int(i) for i in order[:high] if weights[i] >= 0.005]
    if len(keep) < low:
        keep = [int(i) for i in order[:low]]

    rest = [int(i) for i in order if int(i) not in keep]
    while True:
        hi = np.zeros_like(constraints.hi)
        # Потолок на бумагу считается от фактического числа позиций: в
        # составе из трёх бумаг доля в треть — норма, а не концентрация.
        room = min(0.5, max(1.4 / max(1, len(keep)), 0.0))
        for i in keep:
            hi[i] = min(0.5, max(float(constraints.hi[i]), room))
        if _reachable(hi, constraints.groups) >= 1.0 - 1e-9 or not rest:
            break
        keep.append(rest.pop(0))

    if _reachable(hi, constraints.groups) < 1.0 - 1e-9:
        # Даже вся вселенная не набирает 100% под потолками — состава нет.
        return np.zeros_like(weights)

    seed = np.zeros_like(weights)
    seed[keep] = weights[keep]
    return project_with_groups(seed, constraints.lo, hi, constraints.groups)


def _stress(panel: ReturnPanel, weights: np.ndarray) -> dict:
    """Худшее, что уже случалось с этим составом на его собственной истории.

    Не выдуманные сценарии «падение рынка на 30%», а фактические окна:
    сколько состав терял за месяц и за квартал в худшем случае. Владелец
    видит числа, которые действительно происходили.
    """
    series = panel.values @ weights
    result: dict[str, str] = {}
    for label, window in (("месяц", 21), ("квартал", 63), ("год", 252)):
        if series.size < window:
            continue
        rolled = np.convolve(series, np.ones(window), mode="valid")
        result[label] = f"{float(np.exp(rolled.min()) - 1):.1%}"
    if series.size:
        worst_day = float(np.exp(series.min()) - 1)
        result["день"] = f"{worst_day:.1%}"
    return result


def _thesis(card: fund.Fundamental, weight: float) -> str:
    facts = [m.text for m in card.metrics if m.counts and m.text]
    body = "; ".join(facts[:3])
    return f"доля {weight:.0%} — {body}" if body else f"доля {weight:.0%}"


def _kill(card: fund.Fundamental) -> str:
    """Условия выхода, привязанные к тому, из-за чего бумага попала в пакет."""
    if card.asset_class in (AssetClass.OFZ, AssetClass.CORPORATE_BOND):
        return (
            "доходность к погашению упала ниже доходности денежного рынка; "
            "до погашения осталось меньше полугода"
        )
    if card.asset_class is AssetClass.EQUITY:
        return (
            "дивиденды отменены или пропущены; оборот упал ниже порога "
            "ликвидности; вес по пересчёту опустился ниже 0,5%"
        )
    if card.asset_class is AssetClass.MONEY_MARKET:
        return "у фонда появились просадки — измеренный класс перестал быть денежным"
    return "измеренный класс фонда изменился; оборот упал ниже порога"


def build_package(
    panel: ReturnPanel,
    cards: dict[str, fund.Fundamental],
    instruments: dict[str, Instrument],
    *,
    profile_key: RiskProfile,
    package: PackageSize,
    horizon_years: int,
    count: tuple[int, int],
    draws: int,
    cfg,
) -> Package:
    profile = PROFILES[profile_key]
    window = HORIZON_WINDOW.get(horizon_years, 0)
    working = panel if window == 0 or panel.periods <= window else panel.window(
        panel.periods - window, panel.periods
    )

    classes = [instruments[c].asset_class for c in working.columns]
    target_positions = count[1]
    constraints = _constraints(classes, profile, positions=target_positions)

    result = Package(
        profile=profile_key,
        package=package,
        horizon_years=horizon_years,
        admitted=False,
        reason="",
    )
    if working.width < count[0] or working.periods < 120:
        result.reason = (
            f"кандидатов {working.width}, истории {working.periods} дней — "
            f"для пакета нужно минимум {count[0]} бумаг и 120 дней"
        )
        return result

    resampled = resampled_weights(
        working.values,
        constraints,
        target_volatility=profile.target_volatility,
        draws=draws,
    )
    if resampled.draws == 0:
        result.reason = "оптимизатор не сошёлся ни на одной выборке"
        return result

    weights = _trim(resampled.weights, count, constraints)
    if abs(float(weights.sum())) < 1e-6:
        # Проекция вернула нули: потолки классов профиля вместе не дают 100%
        # на отобранных бумагах. Это состояние вселенной, а не приговор
        # рынку, и называть его надо именно так — иначе экран скажет
        # «вне обучения состав теряет 0.0% годовых».
        present = sorted({str(c) for c in classes})
        result.reason = (
            "состав не собран: потолки профиля не дают набрать 100% из "
            f"отобранных бумаг (в отборе только {', '.join(present)}). "
            "Нужны бумаги классов, которым профиль оставляет место."
        )
        return result

    def rebuild(train: np.ndarray) -> np.ndarray:
        # Окно горизонта применяется и внутри проверки: иначе проверялся бы
        # не тот способ счёта, которым получен показанный состав.
        piece = train if window == 0 or train.shape[0] <= window else train[-window:]
        mu = annualise_mean(piece)
        cov = annualise_cov(shrunk_covariance(piece))
        w = optimise_to_volatility(
            mu, cov, constraints, target_volatility=profile.target_volatility
        )
        return _trim(w, count, constraints)

    wf = cfg.section("backtest")["walk_forward"]
    train_days = int(wf["train_months"]) * 21
    test_days = int(wf["test_months"]) * 21
    # Проверка идёт по всей истории, а не по окну горизонта. Годовой пакет
    # считается по последним двум годам — но доверия он заслуживает лишь
    # тем, что такой способ счёта работал и раньше, на других режимах.
    # Проверять его на том же куске, из которого взяты веса, значит не
    # проверять вовсе.
    report = walk_forward(panel, rebuild, train=train_days, test=test_days)
    verdict = judge(report, drawdown_limit=profile.drawdown_limit)

    realised = performance(working.values @ weights)
    result.volatility = realised.volatility
    result.cvar_95 = realised.cvar_95
    # Стресс — по всей истории: «худшее, что уже случалось» не может
    # ограничиваться последними двумя годами.
    result.stress = _stress(panel, weights)

    if report.performed and report.combined is not None:
        spread = float(np.std([f.result.cagr for f in report.folds])) if report.folds else 0.0
        centre = report.combined.cagr
        result.expected_low = centre - spread
        result.expected_high = centre + spread
        result.drawdown = report.combined.max_drawdown
    else:
        result.expected_low = 0.0
        result.expected_high = 0.0
        result.drawdown = realised.max_drawdown

    result.admitted = verdict.admitted
    result.meets_target = verdict.meets_target
    result.warnings = list(verdict.warnings)
    result.reason = verdict.note
    result.rationale = (
        f"{profile.title}: целевые колебания {profile.target_volatility:.0%}, "
        f"состав усреднён по {resampled.draws} бутстрэп-выборкам. "
        f"{report.summary}"
    )

    mu_annual = annualise_mean(working.values)
    for i, instrument_id in enumerate(working.columns):
        if weights[i] < 1e-6:
            continue
        card = cards[instrument_id]
        instrument = instruments[instrument_id]
        result.positions.append(
            Position(
                instrument_id=instrument_id,
                title=instrument.title or instrument.symbol,
                asset_class=instrument.asset_class,
                weight=float(weights[i]),
                stability=float(resampled.dispersion[i]),
                role=ROLE_BY_CLASS.get(instrument.asset_class, "прочее"),
                thesis=_thesis(card, float(weights[i])),
                kill=_kill(card),
                score=card.score,
                expected_return=float(mu_annual[i]),
            )
        )
    result.positions.sort(key=lambda p: -p.weight)
    return result


# ── Прогон целиком ───────────────────────────────────────────────────────


def build_all(
    session: Session,
    *,
    draws: int = 120,
    fetch=None,
    profiles: tuple[RiskProfile, ...] = tuple(RiskProfile),
    horizons: tuple[int, ...] = (1, 5),
) -> BuildReport:
    """Пересобрать все пакеты и сохранить их.

    Старые модели того же профиля, пакета и горизонта удаляются: показывать
    вчерашний состав рядом с сегодняшним нельзя — владелец не обязан
    угадывать, какой из них действующий (UX-ТЗ §7.1, «пакет протухает»).
    """
    cfg = get_config()
    report = BuildReport()

    universe = investment_universe(session)
    report.universe = len(universe)
    if not universe:
        report.note = (
            "инвестиционной вселенной нет: доски биржи ещё не синхронизированы"
        )
        return _record(session, report)

    min_history = int(cfg.get("universe.investments.min_history_days", 120))
    cards = fund.screen(
        session, universe, min_history_days=min_history, fetch=fetch
    )
    passed = [c for c in cards if not c.rejected]
    report.screened = len(passed)
    report.rejected = [(c.instrument_id, c.rejected) for c in cards if c.rejected]
    if len(passed) < 3:
        report.note = (
            f"фундаментальный срез прошли {len(passed)} бумаг из {len(cards)} — "
            "пакет собирать не из чего"
        )
        return _record(session, report)

    by_id = {i.instrument_id: i for i in universe}
    card_by_id = {c.instrument_id: c for c in passed}

    # Кандидаты: лучшие по срезу внутри класса. Ограничение сверху защищает
    # оптимизатор от ложных связей на широком наборе.
    per_class: dict[AssetClass, list[fund.Fundamental]] = {}
    for card in passed:
        per_class.setdefault(card.asset_class, []).append(card)
    candidates: list[str] = []
    for asset_class, group in per_class.items():
        group.sort(key=lambda c: -c.score)
        limit = 10 if asset_class is AssetClass.EQUITY else 6
        candidates.extend(c.instrument_id for c in group[:limit])
    report.candidates = len(candidates)

    series = daily_closes(session, candidates)
    wf_config = cfg.section("backtest")["walk_forward"]
    train_days = int(wf_config["train_months"]) * 21
    test_days = int(wf_config["test_months"]) * 21
    needed = train_days + test_days
    # Одно окно — это не проверка, а единственное наблюдение: три окна дают
    # девять месяцев вне обучения, и на них оценка заведомо грубая. Целимся в
    # шесть окон и отступаем к минимуму, если истории не хватает.
    panel, dropped = _panel_with_window(
        series,
        needed=needed,
        target=needed + 5 * test_days,
        min_width=4,
        classes={key: by_id[key].asset_class for key in series if key in by_id},
    )
    for instrument_id, why in dropped:
        report.rejected.append((instrument_id, why))
    report.common_days = panel.periods
    if panel.width < 3 or panel.periods < 120:
        report.note = (
            f"общая история кандидатов — {panel.periods} дней по {panel.width} "
            "бумагам; для оптимизации нужно не меньше 120 дней"
        )
        return _record(session, report)
    if panel.periods < needed:
        # Оптимизацию провести можно, а проверить состав на данных, которых
        # он не видел, — нет. Считать и показать непроверенное было бы хуже
        # честного «пока нечем проверить».
        report.note = (
            f"общего окна {panel.periods} дней хватает на оптимизацию, но не на "
            f"проверку на истории: одному циклу нужно {needed} дней "
            f"({int(wf_config['train_months'])} мес. обучения плюс "
            f"{int(wf_config['test_months'])} мес. измерения). "
            "Состав появится, когда наберётся общая история."
        )

    packages = cfg.section("portfolio")["packages"]
    crypto_cap = float(cfg.get("portfolio.crypto_max_weight"))
    for key in PROFILES:
        PROFILES[key].caps[AssetClass.CRYPTO_SPOT] = min(
            PROFILES[key].caps.get(AssetClass.CRYPTO_SPOT, crypto_cap), crypto_cap
        )

    now = datetime.now(UTC)
    for profile_key in profiles:
        for package_key, config_key in (
            (PackageSize.SIMPLE, "simple"),
            (PackageSize.BALANCED, "balanced"),
            (PackageSize.MAX_POTENTIAL, "max_potential"),
        ):
            low, high = packages[config_key]
            for horizon in horizons:
                built = build_package(
                    panel,
                    card_by_id,
                    by_id,
                    profile_key=profile_key,
                    package=package_key,
                    horizon_years=horizon,
                    count=(int(low), int(high)),
                    draws=draws,
                    cfg=cfg,
                )
                report.packages.append(built)
                _persist(session, built, cfg=cfg, now=now)
    if report.packages and report.admitted == 0 and not report.note:
        # Причины разных вариантов чаще всего совпадают — показываем их
        # списком, а не одним общим «не прошёл». Владельцу нужно знать, чего
        # именно не хватило: доходности вне обучения или устойчивости.
        reasons: list[str] = []
        for package in report.packages:
            if package.reason and package.reason not in reasons:
                reasons.append(package.reason)
        report.note = (
            "составы посчитаны, но ни один не прошёл проверку: "
            + "; ".join(reasons[:2])
        )
    return _record(session, report)


def _record(session: Session, report: BuildReport) -> BuildReport:
    """Записать итог прогона: без него «нет» на экране остаётся без причины.

    Хранится один прогон, последний. История прогонов здесь не нужна: вопрос
    к конвейеру всегда один — что мешает сейчас.
    """
    session.execute(delete(PortfolioRun))
    session.add(
        PortfolioRun(
            universe=report.universe,
            screened=report.screened,
            candidates=report.candidates,
            common_days=report.common_days,
            built=len(report.packages),
            admitted=report.admitted,
            note=report.note,
            reasons_json=[
                {
                    "profile": str(p.profile),
                    "package": str(p.package),
                    "horizon_years": p.horizon_years,
                    "admitted": p.admitted,
                    "reason": p.reason,
                }
                for p in report.packages
            ],
            dropped_json=[
                {"instrument_id": key, "reason": why} for key, why in report.rejected
            ],
        )
    )
    session.flush()
    return report


def _persist(session: Session, package: Package, *, cfg, now: datetime) -> None:
    """Сохранить пакет, заменив предыдущий той же формы."""
    stale = list(
        session.execute(
            select(PortfolioModel.id).where(
                PortfolioModel.profile == package.profile,
                PortfolioModel.package == package.package,
                PortfolioModel.horizon_years == package.horizon_years,
            )
        ).scalars()
    )
    if stale:
        # Удаление идёт запросами, а не через ORM-каскад: у модели загружена
        # коллекция весов, и session.delete() пытается сначала обнулить в них
        # ссылку на модель — то есть первичный ключ. Прямой DELETE такой
        # заботы не проявляет и делает ровно то, что просили.
        session.execute(
            delete(PortfolioWeight).where(PortfolioWeight.model_id.in_(stale))
        )
        session.execute(delete(PortfolioModel).where(PortfolioModel.id.in_(stale)))
        session.expire_all()
    session.flush()

    if not package.admitted or not package.positions:
        # Не прошедший проверку состав не сохраняется как модель: модель —
        # это то, что можно показать владельцу. Причина уезжает в отчёт
        # прогона и видна в статусе портфеля.
        return

    model = PortfolioModel(
        profile=package.profile,
        package=package.package,
        horizon_years=package.horizon_years,
        expected_return_low=Decimal(str(round(package.expected_low, 6))),
        expected_return_high=Decimal(str(round(package.expected_high, 6))),
        target_volatility=Decimal(str(round(package.volatility, 6))),
        model_drawdown_limit=Decimal(str(round(package.drawdown, 6))),
        cvar_95=Decimal(str(round(package.cvar_95, 6))),
        rationale=package.rationale,
        stress_json=package.stress,
        config_hash=cfg.config_hash,
        generated_at=now,
        valid_until=now + timedelta(days=7),
    )
    session.add(model)
    session.flush()
    for position in package.positions:
        session.add(
            PortfolioWeight(
                model_id=model.id,
                instrument_id=position.instrument_id,
                asset_class=position.asset_class,
                target_weight=Decimal(str(round(position.weight, 6))),
                role=position.role,
                thesis=position.thesis,
                kill_conditions=position.kill,
                score=Decimal(str(round(position.score, 6))),
                expected_return=Decimal(str(round(position.expected_return, 6))),
            )
        )
