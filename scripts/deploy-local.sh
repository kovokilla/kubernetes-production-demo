#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
require docker kind kubectl helm python3
assert_cluster
for component in backend frontend; do
  docker build -t "demo/$component:local" "$ROOT/apps/$component"
  kind load docker-image "demo/$component:local" --name production-demo
done
python3 "$ROOT/scripts/create-secrets.py"
h upgrade --install demo "$ROOT/charts/webapp" --namespace demo \
  -f "$ROOT/environments/local/values.yaml" --wait --timeout 5m
# Local tags are reused, so explicitly restart even if chart content is unchanged.
k -n demo rollout restart deployment/demo-frontend deployment/demo-backend
k -n demo rollout status deployment/demo-frontend --timeout=300s
k -n demo rollout status deployment/demo-backend --timeout=300s
printf 'Open http://demo.localhost:8080 (or use Host: demo.localhost with 127.0.0.1:8080).\n'
