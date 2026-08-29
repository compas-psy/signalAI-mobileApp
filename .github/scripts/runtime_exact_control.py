from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import aliased

from app.db import get_session_factory
from app.models import Instrument, PaperAbDecision, PaperAbOutcome, TradeIdea

VENUES = {
    "FORTS": {"MOEX", "FORTS"},
    "BYBIT": {"CRYPTO", "BYBIT"},
}
CANDIDATES = ("momentum_v2", "mean_reversion_v1", "crypto_carry_v1", "breakout_v2")
REQUIRED = 40


def _exact_pairs(session, *, aliases: set[str], candidate_version: str, start: datetime):
    control_d = aliased(PaperAbDecision)
    candidate_d = aliased(PaperAbDecision)
    control_o = aliased(PaperAbOutcome)
    candidate_o = aliased(PaperAbOutcome)

    rows = session.execute(
        select(
            control_d.pair_key,
            control_o.net_r,
            candidate_o.net_r,
        )
        .join(
            candidate_d,
            and_(
                candidate_d.pair_key == control_d.pair_key,
                candidate_d.arm_role == "CANDIDATE",
                candidate_d.strategy_version == candidate_version,
                candidate_d.venue.in_(aliases),
                candidate_d.decision_at >= start,
            ),
        )
        .join(control_o, control_o.decision_id == control_d.id)
        .join(candidate_o, candidate_o.decision_id == candidate_d.id)
        .where(
            control_d.arm_role == "CONTROL",
            control_d.venue.in_(aliases),
            control_d.decision_at >= start,
            control_o.evidence_status == "EVALUATED",
            candidate_o.evidence_status == "EVALUATED",
            control_o.net_r.is_not(None),
            candidate_o.net_r.is_not(None),
        )
        .order_by(control_d.pair_key)
    ).all()

    grouped: dict[str, list[tuple[object, object]]] = defaultdict(list)
    for pair_key, control_r, candidate_r in rows:
        grouped[str(pair_key)].append((control_r, candidate_r))

    ambiguous = {key: values for key, values in grouped.items() if len(values) != 1}
    comparable = {key: values[0] for key, values in grouped.items() if len(values) == 1}
    return comparable, ambiguous


def main() -> None:
    now = datetime.now(UTC)
    start = now - timedelta(days=7)
    session = get_session_factory()()
    try:
        print(f"EXACT_CONTROL window={start.isoformat()}..{now.isoformat()} required={REQUIRED}")
        for venue, aliases in VENUES.items():
            for candidate in CANDIDATES:
                comparable, ambiguous = _exact_pairs(
                    session,
                    aliases=aliases,
                    candidate_version=candidate,
                    start=start,
                )
                n = len(comparable)
                remaining = max(0, REQUIRED - n)
                print(
                    "EXACT_N40 "
                    f"venue={venue} candidate={candidate} n={n} required={REQUIRED} "
                    f"remaining={remaining} adequate={str(n >= REQUIRED).lower()} "
                    f"ambiguous={len(ambiguous)}"
                )

        target_ids = ("CRYPTO:PERP:ETHUSDT", "CRYPTO:PERP:XAUTUSDT")
        target_rows = list(
            session.execute(
                select(TradeIdea)
                .where(
                    TradeIdea.instrument_id.in_(target_ids),
                    TradeIdea.signal_time >= now - timedelta(hours=72),
                )
                .order_by(TradeIdea.signal_time)
            ).scalars()
        )
        if not target_rows:
            print("LIVE_TARGET_IDEAS none")
        for idea in target_rows:
            print(
                "LIVE_TARGET_IDEA "
                f"instrument={idea.instrument_id} signal_time={idea.signal_time.isoformat()} "
                f"quality={idea.quality_status} status={idea.status} "
                f"presented={str(bool(idea.was_presented)).lower()} "
                f"version={idea.strategy_version} role={idea.strategy_role}"
            )

        # A compact truth check: how many persisted/presented crypto ideas exist
        # in the same 72h window, independent of replay diagnostics.
        instrument = aliased(Instrument)
        crypto_rows = session.execute(
            select(TradeIdea.was_presented, func.count(TradeIdea.id))
            .join(instrument, instrument.instrument_id == TradeIdea.instrument_id)
            .where(
                TradeIdea.signal_time >= now - timedelta(hours=72),
                instrument.instrument_id.like("CRYPTO:%"),
            )
            .group_by(TradeIdea.was_presented)
        ).all()
        print(f"LIVE_CRYPTO_72H {dict(Counter({str(bool(k)).lower(): int(v) for k, v in crypto_rows}))}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
