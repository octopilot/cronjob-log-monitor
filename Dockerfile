# Build stage: mount host Cargo/rustup for cache reuse; use BuildKit cache for target (host path not visible to daemon in CI)
FROM rust:1.85-bookworm AS builder
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src ./src
ARG CARGO_CACHE_SOURCE
ARG RUSTUP_CACHE_SOURCE
RUN --mount=type=bind,source=${CARGO_CACHE_SOURCE},target=/usr/local/cargo,rw \
    --mount=type=bind,source=${RUSTUP_CACHE_SOURCE},target=/usr/local/rustup,rw \
    --mount=type=cache,target=/app/target \
    env CARGO_TARGET_DIR=/app/target CARGO_BUILD_INCREMENTAL=true \
    cargo build --release

# Run stage
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/target/release/cronjob-log-monitor /usr/local/bin/
ENTRYPOINT ["/usr/local/bin/cronjob-log-monitor"]
