# SignalAI Owner Control Plane v1

## Goal

Give the owner one read-only control surface that explains, separately for FORTS and Bybit/crypto, what the legacy control and new candidate stack are doing, whether comparison evidence is valid, what backtests/OOS gates say, and which risk/exit policy is champion.

## Product contract

The screen is named **Контроль** and lives in Settings as a first-class thin-client screen. It must answer four questions without requiring GitHub Actions or server logs:

1. **Почему я вижу столько идей?** — funnel/availability for control and candidates, including explicit unavailable reasons.
2. **Кто выигрывает competition?** — control vs candidate evidence and a deterministic verdict.
3. **Где backtesting/OOS?** — latest reproducible backtest/gate state and history range.
4. **Что происходит с risk optimizer?** — active champion, sample size, latest optimizer run, bounded candidates and promotion status.

## Non-goals

- No manual strategy promotion from mobile in v1.
- No parameter editor, arbitrary grid search or unconstrained optimizer.
- No direct order/execution mutations.
- No weakening of existing admission/risk thresholds to increase signal volume.

## Venue scope

UI switch:

- `FORTS`
- `BYBIT`

Server accepts `venue=FORTS|BYBIT` and maps BYBIT to the crypto venue/asset class used by persisted evidence.

## Control-plane payload

`GET /api/v1/control/dashboard?venue=FORTS|BYBIT&window_hours=168`

Returns:

- `generated_at`, `venue`, `window_hours`
- `health`: `OK | DEGRADED | BROKEN_INPUT | NO_SAMPLE`
- `funnel`
  - legacy ideas created/presented/status buckets
  - candidate Shadow totals by strategy version: observations, evaluated, unavailable, emitted, top unavailable reasons
  - Paper A/B totals by arm/version: decisions, emitted, evaluated outcomes, pending outcomes, mean net R when available
- `competition`
  - control version
  - candidate rows
  - comparable sample count
  - metrics available from Paper A/B outcomes
  - deterministic verdict per candidate: `CONTROL_WINNING | CANDIDATE_WINNING | WAITING_FOR_SAMPLE | BROKEN_INPUT | INSUFFICIENT_OUTCOMES`
- `backtest`
  - latest non-risk optimizer run for the selected venue/universe
  - period, trades, PF, expectancy R, max drawdown, Sharpe/Sortino, PBO, top-5 contribution, gate status, config hash, engine version
  - configured walk-forward and paper/live gates
- `risk_optimizer`
  - active `risk_exit_policy` champion, candidate id, version, sample size, promoted_at, OOS metrics/review
  - latest `risk-exit-v2:*` BacktestRun and gate detail
  - configured cadence/min sample/min OOS improvement and bounded candidate ids

All payloads are read-only. Missing evidence is represented explicitly; the API must not synthesize zeros that look like measured PnL.

## Bybit crypto carry P0

The production Shadow collector must no longer hard-code `crypto_carry_facts=None` for every crypto instrument when the public Bybit market adapter is available.

The default provider must:

1. Keep bar-only strategies independent from derivative supplemental inputs.
2. For `CRYPTO_PERPETUAL`, resolve point-in-time Bybit carry facts through the existing public market adapter.
3. Preserve failures as `INPUT_UNAVAILABLE` with a stable reason code; never turn source failures into an evaluated no-signal.
4. Use explicit, auditable carry cost assumptions only. No hidden numeric fallback is allowed.
5. Never create owner ideas, paper trades, notifications or execution intents from Shadow.

For v1, carry cost assumptions are configured under `shadow.crypto_carry` and are intentionally bounded/read-only:

- `execution_cost_bps`
- `hedge_carry_bps_per_interval`
- `funding_uncertainty_bps_per_interval`

The config hash remains part of measurement identity.

## UI layout

### Header

- title/Settings pill: `Контроль`
- segmented venue switch `FORTS | BYBIT`
- overall health badge and refresh

### Section 1 — Сейчас

Two compact lanes:

- `Старая · CONTROL`
- `Новая · CANDIDATE`

Show counts for generated/evaluated/emitted/presented where meaningful. Candidate lane shows broken-input count separately.

Below: **Почему не дошло** with top unavailable reasons, never buried in diagnostics copy.

### Section 2 — Competition

For each candidate:

- version
- control/candidate emitted count
- comparable/evaluated outcomes
- mean net R when available
- verdict badge
- sample warning

A candidate with dominant `INPUT_UNAVAILABLE` must render `BROKEN INPUT`, not `WAITING`.

### Section 3 — Backtest / OOS

Latest run card plus configured gates. Show `нет измерения`, not zeros, when a metric is absent.

### Section 4 — Risk optimizer

Show champion and latest optimizer run, sample `N/min`, last decision/gate, bounded candidate ids and the fact that absolute risk caps cannot be increased by the optimizer.

## Acceptance criteria

1. Production crypto Shadow no longer reports `FUNDING_FACTS_UNAVAILABLE` solely because the default provider never calls the existing Bybit adapter.
2. Bybit adapter/source failures remain fail-closed as `INPUT_UNAVAILABLE`.
3. `/control/dashboard` works on an empty database and returns `NO_SAMPLE` without 500.
4. `/control/dashboard` surfaces a 100% unavailable `crypto_carry_v1` as `BROKEN_INPUT`.
5. Paper A/B pending outcomes are not counted as zero-return outcomes.
6. Risk optimizer snapshot reports configured min sample/cadence and active champion independently of venue.
7. Thin mobile has a visible `Контроль` Settings pill and can switch FORTS/BYBIT.
8. UI never offers promotion or parameter mutation in v1.
9. Existing owner scan, admission, risk, paper and execution behavior remain unchanged.
