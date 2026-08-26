# ADR-0002: Lighter Canary live-money boundary

- Status: **accepted for Canary v1 policy; final real-money activation pending**
- Date: 2026-08-21
- Owner approval: 2026-08-26
- Owners: Product owner + Integrator/CTO + Security/Execution reviewer
- Extends: [ADR-0001](0001-live-promotion-gates.md)

## Decision

SignalAI keeps one execution core and adds an immutable, fail-closed Canary policy
on top of the existing mode, kill-switch, replay, reconciliation and audit
primitives. Approval of this ADR **does not** itself authorize funding, mainnet
provider calls, `paper_only=false` or real orders. Final activation is a separate
owner action bound to the exact source/config/policy/evidence payload.

## Owner-approved Canary v1 envelope

The owner approved a USD 100 economic risk budget. Lighter perpetual collateral is
implemented as **100 USDC**; the final activation view must show `100 USDC`
explicitly before any real-money transition.

| Control | Canary v1 |
| --- | --- |
| Venue/environment | Lighter mainnet |
| Scope | exactly one Lighter BTC perpetual instrument; market index resolved from authoritative provider mapping |
| Strategy | one frozen deterministic PAPER champion/version/digest only |
| Capital | 100 USDC |
| Max order notional | 10 USDC |
| Max instrument notional | 25 USDC |
| Max gross notional | 25 USDC |
| Max open positions | 1 |
| Max concurrent entry orders | 1 |
| Max leverage | 1x |
| Daily realized + unrealized loss stop | 3 USDC |
| Total Canary loss stop | 7 USDC |
| Max order submissions/day | 20 |
| Max completed trades/day | 6 |
| Owner step-up TTL | 300 seconds |
| Automatic resume after HALT | forbidden |
| Slippage hard ceiling | 30 bps/execution; initial median target <=15 bps |
| Protection arm | target <=2 s; hard fail/HALT at 5 s |
| Evidence window | >=20 completed trades across >=3 trading days with no unresolved safety/execution/reconciliation invariant violation |

Runtime/strategy/risk/provider constraints may always reduce effective risk below
this envelope; no runtime component may increase it. Any increase of capital,
allowlist, leverage, caps or recovery authority requires a new owner decision.

## Credential and withdrawal boundary

Lighter's normal write API key must not be represented as provider-side
`no-withdrawal`: current provider semantics include secure-withdrawal capability
back to the owner's L1 address. SignalAI therefore uses compensating controls:

1. a dedicated Canary sub-account containing only Canary capital;
2. the Ethereum private key is **never stored on the SignalAI VPS**;
3. the server-side signer/transport allowlists required trading transaction types
   and rejects withdrawal/transfer operations before provider signing;
4. credential creation/rotation/revocation creates a new opaque
   `credential_generation_id`; any generation mismatch invalidates readiness;
5. raw keys, signatures, signed payloads and secret-derived fingerprints never
   enter audit metadata.

This residual provider capability is accepted for the bounded 100-USDC Canary,
subject to the controls above. It is not permission for SignalAI to perform
withdrawal or transfer operations.

## Immutable policy and authoritative preflight

Each Canary candidate is an append-only canonical snapshot binding:

- source SHA and engine config hash;
- exact strategy family/version;
- live credential generation + account/API-key indexes;
- provider market mapping + SignalAI instrument allowlist;
- capital/currency and valuation source/time/rule;
- all numerical hard caps;
- exact durable evidence refs;
- validity interval, actor and correlation id.

The canonical SHA-256 covers the complete non-secret snapshot. Any mutation,
expiry, credential rotation, source/config drift or evidence mismatch makes the
candidate stale and fails closed.

Readiness is server-authoritative. Mobile/operator input cannot supply trusted
proof flags. Missing, stale, ambiguous or unavailable evidence blocks progress
without provider submission.

## Owner activation contract

Canary v1 policy approval and real-money activation are deliberately separate.
The approved static profile uses a five-minute (300 s) owner step-up window.
Before future SANDBOX→CANARY mutation the owner must see and confirm one exact
payload containing at least:

- `capital=100 USDC`;
- venue/account and non-secret credential generation;
- exact BTC perpetual mapping;
- frozen strategy/version and source/config hashes;
- full hard-cap set;
- evidence/readiness state;
- challenge nonce, issued-at and expiry.

Confirmation must be cryptographic, single-use and replay-safe. The server must
re-run authoritative preflight under the execution lock immediately before mode
mutation. A bearer token, boolean flag or prior approval of this ADR is not a
substitute for that final confirmation.

Until that flow is explicitly completed, the authoritative state remains
fail-closed and no real order is permitted.

## Submit-time and safety invariants

Every future Canary submit must re-check mode, kill switch, policy hash,
source/config, credential generation, allowlist, hard caps, fresh valuation,
positions/orders and durable order identity immediately before provider I/O.
Network ambiguity reconciles the existing identity; it never creates a replacement
CREATE with a new identity/nonce.

Credential/source/config/policy drift, cap/allowlist breach, missing protection,
reconciliation ambiguity, security incident or owner halt triggers
HALT_NEW_ENTRIES and risk downshift. Automatic resume is forbidden. Recovery
requires a new authoritative preflight and fresh owner step-up. Blind flatten is
not permitted where state is ambiguous; open risk remains reconciled/protected.

## Non-goals

This ADR does not authorize Scaled LIVE, ML challenger promotion, multi-strategy
expansion, additional markets or larger capital. SAI-089 microstructure thresholds
and SAI-090 ML promotion remain evidence-driven later decisions rather than guessed
values added to the first Canary.

## Acceptance criteria

- [x] Owner approved capital/currency and numerical Canary v1 hard caps.
- [x] Owner approved Lighter BTC-perpetual-only scope and one frozen deterministic strategy.
- [x] Owner approved 300-second step-up TTL and no automatic resume.
- [x] Lighter credential residual withdrawal semantics are documented honestly with compensating controls.
- [ ] Exact final source/config/strategy/credential/policy snapshot has fresh authoritative evidence.
- [ ] Signer/transport proof demonstrates withdrawal/transfer rejection and absence of Ethereum private key on the VPS.
- [ ] Cryptographic single-use owner challenge/confirm flow passes replay/expiry/concurrency tests.
- [ ] Final owner activation confirms the exact `100 USDC` payload.
- [ ] Canary evidence window completes before any Scaled LIVE decision.

## Revisit conditions

Revisit this ADR if Lighter changes auth/withdrawal/order semantics, if execution
core safety properties change, or if Canary evidence demonstrates that the caps or
recovery policy are insufficient. Any risk expansion requires a new owner decision
and never occurs automatically.
