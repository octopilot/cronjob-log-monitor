# CronJob log monitor (stuck-detector)

**Location:** `cronjob-log-monitor` (standalone repo). This tool is **general-purpose** and useful for any CronJob/Job that needs stuck-pod detection.

Rust **edition 2021**, **stable** toolchain (rust-version 1.75+). Use Rust 2024 edition when it is stable if desired.

Detects CronJob/Job pods that have had **no log activity** for a configurable period and **deletes** them so the Job fails and the CronJob can retry. Runs as a separate controller (no sidecar, no app changes). Emits Datadog metrics and exposes health endpoints.

Product requirements and design are documented in `docs/PRD.md`.

## Build

```bash
cargo build --release
```

## Run

Required environment variables:

- `NAMESPACE` – Kubernetes namespace to watch (e.g. `myapp`)
- `MONITOR_LABEL_SELECTOR` – Label selector for pods to monitor (e.g. `monitor-logs=true`). Helm must add this label to CronJob pod templates when the log monitor is enabled.

Optional:

- `MONITOR_POD_NAME_REGEX` – Only monitor pods whose **name** matches this regex (e.g. `^myapp-.+`)
- `MONITOR_LABEL_REGEX` – Format `label_key=regex` (e.g. `job-name=^myapp-.+`) to filter by a label value
- `MAX_SILENCE_MINUTES` – No new log activity for this many minutes → consider stuck (default: `5`)
- `CHECK_INTERVAL_SECONDS` – How often to fetch logs per pod (default: `30`)
- `GRACE_PERIOD_SECONDS` – Do not consider stuck until pod has been running this long (default: `120`)
- `HEALTH_PORT` – Port for liveness/readiness HTTP server (default: `1234`)
- `LOG_TIMESTAMP_FIELD` – JSON field for last activity (default: `@timestamp`); if unset, uses “last line received” time
- `DD_AGENT_HOST`, `DD_DOGSTATSD_PORT` – Datadog DogStatsD (default: `127.0.0.1:8125`)
- `DD_ENABLED` or `DATADOG_METRICS_ENABLED` – Set to `false` to disable metrics (e.g. in dev)
- `RBAC_MAX_ATTEMPTS` – Max attempts to wait for RBAC before starting watch (default: `15`)

Example (minimal):

```bash
export NAMESPACE=myapp
export MONITOR_LABEL_SELECTOR=monitor-logs=true
./target/release/cronjob-log-monitor
```

## Health endpoints

- `GET /health`, `GET /healthz` – 200 + JSON with status and ready flag
- `GET /live`, `GET /liveness` – 200 if process is running
- `GET /ready`, `GET /readyz` – 200 when Kube client is ready and watcher has started; 503 otherwise

Configure Kubernetes liveness and readiness probes to use these (e.g. `httpGet` on `HEALTH_PORT`).

## RBAC

The controller needs:

- `pods`: list, get, watch, delete
- `pods/log`: get

Create a ServiceAccount, Role/ClusterRole, and RoleBinding granting these in the namespace(s) you watch.

## Local E2E (Tilt)

To run the full pipeline locally (Kind + registry-tls + op build + Flux + Helm chart deploy) and troubleshoot the Helm OCI chart path end-to-end, see **[docs/local-e2e.md](docs/local-e2e.md)**. Prerequisites: kind, kubectl, flux CLI, op (octopilot-pipeline-tools), jq; Docker configured for insecure registry `localhost:5001`. Then: `kind create cluster --name cronjob-log-monitor --config kind-config.yaml` and `tilt up`.

## Runbook (troubleshooting)

- **Controller not seeing pods** – Ensure the CronJob’s pod template has the label from `MONITOR_LABEL_SELECTOR` (e.g. `monitor-logs=true`). If using regex, check `MONITOR_POD_NAME_REGEX` or `MONITOR_LABEL_REGEX` matches your pod names/labels.
- **RBAC / 401 / 403** – The controller retries with backoff. Ensure the ServiceAccount has the permissions above and that the RoleBinding binds the Role to the controller’s ServiceAccount.
- **Datadog not receiving metrics** – Set `DD_AGENT_HOST` (and optionally `DD_DOGSTATSD_PORT`) to the Datadog agent host/port. In-cluster this is often the agent sidecar or a service. Set `DD_ENABLED=false` to disable sending.
