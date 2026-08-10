from datetime import UTC, datetime
from types import SimpleNamespace

from app.portfolio import equity_ranking_refresh as refresh


def test_refresh_keeps_previous_snapshot_until_new_equity_d1(monkeypatch):
    previous = SimpleNamespace(data_as_of=datetime(2026, 8, 8, tzinfo=UTC))
    monkeypatch.setattr(refresh, "latest_equity_d1", lambda _session: datetime(2026, 8, 8, tzinfo=UTC))
    monkeypatch.setattr(refresh, "latest_snapshot", lambda _session: previous)

    called = {"build": 0}

    def build(_session, *, force=False):
        called["build"] += 1
        return SimpleNamespace()

    monkeypatch.setattr(refresh, "build_daily_ranking", build)

    detail = refresh.refresh(object())

    assert called["build"] == 0
    assert "новых D1" in detail


def test_refresh_rebuilds_same_day_snapshot_when_fresh_d1_arrives(monkeypatch):
    previous = SimpleNamespace(data_as_of=datetime(2026, 8, 8, tzinfo=UTC))
    monkeypatch.setattr(refresh, "latest_equity_d1", lambda _session: datetime(2026, 8, 10, tzinfo=UTC))
    monkeypatch.setattr(refresh, "latest_snapshot", lambda _session: previous)

    seen = {"force": None}
    snapshot = SimpleNamespace()

    def build(_session, *, force=False):
        seen["force"] = force
        return snapshot

    monkeypatch.setattr(refresh, "build_daily_ranking", build)
    monkeypatch.setattr(refresh, "summary", lambda value: "updated" if value is snapshot else "wrong")

    assert refresh.refresh(object()) == "updated"
    assert seen["force"] is True


def test_refresh_builds_initial_snapshot_when_d1_exists(monkeypatch):
    monkeypatch.setattr(refresh, "latest_equity_d1", lambda _session: datetime(2026, 8, 10, tzinfo=UTC))
    monkeypatch.setattr(refresh, "latest_snapshot", lambda _session: None)

    seen = {"force": None}
    snapshot = SimpleNamespace()

    def build(_session, *, force=False):
        seen["force"] = force
        return snapshot

    monkeypatch.setattr(refresh, "build_daily_ranking", build)
    monkeypatch.setattr(refresh, "summary", lambda _value: "initial")

    assert refresh.refresh(object()) == "initial"
    assert seen["force"] is False
