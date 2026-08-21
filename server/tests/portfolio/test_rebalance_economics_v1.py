"""DB-independent contract for fail-closed rebalance economics."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.portfolio.rebalance import (
    Action,
    ActionEconomics,
    BrokerFinalEconomics,
    Draft,
    EconomicsStatus,
    RebalanceEconomicsPolicy,
    _with_broker_finals,
    plan,
)


def _model() -> SimpleNamespace:
    return SimpleNamespace(
        id="model-v1",
        weights=(
            SimpleNamespace(instrument_id="A", target_weight=Decimal("0.5")),
            SimpleNamespace(instrument_id="B", target_weight=Decimal("0.5")),
        ),
    )


def _holding(
    instrument_id: str,
    value: str,
    *,
    quantity: str,
    market_price: str | None,
    average_price: str | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        instrument_id=instrument_id,
        market_value=Decimal(value),
        quantity=Decimal(quantity),
        market_price=None if market_price is None else Decimal(market_price),
        average_price=None if average_price is None else Decimal(average_price),
    )


def _policy() -> RebalanceEconomicsPolicy:
    return RebalanceEconomicsPolicy(
        policy_id="fixture-fee-and-gain-v1",
        fee_bps=Decimal("15"),
        capital_gain_tax_rate=Decimal("0.13"),
    )


def test_unknown_policy_never_becomes_zero_cost_action():
    draft = plan(
        _model(),
        [
            _holding(
                "A", "80000", quantity="800", market_price="100", average_price="60"
            ),
            _holding(
                "B", "20000", quantity="200", market_price="100", average_price="80"
            ),
        ],
    )

    assert draft.actionable is False
    assert draft.economics_status is EconomicsStatus.UNKNOWN
    assert draft.estimated_costs_rub is None
    assert draft.estimated_tax_rub is None
    assert all(action.economics.blockers == ("fee_policy",) for action in draft.actions)


def test_policy_uses_lot_floor_fee_tax_and_currency_rounding():
    draft = plan(
        _model(),
        [
            _holding(
                "A", "80000", quantity="800", market_price="101", average_price="60"
            ),
            _holding(
                "B", "20000", quantity="200", market_price="101", average_price="80"
            ),
        ],
        economics_policy=_policy(),
        lot_sizes={"A": 10, "B": 10},
    )

    sell = next(action for action in draft.actions if action.side == "SELL")
    buy = next(action for action in draft.actions if action.side == "BUY")
    assert draft.actionable is True
    assert draft.economics_status is EconomicsStatus.ESTIMATED
    assert sell.economics.order_quantity == Decimal("290")
    assert sell.economics.order_notional_rub == Decimal("29290.00")
    assert sell.economics.estimated_costs_rub == Decimal("43.94")
    assert sell.economics.estimated_tax_rub == Decimal("1545.70")
    assert buy.economics.estimated_costs_rub == Decimal("43.94")
    assert buy.economics.estimated_tax_rub == Decimal("0.00")
    assert draft.estimated_costs_rub == Decimal("87.88")
    assert draft.estimated_tax_rub == Decimal("1545.70")


def test_missing_sell_cost_basis_blocks_even_when_fee_policy_is_configured():
    draft = plan(
        _model(),
        [
            _holding(
                "A", "80000", quantity="800", market_price="100", average_price=None
            ),
            _holding(
                "B", "20000", quantity="200", market_price="100", average_price="80"
            ),
        ],
        economics_policy=_policy(),
        lot_sizes={"A": 10, "B": 10},
    )

    sell = next(action for action in draft.actions if action.side == "SELL")
    assert draft.actionable is False
    assert sell.economics.status is EconomicsStatus.UNKNOWN
    assert sell.economics.estimated_tax_rub is None
    assert "cost_basis" in sell.economics.blockers


def test_broker_final_cannot_make_unknown_or_unidentified_action_actionable():
    draft = Draft(
        actions=[
            Action(
                instrument_id="A",
                side="SELL",
                target_weight=Decimal("0.5"),
                actual_weight=Decimal("0.8"),
                amount_rub=Decimal("30000"),
                reason="fixture",
                economics=ActionEconomics(blockers=("fee_policy",)),
            ),
            Action(
                instrument_id="B",
                side="BUY",
                target_weight=Decimal("0.5"),
                actual_weight=Decimal("0.2"),
                amount_rub=Decimal("30000"),
                reason="fixture",
                economics=ActionEconomics(
                    status=EconomicsStatus.ESTIMATED,
                    actionable=True,
                ),
            ),
        ]
    )

    updated = _with_broker_finals(
        draft,
        {
            "A": BrokerFinalEconomics(
                costs_rub=Decimal("45"),
                tax_rub=Decimal("1500"),
                reference="broker:A:2026-08-21",
                executed_quantity=Decimal("300"),
                executed_notional_rub=Decimal("30000"),
            ),
            "B": BrokerFinalEconomics(
                costs_rub=Decimal("45"),
                tax_rub=Decimal("0"),
                reference="broker:B:2026-08-21",
                executed_quantity=Decimal("300"),
                executed_notional_rub=Decimal("30000"),
            ),
        },
    )

    unknown, unidentified = (action.economics for action in updated.actions)
    assert unknown.status is EconomicsStatus.UNKNOWN
    assert unknown.actionable is False
    assert unknown.blockers == ("fee_policy",)
    assert unknown.broker_final_costs_rub is None
    assert unknown.broker_final_tax_rub is None
    assert unidentified.status is EconomicsStatus.ESTIMATED
    assert unidentified.actionable is True
    assert unidentified.order_quantity is None
    assert unidentified.order_notional_rub is None
    assert unidentified.broker_final_costs_rub is None
    assert unidentified.broker_final_tax_rub is None


def test_sell_estimate_never_exceeds_authoritative_holding_quantity():
    draft = plan(
        _model(),
        [
            _holding(
                "A", "80000", quantity="100", market_price="100", average_price="60"
            ),
            _holding(
                "B", "20000", quantity="200", market_price="100", average_price="80"
            ),
        ],
        economics_policy=_policy(),
        lot_sizes={"A": 10, "B": 10},
    )

    sell = next(action for action in draft.actions if action.side == "SELL")
    assert draft.actionable is False
    assert sell.economics.status is EconomicsStatus.UNKNOWN
    assert sell.economics.order_quantity is None
    assert "holding_quantity" in sell.economics.blockers
