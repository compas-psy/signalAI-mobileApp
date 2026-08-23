# Owner Step-Up Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cryptographic, biometric-per-use owner step-up proof primitive without granting CANARY/LIVE execution authority.

**Architecture:** Reuse existing owner-provisioned device pairing to enroll a P-256 public key that is generated and retained in Android Keystore. Server-generated single-use challenges bind a domain-separated canonical action payload to that key and return a non-authorizing proof receipt after ECDSA verification. Canary activation remains disconnected and blocked.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL/Alembic, PyCryptodome already present through locked server dependencies, Flutter/Dart, Android Kotlin, Android Keystore/BiometricPrompt.

**Spec:** `docs/superpowers/specs/2026-08-23-owner-step-up-foundation-design.md`

## Global Constraints

- No CANARY/LIVE mode change, provider I/O, live credential mutation, capital allocation or kill-switch clear in this plan.
- Ordinary device bearer is identification only, never fresh owner presence.
- Owner public key enrollment/replacement is allowed only inside the existing owner-provisioned pairing capability.
- Private key never leaves Android Keystore.
- P-256 + SHA-256 only; malformed/expired/replayed/mismatched proof fails closed.
- Final Canary TTL and activation wiring remain owner/ADR decisions.

---

### Task 1: Persist owner key and challenge state

**Files:**
- Modify: `server/app/models/device.py`
- Modify: `server/app/models/__init__.py`
- Create: `server/alembic/versions/0044_owner_step_up.py`
- Test: `server/tests/security/test_owner_step_up_schema.py`

**Interfaces:**
- Produces `DeviceOwnerKey` and `OwnerStepUpChallenge` SQLAlchemy models.

- [ ] Add failing PostgreSQL tests for one-active-key-per-device, bounded algorithms/hash/nonce, FK challenge→key and monotonic challenge consumption.
- [ ] Run the focused test and retain the RED receipt.
- [ ] Add models and migration with DB constraints/indexes.
- [ ] Run focused tests and migration/model-parity checks GREEN.
- [ ] Commit.

### Task 2: Enroll P-256 public key only through pairing

**Files:**
- Modify: `server/app/api/v1/device_enrollment.py`
- Modify: `server/app/device_enrollment.py`
- Create: `server/app/owner_step_up.py`
- Test: `server/tests/security/test_owner_step_up_enrollment.py`

**Interfaces:**
- Produces `validate_owner_public_key_spki_b64(...)` and pairing-time `enroll_owner_key(...)`.
- Pair request gains optional `owner_public_key_spki_b64`.

- [ ] Add failing tests proving valid P-256 enrollment succeeds, malformed/non-P-256 keys fail, bearer-only flows cannot replace the key, and re-pair can replace it only under owner pairing capability.
- [ ] Run focused tests for RED.
- [ ] Implement strict DER SPKI/base64/P-256 validation and pairing-bound enrollment.
- [ ] Preserve pairing backward compatibility when the field is absent.
- [ ] Run focused tests GREEN.
- [ ] Commit.

### Task 3: Server challenge and signature verifier

**Files:**
- Modify: `server/app/owner_step_up.py`
- Create: `server/app/api/v1/owner_step_up.py`
- Modify router registration in the existing API bootstrap file.
- Test: `server/tests/security/test_owner_step_up_challenge.py`

**Interfaces:**
- `issue_owner_step_up_challenge(db, credential_id, purpose, payload, ttl)` returns challenge id, canonical message, expiry.
- `verify_owner_step_up_signature(db, credential_id, challenge_id, signature_b64)` returns a proof receipt with purpose/payload hash/device/key fingerprint/verified_at.
- API purpose is fixed to `OWNER_STEP_UP_SELF_TEST`; no arbitrary-action or Canary authorization endpoint.

- [ ] Add failing tests for deterministic canonical payload binding, wrong key/signature/device/payload, expiry, replay and successful single consumption.
- [ ] Add tests proving no execution mode/kill-switch mutation.
- [ ] Run RED.
- [ ] Implement random 32-byte nonce, domain-separated message, ECDSA P-256 SHA-256 DER verification and transaction-safe consume.
- [ ] Add bounded no-store self-test challenge/verify API.
- [ ] Run focused tests GREEN.
- [ ] Commit.

### Task 4: Android Keystore biometric signer

**Files:**
- Modify: `android/app/src/main/kotlin/ru/signalai/app/MainActivity.kt`
- Modify the existing Dart native bridge/service that invokes `ru.signalai.app/native`.
- Test: existing Flutter/native bridge unit tests plus new owner-step-up contract tests.

**Interfaces:**
- `ensureOwnerStepUpKey` returns base64 SPKI only.
- `signOwnerStepUpMessage` accepts exact UTF-8 message and returns base64 DER ECDSA signature after `BIOMETRIC_STRONG` authentication.

- [ ] Add failing Dart/native-contract tests that require the two methods and assert no private-key material is returned or persisted in Dart.
- [ ] Run RED.
- [ ] Implement Android Keystore P-256 key creation with per-use biometric authentication and `BiometricPrompt.CryptoObject(Signature)`.
- [ ] Add bridge calls without changing current app flow or automatically prompting users.
- [ ] Run Flutter analyze/tests GREEN.
- [ ] Commit.

### Task 5: Exact-head verification and security review

**Files:**
- Update docs only if verification reveals a factual correction.

- [ ] Run exact-head Quality Gate: server imports, Alembic single-head/model parity, full pytest, Flutter analyze/test, tracked-secret scan and delivery validation.
- [ ] Review source→sink boundaries: pairing capability → owner key; bearer → self-test challenge only; biometric signature → proof receipt only; confirm no path reaches mode/credential/provider/safety mutation.
- [ ] Record remaining owner blockers: ADR-0002 acceptance, final TTL, exact Canary policy/provider account, physical biometric acceptance.
- [ ] Merge only if exact-head QG is GREEN and no Critical/High remains.
