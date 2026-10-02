# BMW CarData → MQTT Bridge

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A small C++ bridge that connects BMW ConnectedDrive CarData MQTT streaming to
your own MQTT broker. It authenticates through BMW's OAuth2 device flow,
refreshes tokens automatically and forwards vehicle telemetry in real time.
This fork is based on [dj0abr/bmw-mqtt-bridge](https://github.com/dj0abr/bmw-mqtt-bridge).

Run it directly on Debian, Ubuntu or Raspberry Pi OS, or in Docker. This README
contains the installation, configuration, operation and development documentation.

## Contents

- [Features and requirements](#features-and-requirements)
- [Project structure](#project-structure)
- [Get your BMW IDs](#get-your-bmw-ids)
- [Docker installation](#docker-installation)
- [Native installation](#native-installation)
- [Environment variables](#environment-variables)
- [MQTT topics](#mqtt-topics)
- [MQTT retain](#mqtt-retain)
- [Systemd service](#systemd-service)
- [Lint checks](#lint-checks)
- [Pre-deployment tests](#pre-deployment-tests)
- [Security](#security)
- [License](#license)
- [Credits](#credits)

## Features and requirements

- MQTT v5, reason codes, watchdog and automatic reconnection.
- Automatic renewal using BMW's `refresh_token`.
- Retained JSON connection status and optional split telemetry topics.
- Configurable local MQTT client ID, topic prefix and TLS certificate verification.
- Docker Compose with persistent tokens and a main-loop heartbeat healthcheck.
- Lightweight runtime using libmosquitto, libcurl and the bundled nlohmann/json header.

The original project was tested on Debian 12, Ubuntu 22.04+ and Raspberry Pi OS
Bookworm, with libmosquitto 2.0+, libcurl 7.74+ and g++ 10+. Native compilation
requires C++17 and pkg-config. Docker usage requires Docker with the Compose
plugin (Docker 24+ is the documented baseline).

## Project structure

```text
bmw-mqtt-bridge/
├── resources/
│   ├── src/
│   │   ├── bmw_mqtt_bridge.cpp
│   │   └── json.hpp
│   ├── bmw_flow.sh
│   ├── compile.sh
│   ├── docker-entrypoint.sh
│   ├── install_deps.sh
│   ├── install_lint_tools.py
│   └── lint.py
├── tests/
├── .github/workflows/
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── linter-check.sh
├── test-check.sh
├── LICENSE
└── README.md
```

## Get your BMW IDs

Before you can use the bridge, you must retrieve your personal **BMW CarData identifiers**.

For Docker, enter the IDs in the host `.env` copied from `.env.example`. For
native use, `resources/bmw_flow.sh` can create a configuration file in the state
directory. Obtain the IDs as follows:

1. Go to the [MyBMW website](https://www.bmw-connecteddrive.com/)
   (You should already have an account and your car must be registered.)
2. Navigate to **Personal Data → My Vehicles → CarData**
3. Click on **"Create Client ID"**
   ⚠️ *Do **not** click on "Authenticate Vehicle"!*
4. Copy the **Client ID** and insert it into the `.env` file
5. Scroll down to **CARDATA STREAM → Show Connection Details**
6. Copy the **USERNAME** and insert it into `.env` file as **BMW_GCID**
7. The other options in the `.env` file are for advanced setups – you can safely ignore them in most cases

After this setup, your bridge will be able to authenticate against the official BMW CarData MQTT interface.

At **CARDATA STREAM** don't forget to click `Change data selection` and activate the topics you want to receive.

## Docker installation

### Configuration

Clone this fork and prepare the host configuration:

```bash
git clone https://github.com/alaub81/bmw-mqtt-bridge.git
cd bmw-mqtt-bridge
cp .env.example .env
```

Edit `.env` and set `BMW_CLIENT_ID`, `BMW_GCID` and your
local MQTT settings. Docker Compose passes the listed values to the container
as environment variables. The `.env` is not mounted or copied into the image.
`BMW_LOAD_ENV_FILE=0` prevents the bridge and authentication script from loading
an old `.env` from the state directory. Environment variables are visible to
users with Docker access; passing values this way does not make them secrets.

### Persistent tokens: Docker volume

Compose mounts the named Docker volume `data-token` at `/app/conf`. The explicit
`name: data-token` keeps its actual Docker name exactly `data-token`, without a
Compose project prefix. Configuration is still supplied from the host `.env`
through environment variables. No token secrets or host bind mount are required.

The tokens live at these paths inside the container:

```text
/app/conf/id_token
/app/conf/refresh_token
```

The bridge renews tokens automatically and saves them with permissions `0600`.
All state files live directly in `/app/conf`, without an additional subdirectory.
On the first bridge start after an update, the entrypoint moves a complete token
pair from the old `/app/conf/bmw-mqtt-bridge` directory into `/app/conf`. It also
moves accompanying state files when their destination does not exist. Existing
tokens in `/app/conf` are never overwritten; incomplete pairs require
reauthentication. An empty legacy directory is removed after migration.
The volume survives container replacement and `docker compose down`.
`docker compose down -v` deletes it and requires authentication again.

### Initial authentication or reauthentication

```bash
docker compose build
docker compose stop bmw-mqtt-bridge
docker compose run --rm -it bmw-mqtt-bridge ./bmw_flow.sh
```

Follow the displayed BMW login instructions. The helper receives its configuration
from Compose and saves tokens directly into the shared volume. It does not create
or require a `.env` inside the container. The image includes the required tools,
including OpenSSL. Then start the bridge:

```bash
docker compose up -d
docker compose logs -f bmw-mqtt-bridge
```

A fresh volume is empty: previous token files in a host bind-mount directory or
secret files are not migrated automatically. Either authenticate as above or
copy the latest valid pair into the volume before starting. Old files are left
untouched. Do not run multiple bridges with the same BMW_GCID or token volume.

### Configuration changes

```bash
docker compose up -d --force-recreate
```

A plain restart does not apply changed Compose environment variables.

### TLS connection to your own MQTT broker

For a broker with a self-signed certificate, enable encryption and disable
certificate verification in the host `.env`:

```dotenv
MQTT_LOCAL_HOST=mqtt.example.local
MQTT_LOCAL_PORT=8883
MQTT_LOCAL_TLS=true
MQTT_LOCAL_TLS_VERIFY=false
MQTT_LOCAL_USER=your-user
MQTT_LOCAL_PASSWORD=your-password
```

`MQTT_LOCAL_HOST` is a hostname or IP, without an `mqtts://` prefix. Select the actual
TLS port configured on your broker; changing the port alone does not enable TLS.
The switches accept `true/false`, case insensitive. By default,
TLS is disabled and verification is enabled whenever TLS is switched on.
Turning verification off disables both certificate chain and hostname checks:
the connection is encrypted, but the server identity is not checked. These
settings apply only to your own broker, not to the BMW connection.

Alternatively, keep `MQTT_LOCAL_TLS_VERIFY=true` and trust your self-signed certificate
or its issuing CA by mounting a PEM certificate file read-only:

```yaml
services:
  bmw-mqtt-bridge:
    volumes:
      - data-token:/app/conf
      - ./certs/mqtt-ca.crt:/app/certs/mqtt-ca.crt:ro
```

Set `MQTT_LOCAL_TLS_CA_FILE=/app/certs/mqtt-ca.crt` in `.env`. `MQTT_LOCAL_HOST` must match
the certificate's hostname/IP. Without a custom CA file, the image's system CA
bundle is used. A missing/unreadable CA file causes startup to fail rather than
falling back to plaintext. Mutual TLS with client certificates is not configured.

After changing settings or the code:

```bash
docker compose up -d --build --force-recreate
```

### Local MQTT client ID

Set `MQTT_LOCAL_BMW_CLIENT_ID=bmw5-bridge` in the host `.env` to use a fixed client ID for
your MQTT broker. Leave it empty to let Mosquitto generate a random ID. Use a
different value for each concurrent instance connected to the same broker.
This setting is independent of the BMW OAuth `BMW_CLIENT_ID` and `MQTT_LOCAL_PREFIX`.

### Compose healthcheck

The healthcheck is defined only in `docker-compose.yml`. Every 30 seconds it checks
that PID 1 is executing `/app/bmw_mqtt_bridge` and that the heartbeat in
`/tmp/bmw-mqtt-bridge-heartbeat` belongs to PID 1 and is no more than 90 seconds
old. Startup grace is 30 seconds, timeout is 5 seconds, and 3 failed checks mark
the container unhealthy.

The bridge writes the timestamp and process ID atomically every 10 seconds from
its main loop, including during MQTT reconnect backoff. A stalled main loop stops
updating the heartbeat. MQTT broker outages and missing vehicle messages do not
affect this liveness check. The heartbeat is removed on startup and clean shutdown.
`BMW_HEARTBEAT_FILE` is supplied by Compose; without it, heartbeat writing is disabled.
The file lives in `/tmp`, outside the persistent token volume.

Docker does not automatically restart a running container solely because its
health status is unhealthy. The existing restart policy handles process exits.

Rebuild the bridge and view the status:

```bash
docker compose up -d --build --force-recreate
docker compose ps
```

## Native installation

On Debian, Ubuntu or Raspberry Pi OS, install dependencies and compile:

```bash
git clone https://github.com/alaub81/bmw-mqtt-bridge.git
cd bmw-mqtt-bridge
bash resources/install_deps.sh
bash resources/compile.sh
bash resources/bmw_flow.sh
```

The dependency helper uses `sudo` to install the compiler, development libraries,
OAuth tools and Mosquitto broker/clients. Ensure `pkg-config` is also installed.
The first authentication run creates `~/.local/state/bmw-mqtt-bridge/.env` and
opens it in `$EDITOR` (default: nano). Enter your `BMW_CLIENT_ID`, `BMW_GCID` and local
MQTT settings, then rerun the helper. Open the displayed URL, complete the BMW
login and consent, and follow the terminal instructions.

The helper is needed for initial authentication and reauthentication. Regular
bridge operation refreshes tokens itself and does not run the device flow.
Start the compiled bridge with:

```bash
./resources/src/bmw_mqtt_bridge
```

If `XDG_STATE_HOME` is set, authentication and the bridge both use
`XDG_STATE_HOME` directly instead of the default state directory. No application
subdirectory is added to an explicitly configured path.

## Environment variables

The tables below describe the connection and publishing settings.
Values are read from the process environment. With `BMW_LOAD_ENV_FILE=1` (the
bare-metal default), the `.env` in the token directory supplies fallback values;
existing environment variables take precedence. Quotes in `.env` are supported.
Docker sets `BMW_LOAD_ENV_FILE=0`: Compose passes configuration from the host
`.env`, and no configuration file is loaded inside the container.

In Docker, tokens are stored in the persistent volume `data-token`. They are created
by the initial authentication and refreshed by the bridge itself. See
[Docker installation](#docker-installation).

---

### 📄 Token & .env Location (fixed)

When file loading is enabled, the program loads `.env` from the **token directory** created by `resources/bmw_flow.sh`:

- Default:
  `$HOME/.local/state/bmw-mqtt-bridge/.env`
- If `$XDG_STATE_HOME` is set:
  `${XDG_STATE_HOME}/.env`

---

### 🌐 BMW CarData Broker

| Variable    | Type | Default                                        | Required | Description |
|-------------|------|-------------------------------------------------|----------|-------------|
| `BMW_CLIENT_ID` | str  | *(none)*                                       | **Yes**  | BMW CarData **Client ID** (GUID) from the MyBMW portal. Placeholder values are rejected. |
| `BMW_GCID`      | str  | *(none)*                                       | **Yes**  | BMW **BMW_GCID / username** for the MQTT broker (from “Show Connection Details”). Placeholder values are rejected. |
| `BMW_HOST`  | str  | `customer.streaming-cardata.bmwgroup.com`      | No       | BMW CarData MQTT hostname. |
| `BMW_PORT`  | int  | `9000`                                          | No       | BMW CarData MQTT port. |

Validation on startup:

- If `BMW_CLIENT_ID` or `BMW_GCID` are missing/placeholder → the program exits with an error.

---

### 🏠 Local MQTT Broker (Mosquitto)

| Variable         | Type | Default     | Required | Description |
|------------------|------|-------------|----------|-------------|
| `MQTT_LOCAL_HOST`     | str  | `127.0.0.1` | No       | Host/IP of your local MQTT broker. |
| `MQTT_LOCAL_PORT`     | int  | `1883`      | No       | Port of your local MQTT broker. |
| `MQTT_LOCAL_BMW_CLIENT_ID` | str | *(empty)* | No | Client ID for your MQTT broker. Empty generates a random ID; configured IDs must be unique per running instance. |
| `MQTT_LOCAL_USER`     | str  | *(empty)*   | No       | Username for local broker authentication (optional). |
| `MQTT_LOCAL_PASSWORD` | str  | *(empty)*   | No       | Password for local broker authentication (optional). |
| `MQTT_LOCAL_TLS` | bool | `false` | No | Enable TLS for the local broker. Accepts `true/false` (case insensitive). |
| `MQTT_LOCAL_TLS_VERIFY` | bool | `true` | No | Verify certificate chain and hostname when TLS is enabled. `false` disables both checks; encryption stays enabled, but the broker identity is not verified. |
| `MQTT_LOCAL_TLS_CA_FILE` | str | `/etc/ssl/certs/ca-certificates.crt` | No | Container path to a PEM CA/certificate file. Used when TLS is enabled. |

### 🧭 Topic Prefix & Status Topic

| Variable       | Type | Default | Required | Description |
|----------------|------|---------|----------|-------------|
| `MQTT_LOCAL_PREFIX` | str  | `bmw/`  | No       | Topic prefix for all republished topics. If empty, the program falls back to `bmw/`. A trailing slash is **enforced** automatically. |
| `BMW_STATUS_STABLE_DELAY` | int  | 5  | No       | delay time for bmw connection state true->false: anti flickering during token refresh |

### ✂️ Split Topics

| Variable        | Type | Default | Required | Description |
|-----------------|------|---------|----------|-------------|
| `MQTT_SPLIT_TOPICS`  | int  | `0`     | No       | `0` = disabled, `1` = enabled. When enabled, JSON payloads are parsed and individual fields are republished under `vehicles/<VIN>/<propertyName>`. |

### 🔁 Retained Messages

| Variable       | Type | Default | Required | Description |
|----------------|------|---------|----------|-------------|
| `MQTT_RETAIN`  | int  | `0`     | No       | `0` = do not retain (default), `1` = retain republished topics. Affects **RAW**, **Legacy**, and **Split** topics. The **status topic** is always retained regardless of this setting. |

### Runtime and state settings

| Variable | Default | Description |
| --- | --- | --- |
| `BMW_LOAD_ENV_FILE` | `1` natively; `0` in Docker | `0` disables the state-directory `.env` loader. The bridge otherwise uses the file as fallback; existing process environment values take precedence. The authentication helper sources this file when loading is enabled. |
| `XDG_STATE_HOME` | Unset natively; `/app/conf` in Docker | Exact directory for state and token files; no subdirectory is appended. When unset, native operation uses `$HOME/.local/state/bmw-mqtt-bridge`. Use the same value for authentication and bridge operation. |
| `BMW_HEARTBEAT_FILE` | Empty natively; `/tmp/bmw-mqtt-bridge-heartbeat` in Compose | Enables the main-loop heartbeat used by the Compose healthcheck. An empty value disables heartbeat writing. |

The supplied `.env.example` enables `MQTT_SPLIT_TOPICS=1`; the executable and Compose
fallback default to `0` when it is not configured. Compose's default `MQTT_LOCAL_HOST`
is `host.docker.internal`, while the native executable defaults to `127.0.0.1`.

## MQTT topics

### Local MQTT reconnect watchdog

Libmosquitto handles ordinary reconnects to your own broker. If the local
connection stays down for 30 seconds, the main loop stops and destroys the old
client and creates a fresh one. This also recovers from a terminated MQTT
network loop. Failed rebuilds are retried at 30-second intervals, independently
of BMW reconnect backoff. The interval uses a monotonic clock and is fixed.

Each replacement restores TLS settings, credentials, client ID, callbacks and
the retained offline Last Will. Publishing is synchronized with replacement;
callbacks can finish without accessing a destroyed client. A successful local
CONNACK triggers a fresh status message. Logs with `[local/log]` expose local
MQTT connection attempts and TLS/network errors; watchdog rebuilds are logged
as `local MQTT disconnected for 30s; rebuilding client`.

The watchdog runs in the main loop, so blocking token refresh or other main-loop
work can delay a check. Vehicle messages received while the local connection is
unavailable are not buffered or replayed. The Docker healthcheck continues to
measure process liveness rather than broker connectivity.

### Status Topic Prefix

By default, the bridge publishes its connection status to:

```text
bmw/status
```

If you want a different topic prefix (for example if you have multiple cars or bridges),
you can configure it using this environment variable in your .env file:

```text
MQTT_LOCAL_PREFIX=mycar/
```

The bridge will then publish:

```text
mycar/status
```

and all other MQTT messages (e.g. `raw`, `vehicles`, etc.) under the same prefix.

**status:**

Reports the connection state to the BMW MQTT broker (true = connected, false = disconnected).

true is published immediately when the connection is established.

false is published only after BMW_STATUS_STABLE_DELAY seconds of continuous disconnect (default: 5).

Set BMW_STATUS_STABLE_DELAY=0 to disable the delay (instant switching).

This debounce avoids brief drops (e.g., during token refresh) from causing flicker in clients that monitor the status.

On a normal shutdown (`SIGTERM`, `SIGINT` or `docker compose stop`), the bridge
publishes retained `connected:false` immediately, bypassing the debounce. It
keeps the local MQTT network loop running while waiting up to two seconds for
the QoS 1 acknowledgement. If publication fails or times out, it closes without
a clean MQTT disconnect so the broker can still send the offline Last Will.
After an abrupt crash, the broker sends that Last Will when it detects the lost
connection; this may take until the keepalive timeout.

---

### Split Topics (Structured JSON Publishing)

By default, the bridge republishes BMW CarData messages exactly as received
into a local topic of the form:

```text
bmw/raw/<VIN>/<eventName>
```

To make integration easier for automation systems (like Home Assistant, Node-RED, etc.),
you can optionally enable **split topics**, which publish each data field under its own sub-topic:

add to your `.env` file:

```text
MQTT_SPLIT_TOPICS=1
```

This will create additional messages like:

```text
bmw/vehicles/<VIN>/fuelPercentage {"value":62.5,"unit":"%","timestamp":1739790000}
bmw/vehicles/<VIN>/range_km       {"value":420}
bmw/vehicles/<VIN>/position       {"value":{"lat":48.1,"lon":11.6},"timestamp":1739790100}
```

## MQTT retain

To ensure Home Assistant and other clients immediately see the last known state after a restart, the bridge can publish its republished MQTT messages **with the Retain flag**.

**Default:** off (`MQTT_RETAIN=0`)
**When enabled:** Retain applies to:

- `bmw/raw/<VIN>/<eventName>`
- `bmw/<VIN>/<eventName>` (Legacy)
- `bmw/vehicles/<VIN>/<propertyName>` (when `MQTT_SPLIT_TOPICS=1`)

The **status topic** `bmw/status` is always retained (LWT), regardless of this setting, to keep availability tracking consistent.

### Enable

edit the file: **.env**

```ini
MQTT_RETAIN=1
```

### Clean up (remove retained messages)

If you want to clear a topic:

```bash
# Remove a retained message by sending an empty retained payload.
mosquitto_pub -t 'bmw/vehicles/<VIN>/range_km' -r -n
```

or, alternatively, use MQTT Explorer

### Notes

- For **stateful topics** (e.g. door lock, availability, battery values) retain is very useful.
- For **high-frequency or transient** topics, retain may be undesirable (it shows an outdated snapshot).
- If you later change your `MQTT_LOCAL_PREFIX`, old retained messages under the previous prefix will remain in your broker until you remove them manually (see above).

## Systemd service

For native operation, create `/etc/systemd/system/bmw-mqtt-bridge.service`:

```ini
[Unit]
Description=BMW CarData MQTT bridge
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=myUserName
WorkingDirectory=/home/myUserName/bmw-mqtt-bridge
ExecStart=/home/myUserName/bmw-mqtt-bridge/resources/src/bmw_mqtt_bridge
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Replace the username and paths with your installation. Run authentication first
as that user so the state-directory `.env` and tokens belong to the service
account. If you use a custom state directory, add `Environment=XDG_STATE_HOME=...`
to the service and use the same value when authenticating.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bmw-mqtt-bridge.service
journalctl -u bmw-mqtt-bridge -f
```

## Lint checks

Run all project lint checks with:

```bash
./linter-check.sh
```

The CI lint job invokes this exact command. It never fixes or stages files and
returns a non-zero exit code if any check fails or a required tool is missing.
All checks run even when an earlier check fails; the final summary lists the
results. No BMW account, token files, running MQTT broker or Docker daemon is needed.

### Tool installation

Use Python 3.12+, Git, Make and a C++ compiler. Node.js and pnpm are not required. On
macOS install Hadolint 2.15.1 on PATH first (for example through Homebrew), since
that release has no macOS binary. The installer downloads native tools with
pinned versions and verified SHA-256 checksums into the ignored `.lint-tools`
directory. Cppcheck is built from its pinned source archive. It leaves system
packages unchanged. Matching tools already present on PATH may be reused.

```bash
python3 -m venv .lint-venv
.lint-venv/bin/python -m pip install --requirement requirements-lint.txt
.lint-venv/bin/python resources/install_lint_tools.py
./linter-check.sh
```

The same dependency installation is used in `.github/workflows/ci.yml`. Python
package versions are pinned in `requirements-lint.txt`, and native tool
versions/checksums in `lint-tools.json`.
The lint script automatically adds project-local tool directories to PATH.

### File coverage

Git supplies existing tracked files and non-ignored untracked files. Renamed
files are checked at their current path; deleted files are omitted. New files
are included before staging them. Ignored `.env`, tokens, generated output and
installed dependencies are excluded, so the check does not inspect local secrets.

| Files | Check |
| --- | --- |
| Every project text file, including ignore files, EditorConfig and LICENSE | UTF-8, LF, final newline, merge markers and trailing whitespace |
| Shell scripts | ShellCheck |
| Dockerfiles | Hadolint |
| YAML, including Compose and linter configuration | yamllint |
| GitHub Actions workflows, including embedded shell scripts | actionlint with ShellCheck |
| Python scripts and tests | Ruff |
| C++ source and project headers | Cppcheck, C++17, default configuration |
| JSON configuration and manifests | JSON syntax and duplicate keys |
| `.env.example` | Assignment syntax and duplicate keys; no shell execution |
| Markdown documentation and issue templates | PyMarkdown |

The vendored `resources/src/json.hpp` receives text encoding/newline/merge-marker
checks and is parsed as an include during C++ analysis. Its upstream formatting
and standalone third-party lint diagnostics are preserved. Markdown allows two
trailing spaces for hard line breaks. Unknown text file types still receive the
common text checks; new programming languages should get a dedicated linter.

### Rules

Rule settings live in `.hadolint.yaml`, `.yamllint.yaml`, `.pymarkdown.yaml`
and `ruff.toml`.
Hadolint permits unpinned Debian package versions so security updates can follow
the selected distribution; other warnings fail the check. Markdown permits long
lines, embedded HTML, bold labels and flexible table alignment to retain the
existing documentation style. PyMarkdown runs in the existing Python environment
and supports the YAML front matter in GitHub issue templates. HTML, JavaScript
and CSS linters are not installed.

This command performs lint checks, not deployment or token authentication.
Run regression tests with the shared local/CI entrypoint:

```bash
./test-check.sh
```

## Pre-deployment tests

Run both checks before building and deploying:

```bash
./linter-check.sh && ./test-check.sh && \
  docker compose up -d --build --force-recreate
```

`test-check.sh` runs all tests under `tests/` from any working directory. It uses
the project-local `.lint-venv` Python when available, otherwise system Python.
Requirements are Python 3.9+, Bash, Git, a C++17 compiler available as `c++`, and
Docker CLI with the Compose plugin. No running Docker daemon, BMW account,
production tokens or MQTT broker is required. Missing dependencies, skipped
tests, an empty suite or failed tests return a non-zero exit code.

The separate CI job **Offline regression tests** invokes exactly
`./test-check.sh`. These tests validate token handling/migration, environment
configuration, TLS settings, MQTT status and shutdown handling, heartbeat and
Compose healthcheck logic, and lint file selection/error handling. They use
temporary directories and compiled extracts of the production C++ code with
simulated MQTT functions. They are offline regression tests, not an end-to-end
test of a running container connected to BMW and an actual MQTT broker.

## Security

- BMW CarData is a private API — use responsibly.
- Never publish or share your `id_token` / `refresh_token`.
- Tokens expire automatically; the bridge refreshes them securely.
- Keep your Mosquitto broker private or protected by authentication.

## License

See [LICENSE](LICENSE) for the full MIT License.
Copyright (c) 2025 Kurt, DJ0ABR

This project also includes [`nlohmann/json`](https://github.com/nlohmann/json)
licensed under the MIT License.

## Credits

- Developed by **Kurt**
- Docker setup and project structure by **oemich**
- Extended MQTT topics by **grogi**
- Uses the official BMW CARDATA STREAMING interface

Contributions, pull requests, and improvements are welcome!
