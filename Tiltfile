# Tilt-based local e2e: Kind cluster "kind" + registry-tls + op build + Flux + Helm chart deploy.
#
# Prerequisites: run `just dev-up` (creates Kind cluster "kind" and ensures registry-tls in Docker).
# If a registry-tls instance is already running, it is reused (no second one started).
# Or: kind + registry running, then `tilt up`. See docs/local-e2e.md.
#
# REGISTRY_PORT: set by dev-up from .registry-port (default 5001). Scripts read .registry-port or env.

allow_k8s_contexts(['kind-kind'])

# ---------------------------------------------------------------------------
# 1. Registry health (registry is running; port from dev-up / .registry-port)
# ---------------------------------------------------------------------------
local_resource(
    'registry-health',
    cmd='python3 scripts/tilt/registry_health.py',
    labels=['infrastructure'],
    resource_deps=[],
)

# ---------------------------------------------------------------------------
# 2. op build (build and push app + chart to local registry)
# ---------------------------------------------------------------------------
local_resource(
    'op-build',
    cmd='python3 scripts/tilt/op_build.py',
    deps=['skaffold.yaml', 'chart/Chart.yaml', 'scripts/tilt/op_build.py'],
    labels=['build'],
    resource_deps=['registry-health'],
)

# ---------------------------------------------------------------------------
# 3. Flux install (export and apply gotk-components)
# ---------------------------------------------------------------------------
local_resource(
    'flux-install',
    cmd='python3 scripts/tilt/flux_install.py',
    deps=['scripts/tilt/flux_install.py'],
    labels=['infrastructure'],
    resource_deps=['registry-health'],
)

# ---------------------------------------------------------------------------
# 4. Deploy (substitute build_result into OCIRepository + HelmRelease, apply)
# ---------------------------------------------------------------------------
# Nodes pull from registry-tls:5000 (same registry container on kind network).
local_resource(
    'flux-deploy',
    cmd='python3 scripts/tilt/flux_deploy.py',
    deps=['k8s/deployment', 'k8s/env/kind', 'scripts/tilt/flux_deploy.py'],
    labels=['deploy'],
    resource_deps=['op-build', 'flux-install'],
)
