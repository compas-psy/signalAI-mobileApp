# Portfolio Issuer Concentration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the remaining Stage 3 portfolio-risk gap by enforcing issuer-level concentration only when a reliable MOEX issuer identity is available, without inferring identity from ticker or display name.

**Architecture:** Extend the MOEX investment-universe adapter to ingest an explicit issuer identity from exchange metadata and persist it in `Instrument.metadata_json`. Portfolio construction will aggregate candidate/position weights by that identity and apply the existing per-name concentration ceiling at issuer level; instruments without reliable issuer identity remain fail-closed for issuer aggregation rather than guessed. Existing class/crypto caps, as-of rules, research guardrails and manual execution remain unchanged.

**Tech Stack:** Python, FastAPI service, SQLAlchemy/PostgreSQL JSONB, MOEX ISS, pytest, existing portfolio optimiser.

## Global Constraints

- Do not infer issuer identity from ticker, security name, or title.
- Preserve strict as-of portfolio behavior merged in PRs #89/#90.
- Preserve existing class/crypto caps and research-evidence rules.
- No broker/execution/live-trading behavior changes.
- TDD RED→GREEN and exact-head Quality Gate are required before merge.
- Keep changes surgical; no unrelated refactors.

---

### Task 1: Ingest reliable issuer identity from MOEX

**Files:**
- Modify: `server/app/market/moex.py`
- Modify: `server/app/market/investments.py`
- Test: existing MOEX/investment-universe tests under `server/tests/`

**Interfaces:**
- Consumes: MOEX ISS security metadata for investment boards.
- Produces: `Instrument.metadata_json["issuer_id"]` only when an explicit exchange-provided issuer identity is present.

- [ ] Write a failing adapter/universe test proving two securities can carry the same explicit issuer identity and that missing identity stays missing.
- [ ] Run the focused test and verify RED.
- [ ] Add the minimal MOEX field ingestion and persistence; do not derive identity from names/tickers.
- [ ] Run focused tests and verify GREEN.
- [ ] Commit the slice.

### Task 2: Enforce issuer-level concentration in portfolio construction

**Files:**
- Modify: `server/app/portfolio/build.py`
- Modify only if required by existing constraint interface: `server/app/portfolio/stats.py`
- Test: existing portfolio build/stats tests under `server/tests/`

**Interfaces:**
- Consumes: explicit `metadata_json.issuer_id` from Task 1.
- Produces: portfolio weights where the aggregate weight of securities sharing an issuer cannot exceed the existing single-name concentration ceiling.

- [ ] Write a failing regression test with two distinct instruments sharing one issuer and demonstrate aggregate issuer exposure can currently breach the ceiling.
- [ ] Run the focused test and verify RED.
- [ ] Implement minimal issuer grouping in the existing optimiser constraint path.
- [ ] Add a regression proving unrelated issuers remain independent and missing issuer identity is never guessed.
- [ ] Run focused tests and verify GREEN.
- [ ] Commit the slice.

### Task 3: Stage 3 acceptance and roadmap bookkeeping

**Files:**
- Modify only focused tests/docs if needed.
- Update GitHub issue #86 after merge.

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: completed Stage 3 policy/risk gate ready for Stage 4 lifecycle work.

- [ ] Run portfolio and investment-universe focused suites.
- [ ] Run the repository Quality Gate on the exact head and require full GREEN.
- [ ] Review diff for accidental trading/execution changes and unrelated churn.
- [ ] Merge through a focused PR.
- [ ] Record Stage 3 completion on issue #86 with PR, merge SHA and Quality Gate evidence.
- [ ] Do not trigger cumulative VPS+sideload release yet unless this is chosen as the meaningful release checkpoint; otherwise bundle it with the next lifecycle milestone per the established cumulative-release rule.
