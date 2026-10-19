#!/usr/bin/env python3
"""Update chart defaults, release values and version after both images are published."""
import argparse
from pathlib import Path
import re
import yaml


def update_release(root, repository, sha, run_number, digest_dir):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repository):
        raise ValueError("Expected owner/repository")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected full Git commit SHA")
    if not re.fullmatch(r"[1-9][0-9]*", str(run_number)):
        raise ValueError("Expected positive workflow run number")
    digests = {}
    for component in ("frontend", "backend"):
        digest = (digest_dir / f"{component}.digest").read_text().strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid {component} image digest")
        digests[component] = digest
    values_path = root / "environments/production/values.yaml"
    chart_path = root / "charts/webapp/Chart.yaml"
    defaults_path = root / "charts/webapp/values.yaml"
    values = yaml.safe_load(values_path.read_text())
    chart = yaml.safe_load(chart_path.read_text())
    defaults = yaml.safe_load(defaults_path.read_text())
    for component, digest in digests.items():
        values[component]["image"] = {
            "repository": f"ghcr.io/{repository.lower()}/{component}",
            "tag": f"sha-{sha}",
            "digest": digest,
        }
        defaults[component]["image"].update(values[component]["image"])
    chart["version"] = f"0.1.{run_number}"
    chart["appVersion"] = sha
    values_path.write_text(yaml.safe_dump(values, sort_keys=False))
    chart_path.write_text(yaml.safe_dump(chart, sort_keys=False))
    defaults_path.write_text(yaml.safe_dump(defaults, sort_keys=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--run-number", required=True)
    parser.add_argument("--digest-dir", required=True, type=Path)
    args = parser.parse_args()
    update_release(Path(__file__).resolve().parents[1], args.repository, args.sha,
                   args.run_number, args.digest_dir)
