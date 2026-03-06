#!/usr/bin/env python3
"""
Development environment startup for cronjob-log-monitor.

Starts Kind cluster "kind", registry-tls in Docker, then Tilt.
Usage: just dev-up
"""

import os
import subprocess
import sys
from pathlib import Path

# Add scripts directory for imports
sys.path.insert(0, str(Path(__file__).parent))


def log_info(msg):
    print(f"[INFO] {msg}")


def log_error(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)


def check_command(cmd):
    import shutil
    if not shutil.which(cmd):
        log_error(f"{cmd} is not installed. Please install it first.")
        sys.exit(1)


def main():
    log_info("Starting cronjob-log-monitor development environment (Kind + registry-tls + Tilt)...")

    check_command("docker")
    check_command("kind")
    check_command("kubectl")
    check_command("tilt")

    # Kind cluster "kind" + registry-tls (reuses existing if running)
    setup_script = Path(__file__).parent / "setup_kind.py"
    result = subprocess.run([sys.executable, str(setup_script)], env=os.environ.copy(), capture_output=False)
    if result.returncode != 0:
        log_error("Failed to setup Kind cluster and registry")
        sys.exit(1)

    # Use context kind-kind
    subprocess.run(
        ["kubectl", "config", "use-context", "kind-kind"],
        capture_output=True,
        check=False,
    )

    # Pass registry port to Tilt (from setup_kind.py .registry-port)
    project_root = Path(__file__).parent.parent
    port_file = project_root / ".registry-port"
    env = os.environ.copy()
    if port_file.exists():
        try:
            env["REGISTRY_PORT"] = port_file.read_text().strip()
        except OSError:
            env["REGISTRY_PORT"] = "5001"
    else:
        env["REGISTRY_PORT"] = "5001"

    log_info("Starting Tilt...")
    subprocess.run(["tilt", "up", "--host", "0.0.0.0"], env=env, check=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        log_info("Tilt stopped. Kind cluster and registry still running (use 'just dev-down' to tear down cluster).")
        sys.exit(0)
