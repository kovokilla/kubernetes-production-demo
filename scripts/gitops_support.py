"""Shared non-secret GitOps configuration and subprocess safety helpers."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def tool_environment():
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([
        str(ROOT / ".tools"), "/Applications/Docker.app/Contents/Resources/bin",
        str(Path.home() / ".docker/bin"), env.get("PATH", ""),
    ])
    return env


def run(args, *, data=None, capture=False, env=None):
    # Never dump stdin or raw stderr on failure: either may include credentials.
    result = subprocess.run(
        [str(a) for a in args], input=data, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE, env=env or tool_environment(),
    )
    if result.returncode:
        raise RuntimeError(f"{Path(str(args[0])).name} operation failed (exit {result.returncode}); inspect protected local service logs")
    return result.stdout.strip() if capture else ""


def validate_config(c):
    if not re.fullmatch(r"production-demo-gitops[-a-z0-9]*", c["cluster_name"]):
        raise ValueError("Refusing a legacy or invalid cluster name")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", c["repository"]):
        raise ValueError("Expected GitHub owner/repository")
    for key in ("source_branch", "gitops_branch"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_/-]*", c[key]):
            raise ValueError("Invalid branch name")
        if any(part in ("", ".", "..") for part in c[key].split("/")):
            raise ValueError("Invalid branch segments")
    if not c["gitops_branch"].startswith("gitops/") or c["source_branch"] == c["gitops_branch"]:
        raise ValueError("Use a separate gitops/... deployment branch")
    ports = [c[k] for k in ("http_port", "https_port", "jenkins_port")]
    if len(set(ports)) != 3 or any(type(p) is not int or not 1024 <= p <= 65535 or p in (8080, 8443, 8090) for p in ports):
        raise ValueError("Use three distinct isolated unprivileged ports")
    if type(c.get("allow_github_writes")) is not bool:
        raise ValueError("Explicit GitHub-write authorization flag required")
    return c


def load_config():
    return validate_config(json.loads(os.environ["DEMO_TF_CONFIG"]))


def state_path(c):
    return Path.home() / ".local/share/kubernetes-production-demo" / c["cluster_name"]


def save_private(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(value)
    path.chmod(0o600)


def repo_url(c):
    return f"git@github.com:{c['repository']}.git"


def versions():
    return dict(line.split("=", 1) for line in (ROOT / "infrastructure/versions.env").read_text().splitlines()
                if line and not line.startswith("#"))


def kube(c, *args):
    return ["kubectl", "--kubeconfig", str(state_path(c) / "kubeconfig"),
            "--context", "kind-" + c["cluster_name"], *args]


def stage_environment(c):
    state = state_path(c)
    return dict(tool_environment(), DEMO_CONTEXT="kind-" + c["cluster_name"],
                KUBECONFIG=str(state / "kubeconfig"), DEMO_STATE_ROOT=str(state),
                DEMO_GITOPS="1", DEMO_HTTP_PORT=str(c["http_port"]),
                DEMO_JENKINS_PORT=str(c["jenkins_port"]),
                DEMO_COMPOSE_FILE=str(state / "compose.yaml"),
                DEMO_COMPOSE_PROJECT=c["cluster_name"])


def main_guard(function):
    try:
        function()
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.SubprocessError):
        # No exception bodies: an upstream command/API may contain secret data.
        print("GitOps operation failed safely. Check prerequisites, source publication, authorization and protected service logs.", file=sys.stderr)
        raise SystemExit(1)
