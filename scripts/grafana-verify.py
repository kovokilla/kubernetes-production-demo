#!/usr/bin/env python3
"""Check login protection, provisioned dashboard and panel queries without logging credentials."""
import argparse
from contextlib import ExitStack
import importlib.util
import json
import re
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
from local_http import LocalHTTPClient, UnsafeLocalRequest

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / ".local/share/kubernetes-production-demo/grafana"


def main(base_url=None):
    spec = importlib.util.spec_from_file_location("monitoring_verify", ROOT / "scripts/monitoring-verify.py")
    monitoring = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monitoring)
    password_path = STATE / "admin_password"
    if not password_path.exists():
        raise SystemExit("The protected local Grafana credential is missing; recover it through your secret-management process.")
    password = password_path.read_text().strip()
    with ExitStack() as stack:
        if base_url:
            url = base_url.rstrip("/")
        else:
            url = stack.enter_context(monitoring.forward("monitoring-grafana", 80, "/api/health"))
        desired = json.loads((ROOT / "charts/monitoring-resources/files/dashboard.json").read_text())
        desired_ids = {panel["id"] for panel in desired["panels"]}
        anonymous = LocalHTTPClient(url)
        authenticated = LocalHTTPClient(url, username="admin", password=password)

        def api(path, data=None):
            headers = {}
            payload = None
            if data is not None:
                payload = json.dumps(data).encode()
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(url + path, headers=headers, data=payload)
            with authenticated.open(request, timeout=20) as response:
                return json.load(response)
        with anonymous.open(url + "/login", timeout=20) as response:
            html = response.read().decode()
        assets = set(re.findall(r'(?:src|href)=["\']([^"\']+\.(?:js|css)(?:\?[^"\']*)?)["\']', html))
        checked = 0
        for asset in assets:
            location = urllib.parse.urlparse(urllib.parse.urljoin(url + "/", asset))
            if location.netloc != urllib.parse.urlparse(url).netloc or not location.path.startswith("/public/"):
                continue
            with anonymous.open(urllib.parse.urlunparse(location), timeout=30) as response:
                size = 0
                while chunk := response.read(65536):
                    size += len(chunk)
                if response.status != 200 or not size or "text/html" in response.headers.get("Content-Type", ""):
                    raise RuntimeError(f"Browser asset is unavailable: {location.path}")
            checked += 1
        if not checked:
            raise RuntimeError("No browser JS/CSS assets were verified")
        print(f"Browser JS/CSS assets available: {checked}")
        plugin = api("/api/plugins/timeseries/settings")
        if plugin.get("id") != "timeseries":
            raise RuntimeError("Built-in time-series panel metadata is unavailable")

        try:
            anonymous.open(url + "/api/dashboards/uid/production-demo-overview", timeout=10)
        except urllib.error.HTTPError as error:
            if error.code not in (401, 403):
                raise
        else:
            raise RuntimeError("The dashboard is accessible without authentication")
        for attempt in range(36):
            try:
                dashboard = api("/api/dashboards/uid/production-demo-overview")
                datasource = api("/api/datasources/uid/prometheus")
                if {panel["id"] for panel in dashboard["dashboard"]["panels"]} == desired_ids:
                    break
                time.sleep(5)
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                time.sleep(5)
        else:
            raise RuntimeError("The dashboard/datasource was not provisioned")
        if not dashboard["meta"].get("provisioned"):
            raise RuntimeError("Dashboard is not managed by file provisioning")
        if datasource["type"] != "prometheus" or datasource["access"] != "proxy":
            raise RuntimeError("Unexpected datasource configuration")
        health = api("/api/datasources/uid/prometheus/health")
        if health.get("status") != "OK":
            raise RuntimeError("Grafana cannot reach the Prometheus datasource")
        panels = dashboard["dashboard"]["panels"]
        metric_panels = [panel for panel in panels if panel.get("targets")]
        if len(metric_panels) != 8:
            raise RuntimeError("Expected eight provisioned metric panels")
        count = 0
        for panel in metric_panels:
            browser_queries = []
            for target in panel.get("targets", []):
                result = api("/api/datasources/proxy/uid/prometheus/api/v1/query?" +
                             urllib.parse.urlencode({"query": target["expr"]}))
                if result.get("status") != "success" or not result["data"]["result"]:
                    raise RuntimeError(f"No data or query error in panel: {panel['title']}")
                browser_queries.append({
                    **target, "datasource": panel["datasource"], "format": "time_series",
                    "range": not target.get("instant", False),
                    "intervalMs": 30000, "maxDataPoints": 800,
                })
                count += 1
            now = int(time.time() * 1000)
            browser = api("/api/ds/query", {
                "from": str(now - 6 * 60 * 60 * 1000), "to": str(now), "queries": browser_queries,
            })
            for target in browser_queries:
                result = browser.get("results", {}).get(target["refId"], {})
                if result.get("error") or not result.get("frames"):
                    raise RuntimeError(f"Browser query failed in panel: {panel['title']}")
            print(f"Panel query OK: {panel['title']}")
        print(f"Grafana verified: login required, browser assets available, {len(metric_panels)} metric panels and {count} GET/POST queries working")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Verify an already-running loopback port-forward")
    args = parser.parse_args()
    try:
        main(args.url)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Grafana verification returned HTTP {error.code}; credential contents were not logged.")
    except UnsafeLocalRequest as error:
        raise SystemExit(str(error))
