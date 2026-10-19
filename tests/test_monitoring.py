from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class MonitoringTests(unittest.TestCase):
    def test_small_stack_and_real_local_receiver(self):
        values = yaml.safe_load((ROOT / "infrastructure/monitoring-values.yaml").read_text())
        self.assertFalse(values["grafana"]["enabled"])
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
