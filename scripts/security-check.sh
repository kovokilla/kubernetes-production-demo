#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
require git python3 gitleaks
gitleaks git "$ROOT" --config "$ROOT/.gitleaks.toml" --redact=100 \
  --no-banner --max-decode-depth 2 --log-opts=--all
python3 - "$ROOT" "$(command -v gitleaks)" <<'PY'
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path(sys.argv[1])
scanner = sys.argv[2]
names = subprocess.check_output(
    ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
).decode().split("\0")
with tempfile.TemporaryDirectory(prefix="publication-source-check-") as temporary:
    snapshot = Path(temporary)
    count = 0
    for name in sorted(set(filter(None, names))):
        relative = Path(name)
        if relative.name == "_README.private.md" or any(
            part in {".tools", ".artifacts", ".venv", ".secrets", "__pycache__", ".terraform"} for part in relative.parts
        ) or relative.name.endswith((".tfstate", ".tfplan", ".tfvars", ".tfvars.json")) or ".tfstate." in relative.name:
            raise SystemExit(f"Private or generated content is publishable: {name}")
        source = root / relative
        if source.is_symlink():
            raise SystemExit(f"Review publishable symlink before scanning: {name}")
        if not source.is_file():
            continue
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        count += 1
    subprocess.run(
        [scanner, "dir", str(snapshot), "--config", str(root / ".gitleaks.toml"),
         "--redact=100", "--no-banner", "--max-decode-depth", "2"],
        check=True,
    )
    print(f"Secret checks passed for reachable Git history and {count} current publishable files.")
PY
