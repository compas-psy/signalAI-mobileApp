# Runtime Diagnostics Design

## Цель

Дать SignalAI минимальный диагностический позвоночник, чтобы пользовательский симптом можно было связать с конкретным server request и текущим состоянием pipeline без временных forensic-веток.

## Scope этого среза

1. Каждый HTTP response получает `X-Request-ID`.
2. Если клиент прислал корректный UUID в `X-Request-ID`, сервер сохраняет его; иначе генерирует новый UUID.
3. Request ID доступен endpoint-коду через `request.state.request_id`.
4. Новый owner-only `GET /api/v1/diagnostics/runtime` агрегирует уже существующие данные БД.
5. Новых таблиц, migrations, SaaS telemetry и изменений trading logic нет.

## Middleware

`RequestIdMiddleware` — внешний middleware. Он не читает body, Authorization или другие credentials. Поэтому correlation ID возвращается также на fail-closed `401/503` от `DeviceTokenMiddleware`.

Порядок регистрации FastAPI:

`OperationalLifecycleMiddleware → DeviceTokenMiddleware → RequestIdMiddleware`

FastAPI/Starlette помещает последний добавленный middleware снаружи предыдущих.

## Runtime snapshot

Endpoint возвращает только агрегаты и timestamps:

```json
{
  "request_id": "uuid",
  "generated_at": "UTC timestamp",
  "ideas": {
    "total": 0,
    "by_status": {},
    "latest_signal_at": null
  },
  "paper": {
    "total": 0,
    "by_status": {},
    "live": 0,
    "unreconciled_live": 0,
    "oldest_live_reconciled_at": null
  },
  "notifications": {
    "total": 0,
    "latest_id": null,
    "latest_created_at": null
  }
}
```

Не возвращаются instrument IDs, titles/bodies/payloads уведомлений, credentials, Authorization headers или market snapshots.

## Security

`/api/v1/diagnostics/runtime` находится под существующим `DeviceTokenMiddleware`. Отдельный bypass не создаётся. Request ID не является authentication token и не даёт доступа к данным.

## Verification

TDD:

1. tests first: request ID generation/preservation/unauthorized response + diagnostics contract;
2. Quality Gate должен упасть на отсутствующем поведении;
3. минимальная реализация;
4. exact-head Quality Gate должен стать зелёным;
5. PR merge не запускает production release автоматически.

## Следующий P0-срез

После этой основы: Android uncaught-error capture → redacted diagnostic event → correlation with request IDs. Это отдельный PR, чтобы не смешивать server diagnostics и mobile crash transport.