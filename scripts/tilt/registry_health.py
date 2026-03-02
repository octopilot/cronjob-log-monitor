#!/usr/bin/env python3
"""Check that the registry is reachable. Used by Tilt registry-health resource."""

import os
import subprocess
import sys
from pathlib import Path


def get_registry_port():
    """Registry port from .registry-port or REGISTRY_PORT env, default 5001."""
    project_root = Path(__file__).resolve().parent.parent.parent
    port_file = project_root / ".registry-port"
    if port_file.exists():
        try:
            return port_file.read_text().strip()
        except OSError:
            pass
    return os.environ.get("REGISTRY_PORT", "5001")


def main():
    port = get_registry_port()
    # Registry may be exposed as http (e.g. Docker port mapping)
    for scheme in ("http", "https"):
        url = f"{scheme}://localhost:{port}/v2/"
        r = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url],
            capture_output=True,
            text=True,
            timeout=5,
        )
        code = (r.stdout or "").strip()
        if r.returncode == 0 and code in ("200", "401"):
            print(f"Registry OK at {url} (nodes use registry-tls:5000)")
            return
    msg = f"ERROR: Registry not ready at localhost:{port}/v2/. Start dev env with: just dev-up"
    print(msg, file=sys.stderr)
    print(msg, file=sys.stdout)
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(1)


if __name__ == "__main__":
    main()
