#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# bmw_flow.sh
#
# Obtain a new BMW CarData OAuth2 device code and generate id_token / refresh_token.
# Requires manual user authorization via the provided BMW login URL.
#
# Copyright (c) 2025 Kurt, DJ0ABR
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
# -----------------------------------------------------------------------------
# bmw_flow.sh
#
# Purpose:
#   Run the BMW OAuth 2.0 Device Authorization Grant (with PKCE) to obtain:
#     - access_token
#     - id_token           (→ use as MQTT password for BMW Streaming MQTT)
#     - refresh_token      (→ use to refresh tokens later)
#
# Usage:
#   ./bmw_flow.sh
#
# Requirements:
#   - bash, curl, jq, openssl
#
# Behavior:
#   - On the first run, this script creates the directory:
#       ~/.local/state/bmw-mqtt-bridge
#   - If no .env file exists, it will be created automatically with placeholders
#     and opened in your text editor (nano by default).
#   - Enter your real CLIENT_ID and GCID, save, exit the editor,
#     and then rerun the script.
#
# Outputs (stored in ~/.local/state/bmw-mqtt-bridge, permissions 644):
#   access_token.txt
#   id_token.txt
#   refresh_token.txt
#
# Notes:
#   - This script is interactive: it prints a verification URL you must open
#     in a browser and confirm login/consent.
#   - Polls the token endpoint respecting "interval" and "slow_down".
#   - id_token is a JWT that contains the 'exp' claim used by the bridge.
#
# Security:
#   - Files are written with safe permissions (rw-r--r--).
#   - Do NOT commit real tokens/IDs to Git repositories.
# -----------------------------------------------------------------------------


set -euo pipefail

# ---------- Fixed locations (no path overrides) ----------
STATE_BASE="${XDG_STATE_HOME:-$HOME/.local/state}"
OUT_DIR="$STATE_BASE/bmw-mqtt-bridge"
ENV_FILE="$OUT_DIR/.env"

# Ensure token dir exists
mkdir -p "$OUT_DIR"

# ---------- Bootstrap .env if missing ----------
if [[ ! -f "$ENV_FILE" ]]; then
  cat >"$ENV_FILE" <<'EOF'
# BMW CarData Credentials
CLIENT_ID=11111111-1111-1111-1111-111111111111
GCID=11111111-1111-1111-1111-111111111111

# Optional MQTT Configuration
#LOCAL_HOST=mosquitto
#LOCAL_PORT=1883
#LOCAL_PREFIX=bmw/
#LOCAL_USER=username
#LOCAL_PASSWORD=password
EOF
  if ! [ -t 0 ]; then
    echo "Non-interactive mode detected."
    echo "Run with an interactive TTY:"
    echo "  docker compose run --rm -it bmw-bridge ./bmw_flow.sh"
    exit 1
  fi
  echo "Created $ENV_FILE"
  echo "Please enter your real CLIENT_ID and GCID into $ENV_FILE."
  "${EDITOR:-nano}" "$ENV_FILE" || true
  echo "Re-run this script after updating $ENV_FILE."
  exit 1
fi

# ---------- Load .env from fixed location ----------
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# ---------- Validate CLIENT_ID / GCID ----------
if [[ -z "${CLIENT_ID:-}" || "$CLIENT_ID" == "11111111-1111-1111-1111-111111111111" ]]; then
  if ! [ -t 0 ]; then
    echo "Non-interactive mode detected."
    echo "Run with an interactive TTY:"
    echo "  docker compose run --rm -it bmw-bridge ./bmw_flow.sh"
    exit 1
  fi
  echo "✖ CLIENT_ID is missing or still the placeholder in $ENV_FILE." >&2
  "${EDITOR:-nano}" "$ENV_FILE" || true
  echo "Re-run this script after updating $ENV_FILE."
  exit 1
fi

if [[ -z "${GCID:-}" || "$GCID" == "11111111-1111-1111-1111-111111111111" ]]; then
  if ! [ -t 0 ]; then
    echo "Non-interactive mode detected."
    echo "Run with an interactive TTY:"
    echo "  docker compose run --rm -it bmw-bridge ./bmw_flow.sh"
    exit 1
  fi
  echo "✖ GCID is missing or still the placeholder in $ENV_FILE." >&2
  "${EDITOR:-nano}" "$ENV_FILE" || true
  echo "Re-run this script after updating $ENV_FILE."
  exit 1
fi

# ---------- Static OAuth config ----------
DEVICE_ENDPOINT="https://customer.bmwgroup.com/gcdm/oauth/device/code"
TOKEN_ENDPOINT="https://customer.bmwgroup.com/gcdm/oauth/token"
SCOPES="authenticate_user openid cardata:streaming:read"

# ---------- Pre-flight checks ----------
need() { command -v "$1" >/dev/null 2>&1 || { echo "✖ Missing dependency: $1" >&2; exit 1; }; }
need curl
need jq
need openssl

# We want token files to be world-readable enough for common service setups (owner RW, group/other R).
umask 022

# ---------- PKCE (per run) ----------
CODE_VERIFIER="$(openssl rand -base64 96 | tr -d '=+/ ' | cut -c1-96)"
CODE_CHALLENGE="$(printf '%s' "$CODE_VERIFIER" \
  | openssl dgst -sha256 -binary \
  | openssl base64 -A \
  | tr '+/' '-_' \
  | tr -d '=')"

echo "1) Requesting device code…"
RESP="$(curl -sS \
  -H "Accept: application/json" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode client_id="$CLIENT_ID" \
  --data-urlencode scope="$SCOPES" \
  --data-urlencode code_challenge="$CODE_CHALLENGE" \
  --data-urlencode code_challenge_method="S256" \
  "$DEVICE_ENDPOINT")"

DEVICE_CODE="$(jq -r '.device_code // empty' <<<"$RESP")"
VERIFY_URL="$(jq -r '.verification_uri_complete // (.verification_uri + "?user_code=" + .user_code)' <<<"$RESP")"
INTERVAL="$(jq -r '.interval // 5' <<<"$RESP")"
EXPIRES_IN="$(jq -r '.expires_in // 300' <<<"$RESP")"

if [[ -z "$DEVICE_CODE" || -z "$VERIFY_URL" ]]; then
  echo "✖ Failed to obtain device_code. Raw response:" >&2
  echo "$RESP" | jq . >&2 || echo "$RESP" >&2
  exit 1
fi

echo "   Device code received:"
echo "$RESP" | jq '{device_code, user_code, verification_uri, verification_uri_complete, interval, expires_in}'

echo
echo "2) Open the following URL in your browser and complete login/consent:"
echo "   $VERIFY_URL"
echo
trap 'echo; echo "Aborted by user."; exit 1' INT
read -r -p "Press Enter once you see 'Anmeldung erfolgreich / Login successful'… " _
trap - INT

echo
echo "3) Polling token endpoint every ${INTERVAL}s (timeout ~${EXPIRES_IN}s)…"
LEFT="$EXPIRES_IN"

while (( LEFT > 0 )); do
  TOK="$(curl -sS \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode grant_type="urn:ietf:params:oauth:grant-type:device_code" \
    --data-urlencode device_code="$DEVICE_CODE" \
    --data-urlencode client_id="$CLIENT_ID" \
    --data-urlencode code_verifier="$CODE_VERIFIER" \
    "$TOKEN_ENDPOINT")"

  ERR="$(jq -r '.error // empty' <<<"$TOK")"

  if [[ -z "$ERR" ]]; then
    echo
    echo "✔ Tokens received:"
    echo "$TOK" | jq '{access_token: .access_token|type, id_token: (.id_token|type), refresh_token: (.refresh_token|type), expires_in}'

    # Write tokens into fixed OUT_DIR (permissions end up 0644 due to umask 022)
    jq -r '.access_token'  <<<"$TOK" > "$OUT_DIR/access_token.txt"
    jq -r '.id_token'      <<<"$TOK" > "$OUT_DIR/id_token.txt"
    jq -r '.refresh_token' <<<"$TOK" > "$OUT_DIR/refresh_token.txt"
    chmod 0644 "$OUT_DIR"/{access_token.txt,id_token.txt,refresh_token.txt} || true

    echo "Saved tokens in: $OUT_DIR"
    echo "→ MQTT password = contents of $OUT_DIR/id_token.txt"
    exit 0
  else
    DESC="$(jq -r '.error_description // ""' <<<"$TOK")"
    case "$ERR" in
      authorization_pending)
        printf "…waiting for user confirmation (remaining ~%ss)\r" "$LEFT"
        ;;
      slow_down)
        INTERVAL=$((INTERVAL+5))
        printf "Server says 'slow_down' → new interval %ss (remaining ~%ss)\r" "$INTERVAL" "$LEFT"
        ;;
      access_denied)
        printf "\n✖ Access denied in browser. %s\n" "$DESC" >&2
        exit 1
        ;;
      expired_token|expired_device_code|invalid_grant)
        printf "\n✖ Device code expired/invalid. %s\n" "$DESC" >&2
        echo "Hint: CODE_VERIFIER must match the device-code run." >&2
        exit 1
        ;;
      *)
        printf "\n✖ Unexpected error: %s (%s)\n" "$ERR" "$DESC" >&2
        echo "$TOK" | jq . >&2 || true
        exit 1
        ;;
    esac
  fi

  sleep "$INTERVAL"
  LEFT=$((LEFT-INTERVAL))
done

echo
echo "✖ Timed out waiting for user confirmation." >&2
exit 1
