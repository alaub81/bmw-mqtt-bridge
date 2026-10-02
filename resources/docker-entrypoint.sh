#!/usr/bin/env bash
set -euo pipefail
umask 077

STATE_DIR="${BMW_TOKEN_DIR:-/app/token}"
ID_FILE="${STATE_DIR}/id_token.txt"
RT_FILE="${STATE_DIR}/refresh_token.txt"

echo "[entrypoint] Expecting tokens in: BMW_TOKEN_DIR=${STATE_DIR}"

# Run custom commands such as the authentication helper directly.
if [[ $# -gt 0 && "$1" != "/app/bmw_mqtt_bridge" ]]; then
  exec "$@"
fi

# Migrate a complete legacy token pair without overwriting any current tokens.
# LEGACY_DIR="${STATE_DIR}/bmw-mqtt-bridge"
# if [[ ! -e "$ID_FILE" && ! -e "$RT_FILE" &&
#       -s "$LEGACY_DIR/id_token" && -s "$LEGACY_DIR/refresh_token" ]]; then
#   for filename in id_token.txt refresh_token.txt access_token.txt .env token_refresh_response.json; do
#     if [[ -f "$LEGACY_DIR/$filename" && ! -e "$STATE_DIR/$filename" ]]; then
#       mv -- "$LEGACY_DIR/$filename" "$STATE_DIR/$filename"
#     fi
#   done
#   rmdir -- "$LEGACY_DIR" 2>/dev/null || true
#   echo "[entrypoint] Migrated legacy tokens into ${STATE_DIR}"
# fi

# Tokens are created by bmw_flow.sh in the persistent data-token volume.
if [[ ! -s "$ID_FILE" || ! -s "$RT_FILE" ]]; then
  echo "[entrypoint] Token pair missing, incomplete or empty in ${STATE_DIR}."
  echo "Please perform the initial authentication, e.g.:"
  echo "  docker compose run --rm -it bmw-mqtt-bridge ./bmw_flow.sh"
  exit 1
fi

# Restrict permissions on tokens carried over from earlier installations, too.
chmod 0644 "$ID_FILE" "$RT_FILE"

# Start the bridge as the main container process.
exec /app/bmw_mqtt_bridge
