# Control / venue backtests / exact N/40

## Scope

Fix three owner-control gaps without changing signal thresholds, admission, risk caps, execution or promotion behavior.

1. Stop presenting read-only historical replay candidates as persisted live ideas.
2. Make champion/challenger progress explicit as exact closed comparable pairs per venue: `N/40`, `remaining`, `sample_adequate`.
3. Establish honest entry-strategy backtests independently for FORTS and BYBIT, with reproducible persisted `BacktestRun` evidence and fail-closed readiness.

## 1. Live vs replay truth

- `runtime_funnel_48h.py` is diagnostic replay only; it rolls DB work back and must label rows `REPLAY_CANDIDATE`.
- Add a separate production query for `TradeIdea` persisted/presented rows so an ACTIVE replay candidate is never described as an app idea.
- Live scanner semantics remain unchanged: latest-watermark scan, no historical H1 backfill, and one non-terminal live idea per instrument.

## 2. Exact N/40

Pairing unit is one unique `PaperAbDecision.pair_key` within the selected venue and time window where both CONTROL and the named CANDIDATE have `PaperAbOutcome.evidence_status == EVALUATED` and non-null `net_r`.

Rules:
- venue must match on both arms;
- candidate version is explicit;
- count distinct pair keys, never decision rows;
- duplicate/ambiguous pair identities fail closed and do not inflate N;
- unmatched, pending and unavailable outcomes are excluded;
- API exposes `comparable_pairs`, `required_pairs=40`, `remaining_pairs=max(0,40-N)`, `sample_adequate=N>=40`;
- UI shows `N/40 · осталось M` independently under FORTS and BYBIT;
- winner verdict is prohibited before `sample_adequate=true`.

## 3. Venue entry backtests

Backtests must be independent research jobs, never part of the live scanner.

### Common contract

Each run persists:
- venue marker (`FORTS` or `CRYPTO`/BYBIT);
- strategy/version;
- period and point-in-time dataset identity/hash;
- config hash and engine version;
- walk-forward fold metadata;
- cost-model identity;
- aggregate and per-setup trade counts;
- PF, expectancy R, max DD, Sharpe/Sortino, PBO/top-5 concentration when measurable;
- explicit gate detail and reason for BLOCKED/INSUFFICIENT states.

No TradeIdea, PaperTrade, order, execution, risk-policy or promotion mutation is permitted.

### BYBIT

- Backfill/retain at least 36 months of point-in-time H1/D1 history for the tested perpetual universe.
- Funding/basis inputs used by carry strategies must be point-in-time and bounded at each evaluation timestamp.
- Run 24m train / 6m validation / 3m OOS / 3m step.

### FORTS

Current per-contract bars cannot honestly satisfy a 36-month strategy backtest because futures expire. Build a continuous point-in-time futures series per underlying with explicit roll events and no forward-looking contract selection.
- preserve source contract and roll timestamp on every stitched segment;
- prevent labels/trades from crossing an invalid roll boundary unless the execution model explicitly handles it;
- then apply the same walk-forward contract.

### Gates

Use existing approved config only:
- aggregate trades >= 200;
- trades/setup >= 40;
- OOS PF >= 1.20;
- OOS expectancy >= 0.12R;
- top-5 contribution <= 0.30.

Insufficient history is `BLOCKED_INSUFFICIENT_HISTORY`, never zero-PnL and never PASS.

## Delivery order

1. Exact N/40 backend + UI contract and tests.
2. Live-vs-replay diagnostic labels + persisted-live query.
3. BYBIT historical backfill + entry backtest runner + scheduled/manual workflow.
4. FORTS continuous-series builder + roll-boundary tests + entry backtest runner.
5. Production run for each venue and control-screen verification.
6. Only after evidence exists: compare candidate performance. No strategy/risk threshold tuning in this change.

## Implementation checkpoint — 2026-08-29

Completed in the first two safe slices:

- runtime exact-control audit is merged and can report venue/candidate N/40 without mutating production;
- dashboard paired evidence now filters both CONTROL and CANDIDATE by the selected venue and counts distinct `pair_key` values;
- dashboard exposes `required_pairs`, `remaining_pairs` and `sample_adequate`; winner verdict remains blocked below 40;
- mobile Control renders `N x/40 · осталось y` under the selected FORTS/BYBIT tab;
- research-only `backtest/bybit_history.py` pages public Bybit candles backward without modifying live `market.crypto.klines()` or canonical `bars`;
- research-only `backtest/forts_continuous.py` preserves source contract and half-open roll boundaries and never synthesises missing bars.

Still intentionally not implemented in this slice:

- immutable multi-stream BYBIT dataset publication (price + mark/index/premium + OI + funding);
- expired-contract FORTS discovery/backfill and continuous dataset publication;
- 36-month data-readiness gate;
- scheduled/manual venue backtest runner and persisted entry-strategy `BacktestRun` results;
- Control-screen `DATA READY` / `BLOCKED — INSUFFICIENT HISTORY` evidence.

Those items form the next isolated implementation PR after this checkpoint passes a fresh full quality gate against the current release base.

## Required tests

- N counts distinct closed comparable pair keys only and is venue-isolated.
- duplicate/ambiguous pairs cannot inflate N.
- N<40 cannot produce a winner verdict.
- replay creates no persistent idea and is labelled replay.
- venue backtest has no TradeIdea/PaperTrade/execution side effects.
- insufficient history fails closed.
- BYBIT funding data is point-in-time bounded.
- FORTS walk-forward samples do not leak across roll boundaries.
- risk-exit optimizer runs are excluded from entry-backtest cards.
