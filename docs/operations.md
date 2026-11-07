# Updates, backup and restore

Run commands in the directory containing your deployment's `docker-compose.yml`
and `.env`. Keep the same directory and Compose project name on updates so Docker
reuses the token volume. [Configuration reference](configuration.md).

## Backup before an update

The complete volume holds credentials in `tokens.json` and, with Homie enabled,
`homie-cache.json`. Back up both together with `.env`, Compose and custom CA files.
A container image alone does not contain this state. Do not publish these backups.
Stop the bridge so refresh and cache writes cannot race the copy.

```bash
umask 077
mkdir -p backups
docker compose stop bmw-mqtt-bridge
docker compose run --rm --no-deps -T --entrypoint tar bmw-mqtt-bridge \
  -C /app/token -czf - . > backups/token-volume.tar.gz
tar -czf backups/config.tar.gz .env docker-compose.yml
tar -tzf backups/token-volume.tar.gz
docker compose start bmw-mqtt-bridge
```

Check each command succeeds before continuing; a failed redirection can leave an
empty archive. Add mounted certificate files to the configuration archive.
Copy backups to another protected system. A token archive can become obsolete
after BMW rotates credentials; a restored archive may require a new login.

## Restore

Use a compatible image (ideally the original digest), restore configuration first,
and stop the bridge. Restore into an empty token volume; do not mix old state with
newer state. The command below writes the configured volume. For a rehearsal,
use a separate directory and `docker compose -p bmb-restore-test` on **every**
command, and never start a second bridge with restored real credentials.

```bash
docker compose stop bmw-mqtt-bridge
docker compose run --rm --no-deps -T --entrypoint tar bmw-mqtt-bridge \
  -C /app/token -xzf - -o < backups/token-volume.tar.gz
```

The `-o` option keeps extracted files owned by the runtime user. List the volume
with `--entrypoint ls ... -la /app/token` and verify the archive contents before
starting. For custom `BMB_BMW_TOKEN_DIR`, adapt the tar path. For root-owned legacy
volumes follow the [ownership migration](configuration.md#non-root-runtime-and-existing-token-volumes).
Rollback across a token/cache migration also needs the matching state backup.

## Update and check

Set `BMB_VERSION` in `.env` to a published version without `v`, or use `latest`.

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 bmw-mqtt-bridge
```

A plain restart does not apply changed environment variables. Use
`docker compose up -d --force-recreate` after configuration changes. Weekly
rebuilds can replace version tags with refreshed base images; record the image
digest if you need an exact rollback. `docker compose down -v` deletes the state.

## Diagnose without vehicle commands

- Inspect `docker compose ps` and connection logs first. Debug logs can expose
  telemetry; redact VINs, account IDs, positions and credentials before sharing.
- `unhealthy` in the detailed template means a stale heartbeat or sustained MQTT
  outage. Docker restart policies do not automatically restart unhealthy processes.
- Connection status does not prove fresh measurements. Check last receipt and
  individual values; a partial message need not update every field.
- To reauthenticate, stop the bridge, run `./bmw_flow.sh` through Compose as in the
  README, then start it. Never run concurrent logins against the same token volume.

## Diagnostic and maintenance tools

All source tools are retained. Measurements use JSONL; summaries use JSON. Keep
these formats separate from widget YAML and configuration files.

| Tool | Parameters and effect | Limits |
| --- | --- | --- |
| `resources/bmw_flow.sh` | Environment from Compose; interactive BMW device login, writes token bundle | Stop bridge first; rotates credentials, not a read-only diagnostic |
| `resources/compile.sh` | Builds both C++ binaries with warning errors | Build environment with Mosquitto/curl headers required |
| `tests/resource_soak.py` | `--compose-file`, `--service` (default `bmw-mqtt-bridge`), `--bridge-version`, `--duration-seconds` (86400), `--interval-seconds` (60), `--output` (JSONL) | Read-only Linux host collector; cgroup v2 and matching `/proc` required; Docker Desktop unsupported |
| `tests/summarize_soak.py` | Positional JSONL file; JSON summary to stdout | No Docker access; rejects malformed/out-of-order schema v2 samples; missing metrics stay null |
| `tests/linter-check.sh` | No supported parameters; runs all lint checks | No fixes; needs pinned tools, no BMW connection |
| `tests/test-check.sh` | No arguments; offline C++/Python/widget regressions | Uses mocks/extracts; no proof of BMW service availability |
| `tests/sanitizer-check.sh` | No arguments; same fixtures with ASan/UBSan | No full production instrumentation or race detection |
| `tests/container-hardening-check.sh` | Optional image name | Docker; disposable volumes and fake auth server; no real credentials |
| `tests/integration_mqtt.py`, `tests/integration_http.py` | Run via their documented Dockerfiles | Isolated brokers/HTTP server; no BMW live test |
| `openHAB/generate_imports.py` | Generate MainUI imports; `--check` detects drift | Python + PyYAML; edit only canonical YAML |

For resource sampling examples, interpretation and missing-metric handling see
[the detailed reference](configuration.md#target-host-resource-measurement).
