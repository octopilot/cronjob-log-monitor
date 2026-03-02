#!/usr/bin/env python3
"""Install Flux (gotk-components) into the cluster. Used by Tilt flux-install resource."""

import subprocess
import sys
from pathlib import Path


def main():
    project_root = Path(__file__).resolve().parent.parent.parent
    flux_dir = project_root / "k8s" / "deployment" / "flux-system"
    flux_dir.mkdir(parents=True, exist_ok=True)

    r = subprocess.run(
        ["flux", "install", "--export"],
        capture_output=True,
        text=True,
        cwd=project_root,
    )
    if r.returncode != 0:
        if r.stdout:
            print(r.stdout, file=sys.stdout)
        if r.stderr:
            print(r.stderr, file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(r.returncode)
    (flux_dir / "gotk-components.yaml").write_text(r.stdout)

    r = subprocess.run(
        ["kubectl", "apply", "-f", str(flux_dir / "gotk-components.yaml")],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        if r.stdout:
            print(r.stdout, file=sys.stdout)
        if r.stderr:
            print(r.stderr, file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(r.returncode)

    for label, _ in [
        ("app=source-controller", "source-controller"),
        ("app=helm-controller", "helm-controller"),
    ]:
        subprocess.run(
            [
                "kubectl", "wait",
                "--for=condition=ready", "pod",
                "-l", label,
                "-n", "flux-system",
                "--timeout=120s",
            ],
            cwd=project_root,
        )
    print("Flux installed.")


if __name__ == "__main__":
    main()
