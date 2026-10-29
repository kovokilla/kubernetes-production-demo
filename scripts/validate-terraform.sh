#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
require terraform
terraform fmt -check -recursive "$ROOT/terraform"
terraform -chdir="$ROOT/terraform/local" init -backend=false -input=false
terraform -chdir="$ROOT/terraform/local" validate
# All test runs are plan-only: never run bootstrap provisioners or GitHub writes.
terraform -chdir="$ROOT/terraform/local" test
