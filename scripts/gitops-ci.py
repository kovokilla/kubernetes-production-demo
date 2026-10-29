#!/usr/bin/env python3
"""CI builds immutable local images and publishes desired state; never deploys."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import time
import urllib.request

from gitops_support import ROOT, main_guard, repo_url, run, tool_environment, validate_config

CONFIG_FILE = Path("/home/jenkins/gitops-config.json")


def config():
    return validate_config(json.loads(CONFIG_FILE.read_text()))


def revision():
    return run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture=True)


def release_tag(sha, build):
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"[1-9][0-9]*", str(build)):
        raise ValueError("Invalid build provenance")
    return f"sha-{sha}-build-{build}"


def build(c):
    sha = revision()
    number = os.environ["BUILD_NUMBER"]
    tag = release_tag(sha, number)
    ids = {}
    for component in ("backend", "frontend"):
        image = f"demo/{component}:{tag}"
        run(["docker", "build", "--platform", "linux/arm64", "--label",
             f"org.opencontainers.image.revision={sha}", "-t", image, ROOT / "apps" / component])
        ids[component] = run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], capture=True)
        run(["kind", "load", "docker-image", image, "--name", c["cluster_name"]])
    spec = importlib.util.spec_from_file_location("release", ROOT / "scripts/update-release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.update_release(ROOT, sha, number, ids)
    run(["helm", "lint", ROOT / "charts/webapp", "--strict", "-f", ROOT / ".artifacts/release/values.yaml"])
    run(["helm", "package", ROOT / "charts/webapp", "--destination", ROOT / ".artifacts/release"])


def writer_environment():
    key = Path(os.environ["GITOPS_KEY"])
    if not key.is_file():
        raise ValueError("Missing CI-bound write credential")
    env = tool_environment()
    env.pop("SSH_AUTH_SOCK", None)
    env["GIT_SSH_COMMAND"] = (
        f"ssh -i {shlex.quote(str(key))} -o IdentitiesOnly=yes -o IdentityAgent=none "
        "-o StrictHostKeyChecking=yes -o UserKnownHostsFile=/home/jenkins/.ssh/known_hosts"
    )
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def publish(c, env=None):
    env = env or writer_environment()
    release = ROOT / ".artifacts/release"
    provenance = json.loads((release / "images.json").read_text())
    if provenance["commit"] != revision():
        raise ValueError("Build/source mismatch")
    marker = {"cluster": c["cluster_name"], "repository": c["repository"]}
    with tempfile.TemporaryDirectory(prefix="gitops-publish-") as directory:
        directory = Path(directory)
        git = ["git", "-C", directory]
        run(git + ["init", "-q"], env=env)
        run(git + ["remote", "add", "origin", repo_url(c)], env=env)
        existing = run(git + ["ls-remote", "--heads", "origin", "refs/heads/" + c["gitops_branch"]], capture=True, env=env)
        if existing:
            run(git + ["fetch", "--depth=1", "origin", "refs/heads/" + c["gitops_branch"]], env=env)
            run(git + ["checkout", "-q", "-b", "release", "FETCH_HEAD"], env=env)
            if json.loads((directory / "gitops-environment.json").read_text()) != marker:
                raise ValueError("Deployment branch belongs to another environment")
        else:
            run(git + ["checkout", "-q", "--orphan", "release"], env=env)
        chart = directory / "charts/webapp"
        if chart.exists():
            shutil.rmtree(chart)  # Only the temporary checkout, never user source.
        shutil.copytree(ROOT / "charts/webapp", chart)
        (directory / "gitops-environment.json").write_text(json.dumps(marker, indent=2) + "\n")
        (directory / "release.json").write_text(json.dumps(provenance, indent=2) + "\n")
        run(git + ["config", "user.name", "GitOps CI"], env=env)
        run(git + ["config", "user.email", "gitops@localhost"], env=env)
        run(git + ["add", "charts/webapp", "gitops-environment.json", "release.json"], env=env)
        changed = run(git + ["status", "--porcelain"], capture=True, env=env)
        if changed:
            run(git + ["commit", "-q", "-m", f"Deploy {provenance['commit']} build {provenance['build']}"], env=env)
            # No force push: a concurrent update fails safely for operator retry.
            run(git + ["push", "origin", "HEAD:refs/heads/" + c["gitops_branch"]], env=env)
        sha = run(git + ["rev-parse", "HEAD"], capture=True, env=env)
        (release / "gitops-revision").write_text(sha + "\n")
        print(f"Published GitOps revision {sha}")


def synced_healthy(app, sha):
    status = app.get("status", {})
    return (
        status.get("sync", {}).get("revision") == sha
        and status.get("sync", {}).get("status") == "Synced"
        and status.get("health", {}).get("status") == "Healthy"
        and status.get("operationState", {}).get("phase") == "Succeeded"
        and status.get("operationState", {}).get("syncResult", {}).get("revision") == sha
    )


def wait(c, *, observer=True, expected=None):
    expected = expected or (ROOT / ".artifacts/release/gitops-revision").read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ValueError("Invalid GitOps revision")
    kube = ["kubectl", "--context", "kind-" + c["cluster_name"]]
    if not observer:
        from gitops_support import kube as cluster_command
        kube = cluster_command(c)
    for attempt in range(90):
        app = json.loads(run(kube + ["-n", "argocd", "get", "application", "demo", "-o", "json"], capture=True))
        if synced_healthy(app, expected):
            print(f"Argo CD Synced/Healthy at {expected}")
            return
        if attempt % 6 == 0:
            print("Waiting for the exact GitOps revision and healthy rollout", flush=True)
        time.sleep(5)
    raise RuntimeError("Argo CD rollout timed out")


def verify_http(c, host):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(
        host + "/api/info", headers={"Host": "demo.localhost"}), timeout=15) as response:
        data = json.load(response)
        if response.status != 200 or not data.get("message") or not data.get("instance"):
            raise RuntimeError("API verification failed")
    with opener.open(urllib.request.Request(host + "/", headers={"Host": "demo.localhost"}), timeout=15) as response:
        html = response.read()
    if html != (ROOT / "apps/frontend/public/index.html").read_bytes():
        raise RuntimeError("Ingress HTML does not match built source")
    print("API and ingress HTML match the built source")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "publish", "wait"])
    action = parser.parse_args().action
    c = config()
    if action == "build":
        build(c)
    elif action == "publish":
        publish(c)
    else:
        wait(c)
        verify_http(c, f"http://{c['cluster_name']}-control-plane:30080")


if __name__ == "__main__":
    main_guard(main)
