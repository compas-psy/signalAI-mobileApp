#!/usr/bin/env bash
set -euo pipefail

HOST="${1:-}"
EXPECTED="${2:-${VPS_SSH_HOST_KEY_SHA256:-}}"

if [ -z "$HOST" ]; then
  echo '::error::VPS host is missing' >&2
  exit 1
fi
case "$EXPECTED" in
  SHA256:*) ;;
  *)
    echo '::error::VPS_SSH_HOST_KEY_SHA256 must be a SHA256: fingerprint' >&2
    exit 1
    ;;
esac

candidate="$(mktemp)"
trap 'rm -f "$candidate"' EXIT

if ! ssh-keyscan -T 10 "$HOST" >"$candidate" 2>/dev/null || [ ! -s "$candidate" ]; then
  echo "::error::unable to obtain SSH host key candidate for $HOST" >&2
  exit 1
fi

matched=''
observed=''
while IFS= read -r line; do
  [ -n "$line" ] || continue
  fingerprint="$(printf '%s\n' "$line" | ssh-keygen -lf - -E sha256 2>/dev/null | awk '{print $2}')"
  [ -n "$fingerprint" ] || continue
  if [ -n "$observed" ]; then
    observed="$observed, $fingerprint"
  else
    observed="$fingerprint"
  fi
  if [ "$fingerprint" = "$EXPECTED" ]; then
    matched="$line"
    break
  fi
done < "$candidate"

if [ -z "$matched" ]; then
  echo "::error::SSH host key fingerprint mismatch for $HOST: expected $EXPECTED; observed ${observed:-none}" >&2
  exit 1
fi

install -d -m 700 "$HOME/.ssh"
printf '%s\n' "$matched" > "$HOME/.ssh/known_hosts"
chmod 600 "$HOME/.ssh/known_hosts"
cat > "$HOME/.ssh/config" <<'EOF'
Host *
  StrictHostKeyChecking yes
  UserKnownHostsFile ~/.ssh/known_hosts
EOF
chmod 600 "$HOME/.ssh/config"

echo "SSH host key verified against VPS_SSH_HOST_KEY_SHA256 ($EXPECTED)."
