#!/usr/bin/env python3
"""Provision local Jenkins credentials without printing or archiving their values."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / ".local/share/kubernetes-production-demo/jenkins"
KUBECTL = str(ROOT / ".tools/kubectl") if (ROOT / ".tools/kubectl").exists() else "kubectl"
CONTEXT = "kind-production-demo"


def capture(command):
    return subprocess.check_output(command, text=True).strip()


def save(name, content):
    path = STATE / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(content)
    path.chmod(0o600)


def sync_volume(volume, names, uid):
    subprocess.run(["docker", "volume", "create", volume], check=True, stdout=subprocess.DEVNULL)
    payload = {name: base64.b64encode((STATE / name).read_bytes()).decode() for name in names}
    script = """
import base64,json,os
from pathlib import Path
payload=json.load(__import__('sys').stdin)
for name,content in payload.items():
    path=Path('/secrets')/name
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o400)
    with os.fdopen(fd,'wb') as output:
        output.write(base64.b64decode(content))
    os.chown(path,UID,UID)
    path.chmod(0o400)
""".replace("UID", str(uid))
    subprocess.run([
        "docker", "run", "--rm", "-i", "--user", "0", "--entrypoint", "python3",
        "-v", f"{volume}:/secrets", "production-demo/jenkins-agent:local", "-c", script,
    ], input=json.dumps(payload), text=True, check=True, stdout=subprocess.DEVNULL)


def provision(refresh_only=False):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    if not refresh_only:
        if not (STATE / "admin_password").exists():
            save("admin_password", secrets.token_urlsafe(32))
        for name, key_type, bits in [
            ("github_deploy_key", "ed25519", None),
            ("agent_key", "rsa", "3072"),
            ("agent_host_key", "ecdsa", "256"),
        ]:
            if not (STATE / name).exists():
                command = ["ssh-keygen", "-q", "-t", key_type, "-N", "", "-f", str(STATE / name)]
                if bits:
                    command += ["-b", bits]
                if name == "agent_key":
                    command += ["-m", "PEM"]
                subprocess.run(command, check=True)
        save("agent_key_public", (STATE / "agent_key.pub").read_text())
        save("agent_host_key_public", (STATE / "agent_host_key.pub").read_text())
        keys = json.loads(capture(["gh", "api", "repos/kovokilla/kubernetes-production-demo/keys"]))
        public_key = (STATE / "github_deploy_key.pub").read_text().strip()
        if not any(item["key"].split()[:2] == public_key.split()[:2] for item in keys):
            result = subprocess.check_output(
                ["gh", "api", "--method", "POST", "repos/kovokilla/kubernetes-production-demo/keys", "--input", "-"],
                input=json.dumps({"title": "Jenkins read-only checkout", "key": public_key, "read_only": True}),
                text=True)
            key_id = json.loads(result)["id"]
            save("github_deploy_key_id", str(key_id))
        meta = json.loads(capture(["gh", "api", "meta"]))
        save("github_known_hosts", "".join(f"github.com {key}\n" for key in meta["ssh_keys"]))

    token = os.environ["KUBE_DEPLOY_TOKEN"]
    ca = json.loads(capture([KUBECTL, "--context", CONTEXT, "-n", "demo",
                            "get", "configmap", "kube-root-ca.crt", "-o", "json"]))["data"]["ca.crt"]
    document = {
        "apiVersion": "v1", "kind": "Config",
        "clusters": [{"name": "production-demo", "cluster": {
            "server": "https://production-demo-control-plane:6443",
            "certificate-authority-data": base64.b64encode(ca.encode()).decode(),
        }}],
        "users": [{"name": "jenkins-deployer", "user": {"token": token}}],
        "contexts": [{"name": CONTEXT, "context": {
            "cluster": "production-demo", "user": "jenkins-deployer", "namespace": "demo",
        }}],
        "current-context": CONTEXT,
    }
    save("kubeconfig", json.dumps(document))
    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "==="))
    expiration = datetime.fromtimestamp(claims["exp"], timezone.utc).isoformat()
    save("credential-expiry", expiration + "\n")
    sync_volume("production-demo-jenkins-secrets", [
        "admin_password", "agent_key", "agent_host_key_public",
        "github_deploy_key", "github_known_hosts", "kubeconfig",
    ], 1000)
    if not refresh_only:
        sync_volume("production-demo-jenkins-agent-secrets", [
            "agent_host_key", "agent_host_key_public", "agent_key_public", "github_known_hosts",
        ], 0)
    print(f"Restricted Jenkins Kubernetes credential expires: {expiration}")
    print(f"Protected local state: {STATE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-only", action="store_true")
    args = parser.parse_args()
    provision(args.refresh_only)
