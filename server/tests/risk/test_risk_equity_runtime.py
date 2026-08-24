from app.pipeline import risk_equity_runtime as runtime


def test_scan_with_configured_equity_forwards_event_calendar(monkeypatch):
    captured = {}
    calendar = object()
    config = object()
    risk_state = object()
    session = object()

    def fake_scan(
        received_session,
        *,
        cfg=None,
        risk_state=None,
        now=None,
        event_calendar=None,
    ):
        captured.update(
            session=received_session,
            cfg=cfg,
            risk_state=risk_state,
            now=now,
            event_calendar=event_calendar,
        )
        return "ok"

    monkeypatch.setattr(runtime, "_ORIGINAL_SCAN", fake_scan)

    result = runtime.scan_with_configured_equity(
        session,
        cfg=config,
        risk_state=risk_state,
        event_calendar=calendar,
    )

    assert result == "ok"
    assert captured == {
        "session": session,
        "cfg": config,
        "risk_state": risk_state,
        "now": None,
        "event_calendar": calendar,
    }
