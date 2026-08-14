# Pipeline health counters — P0 observability slice

Issue: #39.

## Goal

Extend the authenticated runtime snapshot with enough aggregate evidence to tell whether a failure occurred before decision, during lifecycle management, in market-data quality, or during an idempotent owner retry.

## Existing durable sources only

No new table or migration is introduced. Counters are derived from:

- `PaperTrade` and `IdeaSkip` for owner decisions;
- append-only `IdeaEvent` for lifecycle transitions;
- `DataQualityEvent` for provider/candle quality degradation;
- append-only `AuditEvent` for successful idempotent decision replays.

## Safe response

`GET /api/v1/diagnostics/runtime` adds aggregate-only sections:

- decisions: approved/rejected counts;
- lifecycle: total, counts by resulting status, latest event timestamp;
- data quality: total, counts by flag, latest event timestamp;
- idempotency: approve/reject replay counts.

The response never returns idea/instrument IDs, audit trace IDs, comments, reason details, market snapshots, notification payloads, credentials or headers.

## Replay correlation

A successful repeated `approve-paper` or `reject` request appends a safe `AuditEvent` with only actor, fixed action name, idea ID as internal subject and the request correlation ID as `trace_id`. Detail and before/after JSON remain empty. This records repair/retry pressure without duplicating the business decision.

HTTP 409 conflicts are deliberately not persisted in this slice: the current request transaction rolls back on the exception, and changing that transaction boundary solely for telemetry would change business semantics. A later counter may use an independent observability sink if justified.

## Verification

Tests first prove empty and populated aggregates, absence of raw payload/detail/trace values, and exact request-ID correlation for approve/reject replays. Full Quality Gate must pass on the exact final PR head before merge.