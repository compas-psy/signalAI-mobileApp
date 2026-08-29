# Bybit visibility, venue backtests, and N/40 control plan

## Goal

Close three owner-observed gaps without changing trading thresholds, risk caps, daily ranking limits, promotion policy, or LIVE execution:

1. distinguish a historical replay candidate from a live persisted/presented idea and make valid live ideas outside the daily top-N visible to the owner;
2. make entry-strategy backtest state explicit and independent for FORTS and BYBIT, with heavy-lane scheduling and fail-closed history readiness;
3. show exact comparable evidence progress as `N / required` and `remaining` separately for FORTS and BYBIT.

## Safety boundaries

- No edits to strategy thresholds, admission gates, `ideas.max_daily_cards`, risk limits, position sizing, broker execution, kill switches, or LIVE mode.
- Existing `/ideas/today` remains the ranked operational top-N.
- Additional visibility is informational: it must not make a hidden idea actionable merely because it is shown.
- Backtest work executes only in the heavy analytical lane and cannot delay market ingest/scan.
- Insufficient historical depth is reported as a blocking readiness state, never coerced into a passing backtest.

## Workstream A — exact N/40

### Server

Extend each candidate paper comparison with:

- `required_comparable_pairs`
- `remaining_comparable_pairs = max(0, required - comparable)`
- `sample_adequate`

The existing venue-scoped paired-outcome query remains the source of truth. Pending, unavailable, unpaired, and non-evaluated outcomes remain excluded.

### Mobile

Render per candidate:

- `N / 40`
- `осталось K` until adequate
- `выборка набрана` when adequate

The FORTS/BYBIT segmented control continues to fetch independent server snapshots.

### Tests

- exact usable paired outcomes only;
- FORTS and BYBIT isolation;
- different candidate versions do not share N;
- remaining is never negative;
- mobile parsing/rendering of N/40.

## Workstream B — Bybit idea visibility

### Root-cause model

The historical 48h funnel is a point-in-time replay over current persisted bars. A replay `ACTIVE` candidate does not prove a live `TradeIdea` was created or presented at that historical timestamp.

Current live pipeline can hide a currently valid idea in two legitimate ways:

1. a non-terminal idea for the instrument already exists, so a duplicate is suppressed;
2. a new idea is persisted but falls outside `ideas.max_daily_cards`, leaving `was_presented=false`.

Also, historical bars can be revised after the original scan, so replay can discover a setup that did not exist from the original live data snapshot.

### Safe visibility fix

Keep `/ideas/today` unchanged as the operational ranked top-N. Add a read-only endpoint for all current non-terminal ACTIVE/WATCH ideas with presentation metadata, and surface a secondary mobile section `Вне топа дня` for valid persisted ideas where `was_presented=false`.

These rows are visibly labelled informational/non-top and do not bypass existing approval/sizing checks.

### Diagnostics

Add read-only owner diagnostics in Control for recent persisted-but-not-presented ideas, including instrument, strategy, status, signal time, and presentation state. This makes future ETH/XAUT-like cases auditable without replay guesswork.

### Tests

- `/ideas/today` semantics unchanged;
- unpresented valid idea appears only in the expanded live endpoint/secondary section;
- terminal/expired/invalid-quality rows excluded;
- presented idea is not duplicated between sections.

## Workstream C — FORTS/BYBIT backtests

### Problem

The deterministic backtest/walk-forward primitives exist, and Control can display `BacktestRun`, but there is no guaranteed production entry-backtest orchestration. Current production bar depth is also materially shorter than the configured 36-month walk-forward requirement, so running the engine immediately would create false confidence.

### Architecture

Add a heavy-lane `entry-backtest` runtime coordinator with independent venue scopes:

- FORTS
- BYBIT

The coordinator first computes data readiness for each venue:

- earliest/latest usable closed data;
- historical months available;
- required months from `backtest.walk_forward.min_history_months`;
- instrument/segment coverage;
- readiness status and blockers.

It writes/updates auditable venue-specific backtest evidence only when the required point-in-time dataset exists. Until then, Control reports `INSUFFICIENT_HISTORY` with exact available vs required depth instead of `no data` or fabricated zero metrics.

The job belongs to `scheduler-heavy`, never the latency-critical market lane. FORTS and BYBIT failures are independent.

### Historical backfill

Add bounded historical backfill primitives separate from live ingest:

- BYBIT: paginated closed D1/H1 history, not the live `limit=400` tail;
- FORTS: historical contracts/segments, preserving contract boundaries rather than pretending one current futures contract has 36 months of continuous history.

Backfill is idempotent and bounded per heavy tick. It must not mutate universe admission or current scanner behavior.

### Execution sequence

1. Ship readiness + heavy-lane coordinator + explicit Control status.
2. Backfill venue history incrementally.
3. Once 36-month point-in-time history is complete, run venue backtests and persist `BacktestRun` separately for FORTS/BYBIT.
4. Apply existing walk-forward/paper-gate criteria; no new thresholds.

### Tests

- heavy lane contains entry-backtest; market lane does not;
- venue failures are isolated;
- <36 months => `INSUFFICIENT_HISTORY`, no pass;
- FORTS contract-segment boundaries remain explicit;
- BYBIT pagination never uses future bars;
- successful run is tagged to exactly one venue and selected correctly by Control.

## Verification

- targeted server tests first (red → green);
- full server test suite;
- Flutter analyze + tests;
- release Android compile if mobile files changed;
- production read-only diagnostics after deploy for ETHUSDT/XAUTUSDT and exact FORTS/BYBIT N/40/backtest readiness.
