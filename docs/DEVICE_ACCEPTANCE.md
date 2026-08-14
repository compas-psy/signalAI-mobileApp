# SignalAI device acceptance

Issue: #6. This checklist is the only basis for a real-device PASS. CI success is necessary but is not a substitute for this session.

## Artifact identity — fill before testing

Test exactly one cumulative thin APK and keep its release metadata beside the result.

- source SHA: `<40-char source_sha from signalai-sideload.json>`
- workflow run: `<GitHub Actions run>`
- APK SHA-256: `<apk_sha256>`
- signer certificate SHA-256: `<signer_certificate_sha256>`
- app/build version: `<Settings → Diagnostics / installed app version>`
- device: `<Samsung model>`
- Android / One UI: `<versions>`
- tested at: `<local timestamp>`

If any artifact identity changes during the session, start a new acceptance record. Do not combine results from two APKs.

## Before the first action

1. Install/update the exact cumulative sideload APK.
2. Cold-launch SignalAI once.
3. Confirm thin/server mode and the intended Engine endpoint.
4. Confirm the app is not using demo fixtures.
5. Record the current device-local runtime diagnostic count. Existing historical events are baseline, not failures of this build.
6. Open the authenticated server runtime diagnostics once and record its `X-Request-ID` / `request_id` plus aggregate state.

## A. Navigation stress

Repeat 30 cycles:

1. Open **Настройки → Подключения**.
2. Return to **Идеи** and switch to another visible ideas pill.
3. Scroll nested rows in both sections.
4. Use Android BACK instead of only in-app controls at least every third cycle.

PASS: shell remains responsive, no grey/error screen, no frozen input, no unexpected process restart.

## B. Idea detail / BACK

Choose one currently visible real server idea and repeat 30 times:

1. Open the idea.
2. Verify the same ticker/idea remains selected.
3. If the server detail contains a TradePlan, Entry/SL/TP and actionability must not disappear after reopening.
4. BACK must close detail first, then unwind app navigation; it must not unexpectedly exit while an internal navigation level remains.

If a chart is unavailable, record the user-facing reason instead of treating a truthful unavailable state as a crash.

## C. Telegram / deep-link flow

Repeat the same real idea link at least 10 times, including:

- app already foregrounded;
- app backgrounded;
- app process cold / launched by the link.

PASS: the requested idea opens each time. A resumed app must refresh the thin feed before detail hydration so a late summary response cannot overwrite the full TradePlan.

## D. Network transitions

Keep the same installed process where possible and perform:

1. Wi-Fi → LTE/mobile data.
2. Mobile data → Wi-Fi.
3. VPN off → on.
4. VPN on → off.
5. Disable connectivity, trigger a safe read/refresh, then restore connectivity and refresh again.

PASS: failures are recoverable and explained; after connectivity returns, safe reads recover without reinstall/restart. TLS interception or geo-block is an environment failure and must stay distinguishable from generic offline state.

## E. Lifecycle

1. Background for at least 15 seconds → foreground; repeat twice.
2. Open a deep link after one background cycle.
3. Force-stop the app, then cold-launch normally.
4. Cold-launch once from a real idea deep link.

PASS: no startup loop, stale grey screen, or lost internal navigation state that prevents normal use.

## Evidence after the session

Record:

- device-local runtime diagnostic count after the session and delta from baseline;
- counts by kind (`flutter`, `async`, `ideaHydration`, `chartLoad`, `sandboxReconciliation`);
- any relevant server runtime diagnostics snapshot/request ID;
- exact reproduction steps for every new unexpected `flutter`/`async` event;
- screenshots only when they clarify a visible defect; do not paste credentials or raw Authorization headers.

## Verdict

**PASS** only when all sections A–E pass on the single identified APK and there are no unexplained new fatal/unhandled runtime events.

**FAIL** when a reproducible runtime regression occurs on that APK. Record the first failing section and evidence; do not average it away with previous successful releases.

Environment failures (provider outage, explicit geo-block, TLS interception, deliberately disabled network) are recorded but are not app-runtime failures if SignalAI remains responsive, reports the cause safely and recovers when the environment is restored.