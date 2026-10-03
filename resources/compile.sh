#!/usr/bin/env bash
set -euo pipefail

# Build the bridge in the Docker builder stage.
cd /build/src

echo "Compiling bmw_mqtt_bridge..."
# pkg-config emits separate compiler/linker flags.
# shellcheck disable=SC2046
g++ -std=c++17 -O2 -pthread \
  bmw_mqtt_bridge.cpp -o bmw_mqtt_bridge \
  $(pkg-config --cflags --libs libmosquitto) -lcurl

echo "Build successful: /build/src/bmw_mqtt_bridge"
