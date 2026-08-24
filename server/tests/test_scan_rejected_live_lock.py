from app.models.enums import QualityStatus
from app.pipeline import scan as scan_module


def test_only_user_quality_ideas_are_persistable_live_candidates():
    predicate = getattr(scan_module, "_is_persistable_live_quality", None)
    assert predicate is not None, "scan must explicitly separate user ideas from rejected admissions"

    assert predicate(QualityStatus.ACTIVE) is True
    assert predicate(QualityStatus.WATCH) is True
    assert predicate(QualityStatus.REJECTED) is False


def test_existing_rejected_trade_idea_does_not_remain_a_live_lock():
    class _Rows:
        def all(self):
            return [
                ("CRYPTO:BTCUSDT", "rejected-id", QualityStatus.REJECTED),
                ("CRYPTO:ETHUSDT", "watch-id", QualityStatus.WATCH),
            ]

    class _Session:
        def execute(self, _statement):
            return _Rows()

    live = scan_module._live_idea_ids(_Session())

    assert "CRYPTO:BTCUSDT" not in live
    assert live["CRYPTO:ETHUSDT"] == "watch-id"
