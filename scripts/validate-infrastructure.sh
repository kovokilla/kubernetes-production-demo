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
python3 - "$ROOT" <<'PY'
from pathlib import Path
import sys
import yaml
root = Path(sys.argv[1])
for path in sorted((root / ".artifacts").glob("infra-*.yaml")):
    documents = [doc for doc in yaml.safe_load_all(path.read_text()) if doc]
    assert documents, path
    if path.name == "infra-traefik.yaml":
        service = next(doc for doc in documents if doc["kind"] == "Service" and doc["metadata"]["name"] == "traefik")
        assert service["spec"]["type"] == "NodePort"
        assert {port["nodePort"] for port in service["spec"]["ports"]} == {30080, 30443}
    print(f"Infrastructure YAML assertions passed: {path.name}, {len(documents)} resources")
PY
