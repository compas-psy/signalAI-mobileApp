from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.models.enums import Direction, PaperStatus
from app.risk.dynamic_exit import advance_v2

NOW = datetime(2026, 8, 17, 9, tzinfo=UTC)


def _bar(minutes: int, *, low: str, high: str, close: str):
    return SimpleNamespace(
        open_time=NOW + timedelta(minutes=minutes),
        low=Decimal(low),
        high=Decimal(high),
        close=Decimal(close),
    )


def _balanced_forts_trade():
    return SimpleNamespace(
        direction=Direction.LONG,
        status=PaperStatus.PENDING,
        entry=Decimal("100"),
        initial_stop=Decimal("95"),
        current_stop=Decimal("95"),
        tp_prices=["105", "110", "115"],
        tp_shares=["0.35", "0.30", "0.35"],
        tps_taken=0,
        realized_r=Decimal("0"),
        opened_at=NOW,
        breakeven_at=None,
        closed_at=None,
        outcome=None,
        close_reason=None,
    )


def test_forts_v2_runs_limit_partial_targets_runner_and_dynamic_stop():
    trade = _balanced_forts_trade()

    # Limit is first touched without reaching a target.
    events = advance_v2(
        trade,
        [_bar(10, low="99", high="101", close="100.5")],
        now=NOW + timedelta(minutes=10),
    )
    assert trade.status is PaperStatus.OPEN
    assert "вход исполнен" in events
    assert trade.tps_taken == 0

    # Two partial targets release risk and move the signed stop forward.
    advance_v2(
        trade,
        [
            _bar(20, low="100", high="105.5", close="104.5"),
            _bar(30, low="101", high="110.5", close="109"),
        ],
        now=NOW + timedelta(minutes=30),
    )
    assert trade.status is PaperStatus.OPEN
    assert trade.tps_taken == 2
    assert trade.current_stop > trade.entry

    # TP3 activates the runner: the final 35% must remain open, protected by
    # the monotonic ATR/MFE trail rather than being booked at a hard TP3.
    events = advance_v2(
        trade,
        [_bar(40, low="104", high="116", close="115")],
        now=NOW + timedelta(minutes=40),
    )
    assert trade.status is PaperStatus.OPEN
    assert trade.tps_taken == 3
    assert any("runner активирован" in event for event in events)
    runner_stop = Decimal(str(trade.current_stop))
    assert runner_stop > trade.entry

    # A later bar crosses the already-existing runner stop. This validates the
    # conservative chronology: trail is not applied retroactively inside the
    # TP3 bar, only on the following bar.
    events = advance_v2(
        trade,
        [
            _bar(
                50,
                low=str(runner_stop - Decimal("0.10")),
                high=str(runner_stop + Decimal("0.50")),
                close=str(runner_stop),
            )
        ],
        now=NOW + timedelta(minutes=50),
    )
    assert trade.status is PaperStatus.CLOSED
    assert trade.close_reason == "динамический стоп"
    assert "динамический стоп" in events
    assert trade.realized_r > 0
