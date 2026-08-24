from app.risk import engine_v2 as runtime


def test_risk_v2_scan_wrapper_forwards_event_calendar(monkeypatch):
    captured = {}
    calendar = object()
    session = object()
    instrument = object()
    config = object()
    risk_state = object()
    now = object()

    monkeypatch.setattr(
        runtime,
        "_edge_snapshot",
        lambda received_session, received_instrument: runtime.EdgeSnapshot(),
    )

    def fake_scan_instrument(
        received_session,
        received_instrument,
        *,
        cfg,
        risk_state,
        now,
        event_calendar=None,
    ):
        captured.update(
            session=received_session,
            instrument=received_instrument,
            cfg=cfg,
            risk_state=risk_state,
            now=now,
            event_calendar=event_calendar,
        )
        return None, [], []

    monkeypatch.setattr(runtime, "_ORIGINAL_SCAN_INSTRUMENT", fake_scan_instrument)

    result = runtime._scan_instrument_v2(
        session,
        instrument,
        cfg=config,
        risk_state=risk_state,
        now=now,
        event_calendar=calendar,
    )

    assert result == (None, [], [])
    assert captured == {
        "session": session,
        "instrument": instrument,
        "cfg": config,
        "risk_state": risk_state,
        "now": now,
        "event_calendar": calendar,
    }
