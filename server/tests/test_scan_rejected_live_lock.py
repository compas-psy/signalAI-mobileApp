from app.models.enums import QualityStatus
from app.pipeline import scan as scan_module


def test_only_user_quality_ideas_are_persistable_live_candidates():
    predicate = getattr(scan_module, "_is_persistable_live_quality", None)
    assert predicate is not None, "scan must explicitly separate user ideas from rejected admissions"

    assert predicate(QualityStatus.ACTIVE) is True
    assert predicate(QualityStatus.WATCH) is True
    assert predicate(QualityStatus.REJECTED) is False
