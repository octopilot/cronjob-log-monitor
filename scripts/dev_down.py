#!/usr/bin/env python3
"""
Development environment shutdown for cronjob-log-monitor.

Stops Tilt and deletes Kind cluster "kind". Does not stop registry-tls
(shared infrastructure). Usage: just dev-down
"""

import subprocess
import sys

CLUSTER_NAME = "kind"
REGISTRY_NAME = "registry-tls"


def log_info(msg):
    print(f"[INFO] {msg}")


def log_warn(msg):
    print(f"[WARN] {msg}")


def main():
    log_info("Stopping cronjob-log-monitor development environment...")

    # Stop Tilt
    log_info("Stopping Tilt...")
    r = subprocess.run(["pkill", "-f", "tilt up"], capture_output=True, text=True, check=False)
    if r.returncode == 0:
        log_info("Tilt stopped")
    else:
        log_warn("No Tilt processes found (or already stopped)")

    # Delete Kind cluster
    log_info(f"Deleting Kind cluster '{CLUSTER_NAME}'...")
    r = subprocess.run(
        ["kind", "delete", "cluster", "--name", CLUSTER_NAME],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0:
        log_info(f"Kind cluster '{CLUSTER_NAME}' deleted")
    else:
        log_warn("Cluster already deleted or does not exist")

    # Registry is shared — do not stop
    log_info(
        f"Registry '{REGISTRY_NAME}' left running (shared). "
        f"Stop manually if needed: docker stop {REGISTRY_NAME}"
    )

    log_info("Development environment stopped.")


if __name__ == "__main__":
    main()
