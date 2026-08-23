from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import app.market.economic_events as economic_events
import app.market.investments as investments
import app.market.universe as universe
import app.pipeline.scan as scan_module
import app.scheduler.runner as runner


def _job(scheduler, name):
    return next(job for job in scheduler.jobs if job.name == name)


class _RecoverableSession:
    """Minimal session double that models a failed DB transaction.

    A venue sync can poison the current transaction.  Entering a nested
    transaction/savepoint must contain that failure so the next venue starts
    from a usable session.
    """

    def __init__(self):
        self.poisoned = False
        self.nested_calls = 0

    @contextmanager
    def begin_nested(self):
        self.nested_calls += 1
        try:
            yield self
        except Exception:
            self.poisoned = False
            raise


def test_universe_forts_db_failure_does_not_poison_bybit(monkeypatch):
    session = _RecoverableSession()

    def broken_forts(current, **_kwargs):
        current.poisoned = True
        raise RuntimeError("FORTS transaction failed")

    def healthy_crypto(current, **_kwargs):
        assert current.poisoned is False, "FORTS failure leaked into Bybit lane"
        return [object()]

    monkeypatch.setattr(universe, "sync_futures", broken_forts)
    monkeypatch.setattr(universe, "sync_crypto", healthy_crypto)
    monkeypatch.setattr(investments, "sync_investments", lambda *_args, **_kwargs: [])

    scheduler = runner.build_default_scheduler()
    detail = _job(scheduler, "universe").run(session)

    assert "crypto: 1" in detail
    assert "MOEX — RuntimeError" in detail
    assert "crypto —" not in detail
    assert session.nested_calls == 3


def test_crypto_bar_wakes_scan_even_when_forts_has_same_latest_timestamp(monkeypatch):
    at = datetime(2026, 8, 23, 18, tzinfo=UTC)
    snapshots = iter(
        [
            {"forts": (at, 120), "crypto": (at, 80)},
            {"forts": (at, 120), "crypto": (at, 81)},
        ]
    )
    scan_calls = []

    # The scheduler must consume independent venue watermarks, not one global
    # max timestamp.  This attribute intentionally does not exist before the
    # production fix, making the regression test RED for the current code.
    monkeypatch.setattr(runner, "market_watermark_snapshot", lambda _session: next(snapshots))

    def fake_scan(_session, **_kwargs):
        scan_calls.append("scan")
        return SimpleNamespace(scanned=1, produced=0, skipped=[], rejections=[])

    monkeypatch.setattr(scan_module, "scan", fake_scan)
    monkeypatch.setattr(economic_events, "load_owned_calendar", lambda **_kwargs: None)

    scheduler = runner.build_default_scheduler()
    scan_job = _job(scheduler, "scan")
    session = object()

    first = scan_job.run(session)
    second = scan_job.run(session)

    assert "просмотрено 1" in first
    assert "просмотрено 1" in second
    assert len(scan_calls) == 2
