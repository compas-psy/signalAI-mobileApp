# Owner Cockpit UX Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SignalAI answer “what do I own, what is open, what is waiting, and what needs me?” without hunting through empty filters.

**Architecture:** Keep the existing five top-level sections and server-owned trading lifecycle. Rebuild Today as an owner cockpit fed by CapitalState, server paper positions, and server ideas; rebuild Ideas as one lifecycle funnel with optional filters. Do not reimplement trading decisions on-device.

**Tech Stack:** Flutter/Dart, existing AppController/EngineClient/CapitalDesk, GitHub Actions quality gate.

## Global Constraints

- Production remains thin-client and server-owned for idea/trade lifecycle.
- Pending paper orders and open paper positions must be distinct in UI.
- “Наблюдение” must disappear from short-term trading UI; use “Формируются”.
- Completed objects stay in Journal.
- Capital sync must use existing broker access and never invent a cross-currency total.
- Make surgical changes only; no unrelated refactors.

---

### Task 1: Lock the new information architecture with tests

**Files:**
- Modify: `test/app_flow_test.dart`
- Create: `test/ideas_funnel_test.dart`

**Interfaces:**
- Consumes: `SignalAiApp`, `AppSection`, `IdeasPill`, demo/server fixtures.
- Produces: regression expectations for owner cockpit labels, funnel labels, and pending/open separation.

- [ ] Add failing widget tests that expect Today to expose `Капитал`, `Ждут входа`, `Позиции открыты`, `Нужно решить`, and `Формируются` without empty KPI placeholders.
- [ ] Add failing unit/widget tests for funnel labels `Все`, `Нужно решить`, `Формируются`, `Ждут входа`, `Позиции открыты`.
- [ ] Add failing tests that classify `PaperPositionStatus.pending` separately from `PaperPositionStatus.open`.
- [ ] Run the PR quality gate and confirm mobile tests fail for the missing UX.

### Task 2: Replace idea filters with lifecycle funnel semantics

**Files:**
- Modify: `lib/state/navigation.dart`
- Modify: `lib/ui/screens/server_ideas_screen.dart`
- Modify: `lib/ui/screens/ideas_screen.dart`
- Modify: `lib/ui/app_shell.dart`
- Modify: `lib/ui/widgets/section_header.dart`

**Interfaces:**
- Consumes: `Idea.readiness`, `Idea.actionable`, `PaperPosition.status`.
- Produces: `IdeasPill { all, decisions, forming, pending, open }` and dynamic pill labels/counts.

- [ ] Implement five lifecycle filters with `all` as the default entry.
- [ ] Keep undecided ideas separate from live paper trades by idea id/symbol as today.
- [ ] Filter pending and open trades by explicit `PaperPositionStatus`.
- [ ] Render all groups in the `Все` view and omit zero-sized groups instead of rendering empty screens.
- [ ] Add counts to Ideas pills while preserving static labels for other sections.
- [ ] Update non-thin `IdeasScreen` to the same terminology/ordering without changing domain decisions.

### Task 3: Rebuild Today as owner cockpit

**Files:**
- Modify: `lib/ui/screens/today_screen.dart`
- Modify: `lib/state/app_controller.dart`

**Interfaces:**
- Consumes: `CapitalState`, `CapitalState.accounts`, `paperPositions`, `ideas`, `RiskCenter`.
- Produces: compact capital strip, pending/open trade sections, decisions, forming candidates, compact daily risk/result.

- [ ] Load cached capital immediately, then start broker reconciliation in the background on app load and whenever Today is entered.
- [ ] Build capital summary from account-level state: show T‑Investments and Bybit separately; show a combined total only when the existing `CapitalState.totalEquity` is honest for the base currency.
- [ ] Show `Позиции открыты` from `PaperPositionStatus.open` and `Ждут входа` from `PaperPositionStatus.pending`.
- [ ] Show `Нужно решить` only when actionable undecided ideas exist.
- [ ] Show top forming candidates from waiting undecided ideas with explicit “ждём триггер” copy.
- [ ] Collapse daily risk/result into a compact summary and remove large empty metric tiles.

### Task 4: Verify and deliver

**Files:**
- Modify tests only if failures reveal a genuine contract mismatch.

**Interfaces:**
- Consumes: GitHub Actions `Quality gate`, Android sideload workflow.
- Produces: green PR and cumulative sideload APK at the release checkpoint.

- [ ] Run the full PR quality gate: secret scan, server tests, `flutter analyze`, `flutter test`.
- [ ] Review the final PR diff for accidental trading-logic changes.
- [ ] Merge only after green verification.
- [ ] Trigger the existing cumulative/sideload Android pipeline and record the exact artifact/run.