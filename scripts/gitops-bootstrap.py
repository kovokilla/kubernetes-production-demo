#!/usr/bin/env python3
"""Terraform's idempotent local bootstrap stages; never adopt the legacy stack."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import time
import urllib.parse

import yaml

from gitops_support import (
    ROOT, kube, load_config, main_guard, repo_url, run, save_private,
    stage_environment, state_path, tool_environment, versions,
)


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def apply(c, objects):
    document = {"apiVersion": "v1", "kind": "List", "items": objects}
    run(kube(c, "apply", "--server-side", "--field-manager=demo-gitops-bootstrap", "-f", "-"),
        data=json.dumps(document), capture=True)


def get(c, kind, name, namespace):
    return json.loads(run(kube(c, "-n", namespace, "get", kind, name, "-o", "json"), capture=True))


def authorization(c):
    if not c["allow_github_writes"]:
        raise SystemExit(
            "GitHub writes are disabled. First publish/review the implementation, then explicitly approve "
            "three deploy-key registrations (two read-only, one repository-write) and CI writes to "
            + c["gitops_branch"] + " using -var=allow_github_writes=true."
        )


def cluster_identity(c):
    return {key: c[key] for key in ("cluster_name", "http_port", "https_port")}


def require_owned(c):
    state = state_path(c)
    marker = state / "owner.json"
    if not marker.exists() or json.loads(marker.read_text()) != cluster_identity(c):
        raise RuntimeError("Missing or mismatched environment ownership marker")
    return state


def preflight(c):
    for command in ("docker", "kind", "kubectl", "helm", "gh", "git", "ssh-keygen", "openssl"):
        if not shutil.which(command, path=tool_environment()["PATH"]):
            raise SystemExit("Missing prerequisite: " + command)
    run(["docker", "info"], capture=True)
    run(["gh", "auth", "status"], capture=True)
    authorization(c)
    # Confirm remote source exists BEFORE any cluster or GitHub modification.
    # Checkout exact code identity, not just an existing filename.
    required = ["Jenkinsfile.gitops", "scripts/gitops-ci.py", "scripts/gitops_support.py",
                "scripts/update-release.py", "scripts/validate.sh", "jenkins/gitops/init.groovy",
                "jenkins/gitops/jenkins.yaml", "jenkins/gitops/Dockerfile",
                "apps/frontend/public/index.html", "apps/frontend/server.js", "apps/backend/server.js"]
    for path in required:
        api = f"repos/{c['repository']}/contents/{path}?ref=" + urllib.parse.quote(c["source_branch"], safe="")
        remote = json.loads(run(["gh", "api", api], capture=True))
        local = (ROOT / path).read_bytes()
        blob = hashlib.sha1(f"blob {len(local)}\0".encode() + local).hexdigest()
        if remote.get("sha") != blob:
            raise SystemExit("Publish the reviewed implementation to the configured source branch first: " + path)
    refs = json.loads(run([
        "gh", "api", f"repos/{c['repository']}/git/matching-refs/heads/{c['gitops_branch']}",
    ], capture=True))
    if any(ref["ref"] == "refs/heads/" + c["gitops_branch"] for ref in refs):
        api = f"repos/{c['repository']}/contents/gitops-environment.json?ref=" + urllib.parse.quote(c["gitops_branch"], safe="")
        marker = json.loads(run(["gh", "api", api], capture=True))
        identity = json.loads(base64.b64decode(marker["content"]))
        if identity != {"cluster": c["cluster_name"], "repository": c["repository"]}:
            raise SystemExit("Refusing to reconcile a deployment branch owned by another environment")
    state = state_path(c)
    if (state / "owner.json").exists():
        require_owned(c)
    else:
        clusters = run(["kind", "get", "clusters"], capture=True).splitlines()
        if c["cluster_name"] in clusters:
            raise SystemExit("Refusing to adopt an existing kind cluster; choose a new isolated name")
        prefix = c["cluster_name"]
        reserved = {prefix + suffix for suffix in ("-secrets", "-agent-secrets", "_jenkins-home", "_agent-work")}
        volumes = set(run(["docker", "volume", "ls", "--format", "{{.Name}}"], capture=True).splitlines())
        containers = run(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + prefix], capture=True)
        if volumes & reserved or containers:
            raise SystemExit("Refusing to adopt pre-existing Docker resources without an environment marker")
        for key in ("http_port", "https_port", "jenkins_port"):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", c[key]))
    print("Prerequisites, remote source, isolated names and authorization verified")


def cluster(c):
    state = state_path(c)
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    state.chmod(0o700)
    clusters = run(["kind", "get", "clusters"], capture=True).splitlines()
    marker = state / "owner.json"
    if c["cluster_name"] in clusters:
        require_owned(c)
        # Re-export only our cluster config if a protected file was lost.
        run(["kind", "export", "kubeconfig", "--name", c["cluster_name"], "--kubeconfig", state / "kubeconfig"], capture=True)
        (state / "kubeconfig").chmod(0o600)
        return
    if marker.exists():
        require_owned(c)
    else:
        save_private(marker, json.dumps(cluster_identity(c)))
    config = yaml.safe_load((ROOT / "infrastructure/kind.yaml").read_text())
    config["name"] = c["cluster_name"]
    config["nodes"][0]["extraPortMappings"][0]["hostPort"] = c["http_port"]
    config["nodes"][0]["extraPortMappings"][1]["hostPort"] = c["https_port"]
    save_private(state / "kind.yaml", yaml.safe_dump(config))
    run(["kind", "create", "cluster", "--name", c["cluster_name"], "--config", state / "kind.yaml",
         "--image", versions()["KIND_NODE_IMAGE"], "--kubeconfig", state / "kubeconfig"])
    (state / "kubeconfig").chmod(0o600)


def helm(c, release, chart, namespace, *, repository=None, version=None, values=None, extra=None):
    command = ["helm", "--kubeconfig", str(state_path(c) / "kubeconfig"), "--kube-context",
               "kind-" + c["cluster_name"], "upgrade", "--install", release, str(chart),
               "--namespace", namespace, "--wait", "--timeout", "10m"]
    if repository:
        command += ["--repo", repository, "--version", version]
    for path in values or []:
        command += ["-f", str(path)]
    command += extra or []
    run(command)


def platform(c):
    require_owned(c)
    run(kube(c, "cluster-info"), capture=True)
    version = versions()["CALICO_VERSION"]
    run(kube(c, "apply", "--server-side", "-f",
             f"https://raw.githubusercontent.com/projectcalico/calico/{version}/manifests/calico.yaml"))
    run(kube(c, "-n", "kube-system", "rollout", "status", "daemonset/calico-node", "--timeout=300s"))
    run(kube(c, "wait", "--for=condition=Ready", "nodes", "--all", "--timeout=300s"))
    run(kube(c, "apply", "-f", ROOT / "infrastructure/namespaces.yaml"))
    for namespace in ("argocd", "monitoring"):
        apply(c, [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}])
    run(["python3", ROOT / "scripts/create-secrets.py"], env=stage_environment(c))
    helm(c, "traefik", "traefik", "ingress", repository="https://traefik.github.io/charts",
         version=versions()["TRAEFIK_CHART_VERSION"],
         values=[ROOT / "infrastructure/traefik-values.yaml", ROOT / "infrastructure/traefik-gitops-values.yaml"])


def keypair(state, name, kind, bits=None, pem=False):
    path = state / name
    if not path.exists():
        args = ["ssh-keygen", "-q", "-t", kind, "-N", "", "-f", path]
        if bits:
            args += ["-b", str(bits)]
        if pem:
            args += ["-m", "PEM"]
        run(args, capture=True)
    path.chmod(0o600)
    save_private(state / (name + "_public"), path.with_suffix(".pub").read_text())


def credentials(c):
    authorization(c)
    state = require_owned(c)
    if not (state / "admin_password").exists():
        save_private(state / "admin_password", secrets.token_urlsafe(32))
    for name, kind, bits, pem in [
        ("github_deploy_key", "ed25519", None, False),
        ("argocd_repo_key", "ed25519", None, False),
        ("gitops_writer_key", "ed25519", None, False),
        ("agent_key", "rsa", 3072, True),
        ("agent_host_key", "ecdsa", 256, False),
    ]:
        keypair(state, name, kind, bits, pem)
    existing = json.loads(run(["gh", "api", f"repos/{c['repository']}/keys"], capture=True))
    for name, read_only in [("github_deploy_key", True), ("argocd_repo_key", True), ("gitops_writer_key", False)]:
        public = (state / (name + "_public")).read_text().strip()
        found = next((key for key in existing if key["key"].split()[:2] == public.split()[:2]), None)
        if found and found["read_only"] != read_only:
            raise RuntimeError("Existing deploy-key permission does not match its role")
        if not found:
            found = json.loads(run(
                ["gh", "api", "--method", "POST", f"repos/{c['repository']}/keys", "--input", "-"],
                data=json.dumps({"title": c["cluster_name"] + " " + name, "key": public, "read_only": read_only}),
                capture=True))
        save_private(state / (name + "_id"), str(found["id"]))
    meta = json.loads(run(["gh", "api", "meta"], capture=True))
    known = "".join(f"github.com {key}\n" for key in meta["ssh_keys"])
    save_private(state / "github_known_hosts", known)
    save_private(state / "gitops-config.json", json.dumps(c))


def argocd(c):
    state = require_owned(c)
    credentials(c)
    public_values = state / "argocd-public-values.yaml"
    save_private(public_values, yaml.safe_dump({
        "configs": {"ssh": {"knownHosts": (state / "github_known_hosts").read_text()}},
    }))
    helm(c, "argocd", "argo-cd", "argocd", repository="https://argoproj.github.io/argo-helm",
         version=versions()["ARGOCD_CHART_VERSION"],
         values=[ROOT / "infrastructure/argocd-values.yaml", public_values])
    run(kube(c, "wait", "--for=condition=Established", "crd/applications.argoproj.io",
             "crd/appprojects.argoproj.io", "--timeout=120s"))
    # Secrets are injected over stdin; neither values nor secret payloads enter TF state.
    apply(c, [
        {"apiVersion": "v1", "kind": "Secret",
         "metadata": {"name": "demo-repository", "namespace": "argocd",
                      "labels": {"argocd.argoproj.io/secret-type": "repository"}},
         "stringData": {"type": "git", "url": repo_url(c), "project": "demo",
                        "sshPrivateKey": (state / "argocd_repo_key").read_text()}},
    ])
    project = yaml.safe_load((ROOT / "infrastructure/gitops-project.yaml").read_text())
    project["spec"]["sourceRepos"] = [repo_url(c)]
    application = yaml.safe_load((ROOT / "infrastructure/gitops-application.yaml").read_text())
    application["spec"]["source"].update(repoURL=repo_url(c), targetRevision=c["gitops_branch"])
    apply(c, [project, application])
    # Preserve the initial login credential privately; never print its contents.
    path = state / "argocd_admin_password"
    if not path.exists():
        secret = get(c, "secret", "argocd-initial-admin-secret", "argocd")
        save_private(path, base64.b64decode(secret["data"]["password"]).decode())


def observer(c):
    """Issue a 30-day client certificate that can only GET this Application."""
    state = require_owned(c)
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
    # Reissue on this stage's replay; no long-lived cluster-admin credentials in CI.
    key = state / "observer.key"
    csr_path = state / "observer.csr"
    save_private(key, "")
    run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
         "-keyout", key, "-out", csr_path, "-subj", "/CN=" + identity], capture=True)
    key.chmod(0o600)
    name = c["cluster_name"] + "-observer-" + secrets.token_hex(4)
    request = {"apiVersion": "certificates.k8s.io/v1", "kind": "CertificateSigningRequest",
               "metadata": {"name": name},
               "spec": {"request": base64.b64encode(csr_path.read_bytes()).decode(),
                        "signerName": "kubernetes.io/kube-apiserver-client",
                        "expirationSeconds": 30 * 86400, "usages": ["client auth"]}}
    apply(c, [request])
    try:
        run(kube(c, "certificate", "approve", name), capture=True)
        for _ in range(30):
            issued = json.loads(run(kube(c, "get", "csr", name, "-o", "json"), capture=True))
            cert = issued.get("status", {}).get("certificate")
            if cert:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Observer certificate was not issued")
        ca = get(c, "configmap", "kube-root-ca.crt", "demo")["data"]["ca.crt"]
        document = {
            "apiVersion": "v1", "kind": "Config",
            "clusters": [{"name": c["cluster_name"], "cluster": {
                "server": f"https://{c['cluster_name']}-control-plane:6443",
                "certificate-authority-data": base64.b64encode(ca.encode()).decode()}}],
            "users": [{"name": identity, "user": {"client-certificate-data": cert,
                       "client-key-data": base64.b64encode(key.read_bytes()).decode()}}],
            "contexts": [{"name": "kind-" + c["cluster_name"], "context": {
                "cluster": c["cluster_name"], "user": identity, "namespace": "argocd"}}],
            "current-context": "kind-" + c["cluster_name"],
        }
        save_private(state / "observer.kubeconfig", json.dumps(document))
    finally:
        run(kube(c, "delete", "csr", name), capture=True)


def compose_document(c):
    prefix = c["cluster_name"]
    return {
        "name": prefix,
        "services": {
            "controller": {
                "image": prefix + "/jenkins-controller:local",
                "build": {"context": str(ROOT), "dockerfile": "jenkins/gitops/Dockerfile"},
                "restart": "unless-stopped",
                "ports": [f"127.0.0.1:{c['jenkins_port']}:8080"],
                "environment": {
                    "JAVA_OPTS": "-Xms256m -Xmx1024m -Djenkins.install.runSetupWizard=false",
                    "CASC_JENKINS_CONFIG": "/usr/share/jenkins/ref/jenkins.yaml",
                    "DEMO_SOURCE_URL": repo_url(c), "DEMO_SOURCE_BRANCH": c["source_branch"],
                    "DEMO_JENKINS_PORT": str(c["jenkins_port"]),
                    "DEMO_AGENT_HOST": prefix + "-agent",
                },
                "volumes": ["jenkins-home:/var/jenkins_home", "bootstrap-secrets:/run/secrets:ro"],
                "networks": ["jenkins", "kind"], "mem_limit": "1536m",
            },
            "agent": {
                "image": prefix + "/jenkins-agent:local",
                "build": {"context": str(ROOT), "dockerfile": "jenkins/agent/Dockerfile"},
                "restart": "unless-stopped",
                "volumes": ["agent-work:/home/jenkins/agent", "agent-secrets:/opt/agent-secrets:ro",
                            "/var/run/docker.sock:/opt/docker.sock"],
                "networks": {"jenkins": {"aliases": [prefix + "-agent"]}, "kind": {}}, "mem_limit": "1536m",
            },
        },
        "volumes": {
            "jenkins-home": {}, "agent-work": {},
            "bootstrap-secrets": {"external": True, "name": prefix + "-secrets"},
            "agent-secrets": {"external": True, "name": prefix + "-agent-secrets"},
        },
        "networks": {"jenkins": {}, "kind": {"external": True, "name": "kind"}},
    }


def sync_volume(c, name, files, uid):
    state = state_path(c)
    run(["docker", "volume", "create", name], capture=True)
    payload = {filename: base64.b64encode((state / filename).read_bytes()).decode() for filename in files}
    code = """
import base64,json,os,sys
from pathlib import Path
for name,value in json.load(sys.stdin).items():
    path=Path('/secrets')/name
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o400)
    with os.fdopen(fd,'wb') as output: output.write(base64.b64decode(value))
    os.chown(path,OWNER,OWNER)
    path.chmod(0o400)
""".replace("OWNER", str(uid))
    run(["docker", "run", "--rm", "-i", "--user", "0", "--entrypoint", "python3",
         "-v", name + ":/secrets", c["cluster_name"] + "/jenkins-agent:local", "-c", code],
        data=json.dumps(payload), capture=True)


def jenkins(c):
    state = require_owned(c)
    observer(c)
    save_private(state / "compose.yaml", yaml.safe_dump(compose_document(c), sort_keys=False))
    compose = ["docker", "compose", "-f", state / "compose.yaml", "-p", c["cluster_name"]]
    run(compose + ["build"])
    sync_volume(c, c["cluster_name"] + "-secrets", [
        "admin_password", "agent_key", "agent_host_key_public", "github_deploy_key",
        "gitops_writer_key", "github_known_hosts", "observer.kubeconfig",
    ], 1000)
    sync_volume(c, c["cluster_name"] + "-agent-secrets", [
        "agent_host_key", "agent_host_key_public", "agent_key_public",
        "github_known_hosts", "gitops-config.json",
    ], 0)
    run(compose + ["up", "-d", "--force-recreate"])
    run(["python3", ROOT / "scripts/jenkins-client.py", "wait"], env=stage_environment(c))
    run(["python3", ROOT / "scripts/jenkins-client.py", "reload"], env=stage_environment(c))
    os.environ.update(stage_environment(c))
    client = module("jenkins-client")
    with client.request(client.client(), "/job/production-demo/api/json?tree=lastBuild[number]") as response:
        last = (json.load(response).get("lastBuild") or {}).get("number", 0)
    save_private(state / "initial-build-baseline", str(last))
    # Ensure an unchanged existing source also builds after local bootstrap changes.
    run(["python3", ROOT / "scripts/jenkins-client.py", "build"], env=stage_environment(c))


def application(c):
    state = require_owned(c)
    # Reuse the origin-bound local client, but never the legacy credential state.
    os.environ.update(stage_environment(c))
    client = module("jenkins-client")
    opener = client.client()
    for attempt in range(180):
        with client.request(opener, "/job/production-demo/api/json?tree=inQueue,lastBuild[number,result,building]") as response:
            job = json.load(response)
        build = job.get("lastBuild") or {}
        baseline = int((state / "initial-build-baseline").read_text())
        if build.get("number", 0) > baseline and not build.get("building") and not job["inQueue"]:
            if build["result"] != "SUCCESS":
                raise RuntimeError("Initial GitOps build failed; inspect protected Jenkins logs")
            with client.request(opener, f"/job/production-demo/{build['number']}/artifact/.artifacts/release/gitops-revision") as response:
                expected = response.read().decode().strip()
            module("gitops-ci").wait(c, observer=False, expected=expected)
            with client.request(opener, f"/job/production-demo/{build['number']}/artifact/.artifacts/release/images.json") as response:
                provenance = json.load(response)
            for component in ("frontend", "backend"):
                deployment = get(c, "deployment", "demo-" + component, "demo")
                image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
                if image != f"demo/{component}:{provenance['tag']}":
                    raise RuntimeError("Deployed images do not match CI provenance")
            run(kube(c, "-n", "demo", "rollout", "status", "deployment/demo-frontend", "--timeout=300s"))
            run(kube(c, "-n", "demo", "rollout", "status", "deployment/demo-backend", "--timeout=300s"))
            return
        if attempt % 6 == 0:
            print("Waiting for initial Jenkins build and Argo CD delivery", flush=True)
        time.sleep(10)
    raise RuntimeError("Initial application build timed out")


def monitoring(c):
    require_owned(c)
    os.environ.update(stage_environment(c))
    module("grafana-credentials").main()
    module("monitoring-sync-targets").main()
    community = "https://prometheus-community.github.io/helm-charts"
    helm(c, "monitoring", "kube-prometheus-stack", "monitoring", repository=community,
         version=versions()["MONITORING_CHART_VERSION"], values=[ROOT / "infrastructure/monitoring-values.yaml"])
    helm(c, "monitoring-blackbox", "prometheus-blackbox-exporter", "monitoring", repository=community,
         version=versions()["BLACKBOX_CHART_VERSION"], values=[ROOT / "infrastructure/blackbox-values.yaml"])
    run(kube(c, "wait", "--for=condition=Established", "crd/prometheusrules.monitoring.coreos.com",
             "crd/probes.monitoring.coreos.com", "--timeout=120s"))
    helm(c, "monitoring-resources", ROOT / "charts/monitoring-resources", "monitoring",
         extra=["--set", "gitops.enabled=true"])


def verify(c):
    require_owned(c)
    env = stage_environment(c)
    run(["bash", ROOT / "scripts/smoke-test.sh"], env=env)
    run(["python3", ROOT / "scripts/monitoring-verify.py"], env=env)
    run(["python3", ROOT / "scripts/grafana-verify.py"], env=env)
    print("Isolated GitOps stack verified. Credentials remain in protected local state.")


def destroy(c):
    # Terraform destroy/replacement cannot silently remove either local cluster.
    if os.environ.get("DEMO_ALLOW_DESTROY") != c["cluster_name"]:
        raise SystemExit("Destructive teardown blocked. Back up data, then explicitly set DEMO_ALLOW_DESTROY=" + c["cluster_name"])
    state = require_owned(c)
    compose_file = state / "compose.yaml"
    if compose_file.exists():
        run(["docker", "compose", "-f", compose_file, "-p", c["cluster_name"], "down"])
    run(["kind", "delete", "cluster", "--name", c["cluster_name"]])
    # Keep volumes, protected keys and GitHub deployment history for deliberate recovery.
    print("Removed owned cluster/containers. Volumes, local credentials and GitHub keys/branch retained.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = {name: globals()[name] for name in
               ("preflight", "cluster", "platform", "argocd", "jenkins", "application", "monitoring", "verify", "destroy")}
    parser.add_argument("stage", choices=actions)
    actions[parser.parse_args().stage](load_config())


if __name__ == "__main__":
    main_guard(main)
