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
from app.portfolio.rebalance import URGENT_DRIFT, latest_holdings, plan, record

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
    # Налог помечен оценочным: окончательная сумма за брокером (§15.1 UX-ТЗ).
    assert row.tax_is_estimate is True


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
        with TestClient(app) as client:
            body = client.get("/api/v1/portfolio/rebalance").json()
    finally:
        app.dependency_overrides.clear()

    assert body["needed"] is True
    assert body["urgent"] is True
    assert {a["side"] for a in body["actions"]} == {"SELL", "BUY"}
    assert "executable" not in body
    assert "order_id" not in body
    assert str(model.id) == body["model_id"]
