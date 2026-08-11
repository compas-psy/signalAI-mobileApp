from datetime import UTC, datetime
from decimal import Decimal
from inspect import getsource

from sqlalchemy import select

from app.config import get_config
from app.market.http import FetchError
from app.market.review_resilience import review_universe_resilient
from app.models import DataQualityEvent, Instrument
from app.models.enums import (
    AssetClass,
    LiquidityRegime,
    QualityStatus,
    Venue,
)
from app.pipeline import risk_equity_runtime
from app.scoring.selection import RankedIdea, select_daily


NOW = datetime(2026, 8, 11, 6, 0, tzinfo=UTC)


def _ranked(
    idea_id: str,
    instrument_id: str,
    *,
    quality: QualityStatus = QualityStatus.ACTIVE,
    score: str = "75",
    cluster: str | None = None,
) -> RankedIdea:
    return RankedIdea(
        idea_id=idea_id,
        instrument_id=instrument_id,
        cluster=cluster,
        quality_status=quality,
        expected_r=Decimal("0.40"),
        probability=Decimal("0.58"),
        confidence=Decimal("0.60"),
        score=Decimal(score),
        liquidity=LiquidityRegime.GOOD,
    )


def test_scheduler_risk_equity_is_explicit_owner_approved_300k():
    cfg = get_config()
    assert cfg.decimal("risk.equity_rub") == Decimal("300000")
    source = getsource(risk_equity_runtime.scan_with_configured_equity)
    assert 'config.decimal("risk.equity_rub")' in source
    assert "100_000" not in source


def test_forts_and_crypto_do_not_compete_for_same_presentation_slot():
    selection = select_daily(
        [
            _ranked("crypto", "CRYPTO:PERP:BTCUSDT", cluster="crypto"),
            _ranked("forts", "MOEX:FUT:PXU6", cluster="ru_equity"),
        ],
        max_cards=1,
    )

    assert {row.idea.idea_id for row in selection.trade_now} == {"crypto", "forts"}
    assert selection.dropped == ()


def test_same_venue_still_obeys_its_presentation_cap():
    selection = select_daily(
        [
            _ranked("btc", "CRYPTO:PERP:BTCUSDT", score="80"),
            _ranked("eth", "CRYPTO:PERP:ETHUSDT", score="70"),
        ],
        max_cards=1,
    )

    assert len(selection.trade_now) == 1
    assert len(selection.dropped) == 1
    assert "lane crypto" in selection.dropped[0].dropped_reason


def test_watch_slots_are_filled_independently_per_venue():
    selection = select_daily(
        [
            _ranked(
                "crypto-watch",
                "CRYPTO:PERP:DOGEUSDT",
                quality=QualityStatus.WATCH,
            ),
            _ranked(
                "forts-watch",
                "MOEX:FUT:PXU6",
                quality=QualityStatus.WATCH,
            ),
        ],
        max_cards=1,
    )

    assert {row.idea.idea_id for row in selection.wait_for_trigger} == {
        "crypto-watch",
        "forts-watch",
    }


def test_global_bybit_snapshot_failure_preserves_last_good_admission(session):
    item = Instrument(
        instrument_id="CRYPTO:PERP:BTCUSDT",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        symbol="BTCUSDT",
        title="BTC/USDT бессрочный",
        currency="USDT",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        in_universe=True,
        is_tradable=True,
        universe_note="допущен §5: last-known-good",
        metadata_json={
            "admission": {
                "median_daily_notional_usdt": "9000000000",
                "history_days": "365",
                "open_interest": "1000000",
            }
        },
    )
    session.add(item)
    session.flush()

    def broken_fetch(url):
        raise FetchError("timeout", url, "synthetic Bybit outage")

    report = review_universe_resilient(session, now=NOW, fetch=broken_fetch)
    session.refresh(item)

    assert item.is_tradable is True
    assert item.universe_note == "допущен §5: last-known-good"
    assert report.verdicts[item.instrument_id].admitted is True
    assert "последний успешный допуск" in report.verdicts[item.instrument_id].reasons[0]
    assert item.metadata_json["admission"]["history_days"] == "365"

    events = session.execute(
        select(DataQualityEvent).where(DataQualityEvent.source == "bybit")
    ).scalars().all()
    assert any("last-known-good" in event.detail for event in events)
