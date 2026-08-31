from __future__ import annotations

from app.models.enums import Strategy
from app.pipeline.scan import AttributedRejection
from app.scheduler.runner import _terminal_from_rejections
from app.strategies.base import Check, Rejection


def test_setup_rejections_keep_instrument_and_first_failed_check() -> None:
    rejection = Rejection(
        strategy=Strategy.BREAKOUT_RETEST,
        checks=(
            Check(
                name="regime",
                label="Режим",
                passed=True,
                detail="допустим",
            ),
            Check(
                name="breakout",
                label="Пробой",
                passed=False,
                detail="нет подтвержденного пробоя диапазона",
            ),
        ),
        reason="Пробой: нет подтвержденного пробоя диапазона",
    )
    attributed = AttributedRejection(
        instrument_id="CRYPTO:PERP:BTCUSDT",
        rejection=rejection,
    )

    stage, code, detail = _terminal_from_rejections((attributed,))

    assert stage == "SETUP_REJECTED"
    assert code == "BREAKOUT_RETEST:BREAKOUT"
    assert detail == "Пробой: нет подтвержденного пробоя диапазона"
