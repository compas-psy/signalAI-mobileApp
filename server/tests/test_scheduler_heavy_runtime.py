from __future__ import annotations

from app.scheduler import heavy


def test_heavy_main_accepts_p0_heavy_lane_order(monkeypatch):
    """The production heavy entrypoint must accept the scheduler's P0 job order."""

    monkeypatch.setattr(heavy.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(heavy, "get_session_factory", lambda: object())
    monkeypatch.setattr(heavy, "run_forever", lambda *_args, **_kwargs: None)

    assert heavy.main() == 0
