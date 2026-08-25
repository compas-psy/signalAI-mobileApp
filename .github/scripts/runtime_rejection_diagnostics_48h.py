"""Read-only 48h rejection diagnostics for the currently admitted Crypto/FORTS universe.

This script is intentionally diagnostic-only. It replays persisted closed bars point-in-time,
rolls the SQLAlchemy session back, and never changes thresholds or execution state.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median

from sqlalchemy import select

from app.config import get_config
from app.db import get_session_factory
from app.market.candles import Candle
from app.market.economic_events import load_owned_calendar
from app.models import Bar, Instrument
from app.models.enums import AssetClass, Timeframe
from app.pipeline import scan as scan_module
from app.risk.sizing import RiskState

WINDOW_HOURS = 48
LOOKBACK_DAYS = 65


def _candle(row: Bar) -> Candle:
    return Candle(
        open_time=row.open_time,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume_units=row.volume_units,
        volume_notional=row.volume_notional,
        open_interest=row.open_interest,
        is_closed=True,
        source=row.source,
        quality_flags=tuple(row.quality_flags or ()),
    )


def _lane(instrument: Instrument) -> str:
    return "crypto" if instrument.asset_class == AssetClass.CRYPTO_PERPETUAL else "forts"


def _value(value) -> str:
    return str(value.value if hasattr(value, "value") else value)


def _summary(values: list[Decimal]) -> str:
    if not values:
        return "n=0"
    ordered = sorted(values)
    return (
        f"n={len(values)} min={ordered[0]} median={median(ordered)} "
        f"max={ordered[-1]}"
    )


def main() -> None:
    window_end = datetime.now(UTC)
    window_start = window_end - timedelta(hours=WINDOW_HOURS)
    history_floor = window_start - timedelta(days=LOOKBACK_DAYS)
    session = get_session_factory()()
    original_load_bars = scan_module._load_bars
    original_admit = scan_module.admit

    try:
        target_classes = (AssetClass.FUTURES, AssetClass.CRYPTO_PERPETUAL)
        instruments = list(
            session.execute(
                select(Instrument).where(
                    Instrument.asset_class.in_(target_classes),
                    Instrument.in_universe.is_(True),
                    Instrument.is_tradable.is_(True),
                )
            ).scalars()
        )
        instruments.sort(key=lambda item: item.instrument_id)
        target_ids = {item.instrument_id for item in instruments}

        rows = list(
            session.execute(
                select(Bar)
                .where(
                    Bar.instrument_id.in_(target_ids),
                    Bar.timeframe.in_((Timeframe.D1, Timeframe.H1)),
                    Bar.is_closed.is_(True),
                    Bar.open_time >= history_floor,
                    Bar.open_time <= window_end,
                )
                .order_by(Bar.instrument_id, Bar.timeframe, Bar.open_time)
            ).scalars()
        ) if target_ids else []
        cache: dict[tuple[str, Timeframe], list[Candle]] = defaultdict(list)
        for row in rows:
            cache[(row.instrument_id, row.timeframe)].append(_candle(row))

        holder = {"as_of": window_end, "instrument": None, "lane": None}

        def load_bars_asof(_session, instrument_id, timeframe, limit):
            as_of = holder["as_of"]
            close_delta = timedelta(days=1) if timeframe == Timeframe.D1 else timedelta(hours=1)
            cutoff = as_of - close_delta
            eligible = [
                item
                for item in cache.get((instrument_id, timeframe), ())
                if item.open_time <= cutoff
            ]
            return eligible[-limit:]

        admission_rows: list[dict] = []

        def diagnostic_admit(*args, **kwargs):
            verdict = original_admit(*args, **kwargs)
            if _value(verdict.status) == "REJECTED":
                event = kwargs.get("event_assessment")
                admission_rows.append(
                    {
                        "lane": holder["lane"],
                        "instrument": holder["instrument"],
                        "as_of": holder["as_of"],
                        "failed": tuple(g.name for g in verdict.failed),
                        "reason": verdict.reason,
                        "probability": Decimal(str(kwargs.get("probability"))),
                        "expected_r": Decimal(str(kwargs.get("expected_r"))),
                        "rr_tp2": Decimal(str(kwargs.get("rr_tp2"))),
                        "confidence": Decimal(str(kwargs.get("confidence"))),
                        "has_trigger": bool(kwargs.get("has_trigger")),
                        "event": None if event is None else event.as_json(),
                    }
                )
            return verdict

        scan_module._load_bars = load_bars_asof
        scan_module.admit = diagnostic_admit
        cfg = get_config()
        risk_state = RiskState(risk_equity=cfg.decimal("risk.equity_rub"))
        calendar = load_owned_calendar(now=window_end)
        watch_min = Decimal(str(cfg.get("ideas.watch_probability_min")))

        strategy_calls = Counter()
        failed_checks = Counter()
        first_failures = Counter()
        skip_counts = Counter()
        exceptions = Counter()
        scan_calls = 0

        print(
            "REJECTION_DIAG_WINDOW "
            f"utc={window_start.isoformat()}..{window_end.isoformat()} "
            f"current_admitted={len(instruments)} "
            f"by_lane={dict(sorted(Counter(_lane(i) for i in instruments).items()))}",
            flush=True,
        )

        for index, instrument in enumerate(instruments, start=1):
            lane = _lane(instrument)
            h1_opens = [
                item.open_time
                for item in cache.get((instrument.instrument_id, Timeframe.H1), ())
                if window_start - timedelta(hours=1) <= item.open_time <= window_end
            ]
            for open_time in h1_opens:
                as_of = open_time + timedelta(hours=1, seconds=1)
                if not (window_start <= as_of <= window_end):
                    continue
                holder.update(as_of=as_of, instrument=instrument.instrument_id, lane=lane)
                scan_calls += 1
                try:
                    _idea, skipped, rejections = scan_module.scan_instrument(
                        session,
                        instrument,
                        cfg=cfg,
                        risk_state=risk_state,
                        now=as_of,
                        event_calendar=calendar,
                    )
                except Exception as exc:
                    exceptions[(lane, type(exc).__name__, str(exc))] += 1
                    continue

                for item in skipped:
                    skip_counts[(lane, item.stage, item.reason)] += 1
                for rejection in rejections:
                    strategy = _value(rejection.strategy)
                    strategy_calls[(lane, strategy)] += 1
                    failed = rejection.failed
                    if failed:
                        first = failed[0]
                        first_failures[(lane, strategy, first.name, first.label, first.detail)] += 1
                    for check in failed:
                        failed_checks[(lane, strategy, check.name, check.label, check.detail)] += 1

            print(
                f"REJECTION_DIAG_PROGRESS {index}/{len(instruments)} lane={lane} "
                f"instrument={instrument.instrument_id} scan_calls={scan_calls} "
                f"admission_rejected={len(admission_rows)}",
                flush=True,
            )

        gate_counts = Counter(
            (row["lane"], gate) for row in admission_rows for gate in row["failed"]
        )
        probabilities = [row["probability"] for row in admission_rows]
        expected_rs = [row["expected_r"] for row in admission_rows]
        confidences = [row["confidence"] for row in admission_rows]
        rrs = [row["rr_tp2"] for row in admission_rows]
        near_watch = sum(row["probability"] >= watch_min for row in admission_rows)

        print(
            "REJECTION_DIAG_RESULT "
            f"scan_calls={scan_calls} strategy_rejections={sum(strategy_calls.values())} "
            f"admission_rejected={len(admission_rows)} admission_p_ge_watch={near_watch} "
            f"watch_probability_min={watch_min}",
            flush=True,
        )
        print(f"REJECTION_DIAG_STRATEGY_COUNTS {dict(sorted(strategy_calls.items()))}", flush=True)
        print(f"REJECTION_DIAG_ADMISSION_GATES {dict(sorted(gate_counts.items()))}", flush=True)
        print(f"REJECTION_DIAG_ADMISSION_P {_summary(probabilities)}", flush=True)
        print(f"REJECTION_DIAG_ADMISSION_EV {_summary(expected_rs)}", flush=True)
        print(f"REJECTION_DIAG_ADMISSION_RR {_summary(rrs)}", flush=True)
        print(f"REJECTION_DIAG_ADMISSION_CONF {_summary(confidences)}", flush=True)

        print("REJECTION_DIAG_TOP_FIRST_FAILURES", flush=True)
        for (lane, strategy, name, label, detail), count in first_failures.most_common(40):
            print(
                f"  {count}x {lane} / {strategy} / {name} / {label}: {detail}",
                flush=True,
            )

        print("REJECTION_DIAG_TOP_FAILED_CHECKS", flush=True)
        for (lane, strategy, name, label, detail), count in failed_checks.most_common(60):
            print(
                f"  {count}x {lane} / {strategy} / {name} / {label}: {detail}",
                flush=True,
            )

        print("REJECTION_DIAG_ADMISSION_SAMPLES", flush=True)
        for row in admission_rows[:40]:
            print(
                f"  {row['lane']} {row['instrument']} {row['as_of'].isoformat()} "
                f"failed={row['failed']} p={row['probability']} ev={row['expected_r']} "
                f"rr={row['rr_tp2']} conf={row['confidence']} trigger={row['has_trigger']} "
                f"reason={row['reason']} event={row['event']}",
                flush=True,
            )

        print("REJECTION_DIAG_TOP_SKIPS", flush=True)
        for (lane, stage, reason), count in skip_counts.most_common(30):
            print(f"  {count}x {lane} / {stage}: {reason}", flush=True)
        if exceptions:
            print("REJECTION_DIAG_EXCEPTIONS", flush=True)
            for (lane, kind, detail), count in exceptions.most_common(20):
                print(f"  {count}x {lane} {kind}: {detail}", flush=True)
    finally:
        scan_module._load_bars = original_load_bars
        scan_module.admit = original_admit
        session.rollback()
        session.close()


if __name__ == "__main__":
    main()
