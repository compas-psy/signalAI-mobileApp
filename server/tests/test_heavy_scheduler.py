import app.scheduler.heavy as heavy


def test_heavy_entrypoint_accepts_actual_dependency_order(monkeypatch) -> None:
    monkeypatch.setattr(heavy, "get_session_factory", lambda: None)
    monkeypatch.setattr(heavy, "run_forever", lambda *args, **kwargs: None)
    monkeypatch.setattr(heavy.signal, "signal", lambda *args, **kwargs: None)

    assert heavy.main() == 0
