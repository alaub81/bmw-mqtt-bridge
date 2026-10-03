#!/usr/bin/env bash
# Run every project lint check locally. This command never fixes files.
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$PROJECT_DIR"

# Prefer project-local tools; system installations remain supported.
export PATH="$PROJECT_DIR/.lint-venv/bin:$PROJECT_DIR/.lint-tools/bin:$PATH"
exec python3 tests/lint.py "$@"
