# Production SSH host-key pinning

Issue: #43.

## Goal

Remove trust-on-first-use from every production SSH path. A live `ssh-keyscan` result is only an untrusted candidate; it becomes trusted only when its SHA-256 fingerprint matches the separately configured production pin.

## Pin

Repository Actions secret: `VPS_SSH_HOST_KEY_SHA256`.

Accepted form: `SHA256:<OpenSSH base64 fingerprint>` as printed by `ssh-keygen -lf <key> -E sha256`.

The production fingerprint is deliberately not committed to the public repository. It must be verified out of band (for example from the VPS provider console or an already trusted host session) before the secret is created or rotated.

## Shared verifier

`.github/scripts/prepare_known_host.sh`:

1. rejects an empty host or missing/malformed expected SHA-256 pin;
2. obtains current host-key candidates with `ssh-keyscan` without trusting them;
3. calculates each candidate fingerprint with `ssh-keygen -E sha256`;
4. writes only the exact matching key to `~/.ssh/known_hosts`;
5. enables strict host-key checking;
6. fails closed when no candidate matches.

No workflow may fall back to `StrictHostKeyChecking=accept-new`.

## Covered production workflows

- `deploy-release.yml`;
- `deploy-server.yml`;
- `deploy-server-package.yml`;
- `sync-telegram-secrets.yml`.

## Rotation

A legitimate VPS host-key rotation is an explicit operational change: verify the new fingerprint independently, update `VPS_SSH_HOST_KEY_SHA256`, then run the normal deployment/secret-sync workflow. A mismatch reported by the workflow is evidence to investigate, never an authority for automatically changing the pin.

## Verification

Deterministic tests fake both `ssh-keyscan` and `ssh-keygen` and prove matching, mismatching and malformed-pin behavior. Static regression coverage also rejects direct workflow `ssh-keyscan` trust and `accept-new` fallback.