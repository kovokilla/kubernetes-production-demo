#!/usr/bin/env bash
# Source this file; all cluster-changing scripts are confined to this context.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTEXT=kind-production-demo
export PATH="$ROOT/.tools:$PATH"
if ! command -v docker >/dev/null && test -x /Applications/Docker.app/Contents/Resources/bin/docker; then
  export PATH="$ROOT/.tools:/Applications/Docker.app/Contents/Resources/bin:$PATH"
elif ! command -v docker >/dev/null && test -x "$HOME/.docker/bin/docker"; then
  export PATH="$ROOT/.tools:$HOME/.docker/bin:$PATH"
fi

require() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null || {
      printf 'Missing prerequisite: %s\n' "$tool" >&2
      return 1
    }
  done
}

assert_helm_delivery() {
  if test -f "$HOME/.local/share/kubernetes-production-demo/argocd/ownership.json"; then
    printf '%s\n' 'Argo CD owns deployment; use scripts/argocd-setup.py instead of legacy provisioning.' >&2
    return 1
  fi
}

k() {
  kubectl --context "$CONTEXT" "$@"
}

h() {
  helm --kube-context "$CONTEXT" "$@"
}

assert_cluster() {
  require kubectl helm
  k cluster-info >/dev/null
}
