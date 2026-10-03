#!/usr/bin/env bash
# Run the same offline regression suite locally and in CI before deployment.
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$PROJECT_DIR"
export PATH="$PROJECT_DIR/.lint-venv/bin:$PROJECT_DIR/.lint-tools/bin:$PATH"

# Missing dependencies must fail rather than silently skip regression checks.
for tool in python3 bash git c++ docker; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing test dependency: $tool" >&2
    exit 1
  fi
done
if ! docker compose version >/dev/null 2>&1; then
  echo "Missing test dependency: Docker Compose plugin" >&2
  exit 1
fi

exec python3 tests/run_tests.py
