# Политика безопасности SignalAI

Эта политика применяется ко всему репозиторию. Ближайший к проверяемому файлу
вложенный `SECURITY.md`, если он появится, может уточнить, но не ослабить эти
требования без отдельного принятого ADR и решения владельца.

## Система и scope

SignalAI — персональная торговая система с одним владельцем, server-side source
of truth, PostgreSQL, Android thin-client, внешними market-data и execution
providers, а также GitHub Actions для проверки и поставки. Основные защищаемые
активы:

- капитал, позиции, ордера, protection и право менять execution mode;
- broker/exchange credentials, device bearer, ключи подписания и VPS secrets;
- server-owned signal, risk, execution, reconciliation и promotion decisions;
- append-only audit/evidence и exact-source provenance релиза;
- доступность kill switch, recovery и безопасного понижения риска.

Security review охватывает production/runtime code, API/auth, мобильное хранение
секретов, provider adapters, money/risk/execution paths, БД/migrations, CI/CD,
конфигурацию, логирование и operational tooling. Для Lighter live-money scope
дополнительно действуют [threat model](docs/security/lighter-live-money-threat-model.md)
и proposed [ADR-0002](docs/adr/0002-lighter-canary-boundary.md). Proposed controls
нельзя считать реализованными или использовать как основание для suppression.

## Trust boundaries и attacker-controlled input

- Internet, provider REST/WebSocket responses, deep links, API payloads/headers,
  uploaded/imported data и market data недоверенны.
- Android-приложение передаёт owner intent, но не является источником истины для
  promotion evidence, risk/caps, account identity или результата исполнения.
- Device bearer аутентифицирует текущий mobile API, но сам по себе не доказывает
  свежий owner presence для первой live-money активации.
- GitHub PR content, dependencies, build inputs и workflow parameters считаются
  developer-controlled, но не автоматически доверенными для production.
- Server process и PostgreSQL доверяются только в пределах явно проверяемых
  инвариантов. Компрометация VPS/DB остаётся значимой угрозой, а не исключением.
- Provider подтверждает только собственные facts. Его успешный ответ не заменяет
  SignalAI risk, owner, promotion, reconciliation или audit gate.

## Обязательные security properties

### Auth и owner authority

- Все `/api/*` endpoints fail closed при отсутствующем/неверном credential.
- Любая операция, способная добавить live risk, требует server-side authorization;
  client booleans, feature flags и presence credential не являются proof.
- Provision/rotation/revocation live trade credential, очистка kill switch и
  SANDBOX→CANARY требуют принятого owner-sensitive step-up contract.
- Promotion только пошаговый. Автоматика может лишь остановить новые entries,
  усилить kill switch или понизить mode; автоматическое восстановление/повышение
  риска запрещено.

### Credentials и secrets

- Read, testnet-trade и live-trade credentials разделены по slot/environment/
  purpose. Live trade credential не имеет withdrawal permission и не используется
  для analytics либо testnet.
- Secrets хранятся только в предназначенном server/device secret store и не
  попадают в git, APK/build constants, fixtures, issues, PR/chat, logs, telemetry,
  audit или scan artifacts.
- Missing, malformed, rotated, deleted, mismatched или unverifiable credential
  блокирует новый money action. Ошибки наружу санитизированы без secret-bearing
  `str`, `repr`, cause или context.
- Live submit должен быть привязан к текущей non-secret credential generation,
  environment, endpoint/chain, account и API-key scope; старая проверка не
  переносится на другой transport/credential.

### Capital, config и execution

- До отдельного owner decision production остаётся PAPER/SANDBOX; наличие ключа,
  успешного testnet smoke или mainnet endpoint metadata не включает Canary/LIVE.
- Canary использует один immutable server-owned snapshot: exact source SHA,
  engine config hash, credential generation/account, strategy/version, default-
  deny allowlist, preapproved capital и hard caps. Canonical hash покрывает весь
  non-secret snapshot.
- Каждый provider submit заново проверяет mode, kill switch, active snapshot/hash,
  credential generation, allowlist и caps под общей execution serialization.
  Missing/stale/ambiguous evidence блокирует submit.
- Money operations idempotent, replay-safe и reconciled. Неоднозначный provider
  response не разрешает повторить CREATE с новым identity/nonce и не считается
  успехом без authoritative reconciliation.
- Hard caps применяются как минимум из всех действующих лимитов; runtime, optimizer,
  mobile или owner preview не могут их скрыто повысить.

### Demotion, audit и release

- Credential/config change, cap breach, missing protection, reconciliation drift
  или security incident должны сначала остановить новые entries и затем пройти
  принятый reconciliation/demotion runbook.
- Kill switch сериализован с provider submit, переживает restart и не ослабляется
  автоматикой. Flatten без отдельного явного подтверждения запрещён.
- Money/security decisions оставляют DB-enforced append-only audit с UTC time,
  actor/source, correlation, source/config/policy refs и только non-secret scope.
- Production delivery использует принятый immutable source SHA, pinned network
  authenticity и canonical workflows. Merge сам по себе не является release.

## Что считать finding и как калибровать severity

- **Critical:** реалистичный путь без owner gate создать/изменить live order,
  обойти caps/kill switch, включить более рискованный mode, вывести/раскрыть live
  private key либо подменить production source с сопоставимым эффектом.
- **High:** замена live account/credential, повтор/двойной submit, fail-open при
  stale/missing evidence, обход allowlist/protection/reconciliation, очистка
  safety state без authority, или удалённая компрометация server/API с прямым
  влиянием на капитал.
- **Medium:** утечка чувствительных non-secret account/evidence metadata,
  неполный audit, ограниченный DoS safety/monitoring либо weakness, требующая
  дополнительного привилегированного условия и не создающая live risk напрямую.
- **Low:** hardening/defense-in-depth без доказуемого нарушения money, secret,
  auth, audit или release boundary.

Severity определяется достижимым impact и существующими controls, а не названием
файла или успешным тестом. Security regression блокирует promotion независимо от
доходности.

## Out of scope и ограничения

Не являются repository finding сами по себе: внутренние дефекты стороннего
provider, физическая компрометация уже разблокированного устройства без участия
кода SignalAI, локальные test-only fakes без production reachability и отсутствие
доходности стратегии. Но ошибочное доверие этим поверхностям, небезопасная
интеграция либо неспособность fail closed остаются in scope.

Текущее отсутствие Lighter mainnet worker и fail-closed promotion evidence —
compensating controls, а не доказательство готовности. До принятия ADR-0002 и
реализации его gates provisioning live credential, capital allocation, mainnet
smoke и Canary activation запрещены.

## Review и disclosure process

Security-sensitive PR должен:

1. назвать assets, entry points, trust boundaries и свойства из этой политики;
2. пройти targeted diff review и релевантные secret/auth/replay/failure tests;
3. для money/auth/credential/promotion/demotion иметь независимый security review;
4. пройти exact-head Quality Gate; scan/review другого SHA не переносится;
5. не смешивать security fix с изменением strategy/risk thresholds без отдельного
   решения и evidence.

Перед Canary выполняется отдельный reproducible live-money scan по
[SAI-080 checklist](docs/security/SAI-080-live-money-scan.md). Critical/High
findings должны быть исправлены или явно заблокировать activation; молчаливое
accepted risk запрещено.

Уязвимость сообщается владельцу через приватный project channel. Не размещайте
секрет, Authorization header, signed transaction, raw provider payload или
эксплуатационные данные в публичном issue/PR. При подозрении на компрометацию
credential сначала блокируются новые entries и выполняется revoke/rotation;
история исправляется новой audit записью, а не переписыванием старой.
