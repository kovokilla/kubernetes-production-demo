#!/usr/bin/env python3
"""Authenticated local Jenkins operations. Credential contents are never printed."""
import argparse
import http.client
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
from local_http import LocalHTTPClient, UnsafeLocalRequest

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / ".local/share/kubernetes-production-demo/jenkins"
BASE = "http://127.0.0.1:8090"


def client():
    # Authentication is confined to loopback and never written to logs/arguments.
    password = (STATE / "admin_password").read_text().strip()
    return LocalHTTPClient(BASE, username="admin", password=password, cookies=True)


def request(opener, path, data=None, content_type=None):
    headers = {}
    if data is not None:
        crumb = json.load(opener.open(BASE + "/crumbIssuer/api/json", timeout=15))
        headers[crumb["crumbRequestField"]] = crumb["crumb"]
    if content_type:
        headers["Content-Type"] = content_type
    return opener.open(urllib.request.Request(BASE + path, data=data, headers=headers), timeout=30)


def main(action, number=None):
    opener = client()
    if action == "wait":
        for attempt in range(90):
            try:
                with request(opener, "/api/json") as response:
                    if response.status == 200:
                        print("Authenticated Jenkins API is ready")
                        return
            except (urllib.error.URLError, TimeoutError, http.client.RemoteDisconnected, ConnectionError):
                time.sleep(2)
        raise SystemExit("Jenkins did not become ready; inspect sanitized controller logs.")
    if action == "status":
        with request(opener, "/computer/api/json?tree=computer[displayName,offline,temporarilyOffline]") as response:
            print(json.dumps(json.load(response), indent=2))
        with request(opener, "/job/production-demo/api/json?tree=lastBuild[number,result,building,url],inQueue") as response:
            print(json.dumps(json.load(response), indent=2))
    elif action == "lint":
        boundary = "jenkins-pipeline-check"
        content = (ROOT / "Jenkinsfile").read_bytes()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"jenkinsfile\"\r\n\r\n".encode()
                + content + f"\r\n--{boundary}--\r\n".encode())
        with request(opener, "/pipeline-model-converter/validate", body, f"multipart/form-data; boundary={boundary}") as response:
            result = response.read().decode()
            print(result)
            if "successfully validated" not in result:
                raise SystemExit(1)
    elif action == "bootstrap":
        with request(opener, "/job/production-demo/api/json?tree=lastSuccessfulBuild[number],lastBuild[building],inQueue") as response:
            job = json.load(response)
        if not job.get("lastSuccessfulBuild") and not job["inQueue"] and not (job.get("lastBuild") or {}).get("building"):
            with request(opener, "/job/production-demo/build", b"") as response:
                print(f"Initial SCM baseline build queued: HTTP {response.status}")
        else:
            print("SCM baseline exists or its initial build is already pending")
    elif action in ("build", "reload"):
        endpoint = "/job/production-demo/build" if action == "build" else "/configuration-as-code/reload"
        with request(opener, endpoint, b"") as response:
            print(f"{action}: HTTP {response.status}")
    elif action == "log":
        if not number or not number.isdigit():
            raise SystemExit("log requires a numeric build number")
        with request(opener, f"/job/production-demo/{number}/consoleText") as response:
            text = response.read().decode()
        # Additional local defense in case a plugin reports credential values.
        for name in ("admin_password", "agent_key", "github_deploy_key", "kubeconfig"):
            value = (STATE / name).read_text().strip()
            if value:
                text = text.replace(value, "[REDACTED]")
        config = json.loads((STATE / "kubeconfig").read_text())
        text = text.replace(config["users"][0]["user"]["token"], "[REDACTED]")
        print(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["wait", "status", "lint", "bootstrap", "build", "reload", "log"])
    parser.add_argument("number", nargs="?")
    args = parser.parse_args()
    try:
        main(args.action, args.number)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Jenkins API returned HTTP {error.code}; no credential contents were logged.")
    except UnsafeLocalRequest as error:
        raise SystemExit(str(error))
