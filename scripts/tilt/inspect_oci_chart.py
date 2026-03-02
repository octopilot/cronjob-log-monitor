#!/usr/bin/env python3
"""Pull the OCI chart from the registry and extract/inspect contents.

Usage:
  scripts/tilt/inspect_oci_chart.py [--out DIR]

Reads chart ref from build_result.json; uses localhost:REGISTRY_PORT for pull.
Extracts to a directory so you can see whether the artifact is a Helm chart
(Chart.yaml, templates/) or a container image (different layout).
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def get_registry_port(project_root: Path) -> str:
    port_file = project_root / ".registry-port"
    if port_file.exists():
        try:
            return port_file.read_text().strip()
        except OSError:
            pass
    return os.environ.get("REGISTRY_PORT", "5001")


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull OCI chart and inspect contents")
    parser.add_argument("--out", type=Path, default=None, help="Output directory (default: ./chart-inspect)")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent.parent
    build_result = project_root / "build_result.json"
    if not build_result.exists():
        print("Run op build first (build_result.json missing).", file=sys.stderr)
        return 1

    data = json.loads(build_result.read_text())
    builds = {b["imageName"]: b["tag"] for b in data.get("builds", [])}
    chart_image = "ghcr.io/octopilot/cronjob-log-monitor-chart"
    image_chart = builds.get(chart_image)
    if not image_chart:
        print(f"Chart ref not found in build_result.json (looked for {chart_image}).", file=sys.stderr)
        return 1

    port = get_registry_port(project_root)
    # Ref is like localhost:5001/ghcr.io/octopilot/cronjob-log-monitor-chart:0.1.0 or ...@sha256:...
    chart_ref = image_chart
    if chart_ref.startswith("localhost:") or chart_ref.startswith("127.0.0.1:"):
        chart_ref = f"localhost:{port}" + chart_ref.split(":", 1)[1]
    oci_url = f"oci://localhost:{port}/ghcr.io/octopilot/cronjob-log-monitor-chart"
    if ":" in image_chart and "@" not in image_chart:
        version = image_chart.split(":")[-1].strip()
    else:
        version = "0.1.0"

    out_dir = args.out or project_root / "chart-inspect"
    out_dir = out_dir.resolve()
    extract_dir = out_dir / "extract"
    extract_dir.mkdir(parents=True, exist_ok=True)
    print(f"Registry port: {port}")
    print(f"Chart ref from build_result: {image_chart}")
    print(f"Output dir: {out_dir}")

    # 1) Helm pull (treat as Helm chart) — will fail if artifact is not a Helm chart
    helm_dir = out_dir / "helm-pull"
    helm_dir.mkdir(parents=True, exist_ok=True)
    print("\n--- helm pull (oci chart) ---")
    env = os.environ.copy()
    env["HELM_EXPERIMENTAL_OCI"] = "1"
    r = subprocess.run(
        ["helm", "pull", f"{oci_url}", "--version", version, "--untar", "--untardir", str(helm_dir)],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        print("Helm pull succeeded. Contents:")
        for p in sorted(helm_dir.rglob("*")):
            rel = p.relative_to(helm_dir)
            print(f"  {rel}")
        chart_yaml = next(helm_dir.rglob("Chart.yaml"), None)
        if chart_yaml:
            print(f"\nChart.yaml found at {chart_yaml.relative_to(helm_dir)}")
    else:
        print(f"Helm pull failed (artifact may be a container image, not a Helm chart):")
        print(r.stderr or r.stdout)

    # 2) Crane: show manifest (layer media types) and extract artifact to see contents
    print("\n--- crane manifest (layer media types) ---")
    r_crane = subprocess.run(["which", "crane"], capture_output=True, text=True)
    if r_crane.returncode != 0:
        print("Install crane (go install github.com/google/go-containerregistry/cmd/crane@latest) to inspect raw OCI layers.")
    else:
        pull_ref = f"localhost:{port}/ghcr.io/octopilot/cronjob-log-monitor-chart:{version}"
        r = subprocess.run(
            ["crane", "manifest", "--insecure", pull_ref],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            print(f"crane manifest failed: {r.stderr or r.stdout}")
        else:
            print(r.stdout)
        # Pull full artifact to tar and extract (OCI layout: manifest.json + blobs)
        tar_path = out_dir / "artifact.tar"
        r2 = subprocess.run(
            ["crane", "pull", "--insecure", pull_ref, str(tar_path)],
            capture_output=True,
            text=True,
        )
        if r2.returncode == 0:
            subprocess.run(["tar", "-xf", str(tar_path), "-C", str(extract_dir)], check=False)
            print("\nExtracted OCI artifact layout:")
            for p in sorted(extract_dir.rglob("*"))[:60]:
                print(f"  {p.relative_to(extract_dir)}")
            manifest_file = extract_dir / "manifest.json"
            if manifest_file.exists():
                print("\nmanifest.json (media types show if Helm chart or container image):")
                print(manifest_file.read_text()[:2000])

    print(f"\nInspect output under: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
