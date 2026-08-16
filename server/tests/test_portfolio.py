"""Портфельный контур целиком: от вселенной до ответа API.

Проверяется то, из-за чего экран «Пакеты» три недели показывал заглушку:
собирается ли состав вообще, соблюдает ли он потолки профиля, отсеивается ли
бумага без истории и что видит клиент, когда считать ещё нечего.

Данные синтетические, но устроены как рынок: денежный фонд без просадок,
облигационный с малыми колебаниями, акции с общим фактором и своим шумом.
На случайном шуме без структуры оптимизатор проверялся бы вхолостую.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.market.investments import classify_funds
from app.models import Bar, Instrument, PortfolioModel, PortfolioRun
from app.models.enums import AssetClass, PackageSize, RiskProfile, Timeframe, Venue
from app.portfolio import fundamentals as fund
from app.portfolio.build import INCOME_CLASSES, PROFILES, build_all
from tests.conftest import DEVICE_HEADERS


def _no_network(url: str):
    """Заглушка сети: дивиденды не запрашиваются, а объявляются пустыми."""
    return {"dividends": {"columns": ["registryclosedate", "value"], "data": []}}, None


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


def _add_instrument(
    session,
    instrument_id: str,
    asset_class: AssetClass,
    *,
    symbol: str,
    title: str,
    meta: dict | None = None,
) -> Instrument:
    item = Instrument(
        instrument_id=instrument_id,
        venue=Venue.MOEX,
        asset_class=asset_class,
        symbol=symbol,
        title=title,
        currency="RUB",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        lot_size=1,
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        is_tradable=True,
        in_universe=True,
        metadata_json=meta or {"market": "shares", "board": "TQBR"},
    )
    session.add(item)
    # Бары ссылаются на инструмент внешним ключом — без сброса в базу
    # вставка свечей падает раньше, чем тест доходит до сути.
    session.flush()
    return item


def _add_bars(
    session,
    instrument_id: str,
    prices: np.ndarray,
    *,
    turnover: float = 5e8,
    start: datetime | None = None,
) -> None:
    first = start or datetime.now(UTC) - timedelta(days=len(prices) + 1)
    rows = []
    for i, price in enumerate(prices):
        value = float(price)
        rows.append(
            {
                "instrument_id": instrument_id,
                "timeframe": Timeframe.D1,
                "open_time": first + timedelta(days=i),
                "open": Decimal(str(round(value, 4))),
                "high": Decimal(str(round(value * 1.004, 4))),
                "low": Decimal(str(round(value * 0.996, 4))),
                "close": Decimal(str(round(value, 4))),
                "volume_units": Decimal("1000"),
                "volume_notional": Decimal(str(turnover)),
                "is_closed": True,
                "source": "test",
                "quality_flags": [],
            }
        )
    session.bulk_insert_mappings(Bar, rows)
    session.flush()


def _walk(rng, periods: int, drift: float, vol: float, factor=None, beta: float = 0.0):
    shocks = rng.normal(drift, vol, size=periods)
    if factor is not None:
        shocks = shocks + beta * factor
    return 100 * np.exp(np.cumsum(shocks))


@pytest.fixture
def market(session):
    """Небольшой, но полноценный инвестиционный рынок."""
    rng = np.random.default_rng(11)
    periods = 900
    factor = rng.normal(0.0002, 0.011, size=periods)

    _add_instrument(
        session, "MOEX:ETF:LIQUID", AssetClass.BOND_FUND,
        symbol="LIQUID", title="Фонд денежного рынка",
        meta={"market": "shares", "board": "TQTF"},
    )
    _add_bars(session, "MOEX:ETF:LIQUID", _walk(rng, periods, 0.00055, 0.00012))

    _add_instrument(
        session, "MOEX:ETF:BONDS", AssetClass.BOND_FUND,
        symbol="BONDS", title="Фонд облигаций",
        meta={"market": "shares", "board": "TQTF"},
    )
    _add_bars(session, "MOEX:ETF:BONDS", _walk(rng, periods, 0.0004, 0.0018))

    _add_instrument(
        session, "MOEX:BOND:SU26238", AssetClass.OFZ,
        symbol="SU26238RMFS4", title="ОФЗ 26238",
        meta={
            "market": "bonds", "board": "TQOB",
            "yield": "14.2", "duration": "900", "matdate": "2031-05-15",
            "facevalue": "1000",
        },
    )
    _add_bars(session, "MOEX:BOND:SU26238", _walk(rng, periods, 0.00035, 0.0026))

    for name, beta, drift, size in (
        ("SBER", 1.0, 0.0004, "2.1e10"),
        ("LKOH", 0.8, 0.00035, "6.9e8"),
        ("GAZP", 1.2, 0.0001, "2.3e10"),
        ("ROSN", 0.9, 0.0003, "1.0e10"),
    ):
        _add_instrument(
            session, f"MOEX:EQ:{name}", AssetClass.EQUITY,
            symbol=name, title=name,
            meta={"market": "shares", "board": "TQBR", "issuesize": size},
        )
        _add_bars(
            session,
            f"MOEX:EQ:{name}",
            _walk(rng, periods, drift, 0.006, factor=factor, beta=beta),
        )
    session.flush()
    return session


# ── Классификация ────────────────────────────────────────────────────────


def test_класс_фонда_определяется_измерением_а_не_названием(market, session):
    decided = classify_funds(session)
    assert "MOEX:ETF:LIQUID" in decided
    liquid = session.query(Instrument).filter_by(
        instrument_id="MOEX:ETF:LIQUID"
    ).one()
    bonds = session.query(Instrument).filter_by(instrument_id="MOEX:ETF:BONDS").one()
    assert liquid.asset_class is AssetClass.MONEY_MARKET
    assert bonds.asset_class is AssetClass.BOND_FUND
    # Решение записано словами — по примечанию видно, чем оно обосновано.
    assert "денежный" in liquid.universe_note


# ── Фундаментальный срез ─────────────────────────────────────────────────


def test_неприменимое_не_считается_нулём(market, session):
    bond = session.query(Instrument).filter_by(instrument_id="MOEX:BOND:SU26238").one()
    cards = fund.screen(session, [bond], fetch=_no_network)
    card = cards[0]
    by_name = {m.name: m for m in card.metrics}
    assert by_name["yield"].status is fund.Measure.MEASURED
    assert by_name["dividend_yield"].status is fund.Measure.NOT_APPLICABLE
    # Неприменимое в делитель не попадает: иначе облигация проигрывала бы
    # акции только потому, что к ней меньше вопросов.
    assert card.score > 0.0
    assert not card.rejected
    assert "доходность к погашению 14.2%" in card.note


def test_бумага_без_истории_отбраковывается_с_причиной(market, session):
    _add_instrument(
        session, "MOEX:EQ:NEW", AssetClass.EQUITY, symbol="NEW", title="Новичок",
        meta={"market": "shares", "board": "TQBR", "issuesize": "1e9"},
    )
    rng = np.random.default_rng(2)
    _add_bars(session, "MOEX:EQ:NEW", _walk(rng, 30, 0.0002, 0.01))
    session.flush()
    new = session.query(Instrument).filter_by(instrument_id="MOEX:EQ:NEW").one()
    card = fund.screen(session, [new], fetch=_no_network)[0]
    assert card.rejected
    assert "истории" in card.rejected


def test_неликвидная_бумага_не_попадает_в_пакет(market, session):
    _add_instrument(
        session, "MOEX:EQ:THIN", AssetClass.EQUITY, symbol="THIN", title="Неликвид",
        meta={"market": "shares", "board": "TQBR", "issuesize": "1e9"},
    )
    rng = np.random.default_rng(4)
    _add_bars(session, "MOEX:EQ:THIN", _walk(rng, 400, 0.0002, 0.01), turnover=1e5)
    session.flush()
    thin = session.query(Instrument).filter_by(instrument_id="MOEX:EQ:THIN").one()
    card = fund.screen(
        session, [thin], min_turnover_rub=5_000_000, fetch=_no_network
    )[0]
    assert "оборот" in card.rejected


# ── Сборка ───────────────────────────────────────────────────────────────


def test_пакеты_собираются_и_сохраняются(market, session):
    classify_funds(session)
    report = build_all(
        session, draws=20, fetch=_no_network,
        profiles=(RiskProfile.OPTIMAL,), horizons=(1,),
    )
    assert report.universe >= 7
    assert report.screened >= 5
    assert report.packages, report.note
    assert report.admitted >= 1, [p.reason for p in report.packages]

    saved = session.query(PortfolioModel).all()
    assert saved
    for model in saved:
        weights = model.weights
        assert weights
        total = sum(float(w.target_weight) for w in weights)
        assert total == pytest.approx(1.0, abs=1e-4)
        # Каждая позиция объяснена и имеет условие выхода — иначе это не
        # рекомендация, а список тикеров.
        for weight in weights:
            assert weight.role
            assert weight.thesis
            assert weight.kill_conditions


def test_размер_пакета_ограничивает_число_позиций(market, session):
    classify_funds(session)
    build_all(
        session, draws=20, fetch=_no_network,
        profiles=(RiskProfile.OPTIMAL,), horizons=(1,),
    )
    simple = (
        session.query(PortfolioModel)
        .filter_by(package=PackageSize.SIMPLE, horizon_years=1)
        .one_or_none()
    )
    if simple is not None:
        # Размер пакета — потолок широты, а не квота. Снизу его держит только
        # настоящий минимум портфеля: одна бумага — это ставка, а не состав.
        # Добор до заявленного числа затаскивал в пакет бумаги, которым
        # оптимизатор веса не дал.
        assert 2 <= len(simple.weights) <= 5


def test_консервативный_профиль_держит_потолок_по_акциям(market, session):
    classify_funds(session)
    build_all(
        session, draws=20, fetch=_no_network,
        profiles=(RiskProfile.CONSERVATIVE,), horizons=(1,),
    )
    cap = PROFILES[RiskProfile.CONSERVATIVE].caps[AssetClass.EQUITY]
    models = session.query(PortfolioModel).all()
    assert models
    for model in models:
        equity = sum(
            float(w.target_weight)
            for w in model.weights
            if w.asset_class is AssetClass.EQUITY
        )
        assert equity <= cap + 1e-3


def test_повторный_прогон_сохраняет_поколения_каждого_слота(market, session):
    classify_funds(session)
    for _ in range(2):
        build_all(
            session, draws=20, fetch=_no_network,
            profiles=(RiskProfile.OPTIMAL,), horizons=(1,),
        )

    models = session.query(PortfolioModel).all()
    shapes = {(m.profile, m.package, m.horizon_years) for m in models}

    # Stage 4: повторный расчёт создаёт новое поколение, а не уничтожает
    # предыдущую рекомендацию. При этом набор логических слотов не меняется.
    assert len(shapes) == 3
    assert len(models) == 2 * len(shapes)
    for shape in shapes:
        assert sum(
            1
            for model in models
            if (model.profile, model.package, model.horizon_years) == shape
        ) == 2


# ── API ──────────────────────────────────────────────────────────────────


def test_пустой_портфель_называет_шаг_на_котором_стоит(client):
    body = client.get("/api/v1/portfolio/packages").json()
    assert body["packages"] == []
    assert body["status"]["reason"]
    names = [s["name"] for s in body["status"]["stages"]]
    assert names[0] == "Вселенная инструментов"
    assert not body["status"]["stages"][0]["done"]


def test_api_отдаёт_состав_с_долями_строками(market, session, client):
    classify_funds(session)
    build_all(
        session, draws=20, fetch=_no_network,
        profiles=(RiskProfile.OPTIMAL,), horizons=(1,),
    )
    session.flush()
    body = client.get("/api/v1/portfolio/packages?horizon_years=1").json()
    assert body["packages"], body["status"]["reason"]
    package = body["packages"][0]
    assert package["positions"]
    # Доли — строки: из них считается сумма покупки, и double тут недопустим.
    assert isinstance(package["positions"][0]["target_weight"], str)
    assert isinstance(package["expected_return_low"], str)
    assert package["mix"], "разрез по классам нужен для полосы на экране"
    assert sum(float(m["weight"]) for m in package["mix"]) == pytest.approx(1.0, abs=1e-4)
    stages = {s["key"]: s for s in body["status"]["stages"]}
    assert stages["optimisation"]["done"]
    assert stages["walkforward"]["done"]


def test_короткая_бумага_не_обрезает_окно_остальным(market, session):
    """Одна недавно допущенная бумага не должна отменять проверку на истории.

    Панель строится по датам, общим для всех, — и бумага с полугодом истории
    обрезала общее окно до полугода. Скользящей проверке нужно два года
    обучения плюс квартал измерения; она не проводилась ни разу, значит ни
    один состав не мог быть допущен. Ровно это и произошло на сервере: 54
    бумаги с историей и ни одного пакета.
    """
    rng = np.random.default_rng(21)
    _add_instrument(
        session, "MOEX:EQ:FRESH", AssetClass.EQUITY, symbol="FRESH", title="Новичок",
        meta={"market": "shares", "board": "TQBR", "issuesize": "1e10"},
    )
    # 200 дней — выше порога среза (120), но втрое меньше окна проверки.
    _add_bars(session, "MOEX:EQ:FRESH", _walk(rng, 200, 0.0003, 0.006))
    session.flush()

    classify_funds(session)
    report = build_all(
        session, draws=20, fetch=_no_network,
        profiles=(RiskProfile.OPTIMAL,), horizons=(1,),
    )

    # Короткий ряд отброшен с причиной, а не проглочен молча.
    dropped = dict(report.rejected)
    assert "MOEX:EQ:FRESH" in dropped
    assert "истории" in dropped["MOEX:EQ:FRESH"]
    # И составы посчитаны — окно осталось длинным.
    assert report.admitted >= 1, [p.reason for p in report.packages]


def test_прогон_записывает_причину_а_не_только_итог(market, session):
    """«Состава нет» обязано отличать «не считали» от «посчитали и забраковали»."""
    classify_funds(session)
    build_all(
        session, draws=20, fetch=_no_network,
        profiles=(RiskProfile.OPTIMAL,), horizons=(1,),
    )
    session.flush()
    run = session.query(PortfolioRun).one()
    assert run.universe >= 7
    assert run.built == 3, "три размера пакета на один профиль и горизонт"
    assert run.common_days > 0
    # По каждому варианту записано, допущен он и почему.
    assert len(run.reasons_json) == run.built
    for entry in run.reasons_json:
        assert entry["reason"], "отказ без причины недопустим"


# ── Доходные бумаги и падающий рынок ─────────────────────────────────────


def _falling_market(session, *, income_history: int = 900, equity_history: int = 900):
    """Рынок, где акции падают, а денежный рынок и облигации растут."""
    rng = np.random.default_rng(5)

    _add_instrument(
        session, "MOEX:ETF:LQDT", AssetClass.MONEY_MARKET,
        symbol="LQDT", title="Ликвидность",
        meta={"market": "shares", "board": "TQTF"},
    )
    _add_bars(session, "MOEX:ETF:LQDT", _walk(rng, income_history, 0.00055, 0.00012))

    _add_instrument(
        session, "MOEX:ETF:BONDS", AssetClass.BOND_FUND,
        symbol="BONDS", title="Фонд облигаций",
        meta={"market": "shares", "board": "TQTF"},
    )
    _add_bars(session, "MOEX:ETF:BONDS", _walk(rng, income_history, 0.0004, 0.0018))

    factor = rng.normal(-0.0009, 0.012, size=equity_history)
    for name in ("SBER", "GAZP", "LKOH", "ROSN"):
        _add_instrument(
            session, f"MOEX:EQ:{name}", AssetClass.EQUITY,
            symbol=name, title=name,
            meta={"market": "shares", "board": "TQBR", "issuesize": "2.1e10"},
        )
        _add_bars(
            session, f"MOEX:EQ:{name}",
            _walk(rng, equity_history, -0.0009, 0.006, factor=factor, beta=1.0),
        )
    session.flush()


def test_консервативный_пакет_собирается_на_падающем_рынке(session):
    """Падающий рынок акций может длиться годами — деньги ждать не должны.

    Отдельного «доходного» профиля для этого не нужно: денежный рынок и
    облигации есть среди активов, и оптимизатор обязан прийти к ним сам.
    """
    _falling_market(session)
    report = build_all(session, draws=12, fetch=_no_network)

    conservative = [
        p for p in report.packages if p.profile is RiskProfile.CONSERVATIVE
    ]
    admitted = [p for p in conservative if p.admitted]
    assert admitted, (
        "консервативный состав не допущен ни разу: "
        + "; ".join(p.reason for p in conservative if p.reason)
    )
    # Состав пришёл к доходным бумагам сам — не потому, что ему их назначили.
    for package in admitted:
        income = sum(
            p.weight for p in package.positions
            if p.asset_class in INCOME_CLASSES
        )
        assert income > 0.5, f"доходных бумаг только {income:.0%}"


def test_та_же_логика_работает_для_остальных_профилей(session):
    """Оптимальный и агрессивный считаются из того же набора активов.

    Профиль задаёт потолки и целевые колебания, а не отдельный список бумаг.
    Если доходные бумаги дошли до отбора, ими вправе воспользоваться любой
    профиль — и в падающий рынок он это и сделает.
    """
    _falling_market(session)
    report = build_all(session, draws=12, fetch=_no_network)

    for profile in (RiskProfile.OPTIMAL, RiskProfile.AGGRESSIVE):
        packages = [p for p in report.packages if p.profile is profile]
        admitted = [p for p in packages if p.admitted]
        assert admitted, (
            f"{profile.value}: ни один состав не допущен — "
            + "; ".join(p.reason for p in packages if p.reason)
        )
        for package in admitted:
            income = sum(
                p.weight for p in package.positions
                if p.asset_class in INCOME_CLASSES
            )
            assert income > 0, (
                f"{profile.value}: доходных бумаг нет вовсе, хотя рынок падает"
            )


def test_доходные_бумаги_не_режутся_ради_длины_окна(session):
    """Фонды моложе акций — и вылетали первыми ровно те, на которых держится
    положительный состав в падающий год.

    Длинное окно — удобство оценки; бумага, растущая при падающем рынке, —
    единственное, из чего такой состав вообще собирается.
    """
    _falling_market(session, income_history=700, equity_history=1400)
    report = build_all(session, draws=12, fetch=_no_network)

    assert report.income_candidates >= 2, (
        "доходные бумаги выброшены из панели: " + report.income_note
    )
    dropped = {key for key, _ in report.rejected}
    assert "MOEX:ETF:LQDT" not in dropped
    assert "MOEX:ETF:BONDS" not in dropped


def test_без_доходных_бумаг_причина_названа_отдельно(session):
    """«Рынок акций падает» и «доходных бумаг нет во вселенной» — разное."""
    rng = np.random.default_rng(9)
    factor = rng.normal(0.0002, 0.011, size=900)
    for name in ("SBER", "GAZP", "LKOH", "ROSN"):
        _add_instrument(
            session, f"MOEX:EQ:{name}", AssetClass.EQUITY,
            symbol=name, title=name,
            meta={"market": "shares", "board": "TQBR", "issuesize": "2.1e10"},
        )
        _add_bars(
            session, f"MOEX:EQ:{name}",
            _walk(rng, 900, 0.0004, 0.006, factor=factor, beta=1.0),
        )
    session.flush()

    report = build_all(session, draws=12, fetch=_no_network)
    assert report.income_candidates == 0
    assert "доходной бумаги" in report.income_note
