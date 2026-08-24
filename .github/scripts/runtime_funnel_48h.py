"""Read-only 48h funnel diagnostics for currently admitted Crypto/FORTS.

This diagnostic does not change thresholds, persistence, execution mode, or
production state.  It replays the canonical scanner over persisted closed bars
and reports where candidate generation stops: early data/liquidity gates,
strategy checks, and admission gates/metrics.

Historical admission snapshots are not persisted, so the scope is the set of
instruments admitted *now*.  Results are therefore operational diagnostics,
not a perfect reconstruction of the historical universe.
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
TARGET_CLASSES = (AssetClass.FUTURES, AssetClass.CRYPTO_PERPETUAL)


def _lane(instrument: Instrument) -> str:
    return "crypto" if instrument.asset_class == AssetClass.CRYPTO_PERPETUAL else "forts"


def _value(value) -> str:
    return str(value.value if hasattr(value, "value") else value)


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


def _metric_summary(records: list[dict], field: str) -> str:
    values = [Decimal(str(item[field])) for item in records]
    if not values:
        return "n=0"
    return f"n={len(values)} min={min(values)} median={median(values)} max={max(values)}"


def main() -> None:
    window_end = datetime.now(UTC)
    window_start = window_end - timedelta(hours=WINDOW_HOURS)
    history_floor = window_start - timedelta(days=LOOKBACK_DAYS)
    session = get_session_factory()()

    original_load_bars = scan_module._load_bars
    original_components = scan_module._components
    original_admit = scan_module.admit

    context: dict[str, object] = {
        "lane": "",
        "instrument": "",
        "as_of": window_end,
        "strategy": "",
        "direction": "",
    }
    admission_records: list[dict] = []

    try:
        instruments = list(
            session.execute(
                select(Instrument).where(
                    Instrument.asset_class.in_(TARGET_CLASSES),
                    Instrument.in_universe.is_(True),
                    Instrument.is_tradable.is_(True),
                )
            ).scalars()
        )
        instruments.sort(key=lambda item: item.instrument_id)
        target_ids = {item.instrument_id for item in instruments}
        by_lane = Counter(_lane(item) for item in instruments)

        print(
            "FUNNEL_WINDOW "
            f"utc={window_start.isoformat()}..{window_end.isoformat()} "
            f"current_admitted={len(instruments)} by_lane={dict(sorted(by_lane.items()))}",
            flush=True,
        )

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

        as_of_holder = {"value": window_end}

        def load_bars_asof(_session, instrument_id, timeframe, limit):
            as_of = as_of_holder["value"]
            close_delta = timedelta(days=1) if timeframe == Timeframe.D1 else timedelta(hours=1)
            cutoff = as_of - close_delta
            eligible = [
                item
                for item in cache.get((instrument_id, timeframe), ())
                if item.open_time <= cutoff
            ]
            return eligible[-limit:]

        def components_probe(candidate, regime, readings, rr_tp2, has_oi):
            context["strategy"] = _value(candidate.strategy)
            context["direction"] = _value(candidate.direction)
            return original_components(candidate, regime, readings, rr_tp2, has_oi)

        def admit_probe(**kwargs):
            verdict = original_admit(**kwargs)
            thresholds = kwargs["thresholds"]
            event = kwargs.get("event_assessment")
            admission_records.append(
                {
                    "lane": str(context["lane"]),
                    "instrument": str(context["instrument"]),
                    "as_of": context["as_of"],
                    "strategy": str(context["strategy"]),
                    "direction": str(context["direction"]),
                    "status": _value(verdict.status),
                    "probability": Decimal(str(kwargs["probability"])),
                    "expected_r": Decimal(str(kwargs["expected_r"])),
                    "rr_tp2": Decimal(str(kwargs["rr_tp2"])),
                    "confidence": Decimal(str(kwargs["confidence"])),
                    "has_trigger": bool(kwargs["has_trigger"]),
                    "liquidity": _value(kwargs["liquidity"]),
                    "event_status": "" if event is None else str(event.status),
                    "failed": tuple(gate.name for gate in verdict.failed),
                    "reason": verdict.reason,
                    "watch_probability_min": thresholds.watch_probability_min,
                    "active_probability_min": thresholds.active_probability_min,
                    "active_expected_r_min": thresholds.active_expected_r_min,
                    "min_rr_tp2": thresholds.min_rr_tp2,
                    "min_confidence": thresholds.min_confidence,
                }
            )
            return verdict

        scan_module._load_bars = load_bars_asof
        scan_module._components = components_probe
        scan_module.admit = admit_probe

        cfg = get_config()
        risk_state = RiskState(risk_equity=cfg.decimal("risk.equity_rub"))
        calendar = load_owned_calendar(now=window_end)

        scan_calls = 0
        strategy_eval_calls = Counter()
        strategy_rejections = Counter()
        first_failures = Counter()
        failed_checks = Counter()
        failed_details = Counter()
        skip_counts = Counter()
        exception_counts = Counter()

        total = len(instruments)
        for index, instrument in enumerate(instruments, start=1):
            lane = _lane(instrument)
            h1_opens = [
                item.open_time
                for item in cache.get((instrument.instrument_id, Timeframe.H1), ())
                if window_start - timedelta(hours=1) <= item.open_time <= window_end
            ]

            for open_time in h1_opens:
                as_of = open_time + timedelta(hours=1, seconds=1)
                if as_of < window_start or as_of > window_end:
                    continue

                as_of_holder["value"] = as_of
                context.update(
                    lane=lane,
                    instrument=instrument.instrument_id,
                    as_of=as_of,
                    strategy="",
                    direction="",
                )
                scan_calls += 1
                admissions_before = len(admission_records)

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
                    exception_counts[(lane, type(exc).__name__, str(exc))] += 1
                    continue

                admission_happened = len(admission_records) > admissions_before
                if rejections or admission_happened:
                    strategy_eval_calls[lane] += 1

                for rejection in rejections:
                    strategy = _value(rejection.strategy)
                    strategy_rejections[(lane, strategy)] += 1
                    failed = rejection.failed
                    if failed:
                        first = failed[0]
                        first_failures[(lane, strategy, first.name, first.label)] += 1
                    for check in failed:
                        failed_checks[(lane, strategy, check.name, check.label)] += 1
                        failed_details[(lane, strategy, check.name, check.detail)] += 1

                for item in skipped:
                    skip_counts[(lane, item.stage, item.reason)] += 1

            print(
                f"FUNNEL_PROGRESS {index}/{total} lane={lane} "
                f"instrument={instrument.instrument_id} scan_calls={scan_calls} "
                f"admission_candidates={len(admission_records)}",
                flush=True,
            )

        status_counts = Counter((item["lane"], item["status"]) for item in admission_records)
        failed_gate_counts = Counter(
            (item["lane"], gate)
            for item in admission_records
            for gate in item["failed"]
        )

        print(
            "FUNNEL_RESULT "
            f"scan_calls={scan_calls} "
            f"strategy_eval_calls={dict(sorted(strategy_eval_calls.items()))} "
            f"admission_candidates={len(admission_records)} "
            f"admission_status={dict(sorted(status_counts.items()))}",
            flush=True,
        )
        print(
            "FUNNEL_STRATEGY_REJECTIONS "
            + repr(dict(sorted(strategy_rejections.items()))),
            flush=True,
        )
        print("FUNNEL_TOP_STRATEGY_FIRST_FAILURES", flush=True)
        for (lane, strategy, name, label), count in first_failures.most_common(30):
            print(f"  {count}x {lane} {strategy} {name} ({label})", flush=True)

        print("FUNNEL_TOP_STRATEGY_FAILED_CHECKS", flush=True)
        for (lane, strategy, name, label), count in failed_checks.most_common(50):
            print(f"  {count}x {lane} {strategy} {name} ({label})", flush=True)

        print("FUNNEL_TOP_STRATEGY_FAILED_DETAILS", flush=True)
        for (lane, strategy, name, detail), count in failed_details.most_common(40):
            print(f"  {count}x {lane} {strategy} {name}: {detail}", flush=True)

        print(
            "FUNNEL_ADMISSION_FAILED_GATES "
            + repr(dict(sorted(failed_gate_counts.items()))),
            flush=True,
        )
        for lane in sorted(by_lane):
            lane_records = [item for item in admission_records if item["lane"] == lane]
            print(
                f"FUNNEL_ADMISSION_METRICS lane={lane} "
                f"probability[{_metric_summary(lane_records, 'probability')}] "
                f"expected_r[{_metric_summary(lane_records, 'expected_r')}] "
                f"rr_tp2[{_metric_summary(lane_records, 'rr_tp2')}] "
                f"confidence[{_metric_summary(lane_records, 'confidence')}]",
                flush=True,
            )

        print("FUNNEL_TOP_ADMISSION_CANDIDATES", flush=True)
        ranked = sorted(
            admission_records,
            key=lambda item: (
                item["probability"] - item["watch_probability_min"],
                item["expected_r"],
            ),
            reverse=True,
        )
        for item in ranked[:50]:
            p_gap_watch = item["probability"] - item["watch_probability_min"]
            p_gap_active = item["probability"] - item["active_probability_min"]
            ev_gap_active = item["expected_r"] - item["active_expected_r_min"]
            print(
                "  "
                f"{item['lane']} {item['instrument']} {item['as_of'].isoformat()} "
                f"{item['strategy']} {item['direction']} status={item['status']} "
                f"p={item['probability']} p_gap_watch={p_gap_watch} "
                f"p_gap_active={p_gap_active} ev={item['expected_r']} "
                f"ev_gap_active={ev_gap_active} rr={item['rr_tp2']} "
                f"conf={item['confidence']} trigger={item['has_trigger']} "
                f"liq={item['liquidity']} event={item['event_status']} "
                f"failed={list(item['failed'])} reason={item['reason']}",
                flush=True,
            )

        print("FUNNEL_TOP_SKIPS", flush=True)
        for (lane, stage, reason), count in skip_counts.most_common(30):
            print(f"  {count}x {lane} / {stage}: {reason}", flush=True)

        if exception_counts:
            print("FUNNEL_EXCEPTIONS", flush=True)
            for (lane, kind, detail), count in exception_counts.most_common(20):
                print(f"  {count}x {lane} {kind}: {detail}", flush=True)
    finally:
        scan_module._load_bars = original_load_bars
        scan_module._components = original_components
        scan_module.admit = original_admit
        session.rollback()
        session.close()


if __name__ == "__main__":
    main()
