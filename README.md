# SignalAI

Персональная система торговых идей, сопровождения и инвестиционного анализа: серверный источник истины + тонкий Android-клиент.

## Главная цель

Максимизировать долгосрочный риск-скорректированный рост Equity после комиссий, проскальзывания и налогов при контролируемой просадке, risk of ruin, ликвидности и информационной безопасности.

Постоянный рост не гарантируется. Изменения торговой логики должны проходить измеримую проверку и promotion gates из `docs/adr/0001-live-promotion-gates.md`.

## Где находится актуальная правда

Читать в таком порядке:

1. `AGENTS.md` — правила работы над проектом;
2. `docs/ROADMAP.md` — что сделано и что идёт следующим;
3. `docs/adr/` — принятые архитектурные решения;
4. open GitHub issues/PR — исполнимый backlog;
5. default-branch code и актуальные docs.

`HANDOFF.md` — архив ранних итераций и **не** определяет текущую архитектуру или следующие шаги.

## Архитектура сейчас

В рабочем режиме `thin`:

- сервер считает идеи, risk/sizing, ведёт paper lifecycle, research, фоновые задачи и durable notification outbox;
- Android показывает состояние, графики и объяснения, принимает решение владельца и работает с локальным Keystore;
- неподтверждённая идея не становится сделкой;
- server-side lifecycle продолжает работать после закрытия приложения;
- Telegram notifications идут из того же durable server outbox и ведут deep link в конкретную идею.

### T-Invest Sandbox — явное исключение thin-client

Для проверки реального broker transport T‑Invest Sandbox token вводится владельцем на телефоне и хранится только в Android Keystore. После owner approval мобильный sandbox adapter может отправить подтверждённый FORTS plan в T‑Invest Sandbox.

Этот token:

- не встраивается в APK;
- не хранится в git/GitHub Secrets/VPS `.env`;
- не копируется на сервер «для удобства»;
- используется только в sandbox-контуре.

Подробности: `docs/SECRETS.md`.

Live broker credentials и live execution не включаются только фактом наличия кода или токена — для них действует отдельный promotion gate.

## Что уже есть

- thin client / server source of truth;
- owner `approve/reject` с server-side idempotency;
- server-side paper tracking;
- FORTS и crypto presentation lanes;
- живые графики и post-signal progress;
- T‑Invest Sandbox mirror;
- Risk & Exit Engine v2;
- Portfolio Signals (`ACCUMULATE / EARLY / WATCH`);
- ранние equity hypotheses;
- Telegram notifications с H1-графиком и deep link;
- permanent sideload signing;
- единый Quality Gate и provenance-aware delivery.

Текущие приоритеты и незакрытые gaps всегда смотрите в `docs/ROADMAP.md` и open issues.

## Режимы приложения

| Режим | Назначение |
|---|---|
| `thin` | рабочий персональный клиент; server source of truth |
| `demo` | UI на фикстурах, без рыночного смысла |

Legacy `local` не является рабочим режимом персональной поставки и не должен использоваться как fallback при ошибке сервера.

## Локальный запуск

```bash
flutter pub get

# UI demo
flutter run --dart-define=SIGNALAI_MODE=demo

# thin client
flutter run \
  --dart-define=SIGNALAI_MODE=thin \
  --dart-define=SIGNALAI_API_BASE_URL=https://your-gateway
```

`SIGNALAI_DEVICE_TOKEN` не передаётся через `--dart-define`: владелец вводит его в приложении, после чего он хранится в Android Keystore.

## Проверки

Канонический PR gate — `.github/workflows/quality.yml`:

- tracked-secret scan и security invariants;
- server import/compile;
- Alembic graph/model parity;
- PostgreSQL server tests;
- Flutter analyze;
- Flutter tests.

Локально для мобильного слоя:

```bash
flutter analyze
flutter test
```

## Поставка

Merge в default branch **не является production release**.

Нормальный путь:

`logical batch → green PR → merge → explicit Cumulative production release → exact accepted source SHA → canonical VPS/APK workflows`

Основные workflows:

| Workflow | Назначение |
|---|---|
| `quality.yml` | обязательный Quality Gate |
| `release-cumulative.yml` | явный cumulative release логического этапа |
| `deploy-release.yml` | канонический VPS deploy exact source ref |
| `android-sideload.yml` | permanent-signed sideload APK exact source ref |
| `android-release.yml` | release/Play build |

Промежуточные коммиты не требуют APK/VPS deploy. Hotfix/rollback/ops — явные исключения.

Подробности: `docs/DEVELOPMENT_PROCESS.md`, `docs/BUILD.md`, `docs/ANDROID_SIGNING.md`.

## Структура

```text
lib/                    Flutter thin client
  data/api/             server API clients and owner decisions
  data/broker/          device broker/sandbox adapters
  domain/               domain contracts
  ui/                   screens/widgets
android/                native Android/Keystore boundary
server/                 source-of-truth backend, scheduler, research, lifecycle
.github/workflows/      QA, signing and delivery
docs/                   roadmap, API, process, secrets, ADR
```

## Безопасность

Базовые инварианты:

- секреты не коммитятся, не логируются и не встраиваются в APK;
- HMAC secret values не экспортируются из native vault в Dart и используются через native signing boundary;
- withdrawal permission не требуется и не должна выдаваться;
- analytics/read и execution credentials разделяются, если провайдер это позволяет;
- auth, reconciliation, source validation и protection fail closed;
- повтор/рестарт не должен создавать второе внешнее действие;
- backtest, paper, sandbox и live не смешиваются.

Детали credential boundaries: `docs/SECRETS.md`.

## Дисклеймер

Система предназначена для личного использования владельцем счетов. Торговля фьючерсами и криптовалютой может привести к значительным убыткам; наличие сигнала или автоматизированного контура не является гарантией результата.
