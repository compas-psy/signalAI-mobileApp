from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market.candles import Candle
from app.market.derivatives_features import derivatives_change_z


def _bars(*, count: int, spike: bool = False, with_oi: bool = True) -> list[Candle]:
    start = datetime(2026, 8, 20, tzinfo=UTC)
    bars: list[Candle] = []
    price = Decimal("100")
    oi = Decimal("1000")
    for index in range(count):
        # Stable relative changes form a zero-variance baseline; the final bar
        # can then create an unambiguous positive price/OI impulse.
        price_factor = Decimal("1.001")
        oi_factor = Decimal("1.001")
        if spike and index == count - 1:
            price_factor = Decimal("1.03")
            oi_factor = Decimal("1.04")
        next_price = price * price_factor
        next_oi = oi * oi_factor
        bars.append(
            Candle(
                open_time=start + timedelta(hours=index),
                open=price,
                high=max(price, next_price) * Decimal("1.001"),
                low=min(price, next_price) * Decimal("0.999"),
                close=next_price,
                open_interest=next_oi if with_oi else None,
                source="bybit",
            )
        )
        price = next_price
        oi = next_oi
    return bars


def test_derivatives_change_z_returns_real_price_and_oi_signals() -> None:
    snapshot = derivatives_change_z(_bars(count=30, spike=True), lookback=24, min_samples=12)

    assert snapshot.reason is None
    assert snapshot.samples == 24
    assert snapshot.price_change_z is not None
    assert snapshot.oi_change_z is not None
    assert snapshot.price_change_z > 2.0
    assert snapshot.oi_change_z > 2.0


def test_derivatives_change_z_is_explicit_when_oi_history_is_missing() -> None:
    snapshot = derivatives_change_z(_bars(count=30, with_oi=False), lookback=24, min_samples=12)

    assert snapshot.price_change_z is None
    assert snapshot.oi_change_z is None
    assert snapshot.reason == "OI_HISTORY_INSUFFICIENT"


def test_derivatives_change_z_does_not_fake_signal_on_flat_history() -> None:
    bars = _bars(count=30)
    snapshot = derivatives_change_z(bars, lookback=24, min_samples=12)

    assert snapshot.price_change_z == 0.0
    assert snapshot.oi_change_z == 0.0
    assert snapshot.reason is None
