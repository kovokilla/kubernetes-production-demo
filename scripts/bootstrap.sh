#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
assert_legacy_mode
require docker kind kubectl helm python3
docker info >/dev/null
source "$ROOT/infrastructure/versions.env"

if ! kind get clusters | /usr/bin/grep -qx production-demo; then
  kind create cluster --name production-demo \
    --image "$KIND_NODE_IMAGE" --config "$ROOT/infrastructure/kind.yaml"
fi
# Calico supplies both CNI networking and actual NetworkPolicy enforcement.
k apply --server-side -f "https://raw.githubusercontent.com/projectcalico/calico/${CALICO_VERSION}/manifests/calico.yaml"
k -n kube-system rollout status daemonset/calico-node --timeout=300s
k wait --for=condition=Ready nodes --all --timeout=300s
k apply -f "$ROOT/infrastructure/namespaces.yaml"
python3 "$ROOT/scripts/create-secrets.py"

helm repo add traefik https://traefik.github.io/charts --force-update
helm repo update
h upgrade --install traefik traefik/traefik --namespace ingress \
  --version "$TRAEFIK_CHART_VERSION" -f "$ROOT/infrastructure/traefik-values.yaml" --wait --timeout 5m
printf 'Infrastructure ready. Next: bash scripts/deploy-local.sh\n'
