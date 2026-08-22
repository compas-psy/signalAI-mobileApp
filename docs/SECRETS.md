# Ключи и секреты SignalAI

## Главное правило

**Ни один секрет не встраивается в APK, исходный код или `--dart-define`.**
Мобильный thin-client получает торговые идеи от сервера, но текущая архитектура
имеет раздельные device-only credential:

1. `SIGNALAI_DEVICE_TOKEN` — server-side bootstrap secret только для
   `POST /api/v1/device-enrollment/pair`. Он не авторизует business API и не
   сохраняется телефоном после pairing. Pairing дополнительно требует отдельную
   high-entropy `SIGNALAI_DEVICE_PAIRING_SESSION_ID`; сервер хранит только
   verifier, UTC expiry и bounded use count. Raw bootstrap/session не попадают
   в БД, аудит, ошибки или телефонное JSON-хранилище.
2. Выпущенный сервером token устройства — случайный bearer конкретного device
   generation. Сервер хранит только SHA-256 verifier, device id и audit time;
   телефон кладёт исходное значение только в Android Keystore. Rotation сразу
   отзывает старый generation, revoke закрывает его навсегда.
3. Токен **T-Invest Sandbox** — владелец вводит его в Thin → Settings →
   Connections; он хранится только в Android Keystore и используется только
   мобильным sandbox adapter.

`SIGNALAI_RISK_PREVIEW_SIGNING_KEY` и `SIGNALAI_METRICS_TOKEN` — отдельные
server-side secrets. Они не могут использовать bootstrap secret как fallback.

T-Invest Sandbox token **не должен** попадать в GitHub Secrets, VPS `.env`,
серверный credential registry, логи или сборочные параметры. Это подтверждено
текущей реализацией `TInvestSandboxAccess`: сохранение — локальная операция в
Keystore; проверка сети выполняется отдельно.

Серверные runtime-секреты (например Telegram и будущие live broker credentials)
живут только в защищённом server/environment secret store. GitHub Actions может
использовать repository/environment secrets только для поставки, но их значения
не выводятся в логи.

---

## 1. Т-Инвестиции

### Сейчас: Sandbox

Текущий рабочий контур FORTS использует **T-Invest Sandbox** только после
подтверждения владельцем server-side идеи.

- sandbox token вводится на телефоне;
- хранится в Android Keystore;
- не синхронизируется на VPS;
- сохранение токена не ослабляет TLS и не зависит от текущего VPN/proxy route;
- реальный API-вызов по-прежнему проходит строгую TLS-проверку;
- повторное подтверждение server-side решения не должно создавать дубль
  sandbox order.

Исторические свечи и OI для FORTS берутся из MOEX ISS и не требуют ключа.

### Позже: Live

Live-доступ Т-Инвестиций сейчас не является частью принятого production mode.
Если он будет включён после promotion gates из `docs/adr/0001-live-promotion-gates.md`,
его credential должен быть отделён от read-only analytics, иметь минимально
необходимые права и храниться только в server-side secret store.

---

## 2. Bybit

Публичные market data Bybit не требуют API key и должны оставаться без
credential там, где это возможно.

Live execution не включается только фактом наличия ключа. Когда live-контур
будет принят отдельно, ключ исполнения должен:

- иметь только необходимые read/trade permissions;
- **не иметь withdrawal permission**;
- быть отделён от read-only analytics credential;
- храниться только server-side;
- по возможности быть ограничен IP сервера;
- не попадать в приложение, git, CI logs или issue/chat.

---

## 3. Telegram

Telegram notification credentials являются server-side runtime secrets.
Они синхронизируются на VPS только через предназначенный для этого workflow и
не должны попадать в APK или исходный код.

Подтверждение торговой операции через Telegram не следует считать эквивалентом
owner approval в приложении без отдельного принятого ADR и threat model.

---

## 4. MAX и другие notifier adapters

Если добавляется новый notifier, его credential следует хранить по тем же
правилам, что Telegram: server-side only, минимальные права, без логирования и
без сборочных параметров мобильного клиента.

---

## 5. LLM / внешние сервисы

API credentials внешних аналитических сервисов допускаются только server-side.
Перед добавлением нового внешнего сервиса нужно проверить:

- какие данные ему передаются;
- содержит ли payload финансовые/персональные данные;
- retention и logging policy;
- возможность минимизировать или полностью убрать credential из hot path.

---

## 6. Секреты поставки Android/VPS

В GitHub Actions используются только секреты, необходимые для подписанной
поставки и серверного runtime. К ним относятся постоянная Android signing
identity, адрес API и `SIGNALAI_DEVICE_TOKEN` для первичной привязки server ↔
owner device.

Правила:

- постоянный signing key не хранится в репозитории;
- `SIGNALAI_DEVICE_TOKEN` не компилируется в APK и не сохраняется после pair;
- workflow не печатает secret values;
- только выданный device token хранится в Android Keystore;
- production delivery идёт только из явно принятого immutable source SHA;
- временная debug/signing identity не считается production identity.

Подробности Android signing: `docs/ANDROID_SIGNING.md`.
Порядок поставки: `docs/DEVELOPMENT_PROCESS.md`.

---

## Запрещено

- Класть credential в код, tracked `.env`, fixtures, issue, PR body или chat.
- Передавать credential через `--dart-define` или иным способом встраивать его
  в APK.
- Давать broker/exchange credential право вывода средств.
- Использовать один credential одновременно для analytics и исполнения, если
  провайдер позволяет разделить роли.
- Копировать device-only T-Invest Sandbox token на VPS «для удобства».
- Ослаблять TLS только для того, чтобы credential check стал зелёным.

Если есть подозрение на компрометацию, credential считается скомпрометированным:
его нужно отозвать у провайдера, выпустить новый и обновить только в том secret
store, которому он принадлежит.
