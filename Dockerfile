# Build and runtime use the same musl-based distribution.
FROM alpine:3.24 AS builder

RUN apk add --no-cache \
    bash \
    g++ \
    make \
    mosquitto-dev \
    curl-dev \
    pkgconf

WORKDIR /build
COPY ./resources/src ./src
COPY --chmod=0755 ./resources/compile.sh ./scripts/compile.sh
RUN bash ./scripts/compile.sh

FROM alpine:3.24

# Runtime libraries and tools used by the OAuth device flow.
RUN apk add --no-cache \
    bash \
    libstdc++ \
    mosquitto-libs \
    libcurl \
    ca-certificates \
    curl \
    jq \
    openssl

WORKDIR /app
COPY --chmod=0755 --from=builder /build/src/bmw_mqtt_bridge /app/bmw_mqtt_bridge
COPY --chmod=0755 ./resources/bmw_flow.sh .
COPY --chmod=0755 ./resources/docker-entrypoint.sh .

ENV BMB_BMW_HOST=customer.streaming-cardata.bmwgroup.com \
    BMB_BMW_PORT=9000 \
    BMB_MQTT_LOCAL_HOST=host.docker.internal \
    BMB_MQTT_LOCAL_PORT=1883 \
    BMB_MQTT_LOCAL_PREFIX=bmw/

VOLUME ["/app/token"]
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["/app/bmw_mqtt_bridge"]
