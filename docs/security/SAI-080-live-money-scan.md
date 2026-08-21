# SAI-080 reproducible live-money scan checklist and report template

**Status: TEMPLATE — not a completed security scan.**

Policy/code baseline used to author this template:
`22a94f2281daec99fc87a316114da4fe25077b61` (default after PR #201). A completed
report is valid only for the exact immutable `TARGET_SHA` recorded by the scan;
results from another commit/working tree do not transfer.

This procedure is static/read-only. It must not load/provision a real credential,
allocate capital, call mainnet, change execution mode, clear a kill switch, submit
or sign an order, deploy VPS/APK, or print secret-bearing environment/config.

## 1. Scan receipt

A completed copy records all fields below. Do not replace unknown values with a
guess or green default.

| Field | Required value |
| --- | --- |
| Target repository | `https://github.com/compas-psy/signalAI-mobileApp.git` (sanitized, no credentials/query/fragment) |
| `TARGET_SHA` | Exact 40-lowercase-hex immutable commit reviewed |
| Baseline / comparison | Exact SHA or `repository-wide`; explain selection |
| Scan mode and scope | `scoped live-money boundary`; included/excluded paths listed below |
| Started/completed | UTC ISO-8601 timestamps |
| Scanner | Tool/model/plugin version and scan/run ID |
| Reviewers | Security reviewer and execution reviewer |
| Policy inputs | Exact blob SHAs for `SECURITY.md`, threat model and ADR-0002 |
| Tests/evidence | Exact commands, exit codes and CI/run links for `TARGET_SHA` |
| Findings | Critical/High/Medium/Low counts and disposition |
| Limitations | Deferred/unreachable surfaces and reason |
| Owner verdict | `BLOCKED`; only owner may later record a separate Canary decision |

## 2. Immutable-target preflight

Run from a fresh clean checkout with no secret environment loaded:

```bash
set -euo pipefail
test -z "$(git status --porcelain)"
TARGET_SHA="$(git rev-parse HEAD)"
case "$TARGET_SHA" in (*[!0-9a-f]*|'') exit 1;; esac
test "${#TARGET_SHA}" -eq 40
git cat-file -e "$TARGET_SHA^{commit}"
git show -s --format='target=%H%ncommitted=%cI%nsubject=%s' "$TARGET_SHA"
git ls-tree -r --name-only "$TARGET_SHA" > /tmp/signalai-sai080-paths.txt
```

Record the printed SHA/time/subject. Hash the three policy inputs without showing
contents from unrelated secret stores:

```bash
git rev-parse "$TARGET_SHA:SECURITY.md"
git rev-parse "$TARGET_SHA:docs/security/lighter-live-money-threat-model.md"
git rev-parse "$TARGET_SHA:docs/adr/0002-lighter-canary-boundary.md"
```

If any path is absent, the tree is dirty, the SHA is not immutable, or ADR-0002
is not accepted for an activation review, verdict remains **BLOCKED**. A scan may
still run to discover findings, but cannot establish readiness.

## 3. Required scope

Review complete source-to-sink paths, not only files changed by the target PR.

### Included production/config/delivery surfaces

- `server/app/security.py`, `server/app/api/v1/execution.py`,
  `server/app/api/v1/integrations.py`, `server/app/integration_secrets.py`;
- `server/app/execution/{mode,promotion_guard,live_activation,kill_switch,automatic_safety}.py`;
- all `server/app/execution/venues/lighter*.py` and Lighter models/migrations;
- provider submit/claim/worker wiring, risk/cap/allowlist/policy code added by
  SAI-081–084, including every indirect factory/DI/scheduler entry point;
- `server/config/default.yaml`, config hashing/loading and runtime health evidence;
- relevant API schemas/mobile owner-confirmation and credential-management paths;
- `server/alembic/versions/` append-only/credential/policy/activation facts;
- `.github/workflows/`, delivery validators, secret scan, exact-SHA and SSH
  authenticity controls;
- `AGENTS.md`, `SECURITY.md`, `docs/SECRETS.md`, ADR-0001, ADR-0002 and this
  threat model as policy/evidence, never as proof of implementation.

### Supporting tests to read

- auth/secret integration tests;
- execution mode, promotion, activation, kill-switch and automatic-safety tests;
- all Lighter auth/contract/replay/action/private-WS/protection/reconciliation/
  scorecard/testnet tests;
- new SAI-081–084 PostgreSQL concurrency, staleness, failure and audit tests.

### Explicit exclusions

- provider-internal implementation unavailable in this repository;
- unrelated research/UI/business logic with no path to credential, activation,
  risk/caps, order/protection/reconciliation, audit or release boundary;
- real credential/provider/mainnet dynamic testing.

An exclusion never suppresses a reachable integration finding. Record every
deferred sink/entry point and why its coverage is incomplete.

## 4. Mandatory review matrix

For every row record `no_issue_found`, `reported`, `not_applicable` or
`needs_follow_up`, plus exact code/test evidence.

| Surface | Required properties / attack path |
| --- | --- |
| API auth | All business endpoints fail closed; owner-sensitive mutation cannot rely on bearer/boolean alone; no auth bypass via proxy/local host/header ambiguity. |
| Secret lifecycle | Separate read/testnet/live slots; write-only; encrypted; no withdrawal/shared key; generation changes on save/replace/delete; secret-safe validation/errors/audit. |
| Credential/transport binding | Exact live generation + mainnet endpoint/chain + account/API key + transport instance/scope survives preview→confirm→submit; rotation invalidates. |
| Testnet/mainnet separation | SAI-075/testnet evidence cannot activate live or reach mainnet factory; no environment fallback/flag/URL override bypass. |
| Immutable policy/hash | Canonical deterministic non-secret snapshot covers source/config/strategy/generation/capital/caps/allowlist/evidence; DB append-only; stale changes reject. |
| Capital/caps/allowlist | Default deny; conservative valuation; all orders/positions counted; min of limits; rounding/overflow/NaN/negative/unknown fail closed. |
| Promotion/preflight | Stepwise; server-authoritative durable/fresh evidence; mobile flags/IDs are not authority; missing/error/exception blocks before provider I/O. |
| Owner challenge | SANDBOX→CANARY two-step, short TTL, step-up, exact display binding, single use, idempotency, lock/concurrency and full confirmation recheck. |
| Submit boundary | Rechecks mode/kill switch/policy/generation/allowlist/caps under the same serialization immediately before network I/O; no hidden worker/factory bypass. |
| Identity/nonce/replay | Stable identity and request hash; durable nonce scope; ambiguity reconciles; CREATE is never replayed with new identity/nonce; CANCEL recovery is bounded. |
| Provider facts/protection | Parser/account/market binding; stale/foreign facts rejected; protection deadline and reduce-only semantics; no success inferred from adjacent event. |
| Reconciliation/restart | Durable evidence, restart-safe claims, ambiguous/out-of-order events fail closed, no duplicate submit or lost open risk. |
| Halt/demotion | Submit race closed; automatic actions only strengthen/downshift; trigger thresholds enforced; stronger owner state preserved; no auto-resume/blind flatten. |
| Audit/logging | Append-only correlation from generation to demotion; UTC/source/config/policy/account refs; no secrets, signed payload, raw exception cause/context or auth header. |
| Delivery/provenance | Scan, CI, VPS, APK and owner preview refer to accepted exact SHA/config/policy; pinned host authenticity; merge/branch name cannot authorize release. |

## 5. Reproducible checks

These repository checks complement, but do not replace, source-to-sink security
analysis. Run against `TARGET_SHA` and record exact output/exit code:

```bash
python3 tool/ci_secret_scan.py
python3 tool/validate_delivery_workflows.py
git diff --check "$TARGET_SHA^" "$TARGET_SHA"
```

For the completed implementation head also record exact-head Quality Gate results:
PostgreSQL migrations/model parity and execution/Lighter regression tests. Do not
run network tests with real credentials. Scanner discovery/validation must use the
resolved root `SECURITY.md`, this threat model and exact included paths above;
record the scan ID and version so another reviewer can retrieve the canonical
artifact bundle.

## 6. Findings template

One row per validated finding; detailed evidence belongs in the scanner's
canonical artifact bundle, not copied with secrets into this file.

| ID | Severity | Control / source→sink | Reachability and impact | Disposition / fix SHA | Verification |
| --- | --- | --- | --- | --- | --- |
| None recorded in template | — | — | — | — | — |

Rules:

- Critical/High cannot be accepted silently and always leave owner verdict BLOCKED
  until fixed and re-reviewed on the new exact head.
- A Medium accepted risk needs explicit owner, expiry/review condition and
  compensating control in the completed report.
- Tests are corroborating evidence, not proof that an alternate production path
  cannot bypass the control.
- Public errors/evidence excerpts must be redacted and must not include secret-
  derived fingerprints, signed transactions or raw provider responses.

## 7. Coverage and final report

Complete this table; missing rows mean incomplete coverage.

| Surface ID | Paths/entry points reviewed | Evidence receipt | Disposition | Deferred work |
| --- | --- | --- | --- | --- |
| AUTH |  |  | needs_follow_up | Template not executed |
| CREDENTIAL |  |  | needs_follow_up | Template not executed |
| POLICY |  |  | needs_follow_up | Template not executed |
| ACTIVATION |  |  | needs_follow_up | Template not executed |
| SUBMIT_REPLAY |  |  | needs_follow_up | Template not executed |
| PROTECTION_RECONCILIATION |  |  | needs_follow_up | Template not executed |
| DEMOTION |  |  | needs_follow_up | Template not executed |
| AUDIT_SECRECY |  |  | needs_follow_up | Template not executed |
| DELIVERY |  |  | needs_follow_up | Template not executed |

Final completed report must state:

1. exact target/policy SHAs and coverage completeness;
2. findings with validated severity and unresolved limitations;
3. whether every ADR-0002 acceptance property has implementation/test evidence;
4. **security readiness: PASS/BLOCKED**;
5. **owner activation: always a separate decision, never implied by PASS**.

This checked-in template intentionally ends **BLOCKED / not executed**. Filling it
does not itself activate Canary; a completed scan report is committed after the
reviewed code SHA or retained in the canonical scan bundle with an immutable link.
