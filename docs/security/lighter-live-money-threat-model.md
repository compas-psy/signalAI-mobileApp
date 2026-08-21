# Threat model: Lighter Canary live-money boundary

Status: readiness model; no live credential, capital, mainnet call or activation
is authorized by this document.

Scope baseline: `22a94f2281daec99fc87a316114da4fe25077b61`
(default after merged PR #201). This narrow model complements repository-wide
[`SECURITY.md`](../../SECURITY.md) and ADR-0001. The required future boundary is
specified by proposed [ADR-0002](../adr/0002-lighter-canary-boundary.md).

## Overview

SignalAI is a single-owner server-authoritative trading system. For Lighter, the
current repository has separate read/testnet/live credential slots, explicit
mainnet/testnet metadata, durable order identity/nonce/action/reconciliation
primitives, stepwise execution modes, a kill switch and automatic lower-risk
actions. SAI-075 testnet admission is bound to the verified testnet transport and
always reports `eligible_for_live=False`.

The current production state is fail-closed: `risk.paper_only=true`, real
promotion evidence is unwired, and no Lighter provider factory/worker is connected
to CANARY/LIVE. The security objective is to preserve those boundaries while a
future SANDBOX→CANARY path introduces the smallest preapproved real-money scope.

Protected assets are the live signing key, account/positions/capital, order and
protection identity, immutable canary policy, owner authority, kill-switch state,
reconciliation evidence, append-only audit and exact deployed source.

## Threat model, trust boundaries and assumptions

### Actors

- **Owner:** chooses capital/caps/allowlist, provisions/revokes live credentials,
  confirms activation and recovery.
- **Android thin-client:** presents server facts and sends owner intent; it is not
  authoritative for evidence, risk, account identity or mode.
- **SignalAI server/PostgreSQL:** enforces policy, serializes money operations and
  stores secrets/evidence. A compromised process/DB is a high-impact threat.
- **Lighter:** supplies provider facts and executes signed actions; its responses
  are untrusted until parsed, scoped and reconciled.
- **Developer/CI/operator:** can change source, configuration and delivery inputs;
  exact-SHA review and protected secrets constrain this power.
- **Attacker:** may know the public API address, send arbitrary network/provider
  payloads, steal/replay a device bearer, exploit a server/dependency, induce
  network ambiguity, obtain CI/developer access or influence market conditions.

### Trust boundaries

1. **Owner ↔ Android:** device storage and owner presence. A stored bearer proves
   API possession, not necessarily fresh intent for first live activation.
2. **Android ↔ API:** all request fields/headers are untrusted claims. Promotion
   proof, caps, config/account identity and provider outcomes are server-owned.
3. **Server ↔ secret store:** raw key material may cross only inside the server
   worker boundary. Audit/API/logs expose metadata, never the key.
4. **Server ↔ Lighter REST/WebSocket:** endpoint, chain, account, API key,
   transport instance and provider facts must match the active credential/policy.
5. **Policy/config ↔ submit:** mutable runtime input may request less risk but
   cannot expand the immutable owner-approved snapshot.
6. **Kill switch/mode ↔ submit:** safety mutation and provider submission must be
   serialized so no order slips through after a halt.
7. **Source/CI ↔ production:** only the exact reviewed SHA and canonical delivery
   may become runtime; merge or a successful unrelated scan is insufficient.

### Assumptions that require verification

- Lighter live API keys can be restricted to required read/trade permissions and
  cannot withdraw; IP restriction is used if officially supported.
- Official mainnet endpoint/chain and provider credential-check semantics are
  revalidated against current provider documentation before implementation.
- PostgreSQL append-only triggers and advisory locks remain active in production.
- Server time is UTC-synchronized sufficiently to enforce activation/evidence TTL.
- The owner selects numerical limits, exact allowlist, TTL and step-up mechanism;
  agents do not infer them.

## Attack surface, mitigations and attacker stories

| ID | Attacker story / failure | Existing mitigation | Required before Canary |
| --- | --- | --- | --- |
| LM-01 | Stolen device bearer replaces `lighter_trade`, activates Canary or clears safety state. | API is bearer-protected; secrets are write-only. Promotion evidence is currently false. | Owner-sensitive step-up for credential mutation, kill-switch clear and SANDBOX→CANARY; append-only credential-generation audit. |
| LM-02 | Testnet credential/session or successful SAI-075 smoke is reused against mainnet. | Typed credential slots; explicit endpoints; SAI-075 checks testnet endpoint/chain/account/key/transport and is never live-eligible. | Mainnet preflight independently binds live slot, official endpoint/chain, current generation and exact transport; no conversion of testnet READY into live authority. |
| LM-03 | Credential is rotated after preview; confirmation or worker uses stale account/key. | Generic LIVE preview rechecks displayed context/config, but no credential generation exists. | Opaque generation ID changes on create/replace/delete; preview, confirmation and every submit recheck it and halt on mismatch. |
| LM-04 | Client raises capital/caps, changes allowlist or supplies green evidence. | Risk/promotion are server-owned; current evidence provider is fail-closed. | Immutable canonical policy/hash; default-deny server allowlist; authoritative durable evidence provider; extra client fields rejected/ignored as authority. |
| LM-05 | A valid preview is replayed later or after context drift. | Existing CANARY→LIVE flow has durable idempotency and fresh config/context check. | Dedicated SANDBOX→CANARY two-step challenge, short TTL, single use, step-up and recheck of source/config/policy/credential/evidence/mode/kill switch. |
| LM-06 | Concurrent halt/config rotation races with provider submit. | Kill-switch service owns an execution-control advisory lock; automatic safety is one-way. | All Canary claims/submits use the same serialization and re-read active snapshot/generation under it; failure halts before network I/O. |
| LM-07 | Ambiguous timeout causes duplicate CREATE or nonce drift. | Stable client identity, durable nonce reservation/action binding and reconciliation exist; SAI-075 recovery never replays CREATE. | Live worker reuses these exact primitives, treats ambiguity as blocked/reconcile, and never allocates a replacement identity/nonce to “retry” the same intent. |
| LM-08 | Order opens without durable protection, outside allowlist or beyond caps. | Protection/action facts and provider-neutral risk controls exist; no live worker exists. | Pre-submit allowlist/cap check plus post-submit protection deadline/reconciliation; breach immediately HALTs and demotes per policy. |
| LM-09 | WebSocket/provider payload spoofs another account/order or leaks secrets through an exception. | Parsers validate account/market fields; SAI-075 public errors are sanitized. | Live path scopes every fact to active account/key/order identity; public/log/audit exceptions have no raw payload, secret, cause/context. |
| LM-10 | Operator/developer deploys unreviewed code or changes config after owner confirmation. | Exact-source cumulative delivery and pinned VPS SSH authenticity exist. | Preview binds source SHA and config/policy hashes; runtime mismatch halts; scan and release evidence refer to the same accepted SHA. |
| LM-11 | Attacker rewrites activation/demotion/reconciliation history to hide a loss. | Audit, mode events and Lighter evidence have DB append-only protections. | One correlation chain links generation, policy, preflight, challenge, mode, orders, reconciliation and demotion; corrections append new facts. |
| LM-12 | Provider degradation accumulates risk while automation remains green. | Automatic halt/downshift primitives exist and cannot promote/clear safety. | Owner-approved freshness/error/protection/reconciliation thresholds and a tested demotion runbook; recovery is owner-only. |

### Highest-value attack paths

1. **Bearer theft → credential substitution → stale approval → mainnet submit.**
   Break at step-up, generation binding, policy hash and fresh submit-time checks.
2. **Config/allowlist bypass → oversized or wrong-market order.** Break at immutable
   snapshot, default-deny mapping and atomic cap enforcement.
3. **Network ambiguity → duplicate CREATE → unprotected exposure.** Break at stable
   identity/nonce, reconciliation-first recovery and protection deadline.
4. **Halt race → order after stop.** Break by sharing execution serialization and
   re-reading kill switch immediately before provider I/O.
5. **Compromised log/audit path → signing-key disclosure.** Break by structured
   non-secret metadata only and secret-safe exceptions without cause/context.

### Out-of-scope stories that do not suppress integration findings

Provider-internal solvency/protocol defects, physical coercion of the owner and
profitability of the strategy are not defects in this repository by themselves.
SignalAI still must fail closed on malformed/out-of-scope provider facts, keep
capital bounded, expose degradation and preserve recovery evidence.

## Severity calibration

- **Critical:** unauthorized live signing/submit, withdrawal-capable key exposure,
  reliable cap/kill-switch bypass, or production-source compromise leading
  directly to uncontrolled capital loss.
- **High:** credential/account substitution, duplicate live CREATE, stale owner
  challenge accepted, default-allow market routing, missing protection accepted,
  reconciliation ambiguity treated as success, or automatic promotion/recovery.
- **Medium:** incomplete non-secret audit correlation, bounded metadata exposure,
  or safety-monitoring DoS that does not bypass fail-closed state.
- **Low:** defense-in-depth improvement with no realistic path to secrets,
  authority, incorrect order or loss of forensic evidence.

The absence of a current live worker lowers present reachability but does not
lower the severity of a defect introduced into the activation/submit boundary.
Tests demonstrate intended behavior, not production effectiveness.

## Unresolved owner decisions

- exact canary capital, currency/valuation policy and every numerical hard cap;
- exact market/instrument allowlist, strategy version and canary validity/order budget;
- activation challenge TTL and owner step-up mechanism;
- provider-error/freshness/reconciliation thresholds and demotion target;
- recovery authority and whether recovery requires a fresh two-step challenge;
- accepted residual risks after the exact-head SAI-080 scan.

Repository: codex-security-target/v1:sha256:17677bbe4517eb48b6c0dd68f672d7f87de9748dc1c6447e7957a1b5c64b452c
Version: 22a94f2281daec99fc87a316114da4fe25077b61
