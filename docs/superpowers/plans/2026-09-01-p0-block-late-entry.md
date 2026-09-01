# P0 Block Late Entry Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A FORTS idea that the live market has already invalidated for a new entry (target/stop reached or target reached before confirmed entry) must be impossible to approve as a new paper trade, and the mobile UI must show that the opportunity is missed instead of offering confirmation.

**Architecture:** Keep strategy generation and immutable idea snapshots unchanged. Treat live market path as execution/admission state. The server is the authority at the money boundary: immediately before `/approve-paper`, recompute fresh FORTS market progress and fail closed if a new entry is late. The Flutter client receives the same progress already used by the chart and lifts it to the detail screen so the action bar mirrors the server verdict.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, pytest, Flutter/Dart.

---

### Task 1: Lock the server money boundary with a regression test

**Files:**
- Create: `server/tests/test_late_entry_admission.py`
- Test: `server/tests/test_late_entry_admission.py`

**Step 1: Write the failing test**

Create an actionable FORTS idea (`ACTIVE` + `TRIGGERED`), stub fresh MOEX 10m candles so TP2 trades after signal before the planned entry, POST `/api/v1/ideas/{id}/approve-paper`, and assert:
- HTTP 409;
- response explains the entry is late/missed;
- no `PaperTrade` exists.

Add a second regression proving a normal untouched FORTS idea is still approvable, so the safety fix cannot silently disable all approvals.

**Step 2: Verify RED**

Run the targeted test in CI / repository test environment. The late-entry test must fail against current code because approval currently ignores `market-progress`.

### Task 2: Reuse fresh FORTS progress at approval

**Files:**
- Modify: `server/app/api/v1/idea_progress.py`
- Modify: `server/app/api/v1/ideas.py`
- Test: `server/tests/test_late_entry_admission.py`

**Step 1: Add one reusable fresh-progress loader**

Extract the existing instrument lookup + guarded 10m candle load into a helper that returns the same `IdeaMarketProgress` used by the read endpoint. Do not change strategy score, idea creation, or immutable lifecycle data.

**Step 2: Add fail-closed approval admission for FORTS**

Immediately before paper trade creation, after the existing supervisor refresh, request fresh FORTS progress. Reject with HTTP 409 when any proven late-entry condition is present (`late`, target reached, stop reached, `MISSED_BEFORE_ENTRY`). If fresh FORTS market data cannot be established at the money boundary, reject rather than creating a trade from stale knowledge. Unsupported venues keep their current flow until they have equivalent live-progress support.

**Step 3: Verify GREEN**

Run the targeted regression test and relevant existing idea-progress/API tests.

### Task 3: Make mobile action state use the same live progress

**Files:**
- Modify: `lib/ui/widgets/idea_chart_card.dart`
- Modify: `lib/ui/screens/idea_detail_screen.dart`
- Test: relevant Flutter widget/unit tests (create a focused test if no suitable one exists)

**Step 1: Write failing UI regression**

Cover the contradiction from the owner screenshot: live progress says `blocksNewEntry=true`, therefore the detail screen must not expose an enabled paper-confirm action and must display a missed/blocked verdict.

**Step 2: Lift progress out of the chart widget**

Add an optional progress callback to `IdeaChartCard`; in `IdeaDetailScreen` keep current progress for the selected idea, clear it on idea change, and include `blocksNewEntry` in the confirmation gate.

**Step 3: Make wording unambiguous**

When live progress blocks a new entry, show `Сделка упущена` / `Вход запрещён` and the server/progress summary. Keep score/R:R as original setup characteristics, not as a current permission to trade.

**Step 4: Verify GREEN**

Run the focused Flutter test plus affected widget/domain tests.

### Task 4: Quality gate and review

**Files:**
- No speculative refactors.

**Step 1: Run server regression suite relevant to ideas, progress, owner flow and paper lifecycle.**

**Step 2: Run Flutter analyze/tests required by project Quality Gate.**

**Step 3: Review the diff specifically for money-boundary fail-closed behavior and accidental strategy changes.**

**Step 4: Open one PR from `sol/p0-block-late-entry` to `claude/release-y40hk5`. Do not release/deploy yet; production delivery is a separate explicit owner decision.**
