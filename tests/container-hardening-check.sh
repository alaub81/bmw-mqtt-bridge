#!/usr/bin/env bash
# Isolated runtime checks; never mounts deployment volumes or credentials.
set -euo pipefail
image="${1:-bmw-mqtt-bridge:hardening-test}"
volume="bmb-hardening-test-$$"
cleanup() { docker volume rm "$volume" >/dev/null; }
docker volume create "$volume" >/dev/null
trap cleanup EXIT
flags=(--rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true
  --tmpfs "/tmp:rw,noexec,nosuid,size=16m,mode=1777" -v "$volume:/app/token")
# Docker populates a fresh named volume with the image's non-root ownership.
docker run "${flags[@]}" "$image" /bin/sh -ec '
  test "$(id -u)" = 10001
  test "$(id -g)" = 10001
  test ! -w /app
  test -w /app/token
  test -w /tmp
  grep -q "NoNewPrivs:[[:space:]]*1" /proc/self/status
  grep -q "CapEff:[[:space:]]*0000000000000000" /proc/self/status
  printf old-id > /app/token/id_token.txt
  printf old-refresh > /app/token/refresh_token.txt
  printf heartbeat > /tmp/bmw-mqtt-bridge-heartbeat
'
# Model an old root-owned token volume, then perform the documented migration.
docker run --rm --network none --user 0:0 --entrypoint /bin/sh -v "$volume:/app/token" "$image" -ec '
  chown -R 0:0 /app/token; chmod 700 /app/token; chmod 600 /app/token/*.txt
'
if docker run "${flags[@]}" "$image" /bin/true; then
  echo 'Root-owned legacy tokens unexpectedly accepted' >&2
  exit 1
fi
docker run --rm --network none --read-only --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE \
  --security-opt no-new-privileges:true --user 0:0 --entrypoint /bin/sh \
  -v "$volume:/app/token" "$image" -ec '
  chown -R 10001:10001 /app/token
'
docker run "${flags[@]}" "$image" /bin/sh -ec '
  test "$(cat /app/token/id_token.txt)" = old-id
  test "$(stat -c %a /app/token/id_token.txt)" = 600
  printf rotated > /app/token/id_token.txt.tmp
  mv /app/token/id_token.txt.tmp /app/token/id_token.txt
  test "$(cat /app/token/id_token.txt)" = rotated
'
# Start the actual bridge with a synthetic unexpired JWT and legacy token files.
# Network isolation prevents any BMW or deployment-broker traffic.
docker run "${flags[@]}" -e BMB_BMW_CLIENT_ID=test-client -e BMB_BMW_GCID=test-account \
  -e BMB_BMW_HOST=127.0.0.1 -e BMB_MQTT_LOCAL_HOST=127.0.0.1 "$image" /bin/bash -ec '
  printf "%s" header.eyJleHAiOjQxMDI0NDQ4MDB9.signature > /app/token/id_token.txt
  printf "%s" migration-refresh > /app/token/refresh_token.txt
  timeout 3 /app/bmw_mqtt_bridge > /tmp/migration.log 2>&1 || true
  test "$(jq -r .refresh_token /app/token/tokens.json)" = migration-refresh
  test "$(jq -r .access_token /app/token/tokens.json)" = ""
  test ! -e /app/token/id_token.txt
  test ! -e /app/token/refresh_token.txt
'
# Exercise the actual OAuth helper with deterministic responses, without networking.
docker run "${flags[@]}" -e BMB_BMW_CLIENT_ID=test-client -e BMB_BMW_GCID=test-account "$image" /bin/bash -ec '
  curl() {
    case "$*" in
      *oauth/device/code*) printf "%s\n" "{\"device_code\":\"test\",\"verification_uri_complete\":\"https://example.invalid\"}" ;;
      *oauth/token*) printf "%s\n" "{\"access_token\":\"test-access\",\"id_token\":\"test-id\",\"refresh_token\":\"test-refresh\"}" ;;
      *) return 2 ;;
    esac
  }
  export -f curl
  /app/bmw_flow.sh <<< ""
  test "$(jq -r .id_token /app/token/tokens.json)" = test-id
  test "$(stat -c %a /app/token/tokens.json)" = 600
'
echo 'Non-root, read-only runtime and legacy-volume migration passed.'
