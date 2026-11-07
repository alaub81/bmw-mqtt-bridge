# BMW CarData MQTT Bridge for Docker and openHAB

Forward BMW CarData vehicle telemetry to your own MQTT broker. Use the data in
openHAB through Homie 4 discovery or consume JSON topics with other MQTT clients.
Based on [dj0abr/bmw-mqtt-bridge](https://github.com/dj0abr/bmw-mqtt-bridge).

## What it does

- BMW OAuth device login, persistent tokens and automatic token refresh.
- RAW JSON, optional per-field topics and optional Homie 4 discovery.
- Reconnect supervision, retained connection status and configurable MQTT TLS.
- Two optional openHAB display widgets for vehicle data; no vehicle commands.
- Docker images for amd64 and arm64, with an unprivileged runtime user.

You need Docker with Compose, a reachable MQTT broker and a BMW account with
CarData streaming access for your vehicle. Available fields and update frequency
depend on BMW and your selected data; cached values can be older than the last
message. This project is an independent community bridge.

## Quick start

Install Docker with the Compose plugin and obtain your
[BMW IDs](docs/configuration.md#get-your-bmw-ids). Create a directory with these two files.

Minimal `docker-compose.yml`:

```yaml
services:
  bmw-mqtt-bridge:
    image: ghcr.io/alaub81/bmw-mqtt-bridge:${BMB_VERSION:-latest}
    env_file: .env
    volumes:
      - data_bmb_token:/app/token
    restart: unless-stopped

volumes:
  data_bmb_token:
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
they remain valid. Connection status is published to `bmw/status` when RAW or split topics are enabled.

For Homie discovery add `BMB_MQTT_HOMIE=1` to `.env` before starting.
No `.things`, JSONPath file or extra bridge configuration file is required.
The optional widgets require Items linked to the discovered channels.

## Guides

- [Installation, environment variables, MQTT topics and TLS](docs/configuration.md)
- [Updates, backup and restore](docs/operations.md)
- [openHAB widgets and individual MainUI imports](openHAB/README.md)
- [Development, linters and test procedures](docs/development.md)
- [Publication check and remaining verification](docs/publication-check.md)
- [Security reporting](SECURITY.md)

The short example has no Docker healthcheck. The optional
[detailed Compose template](docker-compose.example.yml) adds health monitoring,
a read-only filesystem and bounded logs; copy it to `docker-compose.yml` and
use [.env.sample](.env.sample) for the full set of supported settings.

## License and credits

[MIT](LICENSE). Original project by dj0abr; this fork is maintained by alaub81.
