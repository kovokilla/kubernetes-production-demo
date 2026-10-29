#!/usr/bin/env python3
"""Provision a separate Grafana admin Secret without printing its password."""
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(os.environ.get("DEMO_STATE_ROOT", Path.home() / ".local/share/kubernetes-production-demo")) / "grafana"
KUBECTL = str(ROOT / ".tools/kubectl") if (ROOT / ".tools/kubectl").exists() else "kubectl"
BASE = [KUBECTL, "--context", os.environ.get("DEMO_CONTEXT", "kind-production-demo"), "-n", "monitoring"]


def main():
    exists = subprocess.check_output(
        BASE + ["get", "secret", "grafana-admin", "--ignore-not-found", "-o", "name"],
        text=True).strip()
    if exists:
        print("Keeping existing monitoring/grafana-admin Secret")
        return
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    path = STATE / "admin_password"
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(secrets.token_urlsafe(32))
    path.chmod(0o600)
    password = path.read_text().strip()
    if not password:
        raise SystemExit("The protected Grafana admin password file is empty")
    document = {
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": "grafana-admin", "namespace": "monitoring"},
        "data": {
            "admin-user": base64.b64encode(b"admin").decode(),
            "admin-password": base64.b64encode(password.encode()).decode(),
        },
    }
    subprocess.run(BASE + ["create", "-f", "-"], input=json.dumps(document), text=True, check=True)
    print(f"Grafana admin credential stored privately at {path}; no value was printed")


if __name__ == "__main__":
    main()
