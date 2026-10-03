# BMW CarData → MQTT Bridge

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A small C++ bridge that connects BMW ConnectedDrive CarData MQTT streaming to
your own MQTT broker. It authenticates through BMW's OAuth2 device flow,
refreshes tokens automatically and forwards vehicle telemetry in real time.
This fork is based on [dj0abr/bmw-mqtt-bridge](https://github.com/dj0abr/bmw-mqtt-bridge).

Run the bridge in Docker using Docker Compose. This README contains the
installation, configuration, operation and development documentation.

## Contents

- [Features and requirements](#features-and-requirements)
- [Project structure](#project-structure)
- [Get your BMW IDs](#get-your-bmw-ids)
- [Quick start](#quick-start)
- [Docker installation](#docker-installation)
- [Environment variables](#environment-variables)
- [MQTT topics](#mqtt-topics)
- [MQTT retain](#mqtt-retain)
- [Local development](#local-development)
- [Lint checks](#lint-checks)
- [CI and container releases](#ci-and-container-releases)
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

The application runs in Docker using Alpine 3.24. The builder stage compiles the C++17 executable
and installs development libraries; the runtime stage contains the bridge and
OAuth tools. Use Docker with the Compose plugin (Docker 24+ is the documented
baseline). Host deployment uses the prebuilt GitHub Container Registry image
`ghcr.io/alaub81/bmw-mqtt-bridge` for `linux/amd64` and `linux/arm64`.
The `Dockerfile` builds the Alpine image used by CI, releases and local development.
`Dockerfile-Debian` is retained as an alternative build definition.

## Project structure

```text
bmw-mqtt-bridge/
├── resources/
│   ├── src/
│   │   ├── bmw_mqtt_bridge.cpp
│   │   └── json.hpp
│   ├── bmw_flow.sh
│   ├── compile.sh
│   └── docker-entrypoint.sh
├── tests/
│   ├── linter-check.sh
│   ├── test-check.sh
│   ├── install_lint_tools.py
│   ├── lint.py
│   ├── run_tests.py
│   ├── requirements-lint.txt
│   └── test_*.py
├── .github/workflows/
├── .env.sample
├── docker-compose.example.yml
├── docker-compose.dev.yml
├── Dockerfile
├── Dockerfile-Debian
├── LICENSE
└── README.md
```

## Get your BMW IDs

Before you can use the bridge, you must retrieve your personal **BMW CarData identifiers**.

Enter the IDs in the host `.env` copied from `.env.sample`. Obtain the IDs as
follows:

1. Go to the [MyBMW website](https://www.bmw-connecteddrive.com/)
   (You should already have an account and your car must be registered.)
2. Navigate to **Personal Data → My Vehicles → CarData**
3. Click on **"Create Client ID"**
   ⚠️ *Do **not** click on "Authenticate Vehicle"!*
4. Copy the **Client ID** and insert it into the `.env` file
5. Scroll down to **CARDATA STREAM → Show Connection Details**
6. Copy the **USERNAME** and insert it into `.env` file as **BMB_BMW_GCID**
7. The other options in the `.env` file are for advanced setups – you can safely ignore them in most cases

After this setup, your bridge will be able to authenticate against the official BMW CarData MQTT interface.

At **CARDATA STREAM** don't forget to click `Change data selection` and activate the topics you want to receive.

## Quick start

Install Docker with the Compose plugin and obtain your
[BMW IDs](#get-your-bmw-ids). Create a directory with these two files.

Minimal `docker-compose.yml`:

```yaml
services:
  bmw-mqtt-bridge:
    image: ghcr.io/alaub81/bmw-mqtt-bridge:${BMB_VERSION:-latest}
    env_file: .env
    volumes:
      - bmb_data_token:/app/token
    restart: unless-stopped

volumes:
  bmb_data_token:
```

Minimal `.env`:

```dotenv
BMB_BMW_CLIENT_ID=your-client-id
BMB_BMW_GCID=your-account-id
BMB_MQTT_LOCAL_HOST=192.168.1.10
```

Replace the IDs and broker address. This example uses an MQTT broker reachable
at that address on port 1883 without authentication or TLS. Add optional settings
to `.env` as needed; `env_file` passes them to the container. For example, a broker
requiring authentication needs `BMB_MQTT_LOCAL_USER` and `BMB_MQTT_LOCAL_PASSWORD`.
The application uses defaults for all other settings.

Authenticate once, then start the bridge:

```bash
chmod 600 .env
docker compose pull
docker compose run --rm -it bmw-mqtt-bridge ./bmw_flow.sh
docker compose up -d
docker compose logs -f bmw-mqtt-bridge
```

Follow the BMW login instructions shown by the authentication helper. Tokens
persist in the volume; subsequent starts do not require authentication while
they remain valid. Connection status is published to `bmw/status`.

This minimal configuration omits the Docker healthcheck. For the full configuration
including health monitoring, copy `docker-compose.example.yml` to
`docker-compose.yml` and `.env.sample` to `.env` as described below. Your local
configuration files are ignored by Git. The full template explicitly forwards
its listed settings; additional overrides such as `BMB_BMW_TOKEN_DIR` must be added
to the service's `environment` section. See [Environment variables](#environment-variables).

## Docker installation

### Configuration

The host only needs `docker-compose.yml` and a configured `.env`; no source code,
compiler or local image build is required. Download the templates and copy them
to your local configuration files:

```bash
mkdir -p bmw-mqtt-bridge
cd bmw-mqtt-bridge
curl -fsSLo docker-compose.example.yml https://raw.githubusercontent.com/alaub81/bmw-mqtt-bridge/main/docker-compose.example.yml
curl -fsSLo .env.sample https://raw.githubusercontent.com/alaub81/bmw-mqtt-bridge/main/.env.sample
cp docker-compose.example.yml docker-compose.yml
cp .env.sample .env
chmod 600 .env
```

Edit `.env` and set `BMB_BMW_CLIENT_ID`, `BMB_BMW_GCID` and your
local MQTT settings. `BMB_VERSION=latest` selects the newest stable release;
use a published tag such as `BMB_VERSION=1.2.3` (without `v`) to select a version.
An unset or empty `BMB_VERSION` also falls back to `latest`. A push to `main`
runs CI; publishing a release requires a stable Git tag. See
[CI and container releases](#ci-and-container-releases).

Docker Compose passes the connection settings to the container
as environment variables. The `.env` is not mounted or copied into the image.
`BMB_VERSION` is used only by Compose to select the image tag.
The bridge and authentication script never read or create a `.env` inside the
container. Environment variables are visible to
users with Docker access; passing values this way does not make them secrets.

Public GHCR images can be pulled without login. If pulling reports `denied` or
`403 Forbidden`, check that the package is public and the selected tag has been
published. For a private package, run `docker login ghcr.io` using a GitHub
personal access token (classic) with `read:packages` and access to the package;
see [GitHub's container registry authentication documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-to-the-container-registry).

### Connecting to the Docker host

Set `BMB_MQTT_LOCAL_HOST` to an IP address or DNS name reachable from the
container. For a broker on the same Docker network, use its service name
(for example `BMB_MQTT_LOCAL_HOST=mosquitto`). Docker Desktop also provides
`host.docker.internal` for access to host services; on Docker Engine on Linux,
configure a reachable broker address explicitly.

The broker must listen on an interface reachable from the container. A broker
bound only to `127.0.0.1` on a Linux host is not reachable through the bridge
network. No port publishing is needed for the bridge because it makes outbound
connections to both brokers.

### Persistent tokens: Docker volume

Compose mounts the Docker volume `bmb_data_token` at `/app/token`. Docker Compose
prefixes its actual name with the project name (for example,
`bmw-mqtt-bridge_bmb_data_token`). Configuration is supplied from the host `.env`
through environment variables. No token secrets or host bind mount are required.

The tokens live at these paths inside the container:

```text
/app/token/id_token.txt
/app/token/refresh_token.txt
```

The bridge renews tokens automatically and saves them with permissions `0600`
(read/write for the owner only). Authentication uses the same permissions for
all three token files, including `access_token.txt`. On startup, the entrypoint
restricts existing token files to `0600` and removes the obsolete
`token_refresh_response.json`; the bridge no longer stores that response.
Before saving, authentication validates that the response contains all three
tokens as non-empty strings without whitespace. It writes all temporary files
in the token volume before replacing the existing files by atomic renames.
Invalid responses or staging failures leave existing tokens unchanged, and
temporary files are cleaned up on exit. Each file is replaced atomically;
the three files are not a single transaction, so keep the bridge stopped during
reauthentication as shown below.
All state files live directly in `/app/token`, without an additional subdirectory.
The entrypoint requires a complete pair at these paths. It does not automatically
migrate older file names or subdirectories. Existing tokens are never overwritten
by the entrypoint; incomplete pairs require reauthentication or explicit migration.
The volume survives container replacement and `docker compose down`.
`docker compose down -v` deletes it and requires authentication again.

### Initial authentication or reauthentication

```bash
docker compose pull
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
untouched. Do not run multiple bridges with the same BMB_BMW_GCID or token volume.

### Configuration changes

```bash
docker compose up -d --force-recreate
```

A plain restart does not apply changed Compose environment variables.

### Image updates and switching versions

Set `BMB_VERSION` in `.env` to the desired published tag, then download and apply it:

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 bmw-mqtt-bridge
```

Run these commands to update `latest` too; running containers do not update
automatically when GitHub publishes an image. The token volume survives image
updates. To switch back, set a previous compatible tag and repeat the commands.
The release workflow periodically rebuilds version tags with updated base images,
so a version tag selects a code release rather than an immutable image digest.

When switching an existing local-build installation to the GHCR image, keep
the same project directory and Compose project name so the existing `bmb_data_token`
volume is reused. Existing `.env` files can add `BMB_VERSION=latest`; without
that line, the default is already `latest`.

### TLS connection to your own MQTT broker

For a broker with a self-signed certificate, enable encryption and disable
certificate verification in the host `.env`:

```dotenv
BMB_MQTT_LOCAL_HOST=mqtt.example.local
BMB_MQTT_LOCAL_PORT=8883
BMB_MQTT_LOCAL_TLS=true
BMB_MQTT_LOCAL_TLS_VERIFY=false
BMB_MQTT_LOCAL_USER=your-user
BMB_MQTT_LOCAL_PASSWORD=your-password
```

`BMB_MQTT_LOCAL_HOST` is a hostname or IP, without an `mqtts://` prefix. Select the actual
TLS port configured on your broker; changing the port alone does not enable TLS.
The switches accept `true/false`, case insensitive. By default,
TLS is disabled and verification is enabled whenever TLS is switched on.
Turning verification off disables both certificate chain and hostname checks:
the connection is encrypted, but the server identity is not checked. These
settings apply only to your own broker, not to the BMW connection.

Alternatively, keep `BMB_MQTT_LOCAL_TLS_VERIFY=true` and trust your self-signed certificate
or its issuing CA by mounting a PEM certificate file read-only:

```yaml
services:
  bmw-mqtt-bridge:
    volumes:
      - bmb_data_token:/app/token
      - ./certs/mqtt-ca.crt:/app/certs/mqtt-ca.crt:ro
```

Set `BMB_MQTT_LOCAL_TLS_CA_FILE=/app/certs/mqtt-ca.crt` in `.env`. `BMB_MQTT_LOCAL_HOST` must match
the certificate's hostname/IP. Without a custom CA file, the image's system CA
bundle is used. A missing/unreadable CA file causes startup to fail rather than
falling back to plaintext. Mutual TLS with client certificates is not configured.

After changing settings:

```bash
docker compose up -d --force-recreate
```

### Local MQTT client ID

Set `BMB_MQTT_LOCAL_CLIENT_ID=bmw5-bridge` in the host `.env` to use a fixed client ID for
your MQTT broker. Leave it empty to let Mosquitto generate a random ID. Use a
different value for each concurrent instance connected to the same broker.
This setting is independent of the BMW OAuth `BMB_BMW_CLIENT_ID` and `BMB_MQTT_LOCAL_PREFIX`.

### Compose healthcheck

The healthcheck is defined in `docker-compose.example.yml`, copied to your
local `docker-compose.yml`, and inherited by
`docker-compose.dev.yml`. Every 30 seconds it checks
that PID 1 is executing `/app/bmw_mqtt_bridge` and that the heartbeat in
`/tmp/bmw-mqtt-bridge-heartbeat` belongs to PID 1 and is no more than 90 seconds
old. Startup grace is 30 seconds, timeout is 5 seconds, and 3 failed checks mark
the container unhealthy. It also fails when either the local or BMW MQTT
connection has been continuously unavailable for at least
`BMB_HEALTH_MQTT_DISCONNECT_TIMEOUT` seconds (default: 120). Set this positive value
in the host `.env` to change the grace period for both connections. With the
30-second interval and 3 retries, an outage normally marks the container unhealthy
roughly 3 minutes after it starts; the 120-second threshold begins failed checks,
rather than immediately changing Docker's health status.

The bridge writes the timestamp, process ID and separate downtime counters atomically every 10 seconds from
its main loop, including during MQTT reconnect backoff. A stalled main loop stops
updating the heartbeat. Connection changes trigger an additional heartbeat update.
The downtime counters use a monotonic clock, continue across watchdog rebuilds,
and reset when the main loop observes a successful reconnect. The next successful
Docker healthcheck restores `healthy`; both connections must be within their
allowed downtime. No vehicle messages are required, so a parked car does not
cause a failed check. The heartbeat is removed on startup and clean shutdown.
The bridge and healthcheck default to `/tmp/bmw-mqtt-bridge-heartbeat`.
`BMB_BMW_HEARTBEAT_FILE` can override this internal path when explicitly supplied
through the container environment.
The file lives in `/tmp`, outside the persistent token volume.

Docker does not automatically restart a running container solely because its
health status is unhealthy. The existing restart policy handles process exits.

View the status and the latest healthcheck results:

```bash
docker compose ps
docker inspect --format '{{json .State.Health}}' "$(docker compose ps -q bmw-mqtt-bridge)"
```

## Environment variables

Configuration is read exclusively from the container process environment.
All bridge settings use the `BMB_` prefix to avoid collisions with other services
in a shared Compose project. Previous unprefixed names are no longer read;
rename them in existing `.env` files and Compose environment mappings before
updating. `BMB_VERSION` keeps its existing name.
Docker Compose reads the host `.env` and passes the listed options to the
container. Neither the bridge nor the authentication helper loads a file as
fallback. An old `.env` in the token volume is ignored.

Tokens are stored directly in the persistent volume mounted at `/app/token`.
Initial authentication creates them; the bridge refreshes them itself. See
[Docker installation](#docker-installation).

### Container image

| Variable | Default | Description |
| --- | --- | --- |
| `BMB_VERSION` | `latest` | GHCR image tag selected by host Compose. Use a published version without `v`, such as `1.2.3`. Empty/unset uses `latest`. Not passed to the bridge; local development uses its own image. |

### 🌐 BMW CarData Broker

| Variable    | Type | Default                                        | Required | Description |
|-------------|------|-------------------------------------------------|----------|-------------|
| `BMB_BMW_CLIENT_ID` | str  | *(none)*                                       | **Yes**  | BMW CarData **Client ID** (GUID) from the MyBMW portal. Placeholder values are rejected. |
| `BMB_BMW_GCID`      | str  | *(none)*                                       | **Yes**  | BMW **GCID / username** for the MQTT broker (from “Show Connection Details”). Placeholder values are rejected. |
| `BMB_BMW_HOST`  | str  | `customer.streaming-cardata.bmwgroup.com`      | No       | BMW CarData MQTT hostname. |
| `BMB_BMW_PORT`  | int  | `9000`                                          | No       | BMW CarData MQTT port. |

Validation on startup:

- If `BMB_BMW_CLIENT_ID` or `BMB_BMW_GCID` are missing/placeholder → the program exits with an error.

---

### 🏠 Local MQTT Broker (Mosquitto)

| Variable         | Type | Default     | Required | Description |
|------------------|------|-------------|----------|-------------|
| `BMB_MQTT_LOCAL_HOST`     | str  | `host.docker.internal` | No       | Host/IP of your local MQTT broker. |
| `BMB_MQTT_LOCAL_PORT`     | int  | `1883`      | No       | Port of your local MQTT broker. |
| `BMB_MQTT_LOCAL_CLIENT_ID` | str | *(empty)* | No | Client ID for your MQTT broker. Empty generates a random ID; configured IDs must be unique per running instance. |
| `BMB_MQTT_LOCAL_USER`     | str  | *(empty)*   | No       | Username for local broker authentication (optional). |
| `BMB_MQTT_LOCAL_PASSWORD` | str  | *(empty)*   | No       | Password for local broker authentication (optional). |
| `BMB_MQTT_LOCAL_TLS` | bool | `false` | No | Enable TLS for the local broker. Accepts `true/false` (case insensitive). |
| `BMB_MQTT_LOCAL_TLS_VERIFY` | bool | `true` | No | Verify certificate chain and hostname when TLS is enabled. `false` disables both checks; encryption stays enabled, but the broker identity is not verified. |
| `BMB_MQTT_LOCAL_TLS_CA_FILE` | str | `/etc/ssl/certs/ca-certificates.crt` | No | Container path to a PEM CA/certificate file. Used when TLS is enabled. |

### 🧭 Topic Prefix & Status Topic

| Variable       | Type | Default | Required | Description |
|----------------|------|---------|----------|-------------|
| `BMB_MQTT_LOCAL_PREFIX` | str  | `bmw/`  | No       | Topic prefix for all republished topics. If empty, the program falls back to `bmw/`. A trailing slash is **enforced** automatically. |
| `BMB_BMW_STATUS_STABLE_DELAY` | int  | 5  | No       | delay time for bmw connection state true->false: anti flickering during token refresh |

### ✂️ Split Topics

| Variable        | Type | Default | Required | Description |
|-----------------|------|---------|----------|-------------|
| `BMB_MQTT_SPLIT_TOPICS`  | int  | `0`     | No       | `0` = disabled, `1` = enabled. When enabled, JSON payloads are parsed and individual fields are republished under `vehicles/<VIN>/<propertyName>`. |
| `BMB_MQTT_HOMIE` | int | `0` | No | `1` additionally publishes Homie 4 devices and properties under `homie/` for openHAB discovery. Independent of split topics; Homie messages are retained. |

### 🔁 Retained Messages

| Variable       | Type | Default | Required | Description |
|----------------|------|---------|----------|-------------|
| `BMB_MQTT_RETAIN`  | int  | `0`     | No       | `0` = do not retain (default), `1` = retain republished topics. Affects **RAW**, **Legacy**, and **Split** topics. The **status topic** is always retained regardless of this setting. |

### Runtime and state settings

| Variable | Default | Description |
| --- | --- | --- |
| `BMB_BMW_TOKEN_DIR` | `/app/token` | Default token directory shared by authentication and bridge operation. Optional container environment override; adjust the volume mount to the same path. |
| `BMB_BMW_HEARTBEAT_FILE` | `/tmp/bmw-mqtt-bridge-heartbeat` | Default heartbeat path shared by the main loop and Compose healthcheck. Optional container environment override. |
| `BMB_HEALTH_MQTT_DISCONNECT_TIMEOUT` | `120` in Compose | Positive seconds of continuous downtime tolerated for each MQTT connection before healthchecks fail. |

The default Compose file omits both path variables. The application,
authentication helper and entrypoint provide their own defaults, and the
healthcheck uses the same heartbeat fallback. To customize the paths, edit the
service configuration, for example:

```yaml
services:
  bmw-mqtt-bridge:
    environment:
      BMB_BMW_TOKEN_DIR: /app/state
      BMB_BMW_HEARTBEAT_FILE: /tmp/bridge-heartbeat
    volumes:
      - bmb_data_token:/app/state
```

Keep the existing connection environment settings when making this change.
The token volume must be mounted at the chosen `BMB_BMW_TOKEN_DIR`; the healthcheck
reads `BMB_BMW_HEARTBEAT_FILE` automatically. Adding these keys to the host `.env`
alone does not pass them to the container; declare them under `environment`.

Numeric options reject invalid values instead of silently falling back to defaults.
Both MQTT ports must be between 1 and 65535; `BMB_MQTT_SPLIT_TOPICS`, `BMB_MQTT_HOMIE` and
`BMB_MQTT_RETAIN` accept only `0` or `1`. Topic prefixes cannot contain
MQTT wildcards (`+` or `#`). TLS switches accept `true` or `false`.

The supplied `.env.sample` enables `BMB_MQTT_SPLIT_TOPICS=1`; the executable and Compose
fallback default to `0` when it is not configured. The default `BMB_MQTT_LOCAL_HOST`
is `host.docker.internal` in both Compose and the executable.

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
unavailable are not buffered or replayed. The Docker healthcheck measures both
process liveness and prolonged outages of either MQTT connection.

### BMW MQTT reconnect watchdog

The BMW connection also has a 30-second downtime watchdog, using a monotonic
clock. It recreates the client when the connection remains unavailable, including
when DNS/TLS fails before a CONNECT is sent or the network loop terminates.
Each replacement restores MQTT v5, TLS certificate verification, callbacks and
the latest token credentials. Connection and network-loop startup errors are
checked; failed replacements are retried after another 30 seconds. A successful
CONNACK subscribes to the BMW topics again and publishes the current status.

The watchdog starts timing after the application detects a disconnect; MQTT
keepalive or TCP failure detection can take additional time. Ordinary transport
disconnects retain libmosquitto's automatic reconnects with a 1-to-10-second delay.
When BMW rejects CONNECT or sends an error DISCONNECT, the callback requests a
disconnect to suppress library reconnects. The main loop recreates the client
only after the shared backoff expires, without an additional 30-second wait:

| BMW reason | Minimum pause |
| --- | --- |
| Quota exceeded (`151`) | 60 seconds |
| Not authorized (`135`) | 30 seconds |
| Unspecified error, server unavailable or busy (`128`, `136`, `137`) | 20 seconds |
| Other CONNECT rejections / error DISCONNECT reasons | 5 seconds |

The shared deadline uses a monotonic clock. Network errors and token refresh
can extend an existing pause but never shorten it. Rebuilds check the deadline
again after joining the old network thread, so a late callback cannot bypass
the pause. A successful CONNACK clears the backoff. Token refresh attempts also
wait while backoff is active; the local broker watchdog and heartbeat continue.
Blocking token refresh or other main-loop work can delay checks. Downtime used
by the healthcheck is not reset by a rebuild; it resets only after a successful
connection.

### Status Topic Prefix

By default, the bridge publishes its connection status to:

```text
bmw/status
```

If you want a different topic prefix (for example if you have multiple cars or bridges),
you can configure it using this environment variable in your .env file:

```text
BMB_MQTT_LOCAL_PREFIX=mycar/
```

The bridge will then publish:

```text
mycar/status
```

and all other MQTT messages (e.g. `raw`, `vehicles`, etc.) under the same prefix.

**status:**

Reports the connection state to the BMW MQTT broker (true = connected, false = disconnected).

A successful local MQTT reconnect does not imply that BMW is connected. After
local CONNACK, the bridge immediately attempts to publish the current BMW state;
the normal disconnect debounce still applies. Status messages use retained QoS 1
and are refreshed every 30 seconds while the connection state is stable. This
also corrects a retained status overwritten later by a delayed Last Will. The
state is read inside the publisher instead of accepting a caller's earlier
snapshot. Callbacks from retired local clients are ignored.

true is published immediately when the connection is established.

false is published only after BMB_BMW_STATUS_STABLE_DELAY seconds of continuous disconnect (default: 5).

Set BMB_BMW_STATUS_STABLE_DELAY=0 to disable the delay (instant switching).

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
BMB_MQTT_SPLIT_TOPICS=1
```

This will create additional messages like:

```text
bmw/vehicles/<VIN>/fuelPercentage {"value":62.5,"unit":"%","timestamp":1739790000}
bmw/vehicles/<VIN>/range_km       {"value":420}
bmw/vehicles/<VIN>/position       {"value":{"lat":48.1,"lon":11.6},"timestamp":1739790100}
```

### Homie 4 publishing (openHAB discovery)

Enable the additional Homie output in `.env`:

```ini
BMB_MQTT_HOMIE=1
```

The default is `0` (disabled). RAW and Legacy publishing continue as before.
Homie works with either setting of `BMB_MQTT_SPLIT_TOPICS` and uses Homie **4.0.0**,
which openHAB supports, rather than Homie 5.

Each VIN becomes a device at `homie/bmw-<lowercase-VIN>`, with a `telemetry` node.
Each received field under `data` becomes a read-only property: its `$name` is the
original BMW field name, `$unit` comes from the message, and its value topic contains
the scalar value rather than the RAW JSON envelope. Property IDs use `p-` followed
by the hexadecimal UTF-8 bytes of the original field name. This keeps IDs stable
and avoids collisions between dots, hyphens and upper/lowercase names.

JSON numbers use Homie's `float` datatype, including an initial integer `0`, so a
later fractional reading works without changing the channel type. Booleans use
`boolean`; strings use `string`. Objects and arrays are serialized as JSON strings.
Null, missing and empty string values are ignored. Numeric strings remain strings.
If a field's type or unit changes, its description is republished.

Newly encountered fields extend the complete `$properties` list and trigger a
description update (`init`, metadata, values, then `ready`). Fields absent from a
later partial message remain registered. The complete field registry and last
values are saved atomically in `/app/token/homie-cache.json` in the existing volume,
and replayed after a bridge restart or Homie MQTT reconnect. Cached readings may
be old; `ready` reports connectivity, not freshness of every field.

Homie descriptions and values always use retained QoS 1, independently of
`BMB_MQTT_RETAIN`. Each vehicle has its own connection to the configured local broker,
with the same credentials and TLS settings, and a retained `$state=lost` Last Will.
Connections are rebuilt after 30 seconds of continuous downtime. `$state=alert`
indicates that the local Homie connection is online but BMW is disconnected.
Normal shutdown publishes `disconnected`; if it cannot be acknowledged, the
connection closes without a clean disconnect so the broker can deliver `lost`.

In openHAB, enable the Homie discovery support appropriate to your openHAB version
and use your existing MQTT Broker Bridge. Adopt each discovered vehicle from the
Inbox in MainUI; its properties become channels automatically. Link these channels
to Items in MainUI. No `.things` file or JSONPath transformation is needed.
See the [openHAB Homie documentation](https://www.openhab.org/addons/bindings/homie/).
Adding fields to an already adopted Thing should be verified with your installed
openHAB version; the bridge republishes the entire updated description.

Setting `BMB_MQTT_HOMIE=0` stops Homie publishing but does not delete retained Homie
topics or the local cache. To remove a device permanently, clear its retained
`homie/bmw-<VIN>/...` messages in your MQTT client and remove the corresponding
openHAB Thing. Stop the bridge before editing/deleting the cache, otherwise known
fields will be restored from it on the next start.

## MQTT retain

To ensure Home Assistant and other clients immediately see the last known state after a restart, the bridge can publish its republished MQTT messages **with the Retain flag**.

**Default:** off (`BMB_MQTT_RETAIN=0`)
**When enabled:** Retain applies to:

- `bmw/raw/<VIN>/<eventName>`
- `bmw/<VIN>/<eventName>` (Legacy)
- `bmw/vehicles/<VIN>/<propertyName>` (when `BMB_MQTT_SPLIT_TOPICS=1`)

The **status topic** `bmw/status` is always retained (LWT), regardless of this setting, to keep availability tracking consistent.

### Enable

edit the file: **.env**

```ini
BMB_MQTT_RETAIN=1
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
- If you later change your `BMB_MQTT_LOCAL_PREFIX`, old retained messages under the previous prefix will remain in your broker until you remove them manually (see above).

## Local development

Clone the repository and configure your local `.env`:

```bash
git clone https://github.com/alaub81/bmw-mqtt-bridge.git
cd bmw-mqtt-bridge
cp docker-compose.example.yml docker-compose.yml
cp .env.sample .env
chmod 600 .env
```

Set your BMW IDs and MQTT connection settings, then build and authenticate:

```bash
docker compose -f docker-compose.dev.yml build
docker compose -f docker-compose.dev.yml stop bmw-mqtt-bridge
docker compose -f docker-compose.dev.yml run --rm -it bmw-mqtt-bridge ./bmw_flow.sh
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml logs -f bmw-mqtt-bridge
```

`docker-compose.dev.yml` inherits the runtime settings, healthcheck and token
mount from `docker-compose.example.yml` through
[Compose extends](https://docs.docker.com/compose/how-tos/multiple-compose-files/extends/).
It builds the local `Dockerfile` as `bmw-mqtt-bridge:dev` with `pull_policy: build`,
so development uses your working tree regardless of `BMB_VERSION`. Only the
top-level volume declaration is repeated because `extends` does not inherit it.
Always include `-f docker-compose.dev.yml` for development commands, including
`stop`, `down`, `run` and `logs`. Plain `docker compose` uses your copied `docker-compose.yml` for the GHCR deployment.

Both configurations share the same project volume when run from the same
directory. Skip authentication when valid tokens are already present, and run
only one bridge per BMW account/token volume.

After code changes, rebuild and replace the development container:

```bash
docker compose -f docker-compose.dev.yml up -d --build --force-recreate
```

## Lint checks

Run all project lint checks with:

```bash
./tests/linter-check.sh
```

The CI lint job invokes this exact command. It never fixes or stages files and
returns a non-zero exit code if any check fails or a required tool is missing.
All checks run even when an earlier check fails; the final summary lists the
results. No BMW account, token files, running MQTT broker or Docker daemon is needed.
Cppcheck uses `--check-level=normal` instead of the exhaustive default in the
pinned version. Progress messages are enabled; if it exceeds 120 seconds, the
C++ check fails and subsequent checks still run. Pressing Ctrl+C exits with
code 130 and a short interruption message instead of a Python traceback.

### Tool installation

Use Python 3.12+, Git, Make and a C++ compiler. Node.js and pnpm are not required. On
macOS install Hadolint 2.15.1 on PATH first (for example through Homebrew), since
that release has no macOS binary. The installer downloads native tools with
pinned versions and verified SHA-256 checksums into the ignored `.lint-tools`
directory. Cppcheck is built from its pinned source archive. It leaves system
packages unchanged. Matching tools already present on PATH may be reused.

```bash
python3 -m venv .lint-venv
.lint-venv/bin/python -m pip install --requirement tests/requirements-lint.txt
.lint-venv/bin/python tests/install_lint_tools.py
./tests/linter-check.sh
```

CI defines separate linter steps in `.github/workflows/ci.yml` and does not
invoke `tests/linter-check.sh`. Python
package versions are pinned in `tests/requirements-lint.txt`, and native tool
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
| `.env.sample` | Assignment syntax and duplicate keys; no shell execution |
| Markdown documentation and issue templates | PyMarkdown |

The vendored `resources/src/json.hpp` receives text encoding/newline/merge-marker
checks and is parsed as an include during C++ analysis. Its upstream formatting
and standalone third-party lint diagnostics are preserved. Markdown allows two
trailing spaces for hard line breaks. Unknown text file types still receive the
common text checks; new programming languages should get a dedicated linter.

### Rules

Rule settings live in `.hadolint.yaml`, `.yamllint.yaml`, `.pymarkdown.yaml`
and `ruff.toml`.
Hadolint permits unpinned Debian and Alpine package versions so security updates can follow
the selected distribution; other warnings fail the check. Markdown permits long
lines, embedded HTML, bold labels and flexible table alignment to retain the
existing documentation style. PyMarkdown runs in the existing Python environment
and supports the YAML front matter in GitHub issue templates. HTML, JavaScript
and CSS linters are not installed.

This command performs lint checks, not deployment or token authentication.
Run regression tests with the shared local/CI entrypoint:

```bash
./tests/test-check.sh
```

## CI and container releases

`.github/workflows/ci.yml` defines individual linter steps: Hadolint,
ShellCheck, yamllint, actionlint, Ruff, Cppcheck and PyMarkdown. It also checks
text hygiene, JSON and dotenv examples. CI validates Compose, builds the bridge
image and scans it with Trivy. The offline regression suite remains a separate
job using `./tests/test-check.sh`.

`.github/workflows/release.yml` builds and publishes only this project's
`Dockerfile` to `ghcr.io/<repository-owner>/bmw-mqtt-bridge` for `linux/amd64`
and `linux/arm64`. Push a stable version tag to release a container:

```bash
git tag v1.2.3
git push origin v1.2.3
```

Use the next unused version for your project. The versioned image tag is `1.2.3`.
The newest stable release also updates `1.2`, `1` and `latest`. The workflow
retains the template's weekly rebuild of recent tags and its manual rebuild
option; older rebuilds do not overwrite the newest release's aliases. Critical,
fixable Trivy findings prevent publication. No BMW credentials are needed to
build or publish the image.

Dependabot is the sole dependency update bot, configured in
`.github/dependabot.yml`. It checks GitHub Actions and Docker dependencies weekly.
The active workflows live exclusively in `.github/workflows/`.

## Pre-deployment tests

Run both checks before building and starting a local development container:

```bash
./tests/linter-check.sh && ./tests/test-check.sh && \
  docker compose -f docker-compose.dev.yml up -d --build --force-recreate
```

`tests/test-check.sh` runs all tests under `tests/` from any working directory. It uses
the project-local `.lint-venv` Python when available, otherwise system Python.
Requirements are Python 3.9+, Bash, Git, jq, OpenSSL, a C++17 compiler available as `c++`, and
Docker CLI with the Compose plugin. No running Docker daemon, BMW account,
production tokens or MQTT broker is required. Missing dependencies, skipped
tests, an empty suite or failed tests return a non-zero exit code.

The separate CI job **Offline regression tests** invokes exactly
`./tests/test-check.sh`. These tests validate token handling and legacy-state rejection,
environment-only configuration, GHCR image selection and shared development settings,
token response validation, permissions and atomic writes, BMW server backoff
and library reconnect suppression, TLS settings,
MQTT status and shutdown handling, heartbeat and
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
