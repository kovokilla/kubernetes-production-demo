from pathlib import Path
import json
import importlib.util
import sys
import unittest
from unittest.mock import Mock, patch
import yaml

ROOT = Path(__file__).resolve().parents[1]


class MonitoringTests(unittest.TestCase):
    def test_fresh_node_rates_wait_for_all_nodes_without_faking_zero(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        spec = importlib.util.spec_from_file_location("grafana_verifier", ROOT / "scripts/grafana-verify.py")
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        def result(names):
            return {"status": "success", "data": {"result": [
                {"metric": {"nodename": name}, "value": [0, "12.5"]} for name in names]}}
        ready = result(["a", "b", "c"])
        query = Mock(side_effect=[result(["a"]), ready])
        with patch.object(verifier.time, "sleep"):
            self.assertIs(verifier.wait_for_node_series(query, {"a", "b", "c"}), ready)
            with self.assertRaises(RuntimeError):
                verifier.wait_for_node_series(lambda: result(["a"]), {"a", "b", "c"}, attempts=2)

    def test_grafana_has_no_unscoped_authenticated_opener(self):
        source = (ROOT / "scripts/grafana-verify.py").read_text()
        self.assertIn('LocalHTTPClient(url, username="admin", password=password)', source)
        self.assertNotIn("urllib.request.urlopen(", source)

    def test_small_stack_and_real_local_receiver(self):
        values = yaml.safe_load((ROOT / "infrastructure/monitoring-values.yaml").read_text())
        self.assertTrue(values["grafana"]["enabled"])
        self.assertFalse(values["defaultRules"]["create"])
        self.assertFalse(values["prometheusOperator"]["admissionWebhooks"]["enabled"])
        self.assertFalse(values["prometheusOperator"]["tls"]["enabled"])
        spec = values["prometheus"]["prometheusSpec"]
        self.assertEqual(spec["retention"], "24h")
        self.assertEqual(spec["resources"]["limits"]["memory"], "768Mi")
        config = values["alertmanager"]["config"]
        self.assertEqual(config["route"]["receiver"], "local-webhook")
        self.assertEqual(config["route"]["routes"], [])
        url = config["receivers"][0]["webhook_configs"][0]["url"]
        self.assertEqual(url, "http://monitoring-receiver.monitoring.svc:8080/alerts")

    def test_alerts_cover_app_jenkins_and_public_expiry(self):
        data = yaml.safe_load((ROOT / "charts/monitoring-resources/files/alerts.yaml").read_text())
        rules = data["groups"][0]["rules"]
        names = {rule["alert"] for rule in rules}
        self.assertEqual(len(names), len(rules))
        self.assertTrue({
            "DemoUnavailableReplicas", "DemoAPIUnavailable", "JenkinsControllerUnavailable",
            "JenkinsAgentSSHUnavailable", "JenkinsCredentialExpiring", "JenkinsCredentialExpiringSoon",
        } <= names)
        for rule in rules:
            self.assertIn(rule["labels"]["severity"], ["warning", "critical"])
            self.assertTrue(rule["for"])
            self.assertTrue(rule["annotations"]["description"])

    def test_blackbox_uses_no_jenkins_credentials(self):
        modules = yaml.safe_load((ROOT / "infrastructure/blackbox-values.yaml").read_text())["config"]["modules"]
        self.assertEqual(modules["application_api"]["http"]["headers"]["Host"], "demo.localhost")
        self.assertEqual(modules["jenkins_http"]["http"]["valid_status_codes"], [200])
        self.assertNotIn("authorization", str(modules).lower())
        source = (ROOT / "scripts/monitoring-sync-targets.py").read_text()
        self.assertIn('jenkins/credential-expiry"', source)
        self.assertNotIn('STATE / "kubeconfig"', source)

    def test_grafana_is_private_bounded_and_secret_backed(self):
        values = yaml.safe_load((ROOT / "infrastructure/monitoring-values.yaml").read_text())["grafana"]
        self.assertFalse(values["defaultDashboardsEnabled"])
        self.assertEqual(values["admin"]["existingSecret"], "grafana-admin")
        self.assertEqual(values["service"]["type"], "ClusterIP")
        self.assertFalse(values["ingress"]["enabled"])
        self.assertFalse(values["grafana.ini"]["auth.anonymous"]["enabled"])
        self.assertFalse(values["grafana.ini"]["users"]["allow_sign_up"])
        self.assertTrue(values["rbac"]["namespaced"])
        self.assertEqual(values["persistence"]["size"], "1Gi")
        self.assertEqual(values["deploymentStrategy"]["type"], "Recreate")
        self.assertEqual(values["sidecar"]["resources"]["limits"]["memory"], "128Mi")
        self.assertEqual(values["resources"]["limits"]["memory"], "768Mi")
        self.assertEqual(values["env"]["GOMEMLIMIT"], "512MiB")
        for kind in ("dashboards", "datasources"):
            self.assertEqual(values["sidecar"][kind]["searchNamespace"], "monitoring")

    def test_dashboard_uses_existing_datasource_and_core_panels(self):
        dashboard = json.loads((ROOT / "charts/monitoring-resources/files/dashboard.json").read_text())
        self.assertEqual(dashboard["uid"], "production-demo-overview")
        self.assertFalse(dashboard["editable"])
        self.assertEqual(len(dashboard["panels"]), 13)
        self.assertEqual(len({panel["id"] for panel in dashboard["panels"]}), 13)
        queries = []
        for panel in dashboard["panels"]:
            self.assertIn(panel["type"], ["stat", "timeseries", "text"])
            if panel["type"] == "text":
                self.assertIn("Do not sum", panel["options"]["content"])
                continue
            self.assertEqual(panel["datasource"]["uid"], "prometheus")
            queries.extend(target["expr"] for target in panel["targets"])
        combined = " ".join(queries)
        for metric in ("probe_success", "probe_duration_seconds", "kube_deployment_spec_replicas",
                       "kube_pod_container_status_restarts_total",
                       "jenkins_deployer_credential_expiry_timestamp_seconds"):
            self.assertIn(metric, combined)
        self.assertNotIn("jenkins_job", combined)

    def test_node_panels_have_readable_labels_and_shared_vm_warning(self):
        dashboard = json.loads((ROOT / "charts/monitoring-resources/files/dashboard.json").read_text())
        panels = {panel["id"]: panel for panel in dashboard["panels"]}
        self.assertIn("same underlying", panels[9]["options"]["content"])
        self.assertIn('condition="Ready"', panels[10]["targets"][0]["expr"])
        health = panels[11]["targets"][0]["expr"]
        self.assertIn("kube_pod_info", health)
        self.assertNotIn("node_uname_info", health)
        for identity in (10, 11):
            self.assertEqual(panels[identity]["targets"][0]["legendFormat"], "{{node}}")
            self.assertEqual(panels[identity]["fieldConfig"]["defaults"]["noValue"], "Unknown / missing")
        for identity in (12, 13):
            self.assertEqual(panels[identity]["targets"][0]["legendFormat"], "{{nodename}}")
            self.assertEqual(panels[identity]["fieldConfig"]["defaults"]["unit"], "percent")
            self.assertEqual(panels[identity]["fieldConfig"]["defaults"]["max"], 100)
