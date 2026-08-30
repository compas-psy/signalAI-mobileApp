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


def test_multiple_setup_rejections_keep_aggregated_strategy_evidence() -> None:
    rows = (
        AttributedRejection(
            instrument_id="CRYPTO:PERP:BTCUSDT",
            rejection=Rejection(
                strategy=Strategy.TREND_PULLBACK,
                checks=(
                    Check(
                        name="pullback",
                        label="Откат",
                        passed=False,
                        detail="нет возврата в рабочую зону",
                    ),
                ),
                reason="Откат: нет возврата в рабочую зону",
            ),
        ),
        AttributedRejection(
            instrument_id="CRYPTO:PERP:BTCUSDT",
            rejection=Rejection(
                strategy=Strategy.WYCKOFF_REVERSAL,
                checks=(
                    Check(
                        name="pattern",
                        label="Паттерн",
                        passed=False,
                        detail="нет подтвержденного разворота",
                    ),
                ),
                reason="Паттерн: нет подтвержденного разворота",
            ),
        ),
    )

    stage, code, detail = _terminal_from_rejections(rows)

    assert stage == "SETUP_REJECTED"
    assert code == "TREND_PULLBACK:PULLBACK"
    assert "TREND_PULLBACK:PULLBACK" in detail
    assert "WYCKOFF_REVERSAL:PATTERN" in detail
