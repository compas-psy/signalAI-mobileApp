# SAI-080 — completed live-money security review

**Security readiness: BLOCKED**  
**Owner activation: NOT AUTHORIZED — a separate owner decision is always required.**

This is a completed **manual, source-backed source→sink security review**, not a formal Codex Security scanner artifact. The formal Codex Security scanner is unavailable in the current execution environment, so this report must not be represented as scanner certification.

## 1. Immutable review receipt

| Field | Receipt |
| --- | --- |
| Repository | `https://github.com/compas-psy/signalAI-mobileApp.git` |
| `TARGET_SHA` | `90899644cdfc25330e6253497fd1a611965b9a1b` |
| Target Git tree | `4a2e029140f7f578fb9d118b6ed423f73fb2a5cb` |
| Scope | repository-wide live-money boundary, with Lighter Canary as the primary source→sink path and all live-trade credential authorities included |
| Recorded start | `2026-08-23T09:41:43Z` — first remediation PR created from the review |
| Completed | `2026-08-23T10:21:24Z` |
| Reviewer | GPT-5.6 Sol, manual source-backed security/execution review through the GitHub repository interface |
| Formal scanner | unavailable in this environment; no formal Codex Security run ID exists |
| `SECURITY.md` blob | `fc9451cd5a80e16246f8c0ff9271b0a08f4101ed` |
| Lighter threat-model blob | `22c2d184679c6eed7e4cdb00de2efd89bccb91d3` |
| ADR-0002 blob | `f3f9ac483acd3c04453f56a5971c0faf37be7d2c` |
| ADR-0002 status | `proposed` |
| Exact-tree Quality Gate | PR #234 QG #1197, run `32633302451`; branch head `1618f948cffa79d43a134b5ca60c9bdfaa258382` and final target `90899644...` both resolve to Git tree `4a2e029140f7f578fb9d118b6ed423f73fb2a5cb` |
| Server verification | `2038 passed, 7 warnings`; Alembic `0043_lighter_submit_ambiguity` is the single head; `alembic check` reports no new upgrade operations |
| Other verification | Flutter analyze/tests green; tracked-secret scan and delivery-workflow validation green |
| Validated findings | 2 High, 0 Critical; both High findings fixed and re-tested before this target |
| Unresolved reachable Critical/High | none found in the reviewed target |
| Final verdict | `BLOCKED` because governance/owner activation properties are intentionally unresolved; absence of unresolved Critical/High does not authorize Canary |

### QG tree-identity note

The pull-request Quality Gate checks GitHub's synthetic merge object, while the final default-branch merge has its own commit SHA. For #234, the tested branch head `1618f948...` and final merge `90899644...` have the identical tree SHA `4a2e0291...`. Therefore QG #1197 exercised the same repository contents as the target, without pretending that the synthetic merge commit SHA equals `TARGET_SHA`.

## 2. Findings and disposition

| ID | Severity | Source→sink | Impact | Disposition | Verification |
| --- | --- | --- | --- | --- | --- |
| `SAI080-H01` | High | enrolled-device bearer → `POST /api/v1/risk/resume` → durable kill-switch clear | a stolen/ordinary device bearer could clear a safety halt and restore future entry eligibility without fresh owner authority | fixed in PR #232; merge `2aba4c8aab2cf7a23b6d1b81015b3ef0e76e93b9`; bearer-only resume now fails closed with `EXECUTION_KILL_SWITCH_CLEAR_STEP_UP_REQUIRED` | TDD RED proved 200 before fix; final QG #1194: 2033 server tests passed. The generic `/risk/kill-switch` route is not an alternate clear path because the domain setter rejects `CLEAR` and requires the dedicated clear primitive. |
| `SAI080-H02` | High | enrolled-device bearer → live integration PUT/DELETE → `tinvest_trade` / `bybit_trade` credential authority | ordinary bearer could provision, rotate or revoke live-trade credentials without the owner-sensitive step-up mandated by `SECURITY.md`; current provider order reachability was constrained, but credential/account substitution is a High authority-boundary defect | fixed in PR #234; merge `90899644cdfc25330e6253497fd1a611965b9a1b`; all live-trade slots now fail closed for bearer mutation; Lighter preserves its existing specific blocker | TDD RED: exactly 4 failures / 2034 passes for Bybit/T-Invest live PUT/DELETE; GREEN QG #1197: 2038 server tests passed; read/testnet credential management remains unchanged |

No validated Critical finding was discovered. No unresolved reachable High finding remained after the two remediations above and the final re-review of the target tree.

## 3. Mandatory coverage matrix

| Surface | Reviewed production/source paths | Disposition | Evidence / conclusion |
| --- | --- | --- | --- |
| API auth | `server/app/security.py`, integration/risk/execution APIs, device pairing boundary | `no_issue_found` after H01/H02 fixes | active non-revoked device credentials are required for business API; bootstrap credential is limited to pairing; no localhost/proxy bypass; unknown auth policy fails closed. Device bearer is explicitly not treated as fresh owner presence for live credential mutation or safety clear. |
| Secret lifecycle | `server/app/api/v1/integrations.py`, `server/app/integration_secrets.py`, `docs/SECRETS.md`, deployment secret bootstrap | `no_issue_found` after H02 fix | read/testnet/live slots are distinct and write-only. All live-trade API mutation now requires future owner step-up. Lighter live secret uses a dedicated `SIGNALAI_LIGHTER_LIVE_SECRETS_KEY` with no DB-password/generic-key fallback. Secrets are not returned by GET. |
| Credential/transport binding | `lighter_auth.py`, `canary_transport_scope.py`, `lighter_sdk_transport.py`, credential generations | `no_issue_found` | mainnet construction requires live/trade credential plus exact current non-revoked generation, account and API-key scope bound to an immutable Canary snapshot. Rotation/revocation invalidates the prior scope. Mainnet transport remains `eligible_for_live=False`. |
| Testnet/mainnet separation | Lighter environment metadata, testnet smoke/actions, mainnet transport factory | `no_issue_found` | testnet transport has explicit testnet URL/chain and cannot become live; mainnet requires the Canary scope; no URL/environment fallback was found. Testnet evidence alone cannot authorize promotion. |
| Immutable policy/hash | `canary_policy.py`, `canary_transport_scope.py`, migrations 0041–0042 | `no_issue_found` | canonical snapshot binds source/config/strategy/generation/account/API key/allowlists/capital/valuation/hard caps/evidence/expiry. Persisted snapshot is re-canonicalized and row/hash-bound. Snapshot/evidence tables are append-only at DB level. |
| Capital/caps/allowlist | `canary_limits.py`, Canary policy schema and submit guard | `no_issue_found` for implemented non-authorizing boundary | finite Decimal and strict integer validation; NaN/Inf/negative/unknown values fail closed; exact cap set and nonempty unique allowlists; dynamic authority uses the minimum of applicable limits. No owner numerical values were invented by this review. |
| Promotion/preflight | `promotion_guard.py`, `promotion_evidence.py`, `live_activation.py`, `canary_preflight.py`, `canary_activation.py` | `no_issue_found`; activation remains intentionally blocked | generic promotion is stepwise and cannot target LIVE directly. Persisted evidence does not mint owner confirmation. Legacy LIVE confirmation treats the client boolean as non-proof and records `OWNER_STEP_UP_NOT_IMPLEMENTED`. Canary readiness is non-authorizing and `challenge_issuable=False`. |
| Owner challenge | activation/readiness and ADR-0002 | `needs_follow_up` | cryptographic step-up mechanism, challenge TTL, credential lifecycle and exact owner acceptance are unresolved. This is an intentional blocker, not a green security property. No challenge implementation was invented. |
| Submit boundary | `canary_submit_guard.py`, `worker.py`, `kill_switch.py`, `lighter_sdk_transport.py` | `needs_follow_up` before SAI-084 activation; no reachable live bypass found | shared execution-control serialization covers current mode/kill switch and Canary submit checks. The guard is deliberately non-authorizing and returns governance blockers; production worker remains `DisabledExecutionPort`. Final owner-evidence/TTL requirements must be wired before provider I/O can become eligible. |
| Identity/nonce/replay | `lighter_replay.py`, `lighter_actions.py`, migration 0043 | `no_issue_found` | durable order identity/request binding and nonce scope; provider write is preceded by durable `SUBMITTING`; timeouts/ambiguous/hash-bearing outcomes do not reopen a blind CREATE retry. Only an explicit API-level rejection without transaction hash can reopen the exact reserved nonce. |
| Provider facts/protection | `lighter_facts.py`, `lighter_actions.py`, automatic protection safety | `no_issue_found` for implemented boundary | strict finite/timezone/scoped parsing; reduce-only semantics for reduction/protection; missing active protection in Canary can only strengthen safety via HALT. |
| Reconciliation/restart | `lighter_reconciliation.py`, execution worker leases/claims | `no_issue_found` | reconciliation consumes provider evidence only and does not submit/retry. Fresh ambiguity for an already-SUBMITTING Canary action can HALT; historical replay/pre-submit state cannot create a false global safety authority. Worker claim/retry state is durable. |
| Halt/demotion | `kill_switch.py`, `automatic_safety.py`, risk API | `no_issue_found` after H01 fix | automatic actions are one-way lower-risk. Stronger kill-switch state is preserved. `CLEAR` is rejected by the general setter; bearer `/resume` is step-up-blocked. No automatic resume exists. |
| Audit/logging | execution/audit models, migrations, Canary correlation | `no_issue_found` | relevant mode/fill/reconciliation/policy/evidence/action/audit facts are append-only; CI tamper tests demonstrate UPDATE/DELETE rejection. Correlation requires canonical snapshot and full immutable owner scope. Lighter transport errors are sanitized and credential repr redacts the private key. |
| Delivery/provenance | `quality.yml`, `release-cumulative.yml`, `deploy-release.yml`, `android-sideload.yml`, `validate_release_source.py`, `prepare_known_host.sh`, `owner-commands.yml` | `no_issue_found` | cumulative release accepts only the current immutable default SHA; VPS/APK dispatch receives the exact SHA; SSH host key is pinned/strict; APK signer SHA is verified; no bearer is compiled into APK. `/release-current` is restricted to `github.repository_owner` and re-resolves current default SHA. |

## 4. Source→sink conclusions

### Authentication and owner authority

A normal enrolled-device bearer remains sufficient for ordinary personal-app operations and for safety-strengthening actions, but no longer has either of the two live-money authority capabilities found during this review: clearing durable execution safety or mutating live-trade credentials. Client booleans remain data, not authority.

The existing owner-sensitive operations still intentionally stop short of a real cryptographic owner step-up. This is why the review can close the discovered High vulnerabilities while the overall readiness remains BLOCKED.

### Lighter mainnet reachability

The repository contains a narrowly scoped Lighter mainnet SDK transport, but the production execution worker still instantiates `DisabledExecutionPort` and performs no venue requests. The mainnet factory requires exact Canary scope plus current live credential generation and is not marked eligible for scaled LIVE. The current Canary submit guard is non-authorizing and cannot produce `provider_io_eligible=True` while governance blockers remain.

Absence of a reachable live worker was **not** used to downgrade H01/H02: the security policy and threat model classify authority-boundary defects by the capability they would grant once the adjacent live seam is enabled.

### Audit and tamper resistance

Database migrations attach append-only protection to Canary policy/evidence and core execution/audit facts. Exact-head QG logs exercised negative tamper cases: attempted UPDATE/DELETE operations on Canary policy/evidence, promotion evidence, Lighter action bindings, idea/audit evidence and other append-only tables were rejected by `signalai_append_only()`.

### Delivery authenticity

Release orchestration is exact-SHA based. A branch name, PR state or arbitrary comment does not authorize release. The owner-command workflow requires `github.actor == github.repository_owner`; cumulative release then validates that the supplied SHA equals current default head. SSH host authenticity and APK signer identity are pinned and verified independently of source branch naming.

## 5. Unresolved limitations and mandatory owner gates

These items are **not vulnerabilities marked accepted**; they are missing activation requirements that intentionally keep security readiness BLOCKED:

1. ADR-0002 is still `proposed`, not accepted.
2. Owner has not selected the Canary capital amount/currency, valuation rule, exact hard caps, strategy/version and market/instrument allowlist.
3. Cryptographic owner step-up mechanism and credential lifecycle are not implemented/accepted.
4. Challenge TTL, single-use/replay semantics at the final activation boundary and recovery UX remain to be approved and implemented.
5. Evidence freshness, provider-error, reconciliation and demotion thresholds remain owner decisions; no thresholds were invented in this review.
6. Failure-class→downshift mapping, cancel/flatten authority and recovery/resume flow remain unresolved.
7. Provider-side credential permissions such as **no withdrawal** cannot be proven from repository source alone. They must be verified when real credentials are provisioned.
8. No real mainnet credential, capital allocation, provider call, order signature or dynamic mainnet test was used during this review.
9. Formal Codex Security scanning was not available; this artifact is a manual source-backed review and must not be substituted for a formal scanner run if one is later required by policy.
10. Final Samsung acceptance remains external: the previous owner physical pass was successful except for `Настройки → Данные`; replacement APK source `c535e8f52669849ab13aac489dff50110cf93a42` still needs the isolated scroll recheck recorded in issue #6.

## 6. ADR-0002 acceptance status

| Acceptance property | Current evidence | Status |
| --- | --- | --- |
| No unresolved Critical/High in reviewed repository live-money boundary | two High findings discovered and fixed; no remaining reachable Critical/High found on target | satisfied for this manual review, subject to formal-scanner limitation |
| Exact immutable source/config/policy scope | implemented foundations and exact-SHA delivery | partial/satisfied foundation |
| Immutable capital/caps/allowlist | canonical policy foundation implemented | implementation exists; owner values not approved |
| Fresh server-owned evidence | trusted append-only evidence registry exists | implementation exists; owner thresholds/freshness policy not finalized |
| Cryptographic owner challenge | not implemented | **BLOCKED** |
| Short approved TTL / single use | not approved/implemented for Canary | **BLOCKED** |
| Exact submit-time owner/policy binding before provider I/O | non-authorizing guard foundations exist | **BLOCKED until SAI-084 final wiring** |
| Automatic failure response | HALT/downshift primitives exist | **BLOCKED on failure-class thresholds/mapping** |
| Provider credential least privilege / no withdrawal | repository policy only | **BLOCKED on provider-side verification** |
| Owner physical acceptance | prior broad Samsung pass succeeded; isolated post-fix scroll recheck absent | **BLOCKED** |

## 7. Final verdict

**Security readiness: BLOCKED.**

The target has no unresolved reachable Critical/High finding discovered by this manual source-backed review after remediation of `SAI080-H01` and `SAI080-H02`. That statement is deliberately narrower than "safe to trade live".

Canary activation remains prohibited because ADR-0002 is proposed and essential owner/cryptographic/threshold/provider-permission/physical-acceptance evidence is missing. No part of this report creates a challenge, changes execution mode, provisions a credential, clears safety, allocates capital, calls mainnet or authorizes a release.

**Owner activation is always a separate explicit decision and is not implied by this report.**
