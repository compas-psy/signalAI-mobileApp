# Investment Portfolio Evidence Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect mature long-horizon research hypotheses to the existing deterministic portfolio builder without turning early research into automatic trades or bypassing portfolio risk constraints.

**Architecture:** Add a read-only portfolio research-evidence adapter that selects only as-of-safe mature hypothesis versions, reduces them to a bounded signed conviction per instrument, and combines that adjustment with the existing fundamental score only for equity candidate ranking. Persist the exact hypothesis provenance and score decomposition on `PortfolioWeight`, while the existing optimizer, class caps, walk-forward admission, rebalance API and manual execution boundary remain unchanged.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL, Alembic, pytest, existing NumPy portfolio optimizer.

## Global Constraints

- Automatic portfolio influence is allowed only for `CONFIRMED` and `DILIGENCE_READY` research hypotheses.
- `EARLY_CANDIDATE` remains research/watchlist evidence and never changes automatic portfolio ranking.
- Evidence is usable only when `hypothesis.as_of <= build_as_of` and `expires_at` is absent or later than `build_as_of`.
- Only the latest usable version for each hypothesis fingerprint contributes.
- Research impact is bounded to ±0.20 of the equity screening score; risk/class/crypto constraints remain authoritative.
- Missing research evidence preserves existing fundamental-only behavior exactly.
- Portfolio remains advisory/manual; no broker order or live execution path is added.
- Every production code slice follows RED → GREEN and exact-head Quality Gate before merge.

---

### Task 1: Deterministic research evidence adapter

**Files:**
- Create: `server/app/portfolio/research_evidence.py`
- Test: `server/tests/test_portfolio_research_evidence.py`

**Interfaces:**
- Produces: `evidence_for(session, instrument_ids, *, as_of) -> dict[str, PortfolioResearchEvidence]`
- Produces: `PortfolioResearchEvidence.adjust(fundamental_score) -> float`
- Produces: serializable provenance via `PortfolioResearchEvidence.as_json()`.

- [x] Write failing tests proving mature-state gating, as-of/expiry gating, latest-version-only behavior, bounded positive/negative adjustment and unchanged score without evidence.
- [x] Run the test-only branch in Quality Gate and capture the expected RED failure (`ModuleNotFoundError: app.portfolio.research_evidence`).
- [x] Implement the minimal adapter. Compute per-hypothesis conviction as `(evidence_score + economic_score) / 2`, multiply `CONFIRMED` by `0.75` and `DILIGENCE_READY` by `1.0`, apply direction sign, average latest-fingerprint contributions per instrument, and clamp aggregate conviction to `[-1, 1]`.
- [x] Define `combined_score = clamp(fundamental_score + 0.20 * signed_conviction, 0, 1)`.
- [ ] Re-run focused and full tests on the final implementation head.

### Task 2: Persist auditable score decomposition

**Files:**
- Modify: `server/app/models/portfolio.py`
- Create: `server/alembic/versions/0014_portfolio_research_evidence.py`
- Modify: `server/app/portfolio/build.py`
- Test: `server/tests/test_portfolio_research_evidence.py`, `server/tests/test_portfolio_research_screen.py`, `server/tests/test_portfolio_research_persistence.py`

**Interfaces:**
- Adds: `PortfolioWeight.evidence_json: dict`.
- `PortfolioWeight.score` stores the combined screening score for equity positions; `evidence_json` contains `fundamental_score`, `research_adjustment`, `combined_score`, `signed_conviction`, and the contributing hypothesis descriptors.

- [x] Write failing persistence/model-parity tests.
- [x] Add the JSONB column and reversible Alembic migration.
- [x] Pass a fixed build `as_of` through `build_all`; fetch research evidence once for screened equity candidates.
- [x] Sort equity candidates by combined evidence-aware score; leave every non-equity class on the current fundamental score.
- [x] Feed the combined score and evidence into built positions and persisted `PortfolioWeight.evidence_json`.
- [x] Prove missing evidence produces the current candidate score unchanged.

### Task 3: Integration acceptance and merge

**Files:**
- Modify only tests/docs if verification reveals a contract issue.

- [x] Verify no execution/broker code changed.
- [x] Verify portfolio risk constraints and walk-forward admission are untouched.
- [ ] Run exact-head Quality Gate: server imports/migration parity/PostgreSQL pytest, Flutter analyze/tests, secret scan and release-attestation guard.
- [x] Audit the PR diff for unrelated changes; unrelated comment/formatting churn was restored before the final gate.
- [ ] Merge only if exact-head is green.
- [ ] Add evidence to issue #86 and continue to roadmap stage 2 without a production release; this slice is not yet a user-visible milestone large enough for a cumulative VPS+sideload release.
