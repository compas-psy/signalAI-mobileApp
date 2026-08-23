# Owner Step-Up Foundation Design

## Purpose

Build a cryptographic fresh-owner-presence primitive for future SAI-084 Canary activation without granting any execution authority in this change.

The existing enrolled-device bearer remains the ordinary API credential. It may identify the device and request a challenge, but it must never count as owner step-up proof. Fresh proof comes from an Android Keystore P-256 private key that requires `BIOMETRIC_STRONG` authentication for every signature.

## Security properties

- The private key is generated and retained by Android Keystore; it is never exported or uploaded.
- The corresponding P-256 SubjectPublicKeyInfo public key is enrolled only through the existing short-lived owner-provisioned pairing flow, never through an ordinary device bearer endpoint.
- A step-up challenge is server-generated, random, single-use and bound to one active device, one exact purpose and one exact canonical non-secret action payload hash.
- The signed message includes an explicit domain separator, challenge UUID, random nonce, device id, purpose, payload hash, issued timestamp and expiry timestamp.
- Verification accepts only ECDSA P-256 + SHA-256, exact DER signatures, the enrolled active key and an unexpired unused challenge.
- Invalid/malformed/expired/replayed signatures fail closed and do not consume a challenge; a successful verification consumes it exactly once.
- Revoked/lost devices cannot obtain or verify fresh step-up challenges.
- The verifier returns a non-authorizing proof receipt. It does not change execution mode, clear safety, provision live credentials, allocate capital, create a Lighter transport or call a provider.
- SAI-084 remains blocked on accepted ADR-0002, owner-approved challenge TTL, capital/caps/allowlists, provider-account decision and final submit wiring.

## Data model

### `device_owner_keys`

One active public key per `device_id`.

Fields:
- `id UUID` primary key
- `device_id VARCHAR(64)`
- `algorithm VARCHAR(32)` fixed to `ECDSA_P256_SHA256`
- `public_key_spki_b64 TEXT` bounded canonical base64 DER SPKI
- `public_key_sha256 CHAR(64)` non-secret fingerprint for audit/binding
- `enrolled_pairing_session_verifier CHAR(64)` records which owner-provisioned pairing capability established the key
- `enrolled_at timestamptz`
- `revoked_at timestamptz nullable`

A new owner pairing for the same `device_id` may revoke the prior key and enroll a new one; ordinary token rotation does not replace the owner key.

### `owner_step_up_challenges`

Fields:
- `id UUID` primary key
- `device_id VARCHAR(64)`
- `owner_key_id UUID`
- `purpose VARCHAR(64)`
- `payload_hash CHAR(64)`
- `nonce_hex CHAR(64)` generated with 32 random bytes
- `issued_at timestamptz`
- `expires_at timestamptz`
- `consumed_at timestamptz nullable`

The table is mutable only for the monotonic `consumed_at` transition; callers cannot change purpose/payload/key/nonce/expiry after issuance.

## Enrollment

`POST /api/v1/device-enrollment/pair` accepts an optional `owner_public_key_spki_b64`. When present, it is validated as a P-256 public key and enrolled in the same pairing transaction/capability. Existing clients that omit it continue to pair normally but cannot use owner step-up.

No bearer-only endpoint is added for owner-key enrollment or replacement.

## Challenge service

The server module exposes internal functions:

- `issue_owner_step_up_challenge(db, credential_id, purpose, payload, ttl)`
- `verify_owner_step_up_signature(db, credential_id, challenge_id, signature_b64)`

`payload` is canonicalized server-side with deterministic JSON and SHA-256. A future Canary endpoint must construct that payload from the immutable Canary snapshot and server state rather than from a client-supplied digest.

For this foundation, the API exposes a deliberately non-authorizing probe endpoint only for a fixed purpose `OWNER_STEP_UP_SELF_TEST` and server-generated payload containing the authenticated device id. This proves the Android/server ceremony end-to-end without creating a generic arbitrary-action signing oracle.

The production Canary path is not wired in this change.

## Android

A native `MethodChannel` capability in `MainActivity.kt` provides:

- `ownerStepUpPublicKey` → creates/reuses a P-256 Keystore key and returns the SPKI public key
- `ownerStepUpSign` → invokes `BiometricPrompt` with a `Signature` backed by the key and signs exactly the server-provided UTF-8 message

Key generation requires user authentication for every use (`setUserAuthenticationParameters(0, AUTH_BIOMETRIC_STRONG)`) and invalidates the key if biometric enrollment changes where supported.

Dart only receives the public key and final signature. It never receives private-key bytes.

## Error handling

Every cryptographic/parser/database mismatch returns a bounded public error without echoing public-key DER, signature material, nonce-bearing canonical message, auth headers or raw exceptions.

## Tests

Server PostgreSQL tests cover:
- valid P-256 enrollment
- malformed/non-P-256 key rejection
- bearer cannot enroll/replace owner key
- exact payload/domain binding
- wrong key/signature/purpose/payload rejection
- expiry and replay rejection
- successful consume exactly once
- revoked device/key rejection
- no execution-mode or kill-switch mutation

Android/Dart tests cover method-channel contract and verify no private key is exposed. Full biometric hardware behavior remains a physical-device acceptance item; CI validates compile/analyze/unit contracts.

## Non-goals

This change does not choose the final Canary TTL, does not accept ADR-0002, does not make `challenge_issuable=True`, does not authorize SANDBOX→CANARY, and does not resolve the Lighter Standard-key vs Public-Pool provider-account decision.
