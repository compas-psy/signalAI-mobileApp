"""Three owner-facing investment portfolio choices.

The optimiser keeps package-size variants as an implementation detail. This
API exposes exactly one current slot per risk profile at the requested
horizon, including explicit missing or riskier-than-target states.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import PortfolioModel
from ...portfolio.headlines import select_headlines
from ...portfolio.lifecycle import model_diff, previous_generation
from ...schemas.portfolio import (
    HeadlinePortfolioOut,
    HeadlinePortfolioResponse,
    ModelChangesOut,
    PackageOut,
)
from .portfolio import _package_out

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _headline_package(session: Session, model: PortfolioModel) -> PackageOut:
    """Add evidence and generation diff needed by the owner-facing card."""
    package = _package_out(session, model)
    evidence_by_instrument = {
        weight.instrument_id: dict(weight.evidence_json or {}) for weight in model.weights
    }
    positions = [
        position.model_copy(
            update={"evidence": evidence_by_instrument.get(position.instrument_id, {})}
        )
        for position in package.positions
    ]
    diff = model_diff(previous_generation(session, model), model)
    changes = ModelChangesOut(
        added=list(diff.added),
        removed=list(diff.removed),
        weight_changed=list(diff.weight_changed),
    )
    return package.model_copy(update={"positions": positions, "changes": changes})


@router.get("/headlines", response_model=HeadlinePortfolioResponse)
def headlines(
    session: Session = Depends(get_db),
    horizon_years: int = Query(default=1, ge=1, le=30),
) -> HeadlinePortfolioResponse:
    """Return exactly three profile slots for one strict investment horizon."""
    now = datetime.now(UTC)
    models = list(
        session.execute(
            select(PortfolioModel).where(
                PortfolioModel.horizon_years == horizon_years,
            )
        ).scalars()
    )
    selected = select_headlines(models, horizon_years=horizon_years, as_of=now)
    return HeadlinePortfolioResponse(
        horizon_years=horizon_years,
        portfolios=[
            HeadlinePortfolioOut(
                profile=str(item.profile),
                label=item.label,
                status=str(item.status),
                reason=item.reason,
                package=(
                    _headline_package(session, item.model)
                    if item.model is not None
                    else None
                ),
            )
            for item in selected
        ],
    )
