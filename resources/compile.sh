#!/bin/bash
set -euo pipefail

# compile.sh – build bmw_mqtt_bridge inside src directory

# get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="$SCRIPT_DIR/src"
# The Docker builder copies this helper into scripts/ beside the src/ directory.
if [[ ! -d "$SRC_DIR" ]]; then
  SRC_DIR="$ROOT_DIR/src"
fi

cd "$SRC_DIR" || exit 1

echo "Compiling bmw_mqtt_bridge..."
# pkg-config emits separate compiler/linker flags.
# shellcheck disable=SC2046
g++ -std=c++17 -O2 -pthread \
  bmw_mqtt_bridge.cpp -o bmw_mqtt_bridge \
  $(pkg-config --cflags --libs libmosquitto) -lcurl

echo "✅ Build successful: $SRC_DIR/bmw_mqtt_bridge"
