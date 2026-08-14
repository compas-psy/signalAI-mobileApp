# Runtime Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist a secret-safe bounded local crash/error history with build provenance and global Flutter/async capture for Android thin-client diagnostics.

**Architecture:** Add one focused recorder in `lib/monitor/runtime_error_recorder.dart`, backed by existing `LocalStore`. Wire it in `main.dart` without changing trading behavior. Inject immutable source/app identity from the sideload workflow.

**Tech Stack:** Dart 3.10, Flutter 3.44.8, existing `LocalStore`, Flutter test, GitHub Actions.

## Global Constraints

- No external telemetry SDK.
- No new telemetry database.
- No broker/trading/execution behavior changes.
- No raw credentials may be persisted.
- Default retention: newest 50 events.
- Preserve existing crash/unhandled semantics.

---

### Task 1: Recorder behavior

**Files:**
- Create: `test/runtime_error_recorder_test.dart`
- Create: `lib/monitor/runtime_error_recorder.dart`

**Interfaces:**
- Produces: `RuntimeErrorRecorder`, `RuntimeErrorEvent`, `RuntimeBuildIdentity`, `RuntimeErrorKind`.
- `RuntimeErrorRecorder.record({required RuntimeErrorKind kind, required Object error, StackTrace? stackTrace}) -> Future<void>`.
- `RuntimeErrorRecorder.events() -> Future<List<RuntimeErrorEvent>>`.

- [ ] Write tests first for credential redaction plus app/source identity, bounded retention, and persistence across new recorder/store instances.
- [ ] Run the PR quality gate and confirm Flutter tests fail because `runtime_error_recorder.dart` does not yet exist.
- [ ] Implement the minimal recorder: sanitize before write, serialize events under `runtime_error_history`, keep newest `maxEvents`, swallow recorder-storage failures.
- [ ] Re-run the quality gate until recorder tests are green.

### Task 2: Global runtime capture

**Files:**
- Modify: `lib/main.dart`

**Interfaces:**
- Consumes: `RuntimeErrorRecorder.record`.

- [ ] Import `dart:async` and `dart:ui`, create the recorder immediately after `WidgetsFlutterBinding.ensureInitialized()`.
- [ ] Install `FlutterError.onError`, recording `RuntimeErrorKind.flutter` and then delegating to the previous/default handler.
- [ ] Install `PlatformDispatcher.instance.onError`, recording `RuntimeErrorKind.async` and returning `false` so the error remains unhandled.
- [ ] Warm recorder persistence before existing app bootstrap continues.
- [ ] Run analyze/tests.

### Task 3: Immutable build provenance

**Files:**
- Modify: `.github/workflows/android-sideload.yml`
- Modify: `test/runtime_error_recorder_test.dart`

**Interfaces:**
- Consumes Dart defines `SIGNALAI_SOURCE_SHA` and `SIGNALAI_APP_VERSION` through `RuntimeBuildIdentity.current`.

- [ ] Add a test that the recorder's default identity fields are always non-empty.
- [ ] Pass `--dart-define=SIGNALAI_SOURCE_SHA=${{ needs.resolve.outputs.source_sha }}` to the release build.
- [ ] Pass `--dart-define=SIGNALAI_APP_VERSION=1.0.0+$GITHUB_RUN_NUMBER` to the release build, matching the current pubspec build name and workflow build number.
- [ ] Verify workflow validation/secret scan plus Flutter analyze/tests all pass.

### Task 4: Exact-head completion

**Files:**
- PR only; no additional production files unless verification finds a defect.

- [ ] Inspect the PR diff for unrelated changes and secret leakage.
- [ ] Confirm Quality Gate success for the exact PR head SHA.
- [ ] Mark PR ready and squash-merge with `expected_head_sha` pinned.
- [ ] Confirm issue #39 remains open for the explicitly deferred server counters/health-summary follow-up; this PR closes only the frozen device-local slice.