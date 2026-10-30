#!/usr/bin/env python3
"""Install Argo CD or explicitly transfer the existing application from Jenkins."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.parse

import yaml

from gitops_support import (
    ROOT, STATE, ARGO_STATE, JENKINS_STATE, kube, main_guard, repo_url, run,
    save_private, stage_environment, tool_environment, validate_config, versions,
)


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configuration(source="main", repository="kovokilla/kubernetes-production-demo", branch="gitops/production-demo"):
    return validate_config({
        "cluster_name": "production-demo", "repository": repository,
        "source_branch": source, "gitops_branch": branch,
        "http_port": 8080, "https_port": 8443, "jenkins_port": 8090,
    })


def get(c, kind, name, namespace="demo"):
    return json.loads(run(kube(c, "-n", namespace, "get", kind, name, "-o", "json"), capture=True))


def apply(c, items):
    run(kube(c, "apply", "--server-side", "--field-manager=demo-argocd-setup", "-f", "-"),
        data=json.dumps({"apiVersion": "v1", "kind": "List", "items": items}), capture=True)


def helm(c, name, chart, namespace, *, repository=None, version=None, values=None, extra=None):
    args = ["helm", "--kube-context", "kind-production-demo", "upgrade", "--install",
            name, str(chart), "--namespace", namespace, "--wait", "--timeout", "10m"]
    if repository:
        args += ["--repo", repository, "--version", version]
    for path in values or []:
        args += ["-f", str(path)]
    run(args + (extra or []), env=dict(tool_environment(), HELM_DRIVER="secret"))


def install(c):
    # No cluster creation, CNI application, application sync or Jenkins mutation.
    run(kube(c, "get", "nodes"), capture=True)
    apply(c, [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "argocd"}}])
    meta = json.loads(run(["gh", "api", "meta"], capture=True))
    known = "".join(f"github.com {key}\n" for key in meta["ssh_keys"])
    save_private(ARGO_STATE / "github_known_hosts", known)
    public_values = ARGO_STATE / "public-values.yaml"
    save_private(public_values, yaml.safe_dump({"configs": {"ssh": {"knownHosts": known}}}))
    helm(c, "argocd", "argo-cd", "argocd", repository="https://argoproj.github.io/argo-helm",
         version=versions()["ARGOCD_CHART_VERSION"],
         values=[ROOT / "infrastructure/argocd-values.yaml", public_values])
    run(kube(c, "wait", "--for=condition=Established", "crd/applications.argoproj.io",
             "crd/appprojects.argoproj.io", "--timeout=120s"))
    initial = run(kube(c, "-n", "argocd", "get", "secret", "argocd-initial-admin-secret",
                       "--ignore-not-found", "-o", "json"), capture=True)
    if initial:
        save_private(ARGO_STATE / "admin_password",
                     base64.b64decode(json.loads(initial)["data"]["password"]).decode())
    elif not (ARGO_STATE / "admin_password").exists():
        raise RuntimeError("Provide the rotated Argo credential through protected local state")
    if (ARGO_STATE / "ownership.json").exists():
        print("Argo CD installation updated privately; application ownership was not changed.")
    else:
        print("Argo CD installed privately. Jenkins/Helm still owns the application; no Application was created.")


def extract_chart(payload, destination):
    """Extract only regular files/directories under webapp; reject tar traversal."""
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = archive.getmembers()
        if sum(member.size for member in members) > 20 * 1024 * 1024:
            raise ValueError("Unexpected chart archive size")
        for member in members:
            parts = Path(member.name).parts
            if (not parts or parts[0] != "webapp" or ".." in parts or Path(member.name).is_absolute()
                    or not (member.isfile() or member.isdir())):
                raise ValueError("Unsafe chart archive member")
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def canonical(documents):
    return {
        (doc["apiVersion"], doc["kind"], doc["metadata"]["name"]): doc
        for doc in documents if doc
    }


def check_live_equivalence(c, manifest):
    """Compare server-defaulted dry-run results with live non-metadata fields."""
    for desired in yaml.safe_load_all(manifest):
        if not desired:
            continue
        if desired["kind"] == "Secret":
            raise ValueError("Application Secrets must remain externally managed")
        live = get(c, desired["kind"], desired["metadata"]["name"])
        simulated = json.loads(run(kube(c, "-n", "demo", "apply", "--dry-run=server", "-f", "-", "-o", "json"),
                                   data=json.dumps(desired), capture=True))
        for field in ("spec", "data", "rules", "subjects", "roleRef", "automountServiceAccountToken"):
            if simulated.get(field) != live.get(field):
                raise ValueError("Adoption would change live configuration: " + desired["kind"] + "/" + desired["metadata"]["name"])


def snapshot(c, directory):
    client = load("jenkins-client")
    opener = client.client()
    env = dict(tool_environment(), HELM_DRIVER="configmap")
    releases = json.loads(run(["helm", "--kube-context", "kind-production-demo", "-n", "demo",
                              "list", "-o", "json"], capture=True, env=env))
    release = next(item for item in releases if item["name"] == "demo" and item["status"] == "deployed")
    if not re.fullmatch(r"webapp-0\.2\.[1-9][0-9]*", release["chart"]):
        raise ValueError("Expected an existing Jenkins-packaged release")
    build = release["chart"].rsplit(".", 1)[1]
    archive = release["chart"] + ".tgz"
    prefix = f"/job/production-demo/{build}/artifact/.artifacts/release/"
    with client.request(opener, prefix + archive) as response:
        package = response.read()
    with client.request(opener, prefix + "images.json") as response:
        provenance = json.load(response)
    if provenance["commit"] != release["app_version"] or str(provenance["build"]) != build:
        raise ValueError("Archived release provenance does not match the active Helm release")
    chart_root = directory / "charts"
    extract_chart(package, chart_root)
    chart = yaml.safe_load((chart_root / "webapp/Chart.yaml").read_text())
    if "webapp-" + str(chart["version"]) != release["chart"] or chart["appVersion"] != provenance["commit"]:
        raise ValueError("Archived chart does not match the deployed release")
    values = run(["helm", "--kube-context", "kind-production-demo", "-n", "demo", "get", "values",
                  "demo", "--all", "-o", "json"], capture=True, env=env)
    release_dir = directory / ".artifacts/release"
    release_dir.mkdir(parents=True)
    (release_dir / "values.yaml").write_text(yaml.safe_dump(json.loads(values)))
    # Argo consumes chart defaults; make current effective values explicit.
    (chart_root / "webapp/values.yaml").write_text(yaml.safe_dump(json.loads(values)))
    (release_dir / "images.json").write_text(json.dumps(provenance, indent=2))
    rendered = run(["helm", "template", "demo", chart_root / "webapp", "--namespace", "demo",
                    "--kube-version", "1.34.0"], capture=True)
    installed = run(["helm", "--kube-context", "kind-production-demo", "-n", "demo",
                     "get", "manifest", "demo"], capture=True, env=env)
    if canonical(yaml.safe_load_all(rendered)) != canonical(yaml.safe_load_all(installed)):
        raise ValueError("Snapshot chart render differs from the installed Helm manifest")
    check_live_equivalence(c, rendered)
    save_private(directory / "helm-release.json", json.dumps(release))
    print(f"Adoption check passed: {release['chart']}, source {provenance['commit']}; no workload changes required")
    return provenance


def source_is_published(c):
    for path in ("Jenkinsfile.gitops", "scripts/gitops-ci.py", "scripts/gitops_support.py",
                 "scripts/validate.sh", "jenkins/gitops/init.groovy", "jenkins/gitops/jenkins.yaml"):
        endpoint = f"repos/{c['repository']}/contents/{path}?ref=" + urllib.parse.quote(c["source_branch"], safe="")
        remote = json.loads(run(["gh", "api", endpoint], capture=True))
        data = (ROOT / path).read_bytes()
        if remote["sha"] != hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest():
            raise ValueError("Publish the reviewed Argo-only source before switching Jenkins")
    refs = json.loads(run(["gh", "api",
                           f"repos/{c['repository']}/git/matching-refs/heads/{c['gitops_branch']}"], capture=True))
    if any(ref["ref"] == "refs/heads/" + c["gitops_branch"] for ref in refs):
        endpoint = f"repos/{c['repository']}/contents/gitops-environment.json?ref=" + urllib.parse.quote(c["gitops_branch"], safe="")
        marker = json.loads(run(["gh", "api", endpoint], capture=True))
        identity = json.loads(base64.b64decode(marker["content"]))
        if identity != {"cluster": "production-demo", "repository": c["repository"]}:
            raise ValueError("Deployment branch belongs to another environment")


def reuse_credentials(c):
    """Reuse authorized registered keys; never create/register additional keys."""
    previous = STATE / "production-demo-gitops"
    mapping = [
        (previous / "argocd_repo_key", ARGO_STATE / "repo_key", True),
        (previous / "gitops_writer_key", JENKINS_STATE / "gitops_writer_key", False),
    ]
    registered = json.loads(run(["gh", "api", f"repos/{c['repository']}/keys"], capture=True))
    for old, target, read_only in mapping:
        if not target.exists():
            if not old.exists():
                raise ValueError("A previously authorized repository key must be supplied privately")
            save_private(target, old.read_text())
        public = run(["ssh-keygen", "-y", "-f", target], capture=True).split()[:2]
        if not any(key["key"].split()[:2] == public and key["read_only"] == read_only for key in registered):
            raise ValueError("Reused deploy-key access does not match the required role")
    save_private(JENKINS_STATE / "gitops-config.json", json.dumps(c))


def observer(c):
    identity = "demo-gitops-observer"
    apply(c, [
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
         "metadata": {"name": identity, "namespace": "argocd"},
         "rules": [{"apiGroups": ["argoproj.io"], "resources": ["applications"],
                    "resourceNames": ["demo"], "verbs": ["get"]}]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
         "metadata": {"name": identity, "namespace": "argocd"},
         "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": identity},
         "subjects": [{"kind": "User", "name": identity, "apiGroup": "rbac.authorization.k8s.io"}]},
    ])
    key = JENKINS_STATE / "observer.key"
    csr_file = JENKINS_STATE / "observer.csr"
    save_private(key, "")
    run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
         "-out", csr_file, "-subj", "/CN=" + identity], capture=True)
    name = "demo-argocd-observer-" + str(int(time.time()))
    apply(c, [{"apiVersion": "certificates.k8s.io/v1", "kind": "CertificateSigningRequest",
               "metadata": {"name": name}, "spec": {
                   "request": base64.b64encode(csr_file.read_bytes()).decode(),
                   "signerName": "kubernetes.io/kube-apiserver-client",
                   "expirationSeconds": 30 * 86400, "usages": ["client auth"]}}])
    try:
        run(kube(c, "certificate", "approve", name), capture=True)
        for _ in range(30):
            issued = get(c, "csr", name)
            certificate = issued.get("status", {}).get("certificate")
            if certificate:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Observer certificate was not issued")
        ca = get(c, "configmap", "kube-root-ca.crt")["data"]["ca.crt"]
        document = {
            "apiVersion": "v1", "kind": "Config",
            "clusters": [{"name": "production-demo", "cluster": {
                "server": "https://production-demo-control-plane:6443",
                "certificate-authority-data": base64.b64encode(ca.encode()).decode()}}],
            "users": [{"name": identity, "user": {"client-certificate-data": certificate,
                       "client-key-data": base64.b64encode(key.read_bytes()).decode()}}],
            "contexts": [{"name": "kind-production-demo", "context": {
                "cluster": "production-demo", "user": identity, "namespace": "argocd"}}],
            "current-context": "kind-production-demo",
        }
        save_private(JENKINS_STATE / "observer.kubeconfig", json.dumps(document))
    finally:
        run(kube(c, "delete", "csr", name), capture=True)


def volume_copy(volume, names, uid):
    payload = {name: base64.b64encode((JENKINS_STATE / name).read_bytes()).decode() for name in names}
    program = """
import json,base64,os,sys
from pathlib import Path
for name,value in json.load(sys.stdin).items():
    path=Path('/secrets')/name
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o400)
    with os.fdopen(fd,'wb') as output: output.write(base64.b64decode(value))
    os.chown(path,OWNER,OWNER)
    path.chmod(0o400)
""".replace("OWNER", str(uid))
    run(["docker", "run", "--rm", "--network", "none", "-i", "--user", "0", "--entrypoint", "python3",
         "-v", volume + ":/secrets", "production-demo/jenkins-agent:argocd", "-c", program],
        data=json.dumps(payload), capture=True)


def compose(c):
    env = dict(tool_environment(), DEMO_SOURCE_URL=repo_url(c), DEMO_SOURCE_BRANCH=c["source_branch"])
    rendered = run(["docker", "compose", "-p", "production-demo-jenkins",
                    "-f", ROOT / "jenkins/compose.yaml", "-f", ROOT / "jenkins/compose.argocd.yaml",
                    "config"], capture=True, env=env)
    save_private(ARGO_STATE / "compose.yaml", rendered)
    return ["docker", "compose", "-p", "production-demo-jenkins", "-f", str(ARGO_STATE / "compose.yaml")]


def switch_jenkins(c):
    observer(c)
    command = compose(c)
    run(command + ["build"])
    volume_copy("production-demo-jenkins-secrets", ["gitops_writer_key", "observer.kubeconfig"], 1000)
    volume_copy("production-demo-jenkins-agent-secrets", ["gitops-config.json"], 0)
    run(command + ["up", "-d", "--force-recreate"])
    run(["python3", ROOT / "scripts/jenkins-client.py", "wait"], env=stage_environment(c))
    run(["python3", ROOT / "scripts/jenkins-client.py", "reload"], env=stage_environment(c))


def backup(c, directory, client, opener):
    with client.request(opener, "/job/production-demo/config.xml") as response:
        save_private(directory / "job.xml", response.read().decode())
    run(["docker", "stop", "production-demo-jenkins-controller-1"], capture=True)
    try:
        for name in ("jenkins-home", "secrets", "agent-secrets"):
            volume = "production-demo-jenkins_" + name if name == "jenkins-home" else "production-demo-jenkins-" + name
            path = directory / (name + ".tar.gz")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                subprocess.run([
                    "docker", "run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "tar",
                    "-v", volume + ":/source:ro", "production-demo/jenkins-agent:local",
                    "-czf", "-", "-C", "/source", ".",
                ], stdout=output, stderr=subprocess.DEVNULL, check=True, env=tool_environment())
    finally:
        run(["docker", "start", "production-demo-jenkins-controller-1"], capture=True)
        run(["python3", ROOT / "scripts/jenkins-client.py", "wait"])


def record_handoff(c, directory, phase):
    save_private(ARGO_STATE / "handoff.json", json.dumps({
        "config": c, "backup": str(directory), "phase": phase,
    }))


def migrate(c, allowed):
    if not allowed:
        raise SystemExit("Migration requires --allow-git-writes: publish the exact live release and switch deployment ownership.")
    if (ARGO_STATE / "ownership.json").exists():
        raise SystemExit("Argo ownership already recorded; use verify or renew-observer, not a second handoff.")
    source_is_published(c)  # Fail before touching the deployment job.
    reuse_credentials(c)
    client = load("jenkins-client")
    opener = client.client()
    with client.request(opener, "/job/production-demo/api/json?tree=disabled,inQueue,lastBuild[building]") as response:
        job = json.load(response)
    if job["disabled"] or job["inQueue"] or (job.get("lastBuild") or {}).get("building"):
        raise RuntimeError("Wait for an idle, enabled legacy job before migration")
    directory = ARGO_STATE / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True, mode=0o700)
    record_handoff(c, directory, "prepared")
    with client.request(opener, "/job/production-demo/disable", b""):
        pass
    with client.request(opener, "/job/production-demo/api/json?tree=inQueue,lastBuild[building]") as response:
        job = json.load(response)
    if job["inQueue"] or (job.get("lastBuild") or {}).get("building"):
        raise RuntimeError("A build started during the pause; let it finish before retrying")
    # On failure keep the job disabled: never let legacy Helm fight an adopted app.
    backup(c, directory, client, opener)
    provenance = snapshot(c, directory)
    record_handoff(c, directory, "checked")
    install(c)
    ci = load("gitops-ci")
    env = ci.writer_environment(JENKINS_STATE / "gitops_writer_key", ARGO_STATE / "github_known_hosts")
    expected = ci.publish(c, env, release_root=directory, expected_source=provenance["commit"])
    record_handoff(c, directory, "published")
    helm(c, "traefik", "traefik", "ingress", repository="https://traefik.github.io/charts",
         version=versions()["TRAEFIK_CHART_VERSION"], values=[ROOT / "infrastructure/traefik-gitops-values.yaml"],
         extra=["--reuse-values"])
    apply(c, [{"apiVersion": "v1", "kind": "Secret",
               "metadata": {"name": "demo-repository", "namespace": "argocd",
                            "labels": {"argocd.argoproj.io/secret-type": "repository"}},
               "stringData": {"type": "git", "url": repo_url(c), "project": "demo",
                              "sshPrivateKey": (ARGO_STATE / "repo_key").read_text()}}])
    project = yaml.safe_load((ROOT / "infrastructure/gitops-project.yaml").read_text())
    project["spec"]["sourceRepos"] = [repo_url(c)]
    application = yaml.safe_load((ROOT / "infrastructure/gitops-application.yaml").read_text())
    application["spec"]["source"].update(repoURL=repo_url(c), targetRevision=c["gitops_branch"])
    save_private(ARGO_STATE / "ownership.json", json.dumps(c))
    record_handoff(c, directory, "adopting")
    apply(c, [project, application])
    ci.wait(c, observer=False, expected=expected)
    record_handoff(c, directory, "adopted")
    switch_jenkins(c)
    helm(c, "monitoring-resources", ROOT / "charts/monitoring-resources", "monitoring",
         extra=["--set", "gitops.enabled=true"])
    client = load("jenkins-client")
    with client.request(client.client(), "/job/production-demo/enable", b""):
        pass
    run(["python3", ROOT / "scripts/monitoring-sync-targets.py"], env=stage_environment(c))
    record_handoff(c, directory, "active")
    print("Argo CD owns demo; existing Jenkins job now publishes GitOps state. Helm history preserved.")


def renew_observer(c):
    client = load("jenkins-client")
    opener = client.client()
    with client.request(opener, "/job/production-demo/api/json?tree=disabled,inQueue,lastBuild[building]") as response:
        job = json.load(response)
    if job["inQueue"] or (job.get("lastBuild") or {}).get("building"):
        raise RuntimeError("Wait for an idle job before observer renewal")
    with client.request(opener, "/job/production-demo/disable", b""):
        pass
    observer(c)
    volume_copy("production-demo-jenkins-secrets", ["observer.kubeconfig"], 1000)
    run(["python3", ROOT / "scripts/jenkins-client.py", "reload"], env=stage_environment(c))
    if not job["disabled"]:
        with client.request(opener, "/job/production-demo/enable", b""):
            pass


def rollback(c):
    client = load("jenkins-client")
    with client.request(client.client(), "/job/production-demo/api/json?tree=inQueue,lastBuild[building]") as response:
        job = json.load(response)
    if job["inQueue"] or (job.get("lastBuild") or {}).get("building"):
        raise RuntimeError("Wait for an idle job before rollback")
    with client.request(client.client(), "/job/production-demo/disable", b""):
        pass
    raw = run(kube(c, "-n", "argocd", "get", "application", "demo",
                   "--ignore-not-found", "-o", "json"), capture=True)
    if raw:
        app = json.loads(raw)
        if (app["spec"].get("project") != "demo" or app["metadata"].get("finalizers")
                or app["spec"].get("source", {}).get("repoURL") != repo_url(c)
                or app["spec"].get("source", {}).get("targetRevision") != c["gitops_branch"]):
            raise RuntimeError("Refusing rollback for an unexpected or cascading Application")
        run(kube(c, "-n", "argocd", "patch", "application", "demo", "--type=merge",
                 "-p", json.dumps({"spec": {"syncPolicy": {"automated": None}}})), capture=True)
        run(kube(c, "-n", "argocd", "delete", "application", "demo", "--cascade=orphan"), capture=True)
    run(["docker", "compose", "-f", ROOT / "jenkins/compose.yaml", "up", "-d", "--no-build"])
    run(["python3", ROOT / "scripts/jenkins-client.py", "wait"])
    run(["python3", ROOT / "scripts/jenkins-client.py", "reload"])
    helm(c, "monitoring-resources", ROOT / "charts/monitoring-resources", "monitoring",
         extra=["--set", "gitops.enabled=false"])
    if (ARGO_STATE / "ownership.json").exists():
        (ARGO_STATE / "ownership.json").rename(ARGO_STATE / "ownership.previous.json")
    if (ARGO_STATE / "handoff.json").exists():
        (ARGO_STATE / "handoff.json").rename(ARGO_STATE / "handoff.previous.json")
    client = load("jenkins-client")
    with client.request(client.client(), "/job/production-demo/enable", b""):
        pass
    run(["python3", ROOT / "scripts/monitoring-sync-targets.py"])
    print("Legacy job restored. Renew its deploy token before the next Helm deployment; no workloads were uninstalled.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "check", "migrate", "renew-observer", "verify", "rollback"])
    parser.add_argument("--source-branch", default="main")
    parser.add_argument("--repository", default="kovokilla/kubernetes-production-demo")
    parser.add_argument("--gitops-branch", default="gitops/production-demo")
    parser.add_argument("--allow-git-writes", action="store_true")
    args = parser.parse_args()
    c = configuration(args.source_branch, args.repository, args.gitops_branch)
    if args.action in ("renew-observer", "verify", "rollback"):
        marker = ARGO_STATE / "ownership.json"
        journal = ARGO_STATE / "handoff.json"
        if args.action == "rollback" and not marker.exists() and journal.exists():
            c = validate_config(json.loads(journal.read_text())["config"])
        elif not marker.exists():
            raise SystemExit("No completed Argo ownership handoff is recorded")
        else:
            c = validate_config(json.loads(marker.read_text()))
    if args.action == "install":
        install(c)
    elif args.action == "check":
        (ROOT / ".artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="argocd-check-", dir=ROOT / ".artifacts") as directory:
            snapshot(c, Path(directory))
    elif args.action == "migrate":
        migrate(c, args.allow_git_writes)
    elif args.action == "rollback":
        rollback(c)
    elif args.action == "renew-observer":
        renew_observer(c)
    else:
        for command in (["bash", ROOT / "scripts/smoke-test.sh"],
                        ["python3", ROOT / "scripts/monitoring-verify.py"],
                        ["python3", ROOT / "scripts/grafana-verify.py"]):
            run(command, env=stage_environment(c))


if __name__ == "__main__":
    main_guard(main)
