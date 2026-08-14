# Server runtime diagnostics — P0 slice

Issue: #39.

## Goal

Correlate one owner/API request with a safe aggregate runtime snapshot, so a missing idea or stuck paper lifecycle can be located without exposing credentials or raw business payloads.

## Scope

- Add a request UUID to every HTTP response as `X-Request-ID`.
- Preserve a valid incoming UUID; replace invalid/missing values with a generated UUID.
- Expose the ID as `request.state.request_id`.
- Add authenticated `GET /api/v1/diagnostics/runtime`.
- Aggregate existing idea, paper and notification state only.
- No new database tables, migrations, dependencies, SaaS telemetry or trading behavior changes.

## Middleware order

`RequestIdMiddleware` is registered last so Starlette places it outside `DeviceTokenMiddleware`. It does not read the body or Authorization value. Therefore even fail-closed 401/503 API responses carry a correlation ID.

## Runtime response

The owner endpoint returns:

- `request_id` and UTC `generated_at`;
- ideas: total, counts by status, latest signal timestamp;
- paper trades: total, counts by status, live count, unreconciled live count, oldest non-null live reconciliation timestamp;
- notifications: total, latest outbox ID and latest creation timestamp.

It never returns instrument IDs, idea plans, market snapshots, notification dedup keys/title/body/payload, tokens or headers.

## Verification

TDD tests cover generated/preserved/replaced request IDs, unauthorized correlation, empty aggregate state, populated aggregate counters and forbidden payload leakage. Exact-head Quality Gate must pass before merge.