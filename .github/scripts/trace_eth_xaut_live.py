"""Read-only production trace for the ETH/XAUT replay discrepancy."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.db import get_session_factory
from app.models import IdeaEvent, Instrument, TradeIdea

TARGETS = {"CRYPTO:PERP:ETHUSDT", "CRYPTO:PERP:XAUTUSDT"}
START = datetime(2026, 8, 27, 0, 0, tzinfo=UTC)


def main() -> None:
    session = get_session_factory()()
    try:
        rows = session.execute(
            select(TradeIdea, Instrument)
            .join(Instrument, Instrument.instrument_id == TradeIdea.instrument_id)
            .where(
                TradeIdea.instrument_id.in_(TARGETS),
                TradeIdea.signal_time >= START,
            )
            .order_by(TradeIdea.signal_time, TradeIdea.created_at)
        ).all()
        print(f"IDEA_TRACE rows={len(rows)} since={START.isoformat()}")
        for idea, instrument in rows:
            print(
                "IDEA "
                f"instrument={idea.instrument_id} symbol={instrument.symbol} "
                f"strategy={idea.strategy.value} direction={idea.direction.value} "
                f"status={idea.status.value} quality={idea.quality_status.value} "
                f"signal={idea.signal_time.isoformat()} created={idea.created_at.isoformat()} "
                f"expires={idea.expires_at.isoformat()} presented={idea.was_presented} "
                f"rank={idea.presentation_rank} p={idea.p_tp1_before_sl} "
                f"ev={idea.expected_r} rr={idea.rr_tp2} confidence={idea.confidence} "
                f"version={idea.strategy_version} role={idea.strategy_role} id={idea.id}"
            )
            events = session.execute(
                select(IdeaEvent)
                .where(IdeaEvent.idea_id == idea.id)
                .order_by(IdeaEvent.sequence)
            ).scalars().all()
            for event in events:
                print(
                    "  EVENT "
                    f"seq={event.sequence} at={event.occurred_at.isoformat()} "
                    f"{event.old_status}->{event.new_status} "
                    f"reason={event.reason_code} detail={event.reason_detail[:160]}"
                )
    finally:
        session.close()


if __name__ == "__main__":
    main()
