# Stage 5 Owner Portfolio UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the mobile Portfolio screen consume the server-owned three-profile headline contract instead of deriving owner choices from the internal package matrix.

**Architecture:** Add a focused headline domain model and parser next to the existing package contract. Extend `EngineClient` with an exact-horizon headlines read, then route the packages section of `PortfolioScreen` to an owner-facing widget that renders those slots while preserving the existing horizon control and package detail/rebalance capabilities.

**Tech Stack:** Flutter/Dart, existing `ApiClient`/`EngineClient`, existing package domain/widgets, FastAPI backend contract already merged in Stage 4.

## Global Constraints

- The server is the source of truth for which package represents each risk profile.
- The mobile owner screen exposes exactly three choices: «Консервативный», «Сбалансированный», «Доходный».
- Never silently substitute another profile, internal package variant, or horizon.
- Preserve explicit `ready`, `riskier_than_target`, `missing`, and unavailable states.
- Keep existing `/portfolio/packages` support for package details/rebalance compatibility; do not use it to choose owner headlines.
- Use TDD and run the full repository Quality Gate on the exact final head.

---

### Task 1: Headline client contract

**Files:**
- Create: `test/portfolio_headlines_client_test.dart`
- Create: `lib/domain/portfolio/headline.dart`
- Modify: `lib/data/api/engine_contract.dart`
- Modify: `lib/data/api/engine_client.dart`

**Interfaces:**
- Produces: `PortfolioHeadlines`, `PortfolioHeadline`, `PortfolioHeadlineStatus`, `EngineClient.portfolioHeadlines({required int horizonYears})`.

- [ ] Write the failing client test for exact endpoint, ordered labels, and explicit states.
- [ ] Run the test and verify it fails because `portfolioHeadlines`/headline domain types do not exist.
- [ ] Implement minimal headline domain parsing and client method using `/api/v1/portfolio/headlines?horizon_years=N`.
- [ ] Run focused tests to GREEN and commit.

### Task 2: Controller lifecycle by exact horizon

**Files:**
- Modify: `lib/state/app_controller.dart`
- Test: existing/new controller test near portfolio state tests.

**Interfaces:**
- Produces controller state for headline result/loading and a loader keyed by current `PackageHorizon`.

- [ ] Write failing tests showing a horizon change requests headlines for that horizon.
- [ ] Implement minimal loading/state logic without changing unrelated portfolio state.
- [ ] Run focused tests to GREEN and commit.

### Task 3: Three owner-facing portfolio cards

**Files:**
- Create: `lib/ui/screens/portfolio_owner_screen.dart`
- Modify: `lib/ui/screens/portfolio_screen.dart`
- Reuse: existing package summary/detail widgets where practical; avoid copying optimizer-selection logic.
- Test: new widget contract test.

**Interfaces:**
- Consumes: controller headline state and current package horizon.
- Produces: exactly three persistent owner slots with status-aware summaries.

- [ ] Write failing widget test asserting exactly three labels and explicit riskier/missing state copy.
- [ ] Implement owner screen cards and route `PortfolioPill.packages` to them.
- [ ] Keep the existing horizon segmented control above the new content.
- [ ] Run focused widget tests to GREEN and commit.

### Task 4: Regression and release gate

**Files:**
- Only surgical fixes required by actual regression failures.

- [ ] Run Flutter analyze and full Flutter test suite.
- [ ] Run/observe full GitHub Quality Gate: server imports/migrations/pytest, Flutter analyze/tests, secret scan, release attestation.
- [ ] Review final PR diff for unrelated changes.
- [ ] Merge only from the exact GREEN head and update roadmap issue #86.
