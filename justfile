# cronjob-log-monitor — development task runner
# Install just: https://github.com/casey/just
# Usage: just <recipe>

default:
    @just --list

# ── Development (Kind + registry-tls + Tilt) ───────────────────────────────────

# Start dev environment: Kind cluster "kind", registry-tls in Docker, then Tilt.
dev-up:
    python3 scripts/dev_up.py

# Stop dev environment: stop Tilt, delete Kind cluster "kind". Registry left running.
dev-down:
    python3 scripts/dev_down.py

# ── Code quality ──────────────────────────────────────────────────────────────

# Check formatting only (fails if not formatted; use in CI/pre-commit)
fmt-check:
    cargo fmt --all -- --check

# Apply formatting
fmt:
    cargo fmt --all

# Lint with Clippy (warnings = errors)
lint:
    cargo clippy --all-targets --all-features -- -D warnings

# Lint and auto-fix what Clippy can
lint-fix:
    cargo clippy --all-targets --all-features --fix -- -D warnings

# ── Tests ─────────────────────────────────────────────────────────────────────

# Run all tests
test:
    cargo test --all-features

# Run all tests with output
test-verbose:
    cargo test --all-features -- --nocapture

# ── Pre-commit ─────────────────────────────────────────────────────────────────

# Run pre-commit on all files (blocks if fmt or lint fails)
pre-commit:
    pre-commit run --all-files

# Install pre-commit hooks (run once; then unformatted/unlinted code cannot be committed)
install-hooks:
    pre-commit install
    @echo "✅ pre-commit hooks installed — fmt and lint will run on commit"

# Install pre-commit the tool (if not present)
install-pre-commit:
    pip install pre-commit

# Full setup: install pre-commit tool and git hooks
setup: install-pre-commit install-hooks

# ── CI / combined ─────────────────────────────────────────────────────────────

# Everything that must pass before push (fmt check → lint → test)
ci: fmt-check lint test

# ── Build ─────────────────────────────────────────────────────────────────────

build:
    cargo build --release

clean:
    cargo clean
