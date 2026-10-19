#!/usr/bin/env python3
"""Verify monitoring and test local alert delivery without disrupting workloads."""
from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
KUBECTL = str(ROOT / ".tools/kubectl") if (ROOT / ".tools/kubectl").exists() else "kubectl"
BASE = [KUBECTL, "--context", "kind-production-demo", "-n", "monitoring"]


def get(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode()


@contextmanager
def forward(service, remote_port, ready_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        local_port = sock.getsockname()[1]
    process = subprocess.Popen(
        BASE + ["port-forward", "--address", "127.0.0.1",
                f"service/{service}", f"{local_port}:{remote_port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{local_port}"
    try:
        for attempt in range(30):
            if process.poll() is not None:
                raise RuntimeError(f"Port-forward failed for {service}")
            try:
                get(url + ready_path)
                break
            except (OSError, TimeoutError):
                time.sleep(1)
        else:
            raise RuntimeError(f"{service} did not become reachable")
        yield url
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def query(prometheus, expression):
    result = json.loads(get(prometheus + "/api/v1/query?" + urllib.parse.urlencode({"query": expression})))
    if result["status"] != "success":
        raise RuntimeError("Prometheus query failed")
    return result["data"]["result"]


def notifications(receiver):
    metrics = get(receiver + "/metrics")
    match = re.search(r'^monitoring_test_alert_notifications_total\{status="firing"\} (\d+)$', metrics, re.M)
    if not match:
        raise RuntimeError("Receiver delivery-test counter is missing")
    return int(match.group(1))


def main():
    with ExitStack() as stack:
        prometheus = stack.enter_context(forward("monitoring-prometheus", 9090, "/-/ready"))
        alertmanager = stack.enter_context(forward("monitoring-alertmanager", 9093, "/-/ready"))
        receiver = stack.enter_context(forward("monitoring-receiver", 8080, "/healthz"))
        expected = {"application-api", "jenkins-controller", "jenkins-agent-ssh"}
        for attempt in range(24):
            probes = query(prometheus, "probe_success{monitor_target=~\"application-api|jenkins-controller|jenkins-agent-ssh\"}")
            healthy = {item["metric"]["monitor_target"] for item in probes if item["value"][1] == "1"}
            targets = json.loads(get(prometheus + "/api/v1/targets"))["data"]["activeTargets"]
            down = [target for target in targets if target["health"] != "up"]
            if healthy == expected and targets and not down:
                break
            if attempt % 6 == 0:
                print(f"Waiting for probes/scrapes: healthy={sorted(healthy)}, down={len(down)}", flush=True)
            time.sleep(5)
        else:
            for target in down:
                print(json.dumps({"job": target.get("labels", {}).get("job"), "error": target.get("lastError", "")[:300]}))
            raise RuntimeError(f"Not all monitored targets are healthy: {sorted(healthy)}")
        print(f"All {len(targets)} active scrape targets and all three availability probes are healthy")
        groups = json.loads(get(prometheus + "/api/v1/rules?type=alert"))["data"]["groups"]
        rules = [rule for group in groups for rule in group["rules"] if group["name"] == "application-and-jenkins"]
        if len(rules) != 10 or any(rule.get("health") != "ok" for rule in rules):
            raise RuntimeError("Expected ten healthy application/Jenkins alert rules")
        managers = json.loads(get(prometheus + "/api/v1/alertmanagers"))["data"]["activeAlertmanagers"]
        if not managers:
            raise RuntimeError("Prometheus has no active Alertmanager connection")
        expiry = query(prometheus, "jenkins_deployer_credential_expiry_timestamp_seconds")
        if not expiry or float(expiry[0]["value"][1]) <= 0:
            raise RuntimeError("Public credential-expiry metadata is missing")
        print("Ten alert rules loaded; Alertmanager connected; public expiry gauge present")
        baseline = notifications(receiver)
        test_rule = {
            "apiVersion": "monitoring.coreos.com/v1", "kind": "PrometheusRule",
            "metadata": {"name": "monitoring-smoke-test", "namespace": "monitoring",
                         "labels": {"release": "monitoring"}},
            "spec": {"groups": [{"name": "delivery-smoke-test", "rules": [{
                "alert": "MonitoringSmokeTest", "expr": "vector(1)", "for": "0s",
                "labels": {"severity": "info", "namespace": "monitoring"},
                "annotations": {"summary": "Temporary local notification delivery test"},
            }]}]},
        }
        created = False
        try:
            subprocess.run(BASE + ["create", "-f", "-"], input=json.dumps(test_rule), text=True, check=True)
            created = True
            for attempt in range(36):
                if notifications(receiver) > baseline:
                    break
                time.sleep(5)
            else:
                raise RuntimeError("Synthetic alert did not reach the local receiver")
            alerts = json.loads(get(alertmanager + "/api/v2/alerts"))
            if not any(alert.get("labels", {}).get("alertname") == "MonitoringSmokeTest" for alert in alerts):
                raise RuntimeError("Alertmanager did not expose the synthetic alert")
            print("Verified: Prometheus → Alertmanager → local webhook receiver", flush=True)
        finally:
            if created:
                subprocess.run(BASE + ["delete", "prometheusrule", "monitoring-smoke-test", "--wait=true"],
                               check=True, stdout=subprocess.DEVNULL)
                print("Removed synthetic test rule; no application/Jenkins outage was induced")


if __name__ == "__main__":
    main()
