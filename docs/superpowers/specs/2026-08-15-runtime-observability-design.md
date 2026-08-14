# Runtime observability — device-local P0 slice

Issue: #39.

## Goal

Make Android thin-client blocker crashes and unhandled runtime errors diagnosable after restart without sending secrets to third parties or changing trading/execution behavior.

## Frozen scope

- Device-local bounded crash/error history persisted through `LocalStore`.
- Redact credential-like values before persistence.
- Attach app version and immutable source SHA to every event.
- Install global Flutter framework and uncaught async error boundaries during startup.
- No external telemetry SDK, no new telemetry database, no server/broker/trading behavior changes in this slice.

## Architecture

Add a focused `RuntimeErrorRecorder` under `lib/monitor/`. It owns event serialization, redaction, bounded retention, persistence and build identity. `main.dart` creates it immediately after Flutter binding initialization, installs framework/async handlers, warms its local store, then continues the existing bootstrap.

Release builds inject `SIGNALAI_SOURCE_SHA` and `SIGNALAI_APP_VERSION` as Dart defines from the already-resolved immutable sideload source and build number. Development/test builds use explicit safe defaults or constructor-supplied identity.

## Event schema

Each stored event contains:

- UTC timestamp;
- kind (`flutter` or `async`);
- redacted error text;
- redacted stack text when present;
- app version;
- source SHA.

The store keeps only the newest 50 events by default. Recording failures are swallowed so diagnostics can never crash the app or change crash semantics.

## Redaction

Before any write, redact at minimum:

- `Authorization:` / `Authorization=` values;
- `Bearer <credential>` and `Basic <credential>` values;
- common credential fields such as `token`, `access_token`, `refresh_token`, `api_key`, `secret`, and `password` in `key=value` or `key: value` form.

Only sanitized strings reach `LocalStore`.

## Error boundaries

- `FlutterError.onError`: record the framework error and preserve the previous/default handler.
- `PlatformDispatcher.instance.onError`: record uncaught async errors and return `false`, preserving existing fatal/unhandled semantics rather than silently consuming failures.

## Verification

Regression tests must prove:

1. credentials are absent from persisted event JSON;
2. app/source identity is present on every event;
3. retention drops oldest events beyond the configured bound;
4. a second recorder backed by a new `LocalStore` instance can read events written by the first.

The PR quality gate must pass Flutter analyze/tests, secret scan, and server regression jobs before exact-head merge.