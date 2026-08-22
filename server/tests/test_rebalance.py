"""Черновик ребаланса от фактических позиций §6.6.

Владелец сформулировал контур прямо: сервис читает счёт, предлагает пакеты,
следит и предлагает ребаланс — а проводит его владелец руками. Значит здесь
считается расхождение и предложение, и ни при каких условиях не заявка.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Account, Holding, PortfolioModel, PortfolioWeight, RebalanceDraft
from app.models.enums import AssetClass, PackageSize, RiskProfile
from app.portfolio.rebalance import (
    URGENT_DRIFT,
    BrokerFinalEconomics,
    EconomicsStatus,
    RebalanceEconomicsPolicy,
    latest_holdings,
    plan,
    record,
)
from tests.conftest import DEVICE_HEADERS

NOW = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)


def _model(session: Session, weights: dict[str, str]) -> PortfolioModel:
    model = PortfolioModel(
        profile=RiskProfile.CONSERVATIVE,
        package=PackageSize.SIMPLE,
        horizon_years=1,
        expected_return_low=Decimal("0.08"),
        expected_return_high=Decimal("0.12"),
        target_volatility=Decimal("0.06"),
        model_drawdown_limit=Decimal("0.08"),
        rationale="тест",
        generated_at=NOW,
        valid_until=NOW + timedelta(days=30),
        config_hash="0" * 64,
    )
    session.add(model)
    session.flush()
    for instrument_id, share in weights.items():
        session.add(
            PortfolioWeight(
                model_id=model.id,
                instrument_id=instrument_id,
                asset_class=AssetClass.EQUITY,
                target_weight=Decimal(share),
                role="рост капитала",
                thesis="тест",
                kill_conditions="тест",
            )
        )
    session.flush()
    session.refresh(model)
    return model


def _holding(account_id, instrument_id: str, value: str, at: date) -> Holding:
    return Holding(
        account_id=account_id,
        instrument_id=instrument_id,
        as_of=at,
        quantity=Decimal("1"),
        market_value=Decimal(value),
        asset_class=AssetClass.EQUITY,
    )


def _account(session: Session) -> Account:
    account = Account(
        external_id="ACC-1",
        broker="tinvest",
        title="Инвестиции",
        circuit="investment",
    )
    session.add(account)
    session.flush()
    return account


def test_состав_в_допуске_действий_не_требует(session: Session):
    model = _model(session, {"A": "0.5", "B": "0.5"})
    account = _account(session)
    holdings = [
        _holding(account.id, "A", "51000", NOW.date()),
        _holding(account.id, "B", "49000", NOW.date()),
    ]
    draft = plan(model, holdings)
    assert not draft.needed
    assert "в пределах допуска" in draft.reason


def test_расхождение_доли_превращается_в_действие(session: Session):
    model = _model(session, {"A": "0.5", "B": "0.5"})
    account = _account(session)
    holdings = [
        _holding(account.id, "A", "70000", NOW.date()),
        _holding(account.id, "B", "30000", NOW.date()),
    ]
    draft = plan(model, holdings)
    assert draft.needed
    sides = {a.instrument_id: a.side for a in draft.actions}
    assert sides == {"A": "SELL", "B": "BUY"}
    # Сумма считается от фактической стоимости счёта, а не от целевой.
    assert draft.actions[0].amount_rub == Decimal("20000.00")


def test_сначала_продажи_потом_покупки(session: Session):
    """Покупать не на что, пока деньги лежат в другой бумаге."""
    model = _model(session, {"A": "0.2", "B": "0.8"})
    account = _account(session)
    holdings = [
        _holding(account.id, "A", "80000", NOW.date()),
        _holding(account.id, "B", "20000", NOW.date()),
    ]
    draft = plan(model, holdings)
    assert draft.actions[0].side == "SELL"


def test_бумага_вне_состава_закрывается_целиком(session: Session):
    model = _model(session, {"A": "1.0"})
    account = _account(session)
    holdings = [
        _holding(account.id, "A", "80000", NOW.date()),
        _holding(account.id, "OLD", "20000", NOW.date()),
    ]
    draft = plan(model, holdings)
    old = next(a for a in draft.actions if a.instrument_id == "OLD")
    assert old.side == "SELL"
    assert "нет в целевом составе" in old.reason


def test_мелкий_остаток_не_стоит_заявки(session: Session):
    """Бумага на полпроцента счёта не окупит комиссии."""
    model = _model(session, {"A": "1.0"})
    account = _account(session)
    holdings = [
        _holding(account.id, "A", "99500", NOW.date()),
        _holding(account.id, "DUST", "500", NOW.date()),
    ]
    draft = plan(model, holdings)
    assert [a.instrument_id for a in draft.actions] == []


def test_крупное_расхождение_отмечается_как_срочное(session: Session):
    model = _model(session, {"A": "0.5", "B": "0.5"})
    account = _account(session)
    holdings = [
        _holding(account.id, "A", "80000", NOW.date()),
        _holding(account.id, "B", "20000", NOW.date()),
    ]
    draft = plan(model, holdings)
    assert draft.max_drift >= URGENT_DRIFT
    assert draft.urgent


def test_пустой_счёт_не_повод_для_ребаланса(session: Session):
    model = _model(session, {"A": "1.0"})
    draft = plan(model, [])
    assert not draft.needed
    assert "нет позиций" in draft.reason


def test_снимки_разных_дат_не_смешиваются(session: Session):
    """Доля по вчерашней цене одной бумаги и сегодняшней другой — это не
    состав портфеля ни на один момент времени."""
    model = _model(session, {"A": "0.5", "B": "0.5"})
    account = _account(session)
    session.add(_holding(account.id, "A", "10000", date(2026, 7, 30)))
    session.add(_holding(account.id, "A", "70000", NOW.date()))
    session.add(_holding(account.id, "B", "30000", NOW.date()))
    session.flush()

    latest = latest_holdings(session, account.id)
    assert {h.instrument_id for h in latest} == {"A", "B"}
    assert all(h.as_of == NOW.date() for h in latest)


def test_черновик_сохраняется_с_причиной(session: Session):
    model = _model(session, {"A": "0.5", "B": "0.5"})
    account = _account(session)
    holdings = [
        _holding(account.id, "A", "80000", NOW.date()),
        _holding(account.id, "B", "20000", NOW.date()),
    ]
    draft = plan(model, holdings)
    row = record(session, model, draft, now=NOW)
    assert row is not None
    assert row.trigger_reason == "drift_urgent"
    assert len(row.actions_json) == 2
    # Без policy/lot/price/cost-basis неизвестность нельзя маскировать
    # нулём.
    assert row.economics_status == EconomicsStatus.UNKNOWN.value
    assert row.estimated_costs is None
    assert row.estimated_tax is None
    assert row.tax_is_estimate is False


def test_нечего_делать_в_журнал_не_пишется(session: Session):
    model = _model(session, {"A": "0.5", "B": "0.5"})
    account = _account(session)
    holdings = [
        _holding(account.id, "A", "50000", NOW.date()),
        _holding(account.id, "B", "50000", NOW.date()),
    ]
    assert record(session, model, plan(model, holdings)) is None
    assert session.query(RebalanceDraft).count() == 0


def test_апи_отдаёт_предложение_а_не_заявку(session: Session):
    """У ответа нет и не может быть поля «исполнить».

    Контур инвестиций читает счёт токеном на чтение: заявку по этому
    предложению отправлять нечем, и делать вид, что можно, — худшее, что
    здесь можно сделать.
    """
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    model = _model(session, {"A": "0.5", "B": "0.5"})
    account = _account(session)
    session.add(_holding(account.id, "A", "80000", NOW.date()))
    session.add(_holding(account.id, "B", "20000", NOW.date()))
    session.flush()

    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app, headers=DEVICE_HEADERS) as client:
            body = client.get("/api/v1/portfolio/rebalance").json()
    finally:
        app.dependency_overrides.clear()

    assert body["needed"] is True
    assert body["urgent"] is True
    assert {a["side"] for a in body["actions"]} == {"SELL", "BUY"}
    assert "executable" not in body
    assert "order_id" not in body
    assert str(model.id) == body["model_id"]


# ── Снимок фактических позиций ──────────────────────────────────────────────
#
# Токен Т-Инвестиций на чтение лежит в защищённом хранилище телефона и на VPS
# не передаётся: он привязан к пользователю, а не к счёту, и видит все счета
# владельца. Поэтому счёт читает устройство, а сюда приезжает результат
# чтения. До этого ребаланс считать было не от чего — таблица позиций
# заполнялась только в тестах.


def _instrument(session: Session, symbol: str, instrument_id: str) -> None:
    from app.models import Instrument
    from app.models.enums import Venue

    session.add(
        Instrument(
            instrument_id=instrument_id,
            venue=Venue.MOEX,
            asset_class=AssetClass.EQUITY,
            symbol=symbol,
            title=symbol,
            currency="RUB",
            tick_size=Decimal("0.01"),
            tick_value=Decimal("0.01"),
            lot_size=1,
            quantity_step=Decimal("1"),
            min_quantity=Decimal("1"),
            contract_multiplier=Decimal("1"),
        )
    )
    session.flush()


def _put(session: Session, payload: dict) -> dict:
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app, headers=DEVICE_HEADERS) as client:
            return client.post("/api/v1/portfolio/holdings", json=payload).json()
    finally:
        app.dependency_overrides.clear()


def test_снимок_с_устройства_становится_фактическим_составом(session: Session):
    _instrument(session, "LQDT", "MOEX:SHARE:LQDT")
    _instrument(session, "SBER", "MOEX:SHARE:SBER")

    body = _put(
        session,
        {
            "broker": "tinvest",
            "account_id": "ACC-READ",
            "title": "Инвестиции",
            "as_of": "2026-07-31",
            "positions": [
                {"symbol": "LQDT", "quantity": "100", "market_value": "60000"},
                {"symbol": "SBER", "quantity": "10", "market_value": "40000"},
            ],
        },
    )

    assert body["stored"] == 2
    assert body["unknown"] == []
    assert Decimal(body["total_value"]) == Decimal("100000")

    account = session.query(Account).filter_by(external_id="ACC-READ").one()
    # Контур проставляется сервером, а не приходит снаружи: позволить вызову
    # объявить счёт риск-контуром значило бы дать ему менять расчёт лимитов.
    assert account.circuit == "investment"
    holdings = latest_holdings(session, account.id)
    assert {h.instrument_id for h in holdings} == {
        "MOEX:SHARE:LQDT",
        "MOEX:SHARE:SBER",
    }


def test_неопознанная_бумага_не_пропадает_молча(session: Session):
    """Иначе доли посчитались бы от неполной суммы.

    Ребаланс предложил бы докупить то, что уже куплено, — и владелец купил
    бы это второй раз.
    """
    _instrument(session, "LQDT", "MOEX:SHARE:LQDT")

    body = _put(
        session,
        {
            "account_id": "ACC-READ",
            "positions": [
                {"symbol": "LQDT", "quantity": "100", "market_value": "60000"},
                {"symbol": "ЧТОТОСВОЁ", "quantity": "5", "market_value": "40000"},
            ],
        },
    )

    assert body["stored"] == 1
    assert body["unknown"] == ["ЧТОТОСВОЁ"]
    assert "не опознано" in body["note"]


def test_повторное_чтение_того_же_дня_не_удваивает_позиции(session: Session):
    _instrument(session, "LQDT", "MOEX:SHARE:LQDT")
    payload = {
        "account_id": "ACC-READ",
        "as_of": "2026-07-31",
        "positions": [
            {"symbol": "LQDT", "quantity": "100", "market_value": "60000"}
        ],
    }
    _put(session, payload)
    payload["positions"][0]["market_value"] = "61000"
    body = _put(session, payload)

    account = session.query(Account).filter_by(external_id="ACC-READ").one()
    holdings = latest_holdings(session, account.id)
    assert len(holdings) == 1
    assert holdings[0].market_value == Decimal("61000")
    assert body["stored"] == 1


def test_снимки_копятся_по_датам(session: Session):
    """История нужна: ребаланс сравнивает «было» и «стало»."""
    _instrument(session, "LQDT", "MOEX:SHARE:LQDT")
    for day, value in (("2026-07-30", "60000"), ("2026-07-31", "62000")):
        _put(
            session,
            {
                "account_id": "ACC-READ",
                "as_of": day,
                "positions": [
                    {"symbol": "LQDT", "quantity": "100", "market_value": value}
                ],
            },
        )

    account = session.query(Account).filter_by(external_id="ACC-READ").one()
    assert session.query(Holding).filter_by(account_id=account.id).count() == 2
    # Доли считаются по одной дате: смешать вчерашнюю цену одной бумаги с
    # сегодняшней другой — это состав портфеля ни на один момент времени.
    latest = latest_holdings(session, account.id)
    assert len(latest) == 1
    assert latest[0].as_of == date(2026, 7, 31)


def test_снимок_замыкает_контур_до_предложения(session: Session):
    """Прочитали счёт — получили предложение. Раньше цепочка рвалась здесь."""
    _instrument(session, "LQDT", "MOEX:SHARE:LQDT")
    _instrument(session, "SBER", "MOEX:SHARE:SBER")
    _model(session, {"MOEX:SHARE:LQDT": "0.5", "MOEX:SHARE:SBER": "0.5"})
    _put(
        session,
        {
            "account_id": "ACC-READ",
            "positions": [
                {"symbol": "LQDT", "quantity": "100", "market_value": "80000"},
                {"symbol": "SBER", "quantity": "10", "market_value": "20000"},
            ],
        },
    )

    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app, headers=DEVICE_HEADERS) as client:
            body = client.get("/api/v1/portfolio/rebalance").json()
    finally:
        app.dependency_overrides.clear()

    assert body["needed"] is True
    assert "не подключён" not in body["reason"]
    assert {a["side"] for a in body["actions"]} == {"SELL", "BUY"}


# ── Экономика ребаланса ─────────────────────────────


def _economic_holding(
    account_id,
    instrument_id: str,
    value: str,
    *,
    quantity: str,
    market_price: str | None,
    average_price: str | None,
) -> Holding:
    return Holding(
        account_id=account_id,
        instrument_id=instrument_id,
        as_of=NOW.date(),
        quantity=Decimal(quantity),
        market_value=Decimal(value),
        market_price=None if market_price is None else Decimal(market_price),
        average_price=None if average_price is None else Decimal(average_price),
        asset_class=AssetClass.EQUITY,
    )


def _economics_policy() -> RebalanceEconomicsPolicy:
    return RebalanceEconomicsPolicy(
        policy_id="fixture-fee-and-gain-v1",
        fee_bps=Decimal("15"),
        capital_gain_tax_rate=Decimal("0.13"),
    )


def test_rebalance_without_fee_or_cost_basis_is_unknown_and_not_actionable(
    session: Session,
):
    """Zero is never a substitute for missing economics inputs."""
    model = _model(session, {"A": "0.5", "B": "0.5"})
    account = _account(session)
    holdings = [
        _economic_holding(
            account.id,
            "A",
            "80000",
            quantity="800",
            market_price="100",
            average_price=None,
        ),
        _economic_holding(
            account.id,
            "B",
            "20000",
            quantity="200",
            market_price="100",
            average_price="80",
        ),
    ]

    draft = plan(model, holdings)

    assert draft.needed is True
    assert draft.actionable is False
    assert {action.economics.status for action in draft.actions} == {
        EconomicsStatus.UNKNOWN
    }
    assert all(action.economics.estimated_costs_rub is None for action in draft.actions)
    assert all(action.economics.estimated_tax_rub is None for action in draft.actions)
    assert all("fee_policy" in action.economics.blockers for action in draft.actions)


def test_rebalance_uses_configured_lots_fee_and_gain_tax_with_currency_rounding(
    session: Session,
):
    model = _model(session, {"A": "0.5", "B": "0.5"})
    account = _account(session)
    holdings = [
        _economic_holding(
            account.id,
            "A",
            "80000",
            quantity="800",
            market_price="101",
            average_price="60",
        ),
        _economic_holding(
            account.id,
            "B",
            "20000",
            quantity="200",
            market_price="101",
            average_price="80",
        ),
    ]

    draft = plan(
        model,
        holdings,
        economics_policy=_economics_policy(),
        lot_sizes={"A": 10, "B": 10},
    )

    assert draft.actionable is True
    sell = next(action for action in draft.actions if action.side == "SELL")
    buy = next(action for action in draft.actions if action.side == "BUY")
    assert sell.economics.status is EconomicsStatus.ESTIMATED
    assert sell.economics.order_quantity == Decimal("290")
    assert sell.economics.order_notional_rub == Decimal("29290.00")
    assert sell.economics.estimated_costs_rub == Decimal("43.94")
    assert sell.economics.estimated_tax_rub == Decimal("1545.70")
    assert buy.economics.order_quantity == Decimal("290")
    assert buy.economics.estimated_costs_rub == Decimal("43.94")
    assert buy.economics.estimated_tax_rub == Decimal("0.00")
    assert sell.economics.provenance["policy_id"] == "fixture-fee-and-gain-v1"


def test_rebalance_with_policy_but_missing_sell_cost_basis_stays_fail_closed(
    session: Session,
):
    model = _model(session, {"A": "0.5", "B": "0.5"})
    account = _account(session)
    holdings = [
        _economic_holding(
            account.id,
            "A",
            "80000",
            quantity="800",
            market_price="100",
            average_price=None,
        ),
        _economic_holding(
            account.id,
            "B",
            "20000",
            quantity="200",
            market_price="100",
            average_price="80",
        ),
    ]

    draft = plan(
        model,
        holdings,
        economics_policy=_economics_policy(),
        lot_sizes={"A": 10, "B": 10},
    )

    sell = next(action for action in draft.actions if action.side == "SELL")
    assert draft.actionable is False
    assert sell.economics.status is EconomicsStatus.UNKNOWN
    assert "cost_basis" in sell.economics.blockers
    assert sell.economics.estimated_tax_rub is None


def test_record_persists_per_action_estimate_provenance_without_broker_final(
    session: Session,
):
    model = _model(session, {"A": "0.5", "B": "0.5"})
    account = _account(session)
    draft = plan(
        model,
        [
            _economic_holding(
                account.id,
                "A",
                "80000",
                quantity="800",
                market_price="100",
                average_price="60",
            ),
            _economic_holding(
                account.id,
                "B",
                "20000",
                quantity="200",
                market_price="100",
                average_price="80",
            ),
        ],
        economics_policy=_economics_policy(),
        lot_sizes={"A": 10, "B": 10},
    )

    row = record(session, model, draft, now=NOW)

    assert row is not None
    assert row.economics_status == EconomicsStatus.ESTIMATED.value
    assert row.estimated_costs == Decimal("90.00")
    assert row.estimated_tax == Decimal("1560.00")
    assert row.broker_final_costs is None
    assert row.broker_final_tax is None
    assert row.tax_is_estimate is True
    assert {item["economics"]["status"] for item in row.actions_json} == {
        EconomicsStatus.ESTIMATED.value
    }
    assert row.economics_provenance_json["policy_id"] == "fixture-fee-and-gain-v1"


def test_record_keeps_broker_final_amounts_separate_from_estimates(session: Session):
    model = _model(session, {"A": "0.5", "B": "0.5"})
    account = _account(session)
    draft = plan(
        model,
        [
            _economic_holding(
                account.id,
                "A",
                "80000",
                quantity="800",
                market_price="100",
                average_price="60",
            ),
            _economic_holding(
                account.id,
                "B",
                "20000",
                quantity="200",
                market_price="100",
                average_price="80",
            ),
        ],
        economics_policy=_economics_policy(),
        lot_sizes={"A": 10, "B": 10},
    )

    row = record(
        session,
        model,
        draft,
        now=NOW,
        broker_finals={
            "A": BrokerFinalEconomics(
                costs_rub=Decimal("46.00"),
                tax_rub=Decimal("1561.00"),
                reference="broker:A:2026-07-31",
                executed_quantity=Decimal("300"),
                executed_notional_rub=Decimal("30000"),
            ),
            "B": BrokerFinalEconomics(
                costs_rub=Decimal("44.00"),
                tax_rub=Decimal("0"),
                reference="broker:B:2026-07-31",
                executed_quantity=Decimal("300"),
                executed_notional_rub=Decimal("30000"),
            ),
        },
    )

    assert row is not None
    assert row.economics_status == EconomicsStatus.BROKER_FINAL.value
    assert row.estimated_costs == Decimal("90.00")
    assert row.estimated_tax == Decimal("1560.00")
    assert row.broker_final_costs == Decimal("90.00")
    assert row.broker_final_tax == Decimal("1561.00")
    assert row.tax_is_estimate is False
    assert {
        item["economics"]["broker_final_costs_rub"] for item in row.actions_json
    } == {"44.00", "46.00"}


def test_rebalance_api_exposes_unknown_economics_as_non_actionable(session: Session):
    """The current unconfigured default must not look like a zero-cost trade."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    model = _model(session, {"A": "0.5", "B": "0.5"})
    account = _account(session)
    session.add(
        _economic_holding(
            account.id,
            "A",
            "80000",
            quantity="800",
            market_price="100",
            average_price="60",
        )
    )
    session.add(
        _economic_holding(
            account.id,
            "B",
            "20000",
            quantity="200",
            market_price="100",
            average_price="80",
        )
    )
    session.flush()

    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app, headers=DEVICE_HEADERS) as client:
            body = client.get("/api/v1/portfolio/rebalance").json()
    finally:
        app.dependency_overrides.clear()

    assert str(model.id) == body["model_id"]
    assert body["needed"] is True
    assert body["actionable"] is False
    assert body["economics_status"] == EconomicsStatus.UNKNOWN.value
    assert {action["economics_status"] for action in body["actions"]} == {
        EconomicsStatus.UNKNOWN.value
    }
    assert all(action["estimated_costs_rub"] is None for action in body["actions"])
    assert all(action["estimated_tax_rub"] is None for action in body["actions"])
