# Build stage: mount host Cargo/rustup/target via build-args for cache reuse
FROM rust:1.85-bookworm AS builder
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src ./src
ARG CARGO_CACHE_SOURCE
ARG RUSTUP_CACHE_SOURCE
ARG TARGET_CACHE_SOURCE
RUN --mount=type=bind,source=${CARGO_CACHE_SOURCE},target=/usr/local/cargo,rw \
    --mount=type=bind,source=${RUSTUP_CACHE_SOURCE},target=/usr/local/rustup,rw \
    --mount=type=bind,source=${TARGET_CACHE_SOURCE},target=/app/target,rw \
    env CARGO_TARGET_DIR=/app/target CARGO_BUILD_INCREMENTAL=1 \
    cargo build --release

# Run stage
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/target/release/cronjob-log-monitor /usr/local/bin/
ENTRYPOINT ["/usr/local/bin/cronjob-log-monitor"]
