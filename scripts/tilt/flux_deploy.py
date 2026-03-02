#!/usr/bin/env python3
"""Deploy using build_result.json (OCIRepository + HelmRelease). Used by Tilt flux-deploy resource."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def get_registry_port(project_root: Path) -> str:
    """Registry port from .registry-port or REGISTRY_PORT env."""
    port_file = project_root / ".registry-port"
    if port_file.exists():
        try:
            return port_file.read_text().strip()
        except OSError:
            pass
    return os.environ.get("REGISTRY_PORT", "5001")


def get_chart_tags_from_registry(project_root: Path, repo: str) -> list[str]:
    """Return list of tags for repo at localhost registry, or [] on error."""
    port = get_registry_port(project_root)
    url = f"http://localhost:{port}/v2/{repo}/tags/list"
    try:
        with urlopen(Request(url, method="GET"), timeout=10) as r:
            data = json.loads(r.read().decode())
        return data.get("tags") or []
    except (HTTPError, URLError, json.JSONDecodeError):
        return []


def main():
    project_root = Path(__file__).resolve().parent.parent.parent
    build_result_path = project_root / "build_result.json"
    if not build_result_path.exists():
        msg = "Run op-build first."
        print(msg, file=sys.stderr)
        print(msg, file=sys.stdout)
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(1)

    data = json.loads(build_result_path.read_text())
    builds = {b["imageName"]: b["tag"] for b in data.get("builds", [])}

    chart_image = "ghcr.io/octopilot/cronjob-log-monitor-chart"
    monitor_image = "ghcr.io/octopilot/cronjob-log-monitor"
    image_chart = builds.get(chart_image)
    image_monitor = builds.get(monitor_image)

    if not image_chart or not image_monitor:
        print("Missing chart or monitor image in build_result.json", file=sys.stderr)
        print("Missing chart or monitor image in build_result.json", file=sys.stdout)
        for name, tag in builds.items():
            print(f"  {name}: {tag}", file=sys.stderr)
            print(f"  {name}: {tag}", file=sys.stdout)
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(1)

    # Map host refs to cluster refs (localhost:PORT -> registry-tls:5000)
    chart_digest = image_chart.split("@")[-1] if "@" in image_chart else ""
    # Chart tag (version) for ref.tag when digest is missing; helm push uses version as tag
    if ":" in image_chart:
        chart_tag = image_chart.split(":")[-1].split("@")[0]
    else:
        chart_tag = "0.1.0"
    chart_yaml = project_root / "chart" / "Chart.yaml"
    if chart_yaml.exists():
        for line in chart_yaml.read_text().splitlines():
            line = line.strip()
            if line.startswith("version:"):
                chart_tag = line.split(":", 1)[1].strip().strip("'\"")
                break
    # When using tag, prefer chart version (0.1.0); if registry only has "latest", use that
    if not (chart_digest and chart_digest.startswith("sha256:")):
        chart_repo = "ghcr.io/octopilot/cronjob-log-monitor-chart"
        available = get_chart_tags_from_registry(project_root, chart_repo)
        if available and chart_tag not in available and "latest" in available:
            chart_tag = "latest"
            print(f"Chart tag 0.1.0 not in registry; using tag 'latest' (available: {available})", file=sys.stderr)
    # REF_BLOCK: use digest when present (from buildpack helm push), else tag
    if chart_digest and chart_digest.startswith("sha256:"):
        ref_block = f'digest: "{chart_digest}"'
    else:
        ref_block = f'tag: "{chart_tag}"'
    # e.g. localhost:5001/ghcr.io/.../cronjob-log-monitor:latest@sha256:... -> ghcr.io/.../cronjob-log-monitor@sha256:...
    monitor_path = re.sub(r"^[^/]+/", "", image_monitor)
    monitor_path = re.sub(r":[^@]+@", "@", monitor_path)
    chart_oci_url = "registry-tls:5000/ghcr.io/octopilot/cronjob-log-monitor-chart"
    monitor_image_ref = f"registry-tls:5000/{monitor_path}"

    env = os.environ.copy()
    env["CHART_OCI_URL"] = chart_oci_url
    env["CHART_REF_BLOCK"] = ref_block
    env["MONITOR_IMAGE"] = monitor_image_ref

    def envsubst(path: Path, vars_: list[str]) -> str:
        text = path.read_text()
        for key in vars_:
            value = env.get(key, "")
            text = text.replace("${" + key + "}", value)
        return text

    k8s = project_root / "k8s" / "deployment"
    r = subprocess.run(
        ["kubectl", "apply", "-f", str(k8s / "namespace.yaml")],
        cwd=project_root,
    )
    if r.returncode != 0:
        sys.exit(r.returncode)
    oci_yaml = envsubst(k8s / "ocirepository.yaml", ["CHART_OCI_URL", "CHART_REF_BLOCK"])
    r = subprocess.run(["kubectl", "apply", "-f", "-"], input=oci_yaml, text=True, cwd=project_root)
    if r.returncode != 0:
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(r.returncode)
    hr_yaml = envsubst(k8s / "helmrelease.yaml", ["MONITOR_IMAGE"])
    r = subprocess.run(["kubectl", "apply", "-f", "-"], input=hr_yaml, text=True, cwd=project_root)
    if r.returncode != 0:
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(r.returncode)

    r = subprocess.run(
        ["flux", "reconcile", "source", "oci", "cronjob-log-monitor-chart", "-n", "cronjob-log-monitor", "--timeout=2m"],
        cwd=project_root,
    )
    if r.returncode != 0:
        print("flux reconcile source oci failed", file=sys.stderr)
        sys.exit(r.returncode)
    r = subprocess.run(
        ["flux", "reconcile", "helmrelease", "cronjob-log-monitor", "-n", "cronjob-log-monitor", "--timeout=3m"],
        cwd=project_root,
    )
    if r.returncode != 0:
        print("flux reconcile helmrelease failed", file=sys.stderr)
        sys.exit(r.returncode)

    subprocess.run(["kubectl", "get", "all", "-n", "cronjob-log-monitor"], cwd=project_root)
    subprocess.run(["flux", "get", "helmrelease", "-n", "cronjob-log-monitor"], cwd=project_root)

    # Only report success if the HelmRelease is actually ready
    check = subprocess.run(
        ["kubectl", "get", "helmrelease", "cronjob-log-monitor", "-n", "cronjob-log-monitor", "-o", "jsonpath={.status.conditions[?(@.type==\"Ready\")].status}"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0 or check.stdout.strip() != "True":
        msg = "HelmRelease cronjob-log-monitor is not Ready (e.g. Chart.yaml file is missing). Check: flux get helmrelease -n cronjob-log-monitor"
        print(msg, file=sys.stderr)
        sys.exit(1)
    print("Deploy OK.")


if __name__ == "__main__":
    main()
