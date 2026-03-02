#!/usr/bin/env python3
"""Probe the local registry (Registry V2 API): list repositories and tags. Use from host (localhost:PORT)."""

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def get_registry_base():
    """Registry base URL from .registry-port or REGISTRY_PORT env."""
    project_root = Path(__file__).resolve().parent.parent.parent
    port = "5001"
    port_file = project_root / ".registry-port"
    if port_file.exists():
        try:
            port = port_file.read_text().strip()
        except OSError:
            pass
    else:
        port = os.environ.get("REGISTRY_PORT", "5001")
    return f"http://localhost:{port}"


def main():
    base = get_registry_base()
    print(f"Probing registry at {base}\n")

    # Check /v2/
    try:
        req = Request(f"{base}/v2/", method="GET")
        with urlopen(req, timeout=5) as r:
            pass
    except HTTPError as e:
        print(f"GET /v2/ failed: {e.code} {e.reason}")
        sys.exit(1)
    except URLError as e:
        print(f"Registry unreachable: {e.reason}")
        sys.exit(1)

    # Catalog (repositories)
    try:
        req = Request(f"{base}/v2/_catalog?n=500", method="GET")
        with urlopen(req, timeout=10) as r:
            catalog = json.loads(r.read().decode())
    except HTTPError as e:
        body = e.fp.read().decode() if e.fp else ""
        print(f"GET /v2/_catalog failed: {e.code} {e.reason}\n{body}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Invalid catalog JSON: {e}")
        sys.exit(1)

    repos = catalog.get("repositories") or []
    if not repos:
        print("No repositories in registry.")
        return

    print(f"Repositories ({len(repos)}):")
    for name in sorted(repos):
        # Tags list
        try:
            req = Request(f"{base}/v2/{name}/tags/list", method="GET")
            with urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
            tags = data.get("tags") or []
            print(f"  {name}")
            if tags:
                for t in sorted(tags):
                    print(f"    tag: {t}")
            else:
                print("    (no tags)")
        except HTTPError as e:
            print(f"  {name}: tags/list failed {e.code} {e.reason}")
        except json.JSONDecodeError:
            print(f"  {name}: invalid tags/list response")


if __name__ == "__main__":
    main()
