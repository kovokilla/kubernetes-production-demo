#!/usr/bin/env python3
"""Package Jenkins-built local images for the kind cluster."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import yaml


def release_tag(sha, run_number):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected full Git commit SHA")
    if not re.fullmatch(r"[1-9][0-9]*", str(run_number)):
        raise ValueError("Expected positive Jenkins build number")
    return f"sha-{sha}-build-{run_number}"


def update_release(root, sha, run_number, image_ids):
    tag = release_tag(sha, run_number)
    for component in ("frontend", "backend"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_ids.get(component, "")):
            raise ValueError(f"Invalid {component} image ID")
    chart_path = root / "charts/webapp/Chart.yaml"
    defaults_path = root / "charts/webapp/values.yaml"
    chart = yaml.safe_load(chart_path.read_text())
    values = yaml.safe_load(defaults_path.read_text())
    overlay = yaml.safe_load((root / "environments/jenkins/values.yaml").read_text())
    values["ingress"].update(overlay["ingress"])
    for component in ("frontend", "backend"):
        values[component]["image"] = {
            "repository": f"demo/{component}",
            "tag": tag,
            "digest": "",
            "pullPolicy": "Never",
        }
    chart["version"] = f"0.2.{run_number}"
    chart["appVersion"] = sha
    destination = root / ".artifacts/release"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "values.yaml").write_text(yaml.safe_dump(values, sort_keys=False))
    (destination / "images.json").write_text(json.dumps({
        "commit": sha, "build": str(run_number), "tag": tag, "imageIds": image_ids,
    }, indent=2) + "\n")
    chart_path.write_text(yaml.safe_dump(chart, sort_keys=False))
    defaults_path.write_text(yaml.safe_dump(values, sort_keys=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--run-number", required=True)
    args = parser.parse_args()
    tag = release_tag(args.sha, args.run_number)
    ids = {
        component: subprocess.check_output(
            ["docker", "image", "inspect", f"demo/{component}:{tag}", "--format", "{{.Id}}"],
            text=True).strip()
        for component in ("frontend", "backend")
    }
    update_release(Path(__file__).resolve().parents[1], args.sha, args.run_number, ids)
