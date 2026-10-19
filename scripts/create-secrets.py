#!/usr/bin/env python3
"""Generate demo credentials directly into Kubernetes without printing values."""
import base64
import json
import secrets
import subprocess
import sys

context = "kind-production-demo"


def ensure_secret(namespace, name, values):
    result = subprocess.run(
        ["kubectl", "--context", context, "-n", namespace, "get", "secret", name,
         "--ignore-not-found", "-o", "name"],
        check=True, capture_output=True, text=True,
    )
    if result.stdout.strip():
        print(f"Keeping existing Secret {namespace}/{name}")
        return
    document = {
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": name, "namespace": namespace},
        "data": {key: base64.b64encode(value.encode()).decode() for key, value in values.items()},
    }
    subprocess.run(
        ["kubectl", "--context", context, "create", "-f", "-"],
        input=json.dumps(document), text=True, check=True,
    )


if __name__ == "__main__":
    # Values stay in process memory and kubectl stdin, never command arguments.
    try:
        ensure_secret("demo", "demo-app", {"signing-key": secrets.token_hex(32)})
    except subprocess.CalledProcessError:
        sys.exit("Secret provisioning failed; check cluster access and namespace setup.")
