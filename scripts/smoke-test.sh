#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
require kubectl curl python3
assert_cluster
k -n demo rollout status deployment/demo-frontend --timeout=300s
k -n demo rollout status deployment/demo-backend --timeout=300s
curl --fail --silent --show-error --retry 5 --retry-connrefused \
  -H 'Host: demo.localhost' http://127.0.0.1:8080/api/info |
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["message"] and d["instance"] and len(d["signature"]) == 64; print("Ingress → frontend → backend: passed")'

test "$(k -n demo auth can-i get pods --as=system:serviceaccount:demo:demo-observer)" = yes
test "$(k -n demo auth can-i get secrets --as=system:serviceaccount:demo:demo-observer || true)" = no
test "$(k -n demo auth can-i delete deployments --as=system:serviceaccount:demo:demo-observer || true)" = no
test "$(k -n demo auth can-i get pods --as=system:serviceaccount:demo:demo-backend || true)" = no
printf 'RBAC positive and negative checks: passed\n'

# Use actual Ready application pods as positive controls before testing denial.
k -n demo exec deployment/demo-frontend -- node -e '
  fetch("http://demo-backend:8080/api/info", {signal: AbortSignal.timeout(5000)})
    .then(async response => { if (!response.ok || !(await response.json()).instance) process.exit(1); })
    .catch(error => { console.error(error.message); process.exit(1); });'
BACKEND_IP="$(k -n demo get service demo-backend -o jsonpath='{.spec.clusterIP}')"
FRONTEND_IP="$(k -n demo get service demo-frontend -o jsonpath='{.spec.clusterIP}')"

# Accept only an actual timeout as evidence of a dropped connection.
# A refused connection, a missing executable or DNS failure must fail the test.
TCP_CHECK='
  const net = require("node:net");
  const [host, port, expectation] = process.argv.slice(1);
  const socket = net.createConnection({host, port: Number(port)});
  socket.setTimeout(5000);
  socket.on("connect", () => {
    socket.destroy();
    process.exit(expectation === "allow" ? 0 : 1);
  });
  socket.on("timeout", () => {
    socket.destroy();
    process.exit(expectation === "deny" ? 0 : 1);
  });
  socket.on("error", error => { console.error(error.message); process.exit(1); });
'
k -n demo exec deployment/demo-frontend -- node -e "$TCP_CHECK" "$BACKEND_IP" 8080 allow
k -n demo exec deployment/demo-backend -- node -e "$TCP_CHECK" "$FRONTEND_IP" 8080 deny

# Do not reuse/delete a namespace that might belong to someone else.
TEST_NAMESPACE="demo-netcheck-$(date +%s)-$$"
k create namespace "$TEST_NAMESPACE"
cleanup() { k delete namespace "$TEST_NAMESPACE" --wait=false >/dev/null; }
trap cleanup EXIT
k -n "$TEST_NAMESPACE" apply -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata:
  name: network-check
spec:
  automountServiceAccountToken: false
  restartPolicy: Never
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 10001
    seccompProfile: { type: RuntimeDefault }
  containers:
    - name: node
      image: node:24.14.0-alpine3.23
      command: ["node", "-e", "setInterval(() => {}, 1000)"]
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities: { drop: ["ALL"] }
      resources:
        requests: { cpu: 10m, memory: 32Mi }
        limits: { cpu: 100m, memory: 64Mi }
YAML
k -n "$TEST_NAMESPACE" wait --for=condition=Ready pod/network-check --timeout=180s
k -n "$TEST_NAMESPACE" exec network-check -- node -e \
  'require("node:dns").lookup("kubernetes.default.svc.cluster.local", error => process.exit(error ? 1 : 0))'
k -n "$TEST_NAMESPACE" exec network-check -- node -e "$TCP_CHECK" "$BACKEND_IP" 8080 deny
k -n "$TEST_NAMESPACE" exec network-check -- node -e "$TCP_CHECK" "$FRONTEND_IP" 8080 deny
printf 'Allowed and denied pod/namespace traffic checks: passed\n'
printf 'Architecture, RBAC and NetworkPolicy smoke checks passed.\n'
