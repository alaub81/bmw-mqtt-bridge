#!/usr/bin/env bash
set -euo pipefail

# Build the bridge in the Docker builder stage.
cd /build/src

echo "Compiling bmw_mqtt_bridge..."
# Keep warnings fatal in local, CI and release builds for both executables.
compiler_flags=(-std=c++17 -O2 -pthread -Wall -Wextra -Wpedantic -Wshadow -Werror)
# pkg-config emits separate compiler/linker flags.
# shellcheck disable=SC2046
g++ "${compiler_flags[@]}" \
  bmw_mqtt_bridge.cpp -o bmw_mqtt_bridge \
  $(pkg-config --cflags --libs libmosquitto) -lcurl

g++ "${compiler_flags[@]}" bmw_token_store.cpp -o bmw_token_store

echo "Build successful: /build/src/bmw_mqtt_bridge"
