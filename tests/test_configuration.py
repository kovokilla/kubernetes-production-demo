from pathlib import Path
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationTests(unittest.TestCase):
    def test_only_application_and_ingress_namespaces(self):
        documents = list(yaml.safe_load_all((ROOT / "infrastructure/namespaces.yaml").read_text()))
        self.assertEqual([doc["metadata"]["name"] for doc in documents], ["demo", "ingress"])
        self.assertEqual(documents[0]["metadata"]["labels"]["pod-security.kubernetes.io/enforce"], "restricted")

    def test_local_http_and_production_tls_are_deliberate(self):
        local = yaml.safe_load((ROOT / "environments/local/values.yaml").read_text())
        production = yaml.safe_load((ROOT / "environments/production/values.yaml").read_text())
        self.assertFalse(local["ingress"]["tls"]["enabled"])
        self.assertTrue(production["ingress"]["tls"]["enabled"])
        self.assertEqual(local["frontend"]["image"]["pullPolicy"], "Never")
        self.assertEqual(local["backend"]["image"]["pullPolicy"], "Never")

    def test_workflow_limits_credentials_to_optional_deployment(self):
        # BaseLoader preserves GitHub's YAML 1.2 `on` key as text.
        workflow = yaml.load((ROOT / ".github/workflows/ci-cd.yaml").read_text(), Loader=yaml.BaseLoader)
        self.assertIn("on", workflow)
        self.assertIn("pull_request", workflow["jobs"]["build"]["if"])
        self.assertEqual(workflow["permissions"]["contents"], "read")
        self.assertEqual(workflow["jobs"]["release"]["permissions"]["contents"], "read")
        self.assertIn("ENABLE_DEPLOYMENT", workflow["jobs"]["deploy"]["if"])
        self.assertEqual(workflow["jobs"]["deploy"]["environment"], "production")
        for name in ("validate", "build", "release"):
            self.assertNotIn("KUBE_CONFIG_DATA", yaml.safe_dump(workflow["jobs"][name]))

    def test_workflow_shell_syntax(self):
        workflow = yaml.load((ROOT / ".github/workflows/ci-cd.yaml").read_text(), Loader=yaml.BaseLoader)
        for name, job in workflow["jobs"].items():
            for step in job["steps"]:
                if "run" in step:
                    with self.subTest(job=name, step=step.get("name", step["run"][:40])):
                        result = subprocess.run(["bash", "-n"], input=step["run"], text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)

    def test_deployer_cannot_list_or_write_application_secrets(self):
        documents = list(yaml.safe_load_all((ROOT / "infrastructure/deployer-rbac.yaml").read_text()))
        role = next(doc for doc in documents if doc["kind"] == "Role")
        secret_rules = [rule for rule in role["rules"] if "secrets" in rule["resources"]]
        self.assertEqual(len(secret_rules), 1)
        self.assertEqual(secret_rules[0]["verbs"], ["get"])
        self.assertEqual(secret_rules[0]["resourceNames"], ["demo-app", "demo-tls"])
