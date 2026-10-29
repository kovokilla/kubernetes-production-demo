#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
require node python3 helm
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$ROOT/.artifacts"
node --test "$ROOT/apps/backend/server.test.js" "$ROOT/apps/frontend/server.test.js" "$ROOT/tests/receiver.test.mjs"
for file in "$ROOT"/apps/backend/*.js "$ROOT"/apps/frontend/*.js "$ROOT"/apps/frontend/public/*.js; do
  node --check "$file"
done
for file in "$ROOT"/scripts/*.sh; do bash -n "$file"; done
bash -n "$ROOT/jenkins/agent/entrypoint.sh"
node --check "$ROOT/charts/monitoring-resources/files/receiver.mjs"
python3 - "$ROOT" <<'PY'
from pathlib import Path
import ast
import json
import sys
import yaml
root = Path(sys.argv[1])
for path in root.rglob("*"):
    if not path.is_file() or any(part in {".tools", ".artifacts", ".git", ".venv", ".terraform", "__pycache__"} for part in path.relative_to(root).parts[:-1]):
        continue
    if path.suffix == ".py":
        ast.parse(path.read_text(), filename=str(path))
    elif path.suffix == ".json":
        json.loads(path.read_text())
    elif path.suffix in (".yaml", ".yml") and "templates" not in path.parts:
        list(yaml.safe_load_all(path.read_text()))
print("Source, JSON and static YAML syntax checks passed")
PY
python3 -m unittest discover -s "$ROOT/tests" -p 'test_*.py'
for environment in local production jenkins; do
  helm lint "$ROOT/charts/webapp" --strict -f "$ROOT/environments/$environment/values.yaml"
  helm template demo "$ROOT/charts/webapp" --namespace demo --kube-version 1.34.0 \
    -f "$ROOT/environments/$environment/values.yaml" > "$ROOT/.artifacts/$environment.yaml"
  python3 "$ROOT/scripts/check-manifests.py" "$ROOT/.artifacts/$environment.yaml"
done
helm package "$ROOT/charts/webapp" --destination "$ROOT/.artifacts"
helm lint "$ROOT/charts/monitoring-resources" --strict
helm template monitoring-resources "$ROOT/charts/monitoring-resources" --namespace monitoring \
  --kube-version 1.34.0 > "$ROOT/.artifacts/monitoring-resources.yaml"
helm lint "$ROOT/charts/monitoring-resources" --strict --set gitops.enabled=true
helm template monitoring-resources "$ROOT/charts/monitoring-resources" --namespace monitoring \
  --kube-version 1.34.0 --set gitops.enabled=true > "$ROOT/.artifacts/monitoring-gitops.yaml"
printf 'Local validation passed. Live cluster checks: bash scripts/smoke-test.sh\n'
