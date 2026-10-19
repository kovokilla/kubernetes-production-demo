#!/usr/bin/env python3
"""Regression assertions for rendered application manifests (not a schema validator)."""
from pathlib import Path
import sys
import yaml

documents = [doc for doc in yaml.safe_load_all(Path(sys.argv[1]).read_text()) if doc]
resources = {(doc["kind"], doc["metadata"]["name"]): doc for doc in documents}
assert len(resources) == len(documents), "Duplicate resources"
assert not any(doc["kind"] == "Secret" for doc in documents), "Do not render Secret material"
assert len([doc for doc in documents if doc["kind"] == "Deployment"]) == 2
for component in ("frontend", "backend"):
    deployment = resources[("Deployment", f"demo-{component}")]
    pod = deployment["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert pod["serviceAccountName"] == f"demo-{component}"
    container = pod["containers"][0]
    security = container["securityContext"]
    assert security["readOnlyRootFilesystem"] is True
    assert security["allowPrivilegeEscalation"] is False
    assert security["capabilities"]["drop"] == ["ALL"]
    assert all(name in container for name in ("startupProbe", "livenessProbe", "readinessProbe", "resources"))
    assert container["resources"]["requests"]["cpu"]
    assert container["resources"]["limits"]["memory"]
    assert resources[("Service", f"demo-{component}")]["spec"]["type"] == "ClusterIP"
    assert deployment["spec"]["replicas"] == 2
deny = resources[("NetworkPolicy", "demo-default-deny")]["spec"]
assert deny["podSelector"] == {}
assert set(deny["policyTypes"]) == {"Ingress", "Egress"}
assert not deny.get("ingress") and not deny.get("egress")
backend = resources[("NetworkPolicy", "demo-backend")]["spec"]
assert len(backend["ingress"]) == 1
api = backend["ingress"][0]
assert api["ports"] == [{"protocol": "TCP", "port": 8080}]
assert api["from"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == "frontend"
frontend = resources[("NetworkPolicy", "demo-frontend")]["spec"]
assert "namespaceSelector" in frontend["ingress"][0]["from"][0]
assert "podSelector" in frontend["ingress"][0]["from"][0]
for rule in resources[("Role", "demo-reader")]["rules"]:
    assert set(rule["verbs"]) <= {"get", "list", "watch"}
    assert "secrets" not in rule["resources"] and "*" not in rule["resources"]
ingress = resources[("Ingress", "demo")]["spec"]
assert ingress["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"] == "demo-frontend"
secret_env = resources[("Deployment", "demo-backend")]["spec"]["template"]["spec"]["containers"][0]["env"][1]
assert secret_env["valueFrom"]["secretKeyRef"] == {"name": "demo-app", "key": "signing-key"}
assert len(documents) == 15, "Unexpected resources outside the reduced scope"
print(f"Security and architecture assertions passed: {len(documents)} resources in {sys.argv[1]}")
