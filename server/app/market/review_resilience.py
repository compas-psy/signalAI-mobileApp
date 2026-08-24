"""Runtime resilience for market-universe refresh.

Source refresh and admission review are separate scheduler stages. A metadata
refresh must not erase a previously reviewed admission verdict before review
has a chance to run: if review later fails, the last-known-good lane would
otherwise disappear from scanning for hours.

This layer preserves only an already-true ``is_tradable`` value for instruments
that are still present in the fresh exchange snapshot. New instruments remain
blocked until their first review, and missing/expired instruments stay blocked.
The ordinary review still runs afterwards and may revoke admission normally;
no turnover, OI, spread, history or expiry threshold is weakened.

It also keeps the existing protections for global Bybit review blindness and
for seeding a small owner-facing FORTS core into discovery so those contracts
can accumulate enough history to receive an explicit admission verdict.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_config
from ..models import DataQualityEvent, Instrument
from ..models.enums import AssetClass, QualityFlag, Venue
from . import moex, universe

CORE_FUTURES_ROOTS = frozenset({"SI", "CR", "GD", "GL", "SV", "S2", "BR", "NG"})

# Saved before install(), so wrappers never recurse.
_ORIGINAL_REVIEW = universe.review_universe
_ORIGINAL_SYNC_FUTURES = universe.sync_futures
_ORIGINAL_SYNC_CRYPTO = universe.sync_crypto
_MISSING_MARKER = "инструмента нет в снимке рынка"


def review_universe_resilient(
    session: Session,
    *,
    now: datetime | None = None,
    fetch=None,
    cfg=None,
) -> universe.AdmissionReport:
    """Preserve last-known-good crypto admission on global Bybit blindness."""
    crypto_instruments = list(
        session.execute(
            select(Instrument).where(
                Instrument.in_universe.is_(True),
                Instrument.venue == Venue.CRYPTO,
            )
        ).scalars()
    )
    before = {
        item.instrument_id: (
            item.is_tradable,
            item.universe_note,
            deepcopy(item.metadata_json or {}),
        )
        for item in crypto_instruments
    }

    report = _ORIGINAL_REVIEW(session, now=now, fetch=fetch, cfg=cfg)
    if not crypto_instruments:
        return report

    verdicts = [
        report.verdicts.get(item.instrument_id) for item in crypto_instruments
    ]
    global_snapshot_loss = bool(verdicts) and all(
        verdict is not None
        and any(_MISSING_MARKER in reason for reason in verdict.reasons)
        for verdict in verdicts
    )
    if not global_snapshot_loss:
        return report

    for item in crypto_instruments:
        was_tradable, note, metadata = before[item.instrument_id]
        item.is_tradable = was_tradable
        item.universe_note = note
        item.metadata_json = metadata
        old_measured = dict((metadata.get("admission") or {}))
        report.verdicts[item.instrument_id] = universe.Verdict(
            admitted=was_tradable,
            reasons=[
                "Bybit snapshot недоступен/пуст — сохранён последний успешный допуск"
            ],
            measured=old_measured,
        )

    session.add(
        DataQualityEvent(
            source="bybit",
            flag=QualityFlag.SOURCE_CONFLICT.value,
            detail=(
                "review получил глобально пустой/недоступный Bybit snapshot; "
                "is_tradable crypto не изменён, сохранён last-known-good admission"
            ),
        )
    )
    report.admitted = sum(1 for verdict in report.verdicts.values() if verdict.admitted)
    session.flush()
    return report


def _last_tradable_by_id(session: Session, asset_class: AssetClass) -> dict[str, bool]:
    return {
        item.instrument_id: bool(item.is_tradable)
        for item in session.execute(
            select(Instrument).where(Instrument.asset_class == asset_class)
        ).scalars()
    }


def sync_futures_core_seeded(
    session: Session,
    *,
    now: datetime | None = None,
    fetch=None,
    cfg=None,
) -> list[Instrument]:
    """Seed core FORTS discovery and preserve retained reviewed admission."""
    config = cfg or get_config()
    moment = now or datetime.now(UTC)
    kwargs = {"fetch": fetch} if fetch is not None else {}

    # Capture reviewed state before temporary discovery seeds mutate anything.
    previous_tradable = _last_tradable_by_id(session, AssetClass.FUTURES)

    rows, _ = moex.forts_board(**kwargs)
    candidates = universe.futures_candidates(
        rows,
        moment.date(),
        min_days_to_expiry=int(config.get("universe.futures.min_days_to_expiry")),
        filter_by_snapshot=False,
    )
    core = [
        candidate
        for candidate in candidates
        if candidate.root.upper() in CORE_FUTURES_ROOTS
    ]

    if core:
        existing = {
            item.instrument_id: item
            for item in session.execute(
                select(Instrument).where(
                    Instrument.venue == Venue.MOEX,
                    Instrument.asset_class == AssetClass.FUTURES,
                )
            ).scalars()
        }
        for candidate in core:
            series = candidate.near
            instrument_id = f"MOEX:FUT:{series.sec_id}"
            seed = existing.get(instrument_id)
            if seed is None:
                seed = Instrument(
                    instrument_id=instrument_id,
                    venue=Venue.MOEX,
                    asset_class=AssetClass.FUTURES,
                    symbol=series.sec_id,
                    title=series.short_name,
                    currency="RUB",
                    tick_size=series.min_step or Decimal(1),
                    tick_value=series.step_price or Decimal(1),
                    expiry=series.last_trade_date,
                    next_contract=(
                        f"MOEX:FUT:{candidate.next_series.sec_id}"
                        if candidate.can_roll
                        else None
                    ),
                    correlation_cluster=universe.cluster_for(candidate.root),
                    in_universe=False,
                    is_tradable=True,
                    metadata_json={"root": candidate.root, "core_discovery_seed": True},
                )
                session.add(seed)
                existing[instrument_id] = seed
            else:
                meta = dict(seed.metadata_json or {})
                meta["root"] = candidate.root
                meta["core_discovery_seed"] = True
                seed.metadata_json = meta
                # Temporary discovery signal only. A new/unreviewed contract
                # is reset by the original synchronizer and stays blocked.
                seed.is_tradable = True
        session.flush()

    kept = _ORIGINAL_SYNC_FUTURES(session, now=moment, fetch=fetch, cfg=config)
    for instrument in kept:
        meta = dict(instrument.metadata_json or {})
        if meta.pop("core_discovery_seed", None) is not None:
            instrument.metadata_json = meta
        if previous_tradable.get(instrument.instrument_id, False):
            instrument.is_tradable = True
    session.flush()
    return kept


def sync_crypto_admission_continuous(
    session: Session,
    *,
    now: datetime | None = None,
    fetch=None,
    cfg=None,
) -> list[Instrument]:
    """Preserve reviewed admission only for crypto retained by fresh metadata."""
    previous_tradable = _last_tradable_by_id(session, AssetClass.CRYPTO_PERPETUAL)
    kept = _ORIGINAL_SYNC_CRYPTO(session, now=now, fetch=fetch, cfg=cfg)
    for instrument in kept:
        if previous_tradable.get(instrument.instrument_id, False):
            instrument.is_tradable = True
    session.flush()
    return kept


def install() -> None:
    """Install source-resilience wrappers before scheduler captures callbacks."""
    if universe.review_universe is not review_universe_resilient:
        universe.review_universe = review_universe_resilient
    if universe.sync_futures is not sync_futures_core_seeded:
        universe.sync_futures = sync_futures_core_seeded
    if universe.sync_crypto is not sync_crypto_admission_continuous:
        universe.sync_crypto = sync_crypto_admission_continuous


__all__ = [
    "CORE_FUTURES_ROOTS",
    "install",
    "review_universe_resilient",
    "sync_crypto_admission_continuous",
    "sync_futures_core_seeded",
]
