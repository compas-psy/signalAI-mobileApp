"""Read-only 48h canonical replay for production diagnostics.

Runs inside the production API container. It uses only bars that were closed at
each historical as-of instant, never flushes/commits, and reports user-quality
WATCH/ACTIVE episodes separately from strategy/admission rejection evidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.config import get_config
from app.db import get_session_factory
from app.market.candles import Candle
from app.market.economic_events import EventAssessment, load_owned_calendar
from app.models import Bar, Instrument, TradeIdea
from app.models.enums import AssetClass, Timeframe
from app.pipeline import scan as scan_module
from app.risk.sizing import RiskState

WINDOW_HOURS = 48
LOOKBACK_DAYS = 65
USER_QUALITY = {"ACTIVE", "WATCH"}


class _DiagnosticClearCalendar:
    """Counterfactual only: isolate the effect of the broken calendar gate."""

    def assess(self, instrument_id: str, *, as_of: datetime) -> EventAssessment:
        return EventAssessment(
            "CLEAR",
            "DIAGNOSTIC_COUNTERFACTUAL_CLEAR",
            "diagnostic counterfactual: economic-event gate assumed clear",
        )


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


def _extend_episode(
    episodes: list[dict],
    live_episode: dict | None,
    *,
    instrument: Instrument,
    lane: str,
    idea: TradeIdea,
    as_of: datetime,
    current_admitted: set[str],
) -> dict:
    key = (
        instrument.instrument_id,
        _value(idea.strategy),
        _value(idea.direction),
    )
    status = _value(idea.quality_status)
    probability = Decimal(str(idea.p_tp1_before_sl))
    expected_r = Decimal(str(idea.expected_r))
    if live_episode is None or live_episode["key"] != key:
        live_episode = {
            "key": key,
            "lane": lane,
            "start": as_of,
            "end": as_of,
            "best_status": status,
            "max_probability": probability,
            "max_expected_r": expected_r,
            "current_admitted": instrument.instrument_id in current_admitted,
        }
        episodes.append(live_episode)
    else:
        live_episode["end"] = as_of
        if status == "ACTIVE":
            live_episode["best_status"] = "ACTIVE"
        live_episode["max_probability"] = max(live_episode["max_probability"], probability)
        live_episode["max_expected_r"] = max(live_episode["max_expected_r"], expected_r)
    return live_episode


def main() -> None:
    window_end = datetime.now(UTC)
    window_start = window_end - timedelta(hours=WINDOW_HOURS)
    history_floor = window_start - timedelta(days=LOOKBACK_DAYS)
    session = get_session_factory()()
    original_load_bars = scan_module._load_bars

    try:
        recent_ids = set(
            session.execute(
                select(Bar.instrument_id).where(
                    Bar.timeframe == Timeframe.H1,
                    Bar.is_closed.is_(True),
                    Bar.open_time >= window_start - timedelta(hours=1),
                    Bar.open_time <= window_end,
                ).distinct()
            ).scalars()
        )
        target_classes = (AssetClass.FUTURES, AssetClass.CRYPTO_PERPETUAL)
        instruments = list(
            session.execute(
                select(Instrument).where(
                    Instrument.asset_class.in_(target_classes),
                    Instrument.instrument_id.in_(recent_ids),
                )
            ).scalars()
        ) if recent_ids else []
        instruments.sort(key=lambda item: item.instrument_id)
        target_ids = {item.instrument_id for item in instruments}
        current_admitted = {
            item.instrument_id for item in instruments
            if item.in_universe and item.is_tradable
        }
        by_lane = Counter(_lane(item) for item in instruments)
        admitted_by_lane = Counter(
            _lane(item) for item in instruments if item.instrument_id in current_admitted
        )
        print(
            "REPLAY_WINDOW "
            f"utc={window_start.isoformat()}..{window_end.isoformat()} "
            f"all_h1={len(instruments)} by_lane={dict(sorted(by_lane.items()))} "
            f"current_admitted={len(current_admitted)} "
            f"admitted_by_lane={dict(sorted(admitted_by_lane.items()))}",
            flush=True,
        )

        rows = list(
            session.execute(
                select(Bar).where(
                    Bar.instrument_id.in_(target_ids),
                    Bar.timeframe.in_((Timeframe.D1, Timeframe.H1)),
                    Bar.is_closed.is_(True),
                    Bar.open_time >= history_floor,
                    Bar.open_time <= window_end,
                ).order_by(Bar.instrument_id, Bar.timeframe, Bar.open_time)
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
                item for item in cache.get((instrument_id, timeframe), ())
                if item.open_time <= cutoff
            ]
            return eligible[-limit:]

        scan_module._load_bars = load_bars_asof
        cfg = get_config()
        # Match scan()'s canonical default risk state exactly. The replay is
        # diagnostic-only and must not depend on a non-existent config key.
        risk_state = RiskState(risk_equity=Decimal(100_000))
        clear_calendar = _DiagnosticClearCalendar()

        episodes: list[dict] = []
        calendar_clear_episodes: list[dict] = []
        scan_calls = 0
        calendar_counterfactual_calls = 0
        strategy_rejected_calls = Counter()
        strategy_reasons = Counter()
        failed_checks = Counter()
        skipped_counts = Counter()
        admission_reasons = Counter()
        admission_instruments = Counter()
        counterfactual_non_user = Counter()
        exception_counts = Counter()

        for index, instrument in enumerate(instruments, start=1):
            lane = _lane(instrument)
            h1_opens = [
                item.open_time
                for item in cache.get((instrument.instrument_id, Timeframe.H1), ())
                if window_start - timedelta(hours=1) <= item.open_time <= window_end
            ]
            live_episode = None
            clear_live_episode = None
            for open_time in h1_opens:
                as_of = open_time + timedelta(hours=1, seconds=1)
                if not (window_start <= as_of <= window_end):
                    continue
                as_of_holder["value"] = as_of
                scan_calls += 1
                try:
                    idea, skipped, rejections = scan_module.scan_instrument(
                        session,
                        instrument,
                        cfg=cfg,
                        risk_state=risk_state,
                        now=as_of,
                        event_calendar=load_owned_calendar(now=as_of),
                    )
                except Exception as exc:
                    exception_counts[(lane, type(exc).__name__, str(exc))] += 1
                    live_episode = None
                    clear_live_episode = None
                    continue

                # Count every strategy's failed checks, including the two losing
                # strategies when a third strategy produced a candidate.
                for rejection in rejections:
                    strategy = _value(rejection.strategy)
                    strategy_reasons[(lane, strategy, rejection.reason)] += 1
                    for check in rejection.failed:
                        failed_checks[(lane, strategy, check.name, check.detail)] += 1

                if skipped:
                    admission_blocked = False
                    for item in skipped:
                        skipped_counts[(lane, item.stage, item.reason)] += 1
                        if item.stage == "допуск":
                            admission_blocked = True
                            admission_reasons[(lane, item.reason)] += 1
                            admission_instruments[(lane, item.instrument_id, item.reason)] += 1
                    live_episode = None

                    # This second call never represents a production decision.
                    # It isolates exactly what the broken/unavailable event
                    # calendar prevented the remaining admission gates from
                    # deciding. Treat it as an upper bound until a real event
                    # source is operational and historically replayable.
                    if admission_blocked:
                        calendar_counterfactual_calls += 1
                        try:
                            cf_idea, cf_skipped, _ = scan_module.scan_instrument(
                                session,
                                instrument,
                                cfg=cfg,
                                risk_state=risk_state,
                                now=as_of,
                                event_calendar=clear_calendar,
                            )
                        except Exception as exc:
                            exception_counts[(lane, f"counterfactual_{type(exc).__name__}", str(exc))] += 1
                            clear_live_episode = None
                        else:
                            if cf_idea is not None and _value(cf_idea.quality_status) in USER_QUALITY:
                                clear_live_episode = _extend_episode(
                                    calendar_clear_episodes,
                                    clear_live_episode,
                                    instrument=instrument,
                                    lane=lane,
                                    idea=cf_idea,
                                    as_of=as_of,
                                    current_admitted=current_admitted,
                                )
                            else:
                                clear_live_episode = None
                                if cf_skipped:
                                    for cf_skip in cf_skipped:
                                        counterfactual_non_user[(lane, cf_skip.stage, cf_skip.reason)] += 1
                                else:
                                    counterfactual_non_user[(lane, "no_user_quality", "no ACTIVE/WATCH idea")] += 1
                    else:
                        clear_live_episode = None
                    continue

                clear_live_episode = None
                if idea is None:
                    if rejections:
                        strategy_rejected_calls[lane] += 1
                    live_episode = None
                    continue

                status = _value(idea.quality_status)
                if status not in USER_QUALITY:
                    # Defensive only: #261 prevents REJECTED from becoming a
                    # TradeIdea, but a regression must remain visible here.
                    skipped_counts[(lane, "unexpected_quality", status)] += 1
                    live_episode = None
                    continue

                live_episode = _extend_episode(
                    episodes,
                    live_episode,
                    instrument=instrument,
                    lane=lane,
                    idea=idea,
                    as_of=as_of,
                    current_admitted=current_admitted,
                )

            print(
                f"REPLAY_PROGRESS {index}/{len(instruments)} lane={lane} "
                f"instrument={instrument.instrument_id} scan_calls={scan_calls} "
                f"episodes={len(episodes)} admission_skips={sum(admission_reasons.values())} "
                f"calendar_clear_episodes={len(calendar_clear_episodes)}",
                flush=True,
            )

        actual_rows = list(
            session.execute(
                select(TradeIdea).where(
                    TradeIdea.instrument_id.in_(target_ids),
                    TradeIdea.signal_time >= window_start - timedelta(hours=2),
                    TradeIdea.signal_time <= window_end + timedelta(hours=1),
                )
            ).scalars()
        ) if target_ids else []
        actual_user_quality = [row for row in actual_rows if _value(row.quality_status) in USER_QUALITY]
        actual_presented = [row for row in actual_user_quality if row.was_presented]

        def matches_actual(episode: dict) -> bool:
            iid, strategy, direction = episode["key"]
            low = episode["start"] - timedelta(hours=1)
            high = episode["end"] + timedelta(hours=1)
            return any(
                row.instrument_id == iid
                and _value(row.strategy) == strategy
                and _value(row.direction) == direction
                and low <= row.signal_time <= high
                for row in actual_user_quality
            )

        missed_all = [item for item in episodes if not matches_actual(item)]
        current_scope = [item for item in episodes if item["current_admitted"]]
        missed_current = [item for item in current_scope if not matches_actual(item)]
        calendar_clear_current = [item for item in calendar_clear_episodes if item["current_admitted"]]

        def episode_counts(items):
            return dict(sorted(Counter((item["lane"], item["best_status"]) for item in items).items()))

        print(
            "REPLAY_RESULT "
            f"scan_calls={scan_calls} all_h1_signal_episodes={len(episodes)} "
            f"missed_all_h1={len(missed_all)} "
            f"current_admitted_signal_episodes={len(current_scope)} "
            f"missed_current_admitted={len(missed_current)} "
            f"actual_user_quality_ideas={len(actual_user_quality)} "
            f"actual_presented={len(actual_presented)}",
            flush=True,
        )
        print(f"REPLAY_ALL_BY_LANE_STATUS {episode_counts(episodes)}", flush=True)
        print(f"REPLAY_MISSED_ALL_BY_LANE_STATUS {episode_counts(missed_all)}", flush=True)
        print(f"REPLAY_CURRENT_BY_LANE_STATUS {episode_counts(current_scope)}", flush=True)
        print(f"REPLAY_MISSED_CURRENT_BY_LANE_STATUS {episode_counts(missed_current)}", flush=True)
        print(
            "REPLAY_CALENDAR_CLEAR_COUNTERFACTUAL "
            f"calls={calendar_counterfactual_calls} "
            f"user_quality_episodes={len(calendar_clear_episodes)} "
            f"current_admitted_episodes={len(calendar_clear_current)} "
            f"by_lane_status={episode_counts(calendar_clear_episodes)}",
            flush=True,
        )
        print(f"REPLAY_STRATEGY_REJECTED_CALLS {dict(sorted(strategy_rejected_calls.items()))}", flush=True)
        print(f"REPLAY_ADMISSION_BY_REASON {dict(sorted(admission_reasons.items()))}", flush=True)

        print("REPLAY_CALENDAR_CLEAR_EPISODES", flush=True)
        for item in calendar_clear_episodes:
            iid, strategy, direction = item["key"]
            print(
                f"  {item['lane']} {iid} {strategy} {direction} "
                f"{item['best_status']} {item['start'].isoformat()}..{item['end'].isoformat()} "
                f"p_max={item['max_probability']} ev_max={item['max_expected_r']} "
                f"current_admitted={item['current_admitted']}",
                flush=True,
            )

        print("REPLAY_ADMISSION_INSTRUMENTS", flush=True)
        for (lane, iid, reason), count in admission_instruments.most_common(100):
            print(f"  {count}x {lane} {iid}: {reason}", flush=True)

        print("REPLAY_STRATEGY_FAILED_CHECKS", flush=True)
        for (lane, strategy, name, detail), count in failed_checks.most_common(80):
            print(f"  {count}x {lane} {strategy} / {name}: {detail}", flush=True)

        print("REPLAY_STRATEGY_REASONS", flush=True)
        for (lane, strategy, reason), count in strategy_reasons.most_common(50):
            print(f"  {count}x {lane} {strategy}: {reason}", flush=True)

        print("REPLAY_COUNTERFACTUAL_NON_USER", flush=True)
        for (lane, stage, reason), count in counterfactual_non_user.most_common(30):
            print(f"  {count}x {lane} / {stage}: {reason}", flush=True)

        print("REPLAY_TOP_SKIPS", flush=True)
        for (lane, stage, reason), count in skipped_counts.most_common(30):
            print(f"  {count}x {lane} / {stage}: {reason}", flush=True)

        if exception_counts:
            print("REPLAY_EXCEPTIONS", flush=True)
            for (lane, kind, detail), count in exception_counts.most_common(20):
                print(f"  {count}x {lane} {kind}: {detail}", flush=True)
    finally:
        scan_module._load_bars = original_load_bars
        session.rollback()
        session.close()


if __name__ == "__main__":
    main()
