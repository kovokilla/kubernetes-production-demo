#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
require docker gh kubectl python3 ssh-keygen
assert_cluster
docker compose -f "$ROOT/jenkins/compose.yaml" build
k apply -f "$ROOT/infrastructure/deployer-rbac.yaml"
export KUBE_DEPLOY_TOKEN="$(k -n demo create token demo-deployer --duration=24h)"
python3 "$ROOT/scripts/jenkins-provision.py"
unset KUBE_DEPLOY_TOKEN
docker compose -f "$ROOT/jenkins/compose.yaml" up -d
python3 "$ROOT/scripts/jenkins-client.py" wait
python3 "$ROOT/scripts/jenkins-client.py" bootstrap
printf 'Jenkins ready: http://127.0.0.1:8090 (user: admin). Credentials are in the protected local state directory.\n'
