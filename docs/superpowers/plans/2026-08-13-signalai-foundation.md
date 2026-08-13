# SignalAI Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the SignalAI project operating system and make production delivery explicit/cumulative instead of push-driven.

**Architecture:** Governance lives in repository-scoped docs (`AGENTS.md`, roadmap, ADRs, development process). CI keeps existing canonical VPS/APK delivery workflows and adds a thin manual orchestrator; product/runtime logic is untouched.

**Tech Stack:** Markdown, GitHub Actions YAML, existing GitHub workflows.

## Global Constraints

- No trading strategy, scoring, risk sizing, broker execution, paper lifecycle or notification behavior changes.
- No production release during this foundation slice.
- One branch and one PR for the whole foundation slice.
- Exact source provenance remains mandatory for delivery.
- Security-sensitive details remain governed by current code/docs and ADR; do not add secrets.

---

### Task 1: Project governance

**Files:**
- Modify: `AGENTS.md`
- Create: `docs/ROADMAP.md`
- Modify: `docs/DEVELOPMENT_PROCESS.md`

- [ ] Add SignalAI-specific objective, money/security invariants, Context7/skills policy, source-of-truth order and cumulative delivery rule.
- [ ] Add roadmap with P−1, P0, P0.5, P1, P2 and P3 stages.
- [ ] Align development process with one branch/PR per logical slice and explicit production release.

### Task 2: ADR system and live promotion gate

**Files:**
- Create: `docs/adr/README.md`
- Create: `docs/adr/0000-template.md`
- Create: `docs/adr/0001-live-promotion-gates.md`

- [ ] Add ADR lifecycle/status rules and template.
- [ ] Record accepted promotion pipeline `Research → Backtest → OOS/Walk-forward → Shadow → Paper → Sandbox/Testnet → Canary Live → Scaled Live`.
- [ ] Include operational correctness, security gate, owner approval, canary sizing and demotion conditions.

### Task 3: Stop push-driven production delivery

**Files:**
- Modify: `.github/workflows/release-cumulative.yml`

- [ ] Replace the default-branch `push` trigger with `workflow_dispatch` and required `source_ref`.
- [ ] Run the existing full Quality Gate before delivery dispatch.
- [ ] Dispatch existing VPS and Android canonical workflows at most once each for that logical release.
- [ ] Do not embed credentials or SSH/deploy implementation in the orchestrator.

### Task 4: Tracker hygiene

**Files:** GitHub issues only.

- [ ] Update issue #6 so it no longer references merging closed PR #3 and focuses on current device/runtime acceptance.
- [ ] Update issue #4 to remove stale dependency on PR #3 while retaining HIRING/SPREAD acceptance criteria.
- [ ] Create P0 reliability/observability issue.
- [ ] Create P0.5 performance measurement issue.
- [ ] Create CI follow-up for single-QA/selective-delivery optimization.

### Task 5: Verification and PR

- [ ] Compare branch to `claude/release-y40hk5` and confirm no product runtime files changed.
- [ ] Inspect final workflow and confirm manual trigger plus one dispatch per canonical delivery workflow.
- [ ] Open one PR to `claude/release-y40hk5` describing scope, non-scope and known CI follow-up.
- [ ] Verify PR Quality Gate status; do not merge while required checks are red or missing.
