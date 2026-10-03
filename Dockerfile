# -----------------------------------------------------------------------------
# Build stage
FROM debian:trixie-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    g++ \
    make \
    libmosquitto-dev \
    libcurl4-openssl-dev \
    pkg-config \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY ./resources/src ./src
COPY --chmod=0755 ./resources/compile.sh ./scripts/compile.sh
RUN bash ./scripts/compile.sh

# -----------------------------------------------------------------------------
# Runtime stage
FROM debian:trixie-slim

# Install runtime dependencies only (lean but includes nano for interactive setup)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmosquitto1 \
    libcurl4 \
    ca-certificates \
    curl \
    jq \
    openssl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy compiled binary
COPY --chmod=0755 --from=builder /build/src/bmw_mqtt_bridge /app/bmw_mqtt_bridge

# Copy scripts
COPY --chmod=0755 ./resources/bmw_flow.sh .
COPY --chmod=0755 ./resources/docker-entrypoint.sh .


# Default environment
# This is a directory path, not a token value; the token itself is stored in the volume.
# hadolint ignore=DL3064
ENV BMW_TOKEN_DIR=/app/token
ENV BMW_LOAD_ENV_FILE=0 \
    BMW_HOST=customer.streaming-cardata.bmwgroup.com \
    BMW_PORT=9000 \
    MQTT_LOCAL_HOST=host.docker.internal \
    MQTT_LOCAL_PORT=1883 \
    MQTT_LOCAL_PREFIX=bmw/

# Persist token/config directory
VOLUME ["/app/token"]

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["/app/bmw_mqtt_bridge"]
