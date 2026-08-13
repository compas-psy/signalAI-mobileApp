# SignalAI Foundation Design

Date: 2026-08-13
Status: approved by owner in chat before implementation

## Goal

Create a project operating system that keeps SignalAI development aligned to risk-adjusted Equity growth, prevents accidental security/trading regressions, and stops production delivery from firing on every default-branch push.

## Scope

This foundation slice changes governance and CI orchestration only. It does not change signal generation, scoring, risk sizing, broker behavior, paper lifecycle, portfolio logic, or notification behavior.

## Source of truth

The repository uses this order:

1. `AGENTS.md` for mandatory project rules;
2. `docs/ROADMAP.md` for current stage and priorities;
3. accepted ADRs for durable decisions;
4. current issues/PR and default-branch code;
5. supporting development/API/build/security docs.

`HANDOFF.md` remains historical context and no longer defines the current roadmap.

## Product objective

The engineering objective is long-term risk-adjusted Equity growth after realistic costs under drawdown, risk-of-ruin, liquidity and security constraints. No component may encode an assumption of guaranteed monotonic Equity growth.

## Development policy

One logical vertical slice uses one short-lived branch and one coherent PR. External SDK/API work checks current Context7 documentation when available and provider official docs for broker/exchange/security-sensitive behavior. Superpowers are used selectively for debugging, TDD, verification and review; Codex Security is used for security-sensitive changes.

## Architecture decisions

Introduce `docs/adr/` with a template and first accepted ADR defining the promotion pipeline:

`Research → Backtest → OOS/Walk-forward → Shadow → Paper → Sandbox/Testnet → Canary Live → Scaled Live`.

The pipeline separates evidence of edge from execution correctness and security readiness.

## Delivery design

Default-branch pushes must not automatically deploy production or publish a signed APK. Production delivery is an explicit action after a logical batch is accepted.

The cumulative orchestrator is manual-only and delegates exact `source_ref` to the existing canonical VPS and Android delivery workflows no more than once each. Their existing safety/quality gates remain intact.

A later CI improvement should collapse duplicate full QA and selectively skip an unchanged runtime surface; that optimization is explicitly out of this slice.

## Verification

- Governance files exist and are internally consistent.
- Release workflow trigger is `workflow_dispatch`, not `push`.
- Workflow delegates one server and one Android delivery for an explicit source ref.
- No product/runtime source file is changed.
- PR Quality Gate must pass before merge.
- Merging the foundation PR must not automatically trigger the new cumulative production release.
