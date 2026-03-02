#!/usr/bin/env python3
"""Run op build and push to local registry. Used by Tilt op-build resource."""

import json
import os
import subprocess
import sys
from pathlib import Path


def get_registry_host():
    """Registry host (localhost:PORT) from .registry-port or REGISTRY_PORT env."""
    project_root = Path(__file__).resolve().parent.parent.parent
    port_file = project_root / ".registry-port"
    port = "5001"
    if port_file.exists():
        try:
            port = port_file.read_text().strip()
        except OSError:
            pass
    else:
        port = os.environ.get("REGISTRY_PORT", "5001")
    return f"localhost:{port}"


def find_op_binary():
    """Path to op binary: OP_BINARY env or 'op' in PATH."""
    op = os.environ.get("OP_BINARY", "op")
    if os.path.isabs(op) and os.path.isfile(op):
        return op
    import shutil
    path = shutil.which(op)
    if path:
        return path
    return None


def main():
    registry_host = get_registry_host()
    env = os.environ.copy()
    env["SKAFFOLD_DEFAULT_REPO"] = registry_host
    env["SKAFFOLD_INSECURE_REGISTRY"] = registry_host
    env["BUILDX_NO_DEFAULT_ATTESTATIONS"] = "1"
    # So pack lifecycle can reach the registry: no host.docker.internal rewrite in op.
    # Avoids pack containerd workaround building invalid ref (localhost:5001/host.docker.internal:5001/...).
    env["OP_PACK_NETWORK"] = "host"

    op_bin = find_op_binary()
    if not op_bin:
        msg = "Set OP_BINARY to path to op (e.g. from octopilot-pipeline-tools: just build && export OP_BINARY=$PWD/op)"
        print(msg, file=sys.stderr)
        print(msg, file=sys.stdout)
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(1)

    project_root = Path(__file__).resolve().parent.parent.parent
    cmd = [
        op_bin,
        "build",
        "--repo", registry_host,
        "--push",
        "--insecure-registry", registry_host,
        "--platform", "linux/amd64",
    ]
    r = subprocess.run(cmd, cwd=project_root, env=env)
    if r.returncode != 0:
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(r.returncode)

    build_result = project_root / "build_result.json"
    if build_result.exists():
        try:
            data = json.loads(build_result.read_text())
            print("build_result.json:")
            for b in data.get("builds", []):
                print(f"  {b.get('imageName', '')}: {b.get('tag', '')}")
        except (OSError, json.JSONDecodeError):
            pass


if __name__ == "__main__":
    main()
