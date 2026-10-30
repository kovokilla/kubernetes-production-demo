#!/usr/bin/env python3
"""Sync Docker target addresses and non-secret credential expiry metadata."""
from datetime import datetime
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
KUBECTL = str(ROOT / ".tools/kubectl") if (ROOT / ".tools/kubectl").exists() else "kubectl"
BASE = [KUBECTL, "--context", os.environ.get("DEMO_CONTEXT", "kind-production-demo")]
if not shutil.which("docker") and Path("/Applications/Docker.app/Contents/Resources/bin/docker").exists():
    os.environ["PATH"] = "/Applications/Docker.app/Contents/Resources/bin:" + os.environ.get("PATH", "")


def capture(command):
    return subprocess.check_output(command, text=True).strip()


def main():
    if not capture(BASE + ["get", "namespace", "monitoring", "--ignore-not-found", "-o", "name"]):
        print("Monitoring is not installed; target synchronization skipped")
        return
    documents = []
    for service, component, port, port_name in [
        ("jenkins-controller", "controller", 8080, "http"),
        ("jenkins-agent", "agent", 22, "ssh"),
    ]:
        compose = ["docker", "compose", "-f", os.environ.get("DEMO_COMPOSE_FILE", str(ROOT / "jenkins/compose.yaml"))]
        if os.environ.get("DEMO_COMPOSE_PROJECT"):
            compose += ["-p", os.environ["DEMO_COMPOSE_PROJECT"]]
        container = capture(compose + ["ps", "-q", component])
        if not container:
            raise SystemExit(f"Jenkins {component} is not running; start Jenkins first")
        query = '{{with index .NetworkSettings.Networks "kind"}}{{.IPAddress}}{{end}}'
        address = capture(["docker", "inspect", "--format", query, container])
        if not address:
            subprocess.run(["docker", "network", "connect", "--alias", service, "kind", container], check=True)
            address = capture(["docker", "inspect", "--format", query, container])
        ipaddress.IPv4Address(address)
        documents.append({
            "apiVersion": "discovery.k8s.io/v1", "kind": "EndpointSlice",
            "metadata": {
                "name": service, "namespace": "monitoring",
                "labels": {"kubernetes.io/service-name": service,
                           "endpointslice.kubernetes.io/managed-by": "production-demo-monitoring"},
            },
            "addressType": "IPv4",
            "ports": [{"name": port_name, "protocol": "TCP", "port": port}],
            "endpoints": [{"addresses": [address], "conditions": {"ready": True}}],
        })
    # This file contains a public timestamp only; no kubeconfig/token is read.
    path = Path.home() / ".local/share/kubernetes-production-demo/jenkins/credential-expiry"
    expires = path.read_text().strip() if path.exists() and os.environ.get("DEMO_GITOPS") != "1" else ""
    if expires:
        datetime.fromisoformat(expires.replace("Z", "+00:00"))
    documents.append({
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "jenkins-monitoring-metadata", "namespace": "monitoring"},
        "data": {"expires_at": expires},
    })
    subprocess.run(BASE + ["apply", "-f", "-"], check=True,
                   input=json.dumps({"apiVersion": "v1", "kind": "List", "items": documents}), text=True)
    print("Synchronized Jenkins targets and public credential-expiry metadata")


if __name__ == "__main__":
    main()
