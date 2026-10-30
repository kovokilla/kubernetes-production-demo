#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
assert_helm_delivery
require docker kubectl python3
assert_cluster
export KUBE_DEPLOY_TOKEN="$(k -n demo create token demo-deployer --duration=24h)"
python3 "$ROOT/scripts/jenkins-provision.py" --refresh-only
unset KUBE_DEPLOY_TOKEN
python3 "$ROOT/scripts/jenkins-client.py" reload
python3 "$ROOT/scripts/monitoring-sync-targets.py"
