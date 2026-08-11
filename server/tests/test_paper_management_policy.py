from decimal import Decimal

from app.models import TradeIdea
from app.paper import tracker
from app.paper.management_policy import _configured_shares, install
from tests.conftest import idea_kwargs


def test_three_targets_use_signed_40_40_20_policy():
    assert _configured_shares(3) == [
        Decimal("0.40"),
        Decimal("0.40"),
        Decimal("0.20"),
    ]


def test_two_targets_renormalize_to_full_position():
    assert _configured_shares(2) == [Decimal("0.5"), Decimal("0.5")]


def test_new_server_paper_trade_snapshots_same_tp_shares_as_phone(
    instrument, now
):
    install()
    idea = TradeIdea(**idea_kwargs(instrument.instrument_id, now))

    trade = tracker._new_trade(idea, now=now)

    assert trade is not None
    assert [Decimal(value) for value in trade.tp_shares] == [
        Decimal("0.40"),
        Decimal("0.40"),
        Decimal("0.20"),
    ]
    assert sum((Decimal(value) for value in trade.tp_shares), Decimal(0)) == Decimal(1)
