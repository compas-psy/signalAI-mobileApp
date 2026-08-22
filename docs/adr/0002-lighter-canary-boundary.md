# ADR-0002: Lighter Canary live-money boundary

- Status: proposed
- Date: 2026-08-21
- Owners: Product owner + Integrator/CTO + Security/Execution reviewer
- Supersedes: —
- Extends: [ADR-0001](0001-live-promotion-gates.md)

## Контекст

SAI-066–075 создали transport-free Lighter contract, разделённые credential
slots, market/account facts, durable order identity/nonce/actions, private-event
normalization, protection/reconciliation evidence, shadow scorecard и безопасный
testnet create→cancel smoke. Общий execution core уже содержит server-owned mode,
promotion guard, kill switch, автоматическое понижение риска, two-step
CANARY→LIVE challenge и append-only audit.

Эти primitives не образуют разрешение на реальные деньги. Production остаётся
`paper_only`, Lighter action layer не имеет live factory/worker, а authoritative
promotion evidence намеренно false. Перед SAI-081–084 нужно определить один
необходной SANDBOX→CANARY contract без дублирования существующего core.

Связанные требования безопасности: [`SECURITY.md`](../../SECURITY.md) и
[Lighter threat model](../security/lighter-live-money-threat-model.md).

## Decision drivers

- Live credential, account, policy и transport должны иметь одну доказуемую
  identity; testnet evidence не является live authorization.
- Малый капитал, hard caps и allowlist — immutable ограничения, а не UI/config
  hints или оптимизируемые параметры.
- Owner confirmation должна быть свежей, конкретной и replay-safe.
- Ошибка, ambiguity или drift сначала уменьшают риск и никогда автоматически не
  восстанавливают Canary.
- Существующие mode, lock, kill-switch, replay, reconciliation и audit primitives
  переиспользуются; второй execution engine недопустим.
- Документы/скан не используют реальные credentials, capital или mainnet calls.

## Рассмотренные варианты

### A. Feature flag + существующий `lighter_trade`

Отклонён. Наличие ключа или flag не связывает owner decision с капиталом,
account, allowlist, evidence и source/config; rotation создаёт stale authority.

### B. Отдельный Lighter mode/nonce/audit stack

Отклонён. Два источника истины расходятся при restart, kill switch и recovery и
увеличивают attack surface.

### C. Immutable Canary snapshot поверх существующего execution core

Выбран. Новый слой добавляет только отсутствующие policy/evidence/activation
bindings, а order lifecycle и safety mutation остаются общими.

## Решение

ADR становится `accepted` только после заполнения owner decisions в конце
документа и отдельного security/execution review. До этого любая реализация
обязана оставаться fail-closed и не может provision live key или capital.

### 1. Credential boundary и generation binding

Используются существующие slots без новых alias:

- `lighter_read`: live/read only;
- `lighter_testnet_trade`: testnet/trade only;
- `lighter_trade`: live/trade only.

`lighter_trade` хранится только server-side, имеет минимальные read/trade права,
не имеет withdrawal permission и ограничивается server IP, если это официально
поддерживается. Его создание, замена и удаление создают новый opaque random
`credential_generation_id` и append-only факт с actor/time/slot/account index/
API-key index. Raw key, key hash/fingerprint, signed payload и provider response
не входят в metadata/audit.

Provider verification связывает одну generation с `environment=mainnet`,
официальными endpoint/chain, account index, API-key index и exact transport
instance/scope. Preflight, owner challenge, confirmation и каждый submit должны
видеть ту же generation. Missing/delete/rotation/mismatch немедленно делает
evidence stale и блокирует новые entries.

### 2. Immutable Canary config

Перед preview сервер сохраняет append-only snapshot со следующими обязательными
полями:

| Group | Поля / правило |
| --- | --- |
| Identity | `policy_version`, 40-char `source_sha`, 64-char `engine_config_hash`, strategy family/version, `venue=LIGHTER`, `environment=mainnet` |
| Credential | `credential_generation_id`, account index, API-key index; никакого secret-derived значения |
| Scope | Непустой sorted unique allowlist provider market indices и SignalAI instruments; всё остальное запрещено |
| Capital | Положительный preapproved amount + currency; если risk/caps сравниваются в другой валюте, snapshot содержит valuation source/time/rule, а missing/stale valuation блокирует order |
| Hard caps | Max per-order, per-instrument и gross notional; max open positions и entry orders; max leverage; daily и total loss; max order/trade count; validity interval |
| Evidence | Exact refs/freshness для strategy/performance, shadow, testnet, protection/reconciliation, kill-switch drill, security scan и operational health |
| Audit | Created-at UTC, actor, reason/correlation и schema version |

Все decimal values кодируются canonical decimal strings без exponent; timestamps
— UTC ISO-8601; map keys сортируются; allowlists сортируются и дедуплицируются.
`canary_config_hash = SHA-256(canonical UTF-8 JSON)` покрывает весь non-secret
snapshot, включая evidence refs, но не raw credential и mutable telemetry.

Snapshot нельзя UPDATE/DELETE на уровне БД. Новое решение создаёт новую запись и
новый hash. Runtime config, mobile, optimizer или operator могут только выбрать
меньший effective risk; повысить snapshot limit невозможно. Итоговый разрешённый
объём/риск — минимум Canary caps, Risk Engine caps, account/provider constraints
и текущего kill-switch/mode decision.

### 3. Authoritative preflight

SAI-081/082 создаёт server-only evidence provider. Он не принимает proof flags
или authoritative evidence body от mobile. READY возможен только когда:

- current mode ровно SANDBOX и kill switch CLEAR;
- snapshot complete, не expired и его canonical hash пересчитан;
- exact deployed/source/config/strategy/credential scope совпадает со snapshot;
- allowlist mapping однозначен и provider facts свежие;
- required shadow/testnet/protection/reconciliation/security/ops evidence durable,
  fresh и без unresolved ambiguity;
- нет pending recovery, unreconciled action, cap breach или более сильного blocker.

Любое missing/invalid/stale/exception возвращает typed blocker без provider submit.
SAI-075 READY доказывает только testnet path и всегда остаётся отдельным evidence
ref, а не live token.

### 4. Owner-controlled SANDBOX→CANARY

Активация двухшаговая и переиспользует текущие activation/mode/idempotency/audit
primitives, не меняя скрыто семантику существующего `/execution/live/*`
CANARY→LIVE API.

1. **Preview:** после step-up initiation сервер создаёт nonce, `issued_at`,
   `expires_at`, exact `canary_config_hash` и owner-visible summary: venue,
   account, non-secret credential generation, strategy/version, capital/currency,
   caps, allowlist, source/config, evidence status и blockers.
2. **Confirm:** требует отдельный idempotency key, fresh owner step-up и explicit
   confirmation exact preview. Под lock сервер повторно читает mode, kill switch,
   time, source/config/policy hash, credential generation, provider scope и весь
   authoritative preflight до mode mutation.

Challenge имеет короткий owner-approved TTL и single-use semantics для APPLIED,
BLOCKED, STALE и EXPIRED outcomes. Повтор с тем же idempotency key возвращает тот
же результат; другой preview/key конфликтует. Owner bearer или boolean без
принятого step-up proof недостаточен. Частичная активация запрещена.

### 5. Submit-time enforcement

Каждый claim/provider submit использует существующий execution-control lock и
непосредственно перед network I/O проверяет:

- mode CANARY, kill switch CLEAR и active unexpired snapshot;
- exact source/config/policy hash и credential generation/transport scope;
- instrument/market в allowlist;
- proposed action внутри всех capital/caps с учётом current positions, orders и
  conservative fresh prices/valuation;
- stable order identity, durable request hash/nonce reservation и отсутствие
  unresolved prior action.

После network ambiguity система reconciles, а не повторяет CREATE с новой
identity/nonce. Protection и private facts связываются с тем же account/order
scope. Ошибка наружу и в audit secret-safe без raw payload/cause/context.

### 6. Automatic halt, demotion и recovery

SAI-083 добавляет только trigger/evidence policy и вызывает существующие
`automatic_halt_new_entries` / `automatic_downshift`. Автоматика не очищает kill
switch, не повышает mode и не инициирует новое/повторное entry.

Немедленный HALT_NEW_ENTRIES обязателен при credential/source/config/policy drift,
cap/allowlist breach, missing protection, nonce/reconciliation ambiguity,
security incident или owner halt. Freshness/provider error thresholds действуют
по owner-approved значениям.

Порядок: захват общей submit serialization → HALT → прекращение новых claims →
authoritative reconcile → безопасная отмена pending entries, если доказуема →
downshift SANDBOX/PAPER → append-only outcome. Open risk/protection продолжает
reconciliation; blind flatten запрещён. Recovery требует устранённую причину,
новый preflight и отдельный owner-approved flow; автоматического resume нет.

Runbook отдельно фиксирует provider revoke/rotation, проверку surviving positions
и protection, exact-source code rollback, forensic evidence и recovery authority.

### 7. Audit и correlation

Переиспользуются `AuditEvent`, `ExecutionModeEvent`, Lighter identity/action/
reconciliation facts и существующие DB append-only triggers. Новый immutable
Canary snapshot и credential-generation history добавляются только потому, что
существующие mutable vault/activation rows не выражают эти facts.

Одна correlation chain связывает generation → snapshot/hash → preflight →
preview/confirm → mode event → orders/protection/reconciliation → halt/demotion/
recovery. Каждый факт содержит UTC, actor/source, correlation/trace, source SHA,
strategy/policy IDs, config hashes и non-secret account scope. BLOCKED/STALE/
EXPIRED/automatic outcomes сохраняются наравне с APPLIED. Исправление — новая
запись; secret/auth header/signed payload/raw exception никогда не сохраняются.

### 8. Explicit non-goals

Этот ADR не разрешает Scaled LIVE, не выбирает numerical limits за владельца, не
создаёт mainnet smoke, не меняет strategy admission/risk alpha, не дублирует
execution engine и не считает успешный security scan owner approval. CANARY→LIVE
остаётся отдельным будущим решением после реального Canary evidence window.

## Последствия

- Активация становится длиннее, зато credential rotation, config drift и replay
  не наследуют старое owner authority.
- Потребуются immutable snapshot/generation facts и submit-time checks, но mode,
  lock, order/replay/reconciliation и audit остаются общими.
- Мобильный клиент показывает exact server snapshot и step-up outcome, не содержит
  business fallback.
- Любой неполный evidence снижает доступность, но сохраняет капитал; это принятый
  fail-closed trade-off.

## Acceptance criteria

- [ ] ADR переведён в `accepted` только с заполненными owner decisions ниже.
- [ ] Exact-head SAI-080 scan не имеет unresolved Critical/High finding.
- [ ] Live credential generation versioned/audited и bindится к account/key/
      mainnet transport без secret-derived audit fields.
- [ ] Canary snapshot DB-append-only, canonical hash deterministic, allowlist
      default-deny, а изменение любого поля делает preview stale.
- [ ] Preflight принимает только authoritative durable evidence и fail closed на
      missing/stale/error без provider action.
- [ ] SANDBOX→CANARY preview/confirm доказывает TTL, step-up, single use,
      idempotency, concurrent confirmation и full authoritative recheck.
- [ ] Submit-time tests доказывают cap/allowlist/generation/mode/kill-switch checks
      под общей serialization и отсутствие CREATE replay после ambiguity.
- [ ] Demotion drill доказывает monotonic HALT/downshift, restart recovery,
      preservation сильного owner switch и запрет automatic resume/blind flatten.
- [ ] Audit correlation полон, append-only и secret-safe.
- [ ] Cumulative release и Samsung acceptance ссылаются на один accepted source
      SHA/config/policy hash; owner activation остаётся отдельным действием.

## Unresolved owner decisions before `accepted`

1. Capital amount/currency, valuation rule и все numerical hard caps.
2. Exact strategy/version и market/instrument allowlist.
3. Challenge TTL и конкретный step-up mechanism/credential lifecycle.
4. Evidence freshness, provider-error и reconciliation/demotion thresholds.
5. Downshift target по классам failure, cancel/flatten authority и recovery flow.
6. Accepted residual risks после exact-head security scan.

## Условия пересмотра

ADR пересматривается при изменении Lighter auth/chain/order/protection semantics,
credential permission model, execution core или после Canary evidence, которое
показывает недостаточность caps/demotion. Любое расширение капитала/allowlist либо
переход к Scaled LIVE требует нового owner decision и не выполняется автоматически.
