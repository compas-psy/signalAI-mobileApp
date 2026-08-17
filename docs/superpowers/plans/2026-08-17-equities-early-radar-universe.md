# Russian Equities Early Radar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing `equity_rank_v2_early` full Russian-equity universe to the thin Android client as an expandable early radar with server-ranked ordering, anti-chase, confirmation/invalidation, and safe null handling.

**Architecture:** Keep `server/app/portfolio/equity_ranking.py` as the only ranking engine. Extend the existing `/api/v1/research/equity-ranking` response model so persisted early fields are no longer dropped, extend the existing Flutter ranking domain model, and replace the narrow action-card presentation in `InvestmentSignalsScreen` with a full-universe view backed by `EquityRankingSource`. Filters are local presentation only and never alter server rank.

**Tech Stack:** FastAPI/Pydantic/SQLAlchemy/Pytest; Flutter/Dart; existing SIGNAL AI thin-client API layer and design tokens.

## Global Constraints

- Show the full server-ranked Russian-equity universe; no artificial Top-N cutoff.
- Server rank is authoritative; the phone does not recompute ranking, early score, eligibility, anti-chase, confirmation, or invalidation.
- Missing financial measurements stay `null`; UI renders `—`/omits them rather than substituting zero.
- Weak/rejected/late names remain visible.
- This milestone is investment/research advisory only: no equity entry, stop, target, order, approval, PAPER or live execution.
- Preserve strict as-of/no-forward behavior and the existing `equity_rank_v2_early` methodology.
- No changes to crypto/FORTS scanning, portfolio allocation, or broker integration.

---

### Task 1: Server API exposes persisted early-radar fields

**Files:**
- Modify: `server/app/api/v1/equity_rankings.py`
- Create: `server/tests/test_equity_ranking_api_contract.py`

**Interfaces:**
- Consumes: `EquityRankingSnapshot.items_json` produced by `build_daily_ranking()`.
- Produces: `GET /api/v1/research/equity-ranking` items with `rank_change`, `early_score`, `early_state`, `early_eligible`, `chase_penalty`, `why_now`, `confirmation`, `invalidation`, `return_5d`, `return_20d`, `return_3m`, `return_6m`, `breakout_distance`, `turnover_ratio`, `accumulation_score`, `compression_ratio` plus existing fields.

- [ ] **Step 1: Write failing API-contract tests**

Create a snapshot fixture with 12 items and assert `_out(snapshot)` returns all 12 in original rank order. Assert an early item preserves early fields, a late item preserves positive `chase_penalty` and `поздно / не догонять`, and `None` metrics remain `None`.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd server && pytest -q tests/test_equity_ranking_api_contract.py`
Expected: FAIL because `EquityRankingItemOut` currently drops/forbids the early fields needed by the client contract.

- [ ] **Step 3: Extend `EquityRankingItemOut` only**

Add optional/null-safe fields and aliases matching persisted snapshot keys. Do not calculate anything in the API adapter. Normalize names at the API boundary where the stored key differs from the owner contract (`momentum_5d` → `return_5d`, `momentum_20d` → `return_20d`, `momentum_3m` → `return_3m`, `momentum_6m` → `return_6m`, `breakout_distance_63d` → `breakout_distance`, `turnover_ratio_5v20` → `turnover_ratio`, `accumulation_share` → `accumulation_score`). Derive `early_eligible` only as a serialization label from persisted `early_state`: true for `ранняя подготовка` and `формируется`; false otherwise. No ranking mutation.

- [ ] **Step 4: Run focused server tests**

Run: `cd server && pytest -q tests/test_equity_ranking_api_contract.py tests/test_equity_ranking.py tests/test_equity_ranking_refresh.py`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: expose equities early radar contract`

### Task 2: Flutter ranking model parses the full early contract safely

**Files:**
- Modify: `lib/domain/research/equity_ranking.dart`
- Create: `test/equity_ranking_model_test.dart`

**Interfaces:**
- Consumes: JSON from `/api/v1/research/equity-ranking`.
- Produces: `EquityRankingItem` properties for early score/state/eligibility, rank change, why-now, confirmation/invalidation, early metrics, chase penalty and existing fundamental/technical/hypothesis fields.

- [ ] **Step 1: Write failing model tests**

Parse a 12-item payload. Assert all items remain present and ordered; `rankChange`, `earlyScore`, `earlyState`, `earlyEligible`, `whyNow`, confirmation/invalidation, return/breakout/turnover/compression/accumulation and chase fields parse correctly; JSON null stays Dart null.

- [ ] **Step 2: Run focused Flutter test and verify failure**

Run: `flutter test test/equity_ranking_model_test.dart`
Expected: FAIL because the current model does not expose these properties.

- [ ] **Step 3: Extend the existing model**

Add nullable numeric parsing for all measured fields. Keep `score`, `fundamentalScore`, and `technicalScore` existing semantics. Do not convert absent early metrics to `0` except for explicitly non-null contract values such as `chasePenalty` only if the server sends them.

- [ ] **Step 4: Run focused Flutter test**

Run: `flutter test test/equity_ranking_model_test.dart`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: parse equities early radar fields`

### Task 3: Replace narrow investment shortlist UX with full-universe expandable radar

**Files:**
- Modify: `lib/ui/screens/investment_signals_screen.dart`
- Reuse: `lib/data/api/equity_ranking_source.dart`
- Create: `test/equities_early_radar_screen_test.dart`

**Interfaces:**
- Consumes: `EquityRankingSource.load() -> EquityRankingState`.
- Produces: full-universe `Все` view and presentation filters `Ранние`, `Наблюдать`, `Поздно`, while preserving source order.

- [ ] **Step 1: Write failing widget tests**

Use a fixture with 12 equities and assert default `Все` renders ticker #1 and ticker #12, preserving order. Tap one row and assert `Почему сейчас`, `Подтверждение`, `Инвалидация`, and anti-chase/metrics render. Apply each filter and assert only visibility changes; return to `Все` and assert all 12 return. Assert null metrics render `—` rather than `0`.

- [ ] **Step 2: Run focused widget test and verify failure**

Run: `flutter test test/equities_early_radar_screen_test.dart`
Expected: FAIL because the current screen reads the limited `/equity-signals` source and renders only signal cards.

- [ ] **Step 3: Implement the owner screen**

Change the screen data source to `EquityRankingSource`. Keep the existing navigation destination/class name to avoid routing churn. Add: freshness/count intro; horizontal/lightweight filter controls; collapsed rows with `#rank`, ticker/title, overall score, early score/state and rank delta; expandable details grouped as `Почему сейчас`, `Качество идеи`, `Подтверждение`, `Инвалидация`, `Динамика`, `Предупреждения`. Keep all rows in server order. Late state is visually explicit but never hidden in `Все`.

- [ ] **Step 4: Run focused Flutter tests and analyze**

Run: `flutter test test/equity_ranking_model_test.dart test/equities_early_radar_screen_test.dart && flutter analyze`
Expected: PASS/no analyzer errors.

- [ ] **Step 5: Commit**

Commit message: `feat: show full-universe equities early radar`

### Task 4: Regression, PR, release and exact-SHA delivery

**Files:**
- No product files unless a regression requires a surgical fix.

**Interfaces:**
- Consumes: completed branch `codex/equities-early-radar-universe`.
- Produces: green PR merged to `claude/release-y40hk5`, cumulative production deploy, and signed thin-client sideload APK from the exact merge SHA.

- [ ] **Step 1: Run full local/CI-equivalent verification available in repository workflows**

Verify server suite, Flutter tests/analyze, and secret scan. Do not claim success from partial checks.

- [ ] **Step 2: Review diff scope**

Compare branch against `claude/release-y40hk5`. Expected changed scope: design/plan, equity-ranking API contract/test, Flutter ranking model/test, investment-signals screen/widget test only.

- [ ] **Step 3: Open PR**

PR title: `P0: expose full-universe Russian equities early radar`.

- [ ] **Step 4: Wait for and inspect Quality Gate**

If red, inspect the failing job/log, make the smallest correction, and rerun until green.

- [ ] **Step 5: Merge with release trigger**

Merge title must contain `[final-release]` so the established dispatcher triggers cumulative release from the exact merged SHA.

- [ ] **Step 6: Verify production deploy and sideload workflows**

Confirm deployment health and `execution_mode=PAPER`, `paper_only=true`. Confirm Android artifact provenance/source SHA/signature. Do not call protected unauthenticated probe 401s production failures.

- [ ] **Step 7: Deliver APK**

Download the Actions artifact, extract `signalai-sideload.apk`, verify the artifact belongs to the exact merged SHA, and provide the sandbox download link.
