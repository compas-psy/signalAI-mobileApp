"""Runtime resilience for market-universe refresh.

Two source-plumbing failures are handled here without weakening trading gates.

Crypto review: a temporary global Bybit snapshot outage must not turn every
previously admitted crypto instrument off for six hours. Last-known-good
admission is preserved only for that global measurement failure.

FORTS discovery: the first-pass bounding heuristic uses *current* VALTODAY to
limit expensive history ingestion. That is a useful budget guard for the long
tail, but it can permanently starve a core contract that happens to be quiet at
the six-hour universe refresh: no seed -> no history -> no historical-liquidity
memory -> no seed on the next pass. We therefore seed a small owner-facing core
set into the discovery pass (USD/RUB, CNY/RUB, gold, silver, Brent, gas).
Seeding does NOT mark a contract admitted and does NOT lower turnover, OI,
spread, history or expiry thresholds. ``sync_futures`` immediately resets the
seed to non-tradable and the ordinary ``review_universe`` must still admit it.
The practical change is that a core root is measured and gets an explicit
rejection reason instead of remaining silently unobserved.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_config
from ..models import DataQualityEvent, Instrument
from ..models.enums import AssetClass, QualityFlag, Venue
from . import moex, universe

# Quarterlies/cash-settled roots from the current MOEX FORTS code table.
# Identifiers are discovery routing, not trading thresholds. Both USD and RUB
# quoted precious-metal contracts are retained where MOEX has parallel roots.
CORE_FUTURES_ROOTS = frozenset({"SI", "CR", "GD", "GL", "SV", "S2", "BR", "NG"})

# Saved before install(), so wrappers never recurse.
_ORIGINAL_REVIEW = universe.review_universe
_ORIGINAL_SYNC_FUTURES = universe.sync_futures
_MISSING_MARKER = "инструмента нет в снимке рынка"


def review_universe_resilient(
    session: Session,
    *,
    now: datetime | None = None,
    fetch=None,
    cfg=None,
) -> universe.AdmissionReport:
    """Review с last-known-good семантикой при глобальной слепоте Bybit.

    Если снимок Bybit действительно доступен, поведение полностью совпадает
    с исходным review: все исторические, оборотные и OI-пороги применяются как
    раньше. Восстановление срабатывает только когда **все** crypto-инструменты
    одновременно получили один и тот же признак отсутствующего snapshot.
    Это отличает отказ источника от обычного выпадения отдельного тикера.
    """
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


def sync_futures_core_seeded(
    session: Session,
    *,
    now: datetime | None = None,
    fetch=None,
    cfg=None,
) -> list[Instrument]:
    """Guarantee measurement of core FORTS roots without bypassing admission.

    The original synchronizer bounds discovery using current-session turnover,
    historical admission, or an already-tradable row. A transient in-memory
    seed supplies only the third *discovery* condition. The original function
    then overwrites it to ``is_tradable=False`` before any commit and ordinary
    review decides admission from the real thresholds.
    """
    config = cfg or get_config()
    moment = now or datetime.now(UTC)
    kwargs = {"fetch": fetch} if fetch is not None else {}

    rows, _ = moex.forts_board(**kwargs)
    candidates = universe.futures_candidates(
        rows,
        moment.date(),
        min_days_to_expiry=int(config.get("universe.futures.min_days_to_expiry")),
        filter_by_snapshot=False,
    )
    core = [candidate for candidate in candidates if candidate.root.upper() in CORE_FUTURES_ROOTS]

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
            instrument_id = f"MOEX:FUT:{candidate.near.sec_id}"
            seed = existing.get(instrument_id)
            if seed is None:
                seed = Instrument(
                    instrument_id=instrument_id,
                    venue=Venue.MOEX,
                    asset_class=AssetClass.FUTURES,
                    symbol=candidate.near.sec_id,
                    currency="RUB",
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
                # Temporary in the current transaction. _ORIGINAL_SYNC_FUTURES
                # resets every kept candidate to False before review.
                seed.is_tradable = True
        session.flush()

    kept = _ORIGINAL_SYNC_FUTURES(session, now=moment, fetch=fetch, cfg=config)
    for instrument in kept:
        meta = dict(instrument.metadata_json or {})
        if meta.pop("core_discovery_seed", None) is not None:
            instrument.metadata_json = meta
    session.flush()
    return kept


def install() -> None:
    """Install source-resilience wrappers before scheduler captures callbacks."""
    if universe.review_universe is not review_universe_resilient:
        universe.review_universe = review_universe_resilient
    if universe.sync_futures is not sync_futures_core_seeded:
        universe.sync_futures = sync_futures_core_seeded


__all__ = [
    "CORE_FUTURES_ROOTS",
    "install",
    "review_universe_resilient",
    "sync_futures_core_seeded",
]
