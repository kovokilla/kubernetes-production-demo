#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
require helm python3
source "$ROOT/infrastructure/versions.env"
mkdir -p "$ROOT/.artifacts"
render() {
  local release="$1" chart="$2" repo="$3" version="$4" namespace="$5"
  helm template "$release" "$chart" --repo "$repo" --version "$version" \
    --namespace "$namespace" --kube-version 1.34.0 \
    -f "$ROOT/infrastructure/$release-values.yaml" > "$ROOT/.artifacts/infra-$release.yaml"
  printf 'Rendered infrastructure chart: %s %s\n' "$release" "$version"
}
render traefik traefik https://traefik.github.io/charts "$TRAEFIK_CHART_VERSION" ingress
render monitoring kube-prometheus-stack https://prometheus-community.github.io/helm-charts "$MONITORING_CHART_VERSION" monitoring
render argocd argo-cd https://argoproj.github.io/argo-helm "$ARGOCD_CHART_VERSION" argocd
helm template traefik traefik --repo https://traefik.github.io/charts --version "$TRAEFIK_CHART_VERSION" \
  --namespace ingress --kube-version 1.34.0 \
  -f "$ROOT/infrastructure/traefik-values.yaml" -f "$ROOT/infrastructure/traefik-gitops-values.yaml" \
  > "$ROOT/.artifacts/infra-traefik-gitops.yaml"
helm template monitoring-blackbox prometheus-blackbox-exporter \
  --repo https://prometheus-community.github.io/helm-charts --version "$BLACKBOX_CHART_VERSION" \
  --namespace monitoring --kube-version 1.34.0 -f "$ROOT/infrastructure/blackbox-values.yaml" \
  > "$ROOT/.artifacts/infra-blackbox.yaml"
python3 - "$ROOT" <<'PY'
from pathlib import Path
import sys
import yaml
root = Path(sys.argv[1])
for path in sorted((root / ".artifacts").glob("infra-*.yaml")):
    documents = [doc for doc in yaml.safe_load_all(path.read_text()) if doc]
    assert documents, path
    if path.name == "infra-traefik-gitops.yaml":
        deployment = next(doc for doc in documents if doc["kind"] == "Deployment")
        args = deployment["spec"]["template"]["spec"]["containers"][0]["args"]
        assert "--providers.kubernetesingress.ingressendpoint.hostname=demo.localhost" in args
        assert not any("ingressendpoint.publishedservice=" in arg for arg in args)
    if path.name == "infra-argocd.yaml":
        server = next(doc for doc in documents if doc["kind"] == "Service"
                      and doc["metadata"]["name"] == "argocd-server")
        assert server["spec"]["type"] == "ClusterIP"
        params = next(doc for doc in documents if doc["kind"] == "ConfigMap"
                      and doc["metadata"]["name"] == "argocd-cmd-params-cm")
        assert str(params["data"]["server.insecure"]).lower() == "false"
        assert not any(doc["kind"] == "Ingress" for doc in documents)
        appset = next(doc for doc in documents if doc["kind"] == "Deployment"
                      and doc["metadata"]["name"] == "argocd-applicationset-controller")
        assert appset["spec"]["replicas"] == 0
    if path.name == "infra-monitoring.yaml":
        prometheus = next(doc for doc in documents if doc["kind"] == "Prometheus")
        assert prometheus["spec"]["retention"] == "24h"
        assert prometheus["spec"]["retentionSize"] == "512MB"
        assert prometheus["spec"]["resources"]["limits"]["memory"] == "768Mi"
        services = {doc["metadata"]["name"] for doc in documents if doc["kind"] == "Service"}
        assert {"monitoring-prometheus", "monitoring-alertmanager"} <= services
        assert "monitoring-grafana" in services
        grafana = next(doc for doc in documents if doc["kind"] == "Deployment"
                       and doc["metadata"]["name"] == "monitoring-grafana")
        assert grafana["spec"]["strategy"]["type"] == "Recreate"
        roles = {doc["metadata"]["name"] for doc in documents if doc["kind"] == "Role"}
        assert "monitoring-grafana" in roles
        assert not any(doc["kind"] == "ClusterRole" and "grafana" in doc["metadata"]["name"] for doc in documents)
        env = next(container["env"] for container in grafana["spec"]["template"]["spec"]["containers"]
                   if container["name"] == "grafana")
        password = next(item for item in env if item["name"] == "GF_SECURITY_ADMIN_PASSWORD")
        assert password["valueFrom"]["secretKeyRef"]["name"] == "grafana-admin"
    if path.name == "infra-traefik.yaml":
        service = next(doc for doc in documents if doc["kind"] == "Service" and doc["metadata"]["name"] == "traefik")
        assert service["spec"]["type"] == "NodePort"
        assert {port["nodePort"] for port in service["spec"]["ports"]} == {30080, 30443}
    print(f"Infrastructure YAML assertions passed: {path.name}, {len(documents)} resources")
PY
