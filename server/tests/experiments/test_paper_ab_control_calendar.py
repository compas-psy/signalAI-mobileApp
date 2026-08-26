from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.experiments import paper_ab_runtime_v1 as runtime
from app.models import Instrument
from app.models.enums import AssetClass, Venue


AT = datetime(2026, 8, 25, 11, 0, tzinfo=UTC)


def _instrument(session) -> Instrument:
    row = Instrument(
        instrument_id="CRYPTO:PERP:BTCUSDT",
        symbol="BTCUSDT",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        currency="USDT",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("5"),
        contract_multiplier=Decimal("1"),
        is_tradable=True,
        in_universe=True,
        metadata_json={},
    )
    session.add(row)
    session.flush()
    return row


def test_default_control_passes_owned_calendar_to_canonical_scanner(session, monkeypatch) -> None:
    instrument = _instrument(session)
    owned_calendar = object()
    observed: dict[str, object | None] = {}

    monkeypatch.setattr(
        runtime,
        "load_owned_calendar",
        lambda *, now: owned_calendar,
        raising=False,
    )

    def fake_scan_instrument(
        _session,
        _instrument,
        *,
        cfg,
        risk_state,
        now,
        event_calendar=None,
    ):
        observed["event_calendar"] = event_calendar
        return None, [], []

    monkeypatch.setattr(runtime, "scan_instrument", fake_scan_instrument)

    decision = runtime._default_control_provider(runtime.get_config())(
        session,
        instrument,
        AT,
    )

    assert decision.signal_emitted is False
    assert observed["event_calendar"] is owned_calendar
