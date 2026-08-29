# Bybit P0/P1 Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove false Bybit idea suppression, make champion/challenger runtime semantics observable, and complete the immutable 36-month Bybit research/backtest path through Control.

**Architecture:** Keep the current Universe, scan, shadow, DatasetSnapshot, BacktestRun and Control boundaries. Normalize venue-specific facts once at the market/admission boundary, reuse those facts in live and shadow evaluation, publish a deterministic per-symbol funnel, and build historical Bybit datasets as immutable point-in-time snapshots consumed by a backtest runner that never calls live REST during replay.

**Tech Stack:** Python 3.11, SQLAlchemy/PostgreSQL, pytest, existing Bybit v5 client, existing DatasetSnapshot/BacktestRun models, FastAPI Control API, Flutter Control UI.

**Spec:** `docs/plans/2026-08-29-control-backtests-n40.md`

## Global Constraints

- Preserve the existing 30-symbol Bybit active-universe cap and 30-day median liquidity admission.
- Crypto liquidity is measured in USDT; FORTS liquidity remains measured in RUB.
- Historical replay must be point-in-time and must not call live Bybit REST for strategy facts.
- Dataset identity is content-addressed/immutable and must reuse the existing `DatasetSnapshot` storage contract.
- Champion/challenger governance must be visibly distinct from the strategy path that is allowed to publish live ideas.
- No strategy threshold tuning until P0 runtime/data-contract defects are fixed and measurable.

---

### Task 1: Fix crypto liquidity and execution-fact contracts

**Files:**
- Modify: `server/app/pipeline/scan.py`
- Modify: `server/app/shadow/collector_v1.py`
- Modify: `server/app/market/universe.py`
- Test: `server/tests/pipeline/test_bybit_scan_contract.py`
- Test: `server/tests/shadow/test_bybit_shadow_contract.py`

**Interfaces:**
- Consumes: `Instrument.metadata_json["admission"]` produced by Universe.
- Produces: venue-aware liquidity inputs and canonical `relative_spread` execution fact used by scan/shadow.

- [ ] Write failing tests proving BYBIT uses crypto liquidity thresholds/admission facts rather than FORTS RUB thresholds.
- [ ] Write failing test proving shadow reads canonical `admission.relative_spread` and does not return `COST_FACTS_UNAVAILABLE` solely because of the old key mismatch.
- [ ] Run the focused tests and confirm RED.
- [ ] Implement minimal venue-aware fact helpers and replace duplicate threshold/key logic.
- [ ] Run focused tests and confirm GREEN.

### Task 2: Make derivatives flow inputs real and observable

**Files:**
- Modify: `server/app/market/ingest.py`
- Modify: `server/app/pipeline/scan.py`
- Create: `server/app/market/derivatives_features.py`
- Test: `server/tests/market/test_derivatives_features.py`

**Interfaces:**
- Produces: `price_change_z` and `oi_change_z` from aligned historical observations with explicit insufficient-history status.

- [ ] Write failing tests for aligned price/OI z-score calculation and insufficient-history behavior.
- [ ] Confirm RED.
- [ ] Implement pure feature calculation and wire scan to pass real values when available.
- [ ] Confirm GREEN and preserve fail-safe behavior when OI is missing.

### Task 3: Add deterministic Bybit funnel diagnostics and honest competition semantics

**Files:**
- Create: `server/app/control/bybit_funnel.py`
- Modify: `server/app/pipeline/scan.py`
- Modify: `server/app/shadow/collector_v1.py`
- Modify: `server/app/control/dashboard.py`
- Test: `server/tests/control/test_bybit_funnel.py`

**Interfaces:**
- Produces: counts/reason-codes for universe -> data healthy -> liquid -> regime eligible -> strategy evaluated -> setup/cost/RR reject -> published.
- Produces competition fields: `live_generator`, `champion`, `challengers`, `shadow_only`.

- [ ] Write failing tests for funnel aggregation and role semantics.
- [ ] Confirm RED.
- [ ] Implement reason-code recording without changing strategy thresholds.
- [ ] Surface diagnostics in Control payload.
- [ ] Confirm GREEN.

### Task 4: Build immutable multi-stream Bybit historical datasets and 36m readiness

**Files:**
- Modify: `server/app/backtest/bybit_history.py`
- Create: `server/app/backtest/bybit_dataset.py`
- Modify: `server/app/datasets/snapshots.py`
- Test: `server/tests/backtest/test_bybit_dataset.py`

**Interfaces:**
- Required streams: klines, funding, open interest, mark/index/premium where strategy dependencies require them, long/short ratio where enabled.
- Produces: immutable `DatasetSnapshot` plus per-stream coverage/readiness and `DATA_READY`/`DATA_BLOCKED` reason codes.

- [ ] Write failing tests for cursor pagination, PIT timestamps, immutable identity and 36-month per-stream readiness.
- [ ] Confirm RED.
- [ ] Implement paginated collectors and canonical multi-stream snapshot payload.
- [ ] Confirm GREEN.

### Task 5: Add replay-safe Bybit backtest runner and persist evidence

**Files:**
- Create: `server/app/backtest/bybit_runner.py`
- Modify: `server/app/risk/optimizer.py`
- Test: `server/tests/backtest/test_bybit_runner.py`

**Interfaces:**
- Consumes: immutable dataset snapshot id only.
- Produces: `BacktestRun` with strategy/version/dataset hash, walk-forward/OOS metrics, cost assumptions and pass/fail gates.

- [ ] Write failing test proving replay uses only snapshot data and performs no live REST call.
- [ ] Write failing test for persisted dataset hash and OOS evidence.
- [ ] Confirm RED.
- [ ] Implement minimal runner around existing costed/walk-forward primitives.
- [ ] Confirm GREEN.

### Task 6: Schedule research/risk optimization only behind evidence gates

**Files:**
- Modify: `server/app/scheduler/p0_runtime.py`
- Modify: `server/app/risk/optimizer.py`
- Test: `server/tests/scheduler/test_bybit_research_schedule.py`

**Interfaces:**
- Optimizer runs only when dataset readiness and minimum evidence requirements pass; otherwise records blocked status without promotion.

- [ ] Write failing tests for scheduled invocation and readiness blocking.
- [ ] Confirm RED.
- [ ] Wire bounded invocation after research/backtest lifecycle, preserving current live strategy behavior.
- [ ] Confirm GREEN.

### Task 7: Wire Control UI and complete verification

**Files:**
- Modify: Control API/models/widgets already used by `server/app/control/dashboard.py` and the Flutter Control screen.
- Test: existing Control API/Flutter tests plus focused new assertions.

**Interfaces:**
- Control shows Bybit funnel, exact live generator, champion/challenger/shadow roles, dataset coverage/readiness, latest backtest evidence and optimizer runtime state.

- [ ] Add failing API/UI assertions for the new fields/statuses.
- [ ] Confirm RED.
- [ ] Implement UI wiring with no duplicated business logic on mobile.
- [ ] Confirm GREEN.
- [ ] Run full repository Quality gate and fix only regressions caused by this branch.
