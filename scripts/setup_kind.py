#!/usr/bin/env python3
"""
Cronjob-log-monitor Kind cluster setup.

Creates Kind cluster named "kind" and ensures registry-tls is available in Docker
(on the kind network). If a registry-tls container is already running, that
instance is reused; otherwise starts a container named registry-tls on port 5001.
Use via: just dev-up
"""

import subprocess
import sys
import time
from pathlib import Path

CLUSTER_NAME = "kind"
REGISTRY_NAME = "registry-tls"
DEFAULT_REGISTRY_PORT = "5001"
REGISTRY_IMAGE = "ghcr.io/octopilot/registry-tls:latest"
REGISTRY_CONTAINER_PORT = "5000"
REGISTRY_PORT_FILE = ".registry-port"


def log_info(msg):
    print(f"[INFO] {msg}")


def log_error(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)


def run(cmd, check=True, capture_output=True, **kwargs):
    result = subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        capture_output=capture_output,
        text=True,
        check=check,
        **kwargs,
    )
    return result


def ensure_cluster():
    """Create Kind cluster 'kind' from kind-config.yaml if it does not exist."""
    result = run("kind get clusters", check=False, capture_output=True)
    if CLUSTER_NAME in (result.stdout or ""):
        log_info(f"Cluster '{CLUSTER_NAME}' already exists")
        return

    config_path = Path(__file__).parent.parent / "kind-config.yaml"
    if not config_path.exists():
        log_error(f"kind-config.yaml not found at {config_path}")
        sys.exit(1)

    log_info(f"Creating Kind cluster '{CLUSTER_NAME}'...")
    result = run(
        f"kind create cluster --config {config_path}",
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        if "already exists" in (result.stderr or "").lower():
            log_info(f"Cluster '{CLUSTER_NAME}' already exists")
            return
        log_error(f"Failed to create Kind cluster: {result.stderr}")
        sys.exit(1)
    log_info(f"✅ Kind cluster '{CLUSTER_NAME}' created")

    # Wait for kind network to exist
    for _ in range(10):
        r = run("docker network ls --format '{{.Name}}'", check=False, capture_output=True)
        if "kind" in (r.stdout or ""):
            break
        time.sleep(1)
    else:
        log_error("Kind network not found after cluster creation")
        sys.exit(1)

    # Poll until cluster nodes are Ready
    log_info("Waiting for cluster nodes to be ready...")
    for attempt in range(60):
        r = run(
            "kubectl get nodes -o jsonpath='{.items[*].status.conditions[?(@.type==\"Ready\")].status}'",
            check=False,
            capture_output=True,
        )
        if r.returncode == 0 and r.stdout and "True" in r.stdout:
            log_info("✅ Cluster nodes are ready")
            break
        if attempt < 59:
            time.sleep(2)
    else:
        log_error("Cluster nodes did not become ready in time")
        sys.exit(1)


def find_running_registry_tls():
    """
    Find any running container that is registry-tls (by image or name).
    Returns (container_name, host_port) or (None, None). host_port is the host port
    mapped to container port 5000.
    """
    result = run(
        "docker ps --format '{{.Names}}'",
        check=False,
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout:
        return None, None
    for name in result.stdout.strip().splitlines():
        name = name.strip()
        if not name:
            continue
        # Prefer by name
        if name == REGISTRY_NAME:
            port_result = run(
                f"docker port {name} {REGISTRY_CONTAINER_PORT}",
                check=False,
                capture_output=True,
            )
            if port_result.returncode == 0 and port_result.stdout:
                # e.g. "0.0.0.0:5001" or "::5001"
                host_port = port_result.stdout.strip().split(":")[-1].strip()
                if host_port:
                    return name, host_port
            return None, None
        # Else check image
        inspect_result = run(
            f"docker inspect {name} --format '{{{{.Config.Image}}}}'",
            check=False,
            capture_output=True,
        )
        if inspect_result.returncode != 0:
            continue
        image = (inspect_result.stdout or "").strip().lower()
        if "registry-tls" in image or "octopilot/registry" in image:
            port_result = run(
                f"docker port {name} {REGISTRY_CONTAINER_PORT}",
                check=False,
                capture_output=True,
            )
            if port_result.returncode == 0 and port_result.stdout:
                host_port = port_result.stdout.strip().split(":")[-1].strip()
                if host_port:
                    return name, host_port
    return None, None


def ensure_registry_on_kind_network(container_name):
    """Connect container to kind network if not already connected."""
    result = run(
        "docker network inspect kind --format='{{range .Containers}}{{.Name}}{{end}}'",
        check=False,
        capture_output=True,
    )
    if container_name in (result.stdout or ""):
        log_info("Registry already on kind network")
        return
    log_info(f"Connecting '{container_name}' to kind network...")
    run(f"docker network connect kind {container_name}", check=False)
    log_info("✅ Registry connected to kind network")


def ensure_registry():
    """
    Use existing registry-tls if running; otherwise start registry-tls container on 5001.
    Returns (host_port, container_name).
    """
    container_name, host_port = find_running_registry_tls()
    if container_name and host_port:
        log_info(f"Using existing registry-tls container '{container_name}' on port {host_port}")
        ensure_registry_on_kind_network(container_name)
        return host_port, container_name

    # Exists but stopped (only for our named container)
    result = run("docker ps -a --format '{{.Names}}'", check=False, capture_output=True)
    if REGISTRY_NAME in (result.stdout or ""):
        log_info(f"Starting existing container '{REGISTRY_NAME}'...")
        run(f"docker start {REGISTRY_NAME}", check=False)
        ensure_registry_on_kind_network(REGISTRY_NAME)
        return DEFAULT_REGISTRY_PORT, REGISTRY_NAME

    # Create new container on kind network
    log_info(f"Starting registry '{REGISTRY_NAME}' on kind network (host port {DEFAULT_REGISTRY_PORT})...")
    run(
        f"docker run -d --rm --network kind -p {DEFAULT_REGISTRY_PORT}:{REGISTRY_CONTAINER_PORT} "
        f"--name {REGISTRY_NAME} {REGISTRY_IMAGE}"
    )
    log_info(f"✅ Registry '{REGISTRY_NAME}' running at localhost:{DEFAULT_REGISTRY_PORT} (nodes: {REGISTRY_NAME}:{REGISTRY_CONTAINER_PORT})")
    return DEFAULT_REGISTRY_PORT, REGISTRY_NAME


def get_registry_ip(container_name):
    """Get the registry container's IP on the kind network."""
    r = run(
        f"docker inspect {container_name} --format='{{{{range $k,$v := .NetworkSettings.Networks}}}}{{{{if eq $k \"kind\"}}}}{{{{.IPAddress}}}}{{{{end}}}}{{{{end}}}}'",
        check=False,
        capture_output=True,
    )
    if r.returncode == 0 and r.stdout and r.stdout.strip():
        return r.stdout.strip()
    r = run(
        f"docker inspect {container_name} --format='{{{{.NetworkSettings.IPAddress}}}}'",
        check=False,
        capture_output=True,
    )
    return r.stdout.strip() if r.returncode == 0 and r.stdout else None


def configure_containerd_registry(registry_port, container_name):
    """
    Configure containerd on all nodes to use the registry (certs.d).
    Mirror host localhost:<registry_port> -> registry endpoint with skip_verify.
    """
    r = run("kubectl get nodes -o jsonpath='{.items[*].metadata.name}'", check=False, capture_output=True)
    nodes = (r.stdout or "").strip().split()
    if not nodes:
        log_info("No nodes to configure for registry")
        return

    registry_ip = None
    for attempt in range(5):
        registry_ip = get_registry_ip(container_name)
        if registry_ip:
            break
        if attempt < 4:
            time.sleep(2)
    if registry_ip:
        log_info(f"Using registry IP: {registry_ip}")
        registry_endpoint = f"https://{registry_ip}:{REGISTRY_CONTAINER_PORT}"
    else:
        registry_endpoint = f"https://{container_name}:{REGISTRY_CONTAINER_PORT}"
        log_info(f"Using registry endpoint: {registry_endpoint}")

    mirror_host = f"localhost:{registry_port}"
    hosts_toml = f"""server = "https://{mirror_host}"

[host."{registry_endpoint}"]
  capabilities = ["pull", "resolve", "push"]
  skip_verify = true
"""

    for node in nodes:
        log_info(f"Configuring containerd on node: {node}")
        check_r = run(
            f"docker exec {node} cat /etc/containerd/certs.d/{mirror_host}/hosts.toml",
            check=False,
            capture_output=True,
        )
        if check_r.returncode == 0 and registry_endpoint in (check_r.stdout or ""):
            log_info(f"Registry already configured on {node}")
            continue
        run(f"docker exec {node} mkdir -p /etc/containerd/certs.d/{mirror_host}", check=False)
        run(
            f"docker exec -i {node} sh -c 'cat > /etc/containerd/certs.d/{mirror_host}/hosts.toml'",
            input=hosts_toml,
            check=False,
        )
        run(f"docker exec {node} systemctl restart containerd", check=False)
        for _ in range(15):
            if run(f"docker exec {node} ctr version", check=False, capture_output=True).returncode == 0:
                break
            time.sleep(1)
        log_info(f"✅ Configured registry on {node} (certs.d/{mirror_host})")


def write_registry_port(port, project_root):
    """Write registry port to .registry-port so Tilt and dev_up can read it."""
    port_file = project_root / REGISTRY_PORT_FILE
    port_file.write_text(port.strip() + "\n")
    log_info(f"Registry port {port} written to {port_file}")


def poll_registry_ready(port, timeout_sec=60):
    """Poll until registry /v2/ responds successfully or timeout."""
    log_info(f"Waiting for registry at http://localhost:{port} to be ready...")
    for attempt in range(timeout_sec):
        r = run(
            f"curl -sf -o /dev/null -w '%{{http_code}}' http://localhost:{port}/v2/",
            check=False,
            capture_output=True,
        )
        if r.returncode == 0 and r.stdout and r.stdout.strip() == "200":
            log_info("✅ Registry is ready")
            return
        if attempt < timeout_sec - 1:
            time.sleep(1)
    log_error(f"Registry not ready at http://localhost:{port}/v2/ after {timeout_sec}s")
    sys.exit(1)


def main():
    log_info("Checking prerequisites...")
    for cmd in ("docker", "kind", "kubectl"):
        if not __import__("shutil").which(cmd):
            log_error(f"'{cmd}' is not installed")
            sys.exit(1)

    project_root = Path(__file__).parent.parent
    ensure_cluster()
    port, registry_container = ensure_registry()
    write_registry_port(port, project_root)
    configure_containerd_registry(port, registry_container)
    poll_registry_ready(port)
    log_info(f"✅ Setup complete. Registry at localhost:{port}. Run 'tilt up' or use 'just dev-up' to start Tilt.")


if __name__ == "__main__":
    main()
