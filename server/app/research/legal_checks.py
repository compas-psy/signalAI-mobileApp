"""Versioned legal reviews for sources whose registry entry predates review.

The source registry remains fail-closed. This module records an explicit review
performed on 2026-08-07 instead of silently turning an `approved` label into a
permit. The review expires; after that date `authorize()` blocks the source
again until code is updated with a new verified review.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ResearchSource
from ..models.enums import LicenseStatus

TRUDVSEM_CHECKED_AT = datetime(2026, 8, 7, tzinfo=UTC)
TRUDVSEM_REVIEW_DUE_AT = datetime(2026, 11, 7, tzinfo=UTC)
TRUDVSEM_TERMS_URL = "https://trudvsem.ru/opendata"
TRUDVSEM_BASE_URL = "https://opendata.trudvsem.ru/api/v1/vacancies"


def apply_verified_review(
    session: Session,
    source_id: str,
    *,
    now: datetime | None = None,
) -> None:
    """Apply only a review that is explicitly versioned in this module.

    This does not grant a permit. It fills the legal-review fields and leaves
    the final decision to `policy.authorize`, which still checks license,
    expiry, enabled state and requested operations.
    """
    moment = now or datetime.now(UTC)
    row = session.execute(
        select(ResearchSource).where(ResearchSource.source_id == source_id)
    ).scalar_one_or_none()
    if row is None:
        return

    if source_id != "trudvsem":
        return

    row.base_url = TRUDVSEM_BASE_URL
    row.terms_url = TRUDVSEM_TERMS_URL
    row.terms_checked_at = TRUDVSEM_CHECKED_AT
    row.terms_review_due_at = TRUDVSEM_REVIEW_DUE_AT
    # `sync_registry` disables an approved row whose terms had not been
    # reviewed yet. Re-enable only while this explicit review is current;
    # `authorize()` independently checks the same expiry and operations.
    row.enabled = (
        row.license_status is LicenseStatus.APPROVED
        and moment < TRUDVSEM_REVIEW_DUE_AT
    )
    session.flush()


__all__ = [
    "TRUDVSEM_BASE_URL",
    "TRUDVSEM_CHECKED_AT",
    "TRUDVSEM_REVIEW_DUE_AT",
    "TRUDVSEM_TERMS_URL",
    "apply_verified_review",
]
