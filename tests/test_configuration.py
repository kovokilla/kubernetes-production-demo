from pathlib import Path
import subprocess
import re
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

    def test_controller_has_authentication_and_no_build_executors(self):
        config = yaml.safe_load((ROOT / "jenkins/controller/jenkins.yaml").read_text())
        self.assertEqual(config["jenkins"]["numExecutors"], 0)
        self.assertFalse(config["jenkins"]["securityRealm"]["local"]["allowsSignup"])
        self.assertFalse(config["jenkins"]["authorizationStrategy"]["loggedInUsersCanDoAnything"]["allowAnonymousRead"])
        compose = yaml.safe_load((ROOT / "jenkins/compose.yaml").read_text())
        self.assertEqual(compose["services"]["controller"]["ports"], ["127.0.0.1:8090:8080"])
        self.assertNotIn("/var/run/docker.sock", str(compose["services"]["controller"]["volumes"]))
        self.assertIn("agent-secrets:/opt/agent-secrets:ro", compose["services"]["agent"]["volumes"])
        self.assertNotIn("bootstrap-secrets:/run/secrets:ro", compose["services"]["agent"]["volumes"])

    def test_pipeline_security_and_shell_syntax(self):
        pipeline = (ROOT / "Jenkinsfile").read_text()
        self.assertIn("pollSCM('H/2 * * * *')", pipeline)
        self.assertIn("disableConcurrentBuilds()", pipeline)
        self.assertIn("file(credentialsId: 'kind-deployer'", pipeline)
        self.assertIn("--atomic --wait", pipeline)
        for script in re.findall(r"sh '''(.*?)'''", pipeline, flags=re.S):
            result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_deployer_cannot_list_or_write_application_secrets(self):
        documents = list(yaml.safe_load_all((ROOT / "infrastructure/deployer-rbac.yaml").read_text()))
        role = next(doc for doc in documents if doc["kind"] == "Role")
        secret_rules = [rule for rule in role["rules"] if "secrets" in rule["resources"]]
        self.assertEqual(len(secret_rules), 1)
        self.assertEqual(secret_rules[0]["verbs"], ["get"])
        self.assertEqual(secret_rules[0]["resourceNames"], ["demo-app", "demo-tls"])
