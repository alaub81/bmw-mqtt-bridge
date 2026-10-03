#!/usr/bin/env bash
set -euo pipefail
umask 077

STATE_DIR="${BMB_BMW_TOKEN_DIR:-/app/token}"
ID_FILE="${STATE_DIR}/id_token.txt"
RT_FILE="${STATE_DIR}/refresh_token.txt"

echo "[entrypoint] Expecting tokens in: BMB_BMW_TOKEN_DIR=${STATE_DIR}"

# Restrict existing tokens too, including before running the authentication helper.
for token_file in "$ID_FILE" "$RT_FILE" "$STATE_DIR/access_token.txt"; do
  if [[ -f "$token_file" ]]; then
    chmod 0600 "$token_file"
  fi
done
# Remove the obsolete debug response, which can contain a complete token pair.
rm -f "$STATE_DIR/token_refresh_response.json"

# Run custom commands such as the authentication helper directly.
if [[ $# -gt 0 && "$1" != "/app/bmw_mqtt_bridge" ]]; then
  exec "$@"
fi

# Tokens are created by bmw_flow.sh in the persistent bmb_data_token volume.
if [[ ! -s "$ID_FILE" || ! -s "$RT_FILE" ]]; then
  echo "[entrypoint] Token pair missing, incomplete or empty in ${STATE_DIR}."
  echo "Please perform the initial authentication, e.g.:"
  echo "  docker compose run --rm -it bmw-mqtt-bridge ./bmw_flow.sh"
  exit 1
fi

# Start the bridge as the main container process.
exec /app/bmw_mqtt_bridge
