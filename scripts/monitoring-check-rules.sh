#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
require docker tar
# Stream inputs instead of host bind mounts: also works from the Dockerized agent.
tar -cf - -C "$ROOT/charts/monitoring-resources/files" alerts.yaml \
  -C "$ROOT/tests/prometheus" rules.test.yaml |
  docker run --rm -i --entrypoint /bin/sh prom/prometheus:v3.10.0 -c \
    'cd /tmp; tar -xf -; promtool check rules alerts.yaml && promtool test rules rules.test.yaml'
