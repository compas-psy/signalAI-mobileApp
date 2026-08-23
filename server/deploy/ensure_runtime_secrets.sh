#!/usr/bin/env bash
# Ensure server-local runtime secrets and immutable release provenance.
# Never enable xtrace here: generated secret values must not reach logs.

set -euo pipefail
umask 077

ENV_FILE="${1:-}"
KEY_NAME='SIGNALAI_LIGHTER_LIVE_SECRETS_KEY'
SOURCE_KEY='SIGNALAI_SOURCE_SHA'
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ATTESTATION="$SCRIPT_DIR/../../.signalai-source-sha"

fail() {
  printf 'runtime secret setup failed: %s\n' "$1" >&2
  exit 1
}

[ -n "$ENV_FILE" ] || fail 'env file path is required'
[ -f "$ENV_FILE" ] || fail 'env file does not exist'

# Canonical deploy writes the exact QA-verified commit to the release root before
# this helper runs. Persist the same value in the compose env so long-lived API
# and execution containers can prove which release generated acceptance evidence.
# This value is public provenance, not a secret, but it belongs in the same
# atomically managed runtime env because compose consumes it there.
if [ -f "$SOURCE_ATTESTATION" ]; then
  source_sha="$(tr -d '\r\n' < "$SOURCE_ATTESTATION")"
  case "$source_sha" in
    *[!0-9a-f]*|'') fail "${SOURCE_KEY} attestation must be lowercase hex" ;;
  esac
  [ "${#source_sha}" -eq 40 ] || fail "${SOURCE_KEY} attestation must contain 40 characters"

  source_count="$(grep -c "^${SOURCE_KEY}=" "$ENV_FILE" || true)"
  [ "$source_count" -le 1 ] || fail "duplicate ${SOURCE_KEY} entries"

  source_tmp="$(mktemp "${ENV_FILE}.source.XXXXXX")"
  cleanup_source() { rm -f "$source_tmp"; }
  trap cleanup_source EXIT
  awk -v name="$SOURCE_KEY" 'index($0, name "=") != 1 { print }' "$ENV_FILE" > "$source_tmp"
  printf '%s=%s\n' "$SOURCE_KEY" "$source_sha" >> "$source_tmp"
  chmod 0600 "$source_tmp"
  if [ -e "$ENV_FILE" ]; then
    chown --reference="$ENV_FILE" "$source_tmp" 2>/dev/null || true
  fi
  mv -f "$source_tmp" "$ENV_FILE"
  trap - EXIT
  unset source_sha
fi

count="$(grep -c "^${KEY_NAME}=" "$ENV_FILE" || true)"
[ "$count" -le 1 ] || fail "duplicate ${KEY_NAME} entries"

existing=''
if [ "$count" -eq 1 ]; then
  existing="$(sed -n "s/^${KEY_NAME}=//p" "$ENV_FILE")"
fi

if [ -n "$existing" ]; then
  [ "${#existing}" -ge 32 ] || fail "${KEY_NAME} must contain at least 32 characters"
  case "$existing" in
    *$'\r'*|*$'\n'*) fail "${KEY_NAME} must be one line" ;;
  esac
else
  # 32 random bytes rendered as 64 lowercase hex characters. The value is
  # written only to the root-owned env file and never printed.
  generated="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
  [ "${#generated}" -eq 64 ] || fail 'could not generate live vault key'

  tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
  cleanup() { rm -f "$tmp"; }
  trap cleanup EXIT
  awk -v name="$KEY_NAME" 'index($0, name "=") != 1 { print }' "$ENV_FILE" > "$tmp"
  printf '%s=%s\n' "$KEY_NAME" "$generated" >> "$tmp"
  chmod 0600 "$tmp"
  if [ -e "$ENV_FILE" ]; then
    chown --reference="$ENV_FILE" "$tmp" 2>/dev/null || true
  fi
  mv -f "$tmp" "$ENV_FILE"
  trap - EXIT
  unset generated
fi

chmod 0600 "$ENV_FILE"
unset existing
