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
- Create: `server/alembic/versions/0045_owner_step_up.py`
- Test: `server/tests/security/test_owner_step_up_schema.py`

**Interfaces:**
- Produces `DeviceOwnerKey` and `OwnerStepUpChallenge` SQLAlchemy models.

- [x] Add PostgreSQL tests for one-active-key-per-device, bounded algorithms/hash/nonce, FK challenge→key and monotonic challenge consumption.
- [x] Add models and migration with DB constraints/indexes.
- [ ] Run focused/full tests and migration/model-parity checks GREEN on the rebased head.

### Task 2: Enroll P-256 public key only through pairing

**Files:**
- Modify: `server/app/api/v1/device_enrollment.py`
- Modify: `server/app/device_enrollment.py`
- Create: `server/app/owner_step_up.py`
- Test: `server/tests/security/test_owner_step_up_enrollment.py`

**Interfaces:**
- Produces `validate_owner_public_key_spki_b64(...)` and pairing-time owner-key replacement.
- Pair request gains optional `owner_public_key_spki_b64`.

- [x] Add tests proving valid P-256 enrollment succeeds, malformed/non-P-256 keys fail, bearer-only rotation cannot replace the key, and re-pair can replace it only under owner pairing capability.
- [x] Implement strict DER SPKI/base64/P-256 validation and pairing-bound enrollment.
- [x] Preserve pairing backward compatibility when the field is absent.
- [ ] Run focused/full tests GREEN on the rebased head.

### Task 3: Server challenge and signature verifier

**Files:**
- Modify: `server/app/owner_step_up.py`
- Create: `server/app/api/v1/owner_step_up.py`
- Modify: `server/app/main.py`
- Test: `server/tests/security/test_owner_step_up_challenge.py`
- Test: `server/tests/security/test_owner_step_up_api.py`

**Interfaces:**
- `issue_owner_step_up_challenge(...)` returns challenge id, canonical message, expiry.
- `verify_owner_step_up_signature(...)` returns a proof receipt with purpose/payload hash/device/key fingerprint/verified_at.
- API purpose is fixed to `OWNER_STEP_UP_SELF_TEST`; no arbitrary-action or Canary authorization endpoint.

- [x] Add tests for deterministic canonical payload binding, wrong key/signature/device, expiry, replay and successful single consumption.
- [x] Add tests proving no execution mode/kill-switch mutation.
- [x] Implement random 32-byte nonce, domain-separated message, ECDSA P-256 SHA-256 DER verification and transaction-safe consume.
- [x] Add bounded no-store self-test challenge/verify API.
- [ ] Run focused/full tests GREEN on the rebased head.

### Task 4: Android Keystore biometric signer

**Files:**
- Modify: `android/app/src/main/kotlin/ru/signalai/app/MainActivity.kt`
- Create: `android/app/src/main/kotlin/ru/signalai/app/OwnerStepUpSigner.kt`
- Modify: `lib/data/native_bridge.dart`
- Modify: `lib/data/api/device_enrollment.dart`
- Test: `test/device_enrollment_contract_test.dart`
- Test: `test/owner_step_up_mobile_contract_test.dart`

**Interfaces:**
- `ownerStepUpPublicKey` returns base64 SPKI only.
- `ownerStepUpSign` accepts exact UTF-8 message and returns base64 DER ECDSA signature after `BIOMETRIC_STRONG` authentication.

- [x] Add Dart/native-contract tests and assert no private-key material is returned or persisted in Dart.
- [x] Implement Android Keystore P-256 key creation with per-use biometric authentication and `BiometricPrompt.CryptoObject(Signature)`.
- [x] Add bridge calls without changing current app flow or automatically prompting users.
- [ ] Run Flutter analyze/tests GREEN on the rebased head.

### Task 5: Exact-head verification and security review

- [ ] Run exact-head Quality Gate: server imports, Alembic single-head/model parity, full pytest, Flutter analyze/test, tracked-secret scan and delivery validation.
- [ ] Review source→sink boundaries: pairing capability → owner key; bearer → self-test challenge only; biometric signature → proof receipt only; confirm no path reaches mode/credential/provider/safety mutation.
- [ ] Record remaining owner blockers: ADR-0002 acceptance, final TTL, exact Canary policy/provider account, physical biometric acceptance.
- [ ] Merge only if exact-head QG is GREEN and no Critical/High remains.
