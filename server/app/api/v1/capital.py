"""Authenticated owner capital snapshot.

Values are grouped by broker and currency.  No implicit FX conversion is made:
a RUB investment account and USD-valued Bybit equity remain separate facts.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from ...capital.runtime import state
from ...db import get_db
from ...schemas.common import ApiModel

router = APIRouter(tags=["capital"])


class CapitalAccountOut(ApiModel):
    external_id: str
    title: str
    currency: str
    equity: Decimal
    free_margin: Decimal | None = None


class CapitalSourceOut(ApiModel):
    source: str
    title: str
    status: str
    synced_at: datetime | None
    note: str = ""
    equity_by_currency: dict[str, Decimal] = Field(default_factory=dict)
    accounts: list[CapitalAccountOut] = Field(default_factory=list)


class CapitalOut(ApiModel):
    generated_at: datetime
    incomplete: bool
    sources: list[CapitalSourceOut]


@router.get("/capital", response_model=CapitalOut)
def capital_snapshot(db: Session = Depends(get_db)) -> CapitalOut:
    snapshot = state(db)
    return CapitalOut(
        generated_at=snapshot.generated_at,
        incomplete=snapshot.incomplete,
        sources=[
            CapitalSourceOut(
                source=source.source,
                title=source.title,
                status=source.status,
                synced_at=source.synced_at,
                note=source.note,
                equity_by_currency=source.equity_by_currency,
                accounts=[
                    CapitalAccountOut(
                        external_id=account.external_id,
                        title=account.title,
                        currency=account.currency,
                        equity=account.equity,
                        free_margin=account.free_margin,
                    )
                    for account in source.accounts
                ],
            )
            for source in snapshot.sources
        ],
    )
