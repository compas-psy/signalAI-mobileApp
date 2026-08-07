from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.research.legal_checks import (
    TRUDVSEM_CHECKED_AT,
    TRUDVSEM_REVIEW_DUE_AT,
    apply_verified_review,
)
from app.research.policy import CollectionDenied, authorize
from app.research.sources import sync_registry


def test_trudvsem_review_opens_only_until_versioned_due_date(session):
    sync_registry(session, now=TRUDVSEM_CHECKED_AT)

    # Registry entry predates the explicit review and is fail-closed first.
    with pytest.raises(CollectionDenied):
        authorize(session, "trudvsem", {"fetch"}, now=TRUDVSEM_CHECKED_AT)

    apply_verified_review(session, "trudvsem", now=TRUDVSEM_CHECKED_AT)
    permit = authorize(session, "trudvsem", {"fetch", "transform"}, now=TRUDVSEM_CHECKED_AT)
    assert permit.valid_at(TRUDVSEM_CHECKED_AT)

    with pytest.raises(CollectionDenied) as expired:
        authorize(
            session,
            "trudvsem",
            {"fetch"},
            now=TRUDVSEM_REVIEW_DUE_AT + timedelta(seconds=1),
        )
    assert "просрочена" in expired.value.reason
