# Development and verification

Run commands from the repository root. [Back to overview](../README.md).

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

The local lint command never fixes or stages files and
returns a non-zero exit code if any check fails or a required tool is missing.
All checks run even when an earlier check fails; the final summary lists the
results. No BMW account, token files, running MQTT broker or Docker daemon is needed.
Cppcheck uses `--check-level=normal` instead of the exhaustive default in the
pinned version. Progress messages are enabled; if it exceeds 120 seconds, the
C++ check fails and subsequent checks still run. Pressing Ctrl+C exits with
code 130 and a short interruption message instead of a Python traceback.

### Tool installation

Use Python 3.12+, Git, Make and a C++ compiler. Widget regression tests use local Node.js when available, otherwise a temporary
Docker container; pnpm is not required. On
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
trailing spaces for hard line breaks. PNG and JPEG assets receive a matching image-signature check, not text decoding.
Unknown text file types still receive the
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
Requirements are Python 3.9+, PyYAML, Bash, Git, jq, OpenSSL, a C++17 compiler available as `c++`, and
Docker CLI with the Compose plugin. Without local Node.js, widget tests require a running Docker daemon and the
`node:24-alpine` image (downloaded automatically on first use). With local Node.js,
no running Docker daemon is required. No BMW account, production tokens or MQTT
broker is required. Missing dependencies, skipped
tests, an empty suite or failed tests return a non-zero exit code.

The separate CI job **Offline regression tests** invokes exactly
`./tests/test-check.sh`. These tests validate token handling and legacy-state rejection,
environment-only configuration, GHCR image selection and shared development settings,
token response validation, permissions, atomic bundle commits and legacy migration, BMW server backoff
and library reconnect suppression, TLS settings,
MQTT status and shutdown handling, background job completion and snapshot generations,
JWT/refresh validation and response limits,
log levels and repeat suppression, Homie deltas/checkpoints/failure retries, heartbeat and
Compose healthcheck logic, and lint file selection/error handling. They use
temporary directories and compiled extracts of the production C++ code with
simulated MQTT functions. They are offline regression tests, not an end-to-end
test of a running container connected to BMW and an actual MQTT broker.

### Sanitizer regression tests

CI runs the same offline suite a second time with AddressSanitizer and
UndefinedBehaviorSanitizer. Run it locally with the same dependencies and a C++
compiler supporting both sanitizers:

```bash
bash tests/sanitizer-check.sh
```

The runner temporarily wraps `c++` to compile every C++ regression fixture with
`-fsanitize=address,undefined`, debug information and frame pointers. Memory errors
and undefined behavior fail the run, including diagnostics from subprocesses whose
non-zero exit is expected by a negative-path test. The wrapper is
removed on exit. The checks exercise compiled production extracts and test
harnesses; Python, prebuilt third-party libraries and the complete running bridge are not
instrumented. Production images keep their ordinary optimized build flags.
These checks complement the separate real MQTT/HTTP integration tests and do not
establish complete code coverage or detect every possible data race.

### Real MQTT integration tests

The separate test image starts Mosquitto on its own loopback interface, exercises
the production Homie publisher with real libmosquitto and removes the broker on
exit. No host ports, BMW credentials or existing MQTT services are used.

```bash
docker build -f tests/Dockerfile.integration -t bmb-mqtt-integration:test .
docker run --rm --network none bmb-mqtt-integration:test
```

CI runs these tests separately from the offline suite. They cover discovery,
retained values, delta suppression, broker restart/replay, crash Last Will and
acknowledged shutdown. The offline suite additionally executes widget expressions
from the actual YAML using Node.js, including opening states, missing Items and
unit conversions. Storage fault injection covers EROFS, ENOSPC, EINTR, failed
flushes and failed renames, checking preservation of old data and temporary-file
cleanup. Separate restart tests interrupt bundle commits before and after rename,
retry interrupted migrations and reject corrupt canonical state. Repeated bursts exercise queue overflow, checkpointing and final state
across two simulated hours; this is not a multi-day soak or a BMW HTTP integration.

### Slow HTTP refresh integration tests

A separate image compiles the production refresh/form/callback code and uses real
libcurl against a loopback HTTP test server. Only the endpoint is replaced;
production HTTP timeouts remain unchanged. No BMW credentials, host ports or
external network are used:

```bash
docker build -f tests/Dockerfile.http -t bmb-http-integration:test .
docker run --rm --network none bmb-http-integration:test
```

The tests cover a slow successful body, a stalled body hitting the 20-second
production timeout, and shutdown before headers or during a partial body. They
check continued background-job polling, single-flight exclusion, unchanged active
credentials, atomic token commits and preservation of existing tokens on failure.
CI runs them separately; the suite takes roughly 25 seconds. These are refresh
worker integration tests, not a complete BMW/MQTT deployment test.
