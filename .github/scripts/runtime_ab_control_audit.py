from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median

from sqlalchemy import select

from app.db import get_session_factory
from app.models import Bar, Instrument, PaperAbDecision, ShadowObservation, TradeIdea
from app.models.enums import Timeframe


def day(value: datetime) -> str:
    return value.astimezone(UTC).date().isoformat()


def main() -> None:
    now = datetime.now(UTC)
    start_30 = now - timedelta(days=30)
    start_7 = now - timedelta(days=7)
    session = get_session_factory()()
    try:
        ideas = list(
            session.execute(
                select(TradeIdea)
                .where(TradeIdea.signal_time >= start_30)
                .order_by(TradeIdea.signal_time)
            ).scalars()
        )
        print(
            "AB_AUDIT_IDEAS "
            f"window={start_30.isoformat()}..{now.isoformat()} total={len(ideas)}"
        )
        by_day: dict[str, Counter] = defaultdict(Counter)
        by_version = Counter()
        by_role = Counter()
        presented = Counter()
        for idea in ideas:
            key = day(idea.signal_time)
            by_day[key]["total"] += 1
            by_day[key][f"version:{idea.strategy_version}"] += 1
            by_day[key][f"quality:{idea.quality_status}"] += 1
            by_day[key][f"status:{idea.status}"] += 1
            by_version[idea.strategy_version] += 1
            by_role[idea.strategy_role] += 1
            presented["presented" if idea.was_presented else "not_presented"] += 1
        print(f"AB_AUDIT_IDEA_VERSIONS {dict(by_version)}")
        print(f"AB_AUDIT_IDEA_ROLES {dict(by_role)}")
        print(f"AB_AUDIT_IDEA_PRESENTATION {dict(presented)}")
        for key in sorted(by_day):
            print(f"AB_AUDIT_IDEA_DAY {key} {dict(by_day[key])}")

        shadows = list(
            session.execute(
                select(ShadowObservation)
                .where(ShadowObservation.evaluated_at >= start_7)
                .order_by(ShadowObservation.evaluated_at)
            ).scalars()
        )
        shadow_stats = Counter()
        shadow_signal_by_day: dict[str, Counter] = defaultdict(Counter)
        for row in shadows:
            shadow_stats[(row.strategy_version, row.evidence_status, row.signal_emitted)] += 1
            if row.signal_emitted:
                shadow_signal_by_day[day(row.evaluated_at)][row.strategy_version] += 1
        print(
            "AB_AUDIT_SHADOW "
            f"window={start_7.isoformat()}..{now.isoformat()} rows={len(shadows)}"
        )
        for key, count in sorted(shadow_stats.items()):
            version, evidence, emitted = key
            print(
                "AB_AUDIT_SHADOW_STAT "
                f"version={version} evidence={evidence} emitted={emitted} count={count}"
            )
        for key in sorted(shadow_signal_by_day):
            print(f"AB_AUDIT_SHADOW_SIGNAL_DAY {key} {dict(shadow_signal_by_day[key])}")

        decisions = list(
            session.execute(
                select(PaperAbDecision)
                .where(PaperAbDecision.decision_at >= start_7)
                .order_by(PaperAbDecision.decision_at)
            ).scalars()
        )
        decision_stats = Counter(
            (row.arm_role, row.strategy_version, row.signal_emitted) for row in decisions
        )
        print(
            "AB_AUDIT_PAPER_AB "
            f"window={start_7.isoformat()}..{now.isoformat()} rows={len(decisions)}"
        )
        for key, count in sorted(decision_stats.items()):
            role, version, emitted = key
            print(
                "AB_AUDIT_PAPER_AB_STAT "
                f"role={role} version={version} emitted={emitted} count={count}"
            )

        forts = list(
            session.execute(
                select(Instrument)
                .where(
                    Instrument.in_universe.is_(True),
                    Instrument.is_tradable.is_(True),
                    Instrument.instrument_id.like("MOEX:FUT:%"),
                )
                .order_by(Instrument.instrument_id)
            ).scalars()
        )
        counts: list[int] = []
        shallow: list[tuple[str, int]] = []
        for instrument in forts:
            count = len(
                list(
                    session.execute(
                        select(Bar.open_time).where(
                            Bar.instrument_id == instrument.instrument_id,
                            Bar.timeframe == Timeframe.D1,
                            Bar.is_closed.is_(True),
                        )
                    ).scalars()
                )
            )
            counts.append(count)
            if count < 60:
                shallow.append((instrument.instrument_id, count))
        if counts:
            print(
                "AB_AUDIT_FORTS_D1 "
                f"tradable={len(counts)} min={min(counts)} median={median(counts)} "
                f"max={max(counts)} below60={len(shallow)}"
            )
        else:
            print("AB_AUDIT_FORTS_D1 tradable=0")
        for instrument_id, count in shallow:
            print(f"AB_AUDIT_FORTS_SHALLOW instrument={instrument_id} d1={count}")
    finally:
        session.close()


if __name__ == "__main__":
    main()