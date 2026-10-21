#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
require docker kubectl helm python3
assert_cluster
source "$ROOT/infrastructure/versions.env"
k create namespace monitoring --dry-run=client -o yaml | k apply -f -
python3 "$ROOT/scripts/grafana-credentials.py"
python3 "$ROOT/scripts/monitoring-sync-targets.py"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo update prometheus-community
h upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring --version "$MONITORING_CHART_VERSION" \
  -f "$ROOT/infrastructure/monitoring-values.yaml" --wait --timeout 10m
h upgrade --install monitoring-blackbox prometheus-community/prometheus-blackbox-exporter \
  --namespace monitoring --version "$BLACKBOX_CHART_VERSION" \
  -f "$ROOT/infrastructure/blackbox-values.yaml" --wait --timeout 5m
h upgrade --install monitoring-resources "$ROOT/charts/monitoring-resources" \
  --namespace monitoring --wait --timeout 5m
printf 'Monitoring installed. Verify with scripts/monitoring-verify.py and scripts/grafana-verify.py (python3).\n'
