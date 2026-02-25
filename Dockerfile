# Build stage: use BuildKit cache mounts (host paths not visible to daemon in GitHub Actions)
FROM rust:1.85-bookworm AS builder
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/app/target \
    env CARGO_TARGET_DIR=/app/target CARGO_BUILD_INCREMENTAL=true \
    cargo build --release \
    && cp /app/target/release/cronjob-log-monitor /app/cronjob-log-monitor

# Run stage
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/cronjob-log-monitor /usr/local/bin/
ENTRYPOINT ["/usr/local/bin/cronjob-log-monitor"]
