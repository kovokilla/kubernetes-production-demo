import copy
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gitops_support as support


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = load("gitops-bootstrap")
ci = load("gitops-ci")
CONFIG = {
    "cluster_name": "production-demo-gitops", "repository": "owner/repo",
    "source_branch": "main", "gitops_branch": "gitops/local", "http_port": 18080,
    "https_port": 18443, "jenkins_port": 18090, "allow_github_writes": False,
}


class GitOpsConfigurationTests(unittest.TestCase):
    def test_rebuilt_cluster_refreshes_initial_argocd_password_privately(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path = state / "argocd_admin_password"
            path.write_text("old-fixture")
            secret = {"data": {"password": base64.b64encode(b"new-fixture").decode()}}
            with patch.object(bootstrap, "state_path", return_value=state), \
                    patch.object(bootstrap, "run", return_value=json.dumps(secret)):
                bootstrap.sync_argocd_initial_password(CONFIG)
            self.assertEqual(path.read_text(), "new-fixture")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with patch.object(bootstrap, "state_path", return_value=state), \
                    patch.object(bootstrap, "run", return_value=""):
                bootstrap.sync_argocd_initial_password(CONFIG)
            self.assertEqual(path.read_text(), "new-fixture")

    def test_default_is_isolated(self):
        self.assertEqual(support.validate_config(dict(CONFIG)), CONFIG)
        env = support.stage_environment(CONFIG)
        self.assertEqual(env["DEMO_CONTEXT"], "kind-production-demo-gitops")
        self.assertNotEqual(env["DEMO_HTTP_PORT"], "8080")
        self.assertIn("/production-demo-gitops/", env["KUBECONFIG"])
        self.assertNotIn("jenkins/kubeconfig", env["KUBECONFIG"])

    def test_rejects_legacy_names_ports_and_invalid_branches(self):
        for key, value in [
            ("cluster_name", "production-demo"), ("http_port", 8080),
            ("http_port", 18090), ("source_branch", "gitops/local"),
            ("gitops_branch", "main"), ("gitops_branch", "gitops/../main"),
            ("repository", "owner/repo;touch-file"), ("source_branch", "-unsafe"),
        ]:
            with self.subTest(key=key, value=value):
                c = dict(CONFIG, **{key: value})
                with self.assertRaises(ValueError):
                    support.validate_config(c)

    def test_external_writes_are_gated(self):
        with self.assertRaises(SystemExit), patch.object(bootstrap, "run") as run:
            bootstrap.credentials(CONFIG)
        run.assert_not_called()

    def test_destroy_is_gated_before_any_command(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(bootstrap, "run") as run:
            with self.assertRaises(SystemExit):
                bootstrap.destroy(CONFIG)
        run.assert_not_called()

    def test_no_adoption_without_matching_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(bootstrap, "state_path", return_value=state):
                with self.assertRaises(RuntimeError):
                    bootstrap.require_owned(CONFIG)
                (state / "owner.json").write_text(json.dumps({"cluster_name": "other"}))
                with self.assertRaises(RuntimeError):
                    bootstrap.require_owned(CONFIG)
                (state / "owner.json").write_text(json.dumps(bootstrap.cluster_identity(CONFIG)))
                self.assertEqual(bootstrap.require_owned(CONFIG), state)

    def test_owned_cluster_retry_does_not_recreate(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "owner.json").write_text(json.dumps(bootstrap.cluster_identity(CONFIG)))
            (state / "kubeconfig").write_text("test-placeholder")
            with patch.object(bootstrap, "state_path", return_value=state), \
                    patch.object(bootstrap, "run", side_effect=[CONFIG["cluster_name"], ""]) as run:
                bootstrap.cluster(CONFIG)
            self.assertEqual(run.call_count, 2)
            self.assertIn("export", run.call_args_list[1].args[0])
            self.assertEqual((state / "kubeconfig").stat().st_mode & 0o777, 0o600)

    def test_protected_files_reject_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            target = path / "target"
            target.write_text("unchanged")
            link = path / "link"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                support.save_private(link, "new")
            self.assertEqual(target.read_text(), "unchanged")

    def test_command_errors_do_not_echo_stdin_or_stderr(self):
        result = subprocess.CompletedProcess(["test"], 1, stdout="", stderr="dummy-sensitive-output")
        with patch.object(support.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError) as error:
                support.run(["test"], data="dummy-sensitive-input")
        self.assertNotIn("dummy-sensitive", str(error.exception))

    def test_preflight_stops_before_cluster_creation_or_github_writes(self):
        with patch.object(bootstrap.shutil, "which", return_value="/test/tool"), \
                patch.object(bootstrap, "run", return_value="") as run:
            with self.assertRaises(SystemExit):
                bootstrap.preflight(CONFIG)
        self.assertEqual([call.args[0][:2] for call in run.call_args_list],
                         [["docker", "info"], ["gh", "auth"]])

    def test_compose_isolates_names_and_does_not_mount_admin_config(self):
        compose = bootstrap.compose_document(CONFIG)
        self.assertEqual(compose["services"]["controller"]["ports"], ["127.0.0.1:18090:8080"])
        self.assertEqual(compose["volumes"]["bootstrap-secrets"]["name"], "production-demo-gitops-secrets")
        self.assertIn("production-demo-gitops-agent", compose["services"]["agent"]["networks"]["jenkins"]["aliases"])
        self.assertNotIn("/var/run/docker.sock", str(compose["services"]["controller"]))
        self.assertNotIn("kubeconfig", str(compose))
        self.assertNotIn("gitops_writer_key", str(compose["services"]["agent"]))

    def test_argocd_project_is_application_scoped(self):
        project = yaml.safe_load((ROOT / "infrastructure/gitops-project.yaml").read_text())["spec"]
        self.assertEqual(project["destinations"], [{"server": "https://kubernetes.default.svc", "namespace": "demo"}])
        self.assertEqual(project["clusterResourceWhitelist"], [])
        self.assertNotIn("Secret", [resource["kind"] for resource in project["namespaceResourceWhitelist"]])
        app = yaml.safe_load((ROOT / "infrastructure/gitops-application.yaml").read_text())
        self.assertTrue(app["spec"]["syncPolicy"]["automated"]["selfHeal"])
        self.assertTrue(app["spec"]["syncPolicy"]["automated"]["prune"])

    def test_ci_has_no_deployment_permissions_or_helm_upgrades(self):
        pipeline = (ROOT / "Jenkinsfile.gitops").read_text()
        self.assertNotIn("kind-deployer", pipeline)
        self.assertNotIn("upgrade --install", pipeline)
        self.assertIn("gitops-observer", pipeline)
        jcasc = yaml.safe_load((ROOT / "jenkins/gitops/jenkins.yaml").read_text())
        credentials = jcasc["credentials"]["system"]["domainCredentials"][0]["credentials"]
        ids = [next(iter(credential.values()))["id"] for credential in credentials]
        self.assertNotIn("kind-deployer", ids)
        self.assertIn("gitops-writer", ids)
        self.assertIn("gitops-observer", ids)
        entrypoint = (ROOT / "jenkins/agent/entrypoint.sh").read_text()
        self.assertIn("install -m 600 -o jenkins -g jenkins", entrypoint)
        self.assertEqual(ci.CONFIG_FILE, Path("/home/jenkins/gitops-config.json"))

    def test_exact_successful_gitops_revision_required(self):
        sha = "a" * 40
        app = {"status": {"sync": {"revision": sha, "status": "Synced"}, "health": {"status": "Healthy"},
                          "operationState": {"phase": "Succeeded", "syncResult": {"revision": sha}}}}
        self.assertTrue(ci.synced_healthy(app, sha))
        self.assertFalse(ci.synced_healthy(app, "b" * 40))
        for key, value in [("phase", "Running"), ("phase", "Failed")]:
            broken = copy.deepcopy(app)
            broken["status"]["operationState"][key] = value
            self.assertFalse(ci.synced_healthy(broken, sha))

    def test_image_tags_are_unique_and_validated(self):
        self.assertNotEqual(ci.release_tag("a" * 40, 1), ci.release_tag("a" * 40, 2))
        with self.assertRaises(ValueError):
            ci.release_tag("not-a-commit", 1)

    def test_observer_rbac_is_get_only_for_one_application(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(bootstrap, "require_owned", return_value=Path(directory)), \
                    patch.object(bootstrap, "apply") as apply, \
                    patch.object(bootstrap, "run", side_effect=RuntimeError("stop before key creation")):
                with self.assertRaises(RuntimeError):
                    bootstrap.observer(CONFIG)
            role = apply.call_args.args[1][0]
            self.assertEqual(role["rules"], [{"apiGroups": ["argoproj.io"], "resources": ["applications"],
                                             "resourceNames": ["demo"], "verbs": ["get"]}])

    def test_gitops_input_file_is_not_a_secret_mount_for_ci(self):
        config = bootstrap.compose_document(CONFIG)
        agent = config["services"]["agent"]
        self.assertIn("agent-secrets:/opt/agent-secrets:ro", agent["volumes"])
        self.assertNotIn("bootstrap-secrets:/run/secrets:ro", agent["volumes"])

    def test_legacy_provisioning_is_blocked_in_gitops_mode(self):
        for name in ("bootstrap.sh", "deploy-local.sh", "jenkins-setup.sh", "jenkins-refresh-credentials.sh"):
            with self.subTest(script=name):
                env = dict(support.tool_environment(), DEMO_GITOPS="1",
                           DEMO_CONTEXT="kind-production-demo-gitops")
                result = subprocess.run(["bash", str(ROOT / "scripts" / name)],
                                        env=env, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("legacy provisioning command", result.stderr)

    def test_bootstrap_inputs_have_no_secrets_and_order_is_explicit(self):
        terraform = (ROOT / "terraform/local/main.tf").read_text()
        for name in ("preflight", "cluster", "platform", "argocd", "jenkins", "application", "monitoring", "verification"):
            self.assertIn(f'resource "terraform_data" "{name}"', terraform)
        self.assertNotIn("timestamp()", terraform)
        self.assertNotIn("tls_private_key", terraform)
        self.assertNotIn("kubernetes_secret", terraform)
        self.assertIn('when        = destroy', terraform)


class GitOpsHelmTests(unittest.TestCase):
    def test_legacy_and_gitops_rule_and_dashboard_counts(self):
        for gitops, rules_count, metrics_count in [(False, 10, 12), (True, 7, 11)]:
            with self.subTest(gitops=gitops):
                helm = shutil.which("helm", path=support.tool_environment()["PATH"])
                self.assertIsNotNone(helm)
                command = [helm, "template", "monitoring-resources",
                           str(ROOT / "charts/monitoring-resources"), "--namespace", "monitoring"]
                if gitops:
                    command += ["--set", "gitops.enabled=true"]
                documents = list(yaml.safe_load_all(subprocess.check_output(command, text=True)))
                rules = next(d for d in documents if d["kind"] == "PrometheusRule")["spec"]["groups"][0]["rules"]
                self.assertEqual(len(rules), rules_count)
                names = {r["alert"] for r in rules}
                self.assertEqual("JenkinsCredentialExpiring" in names, not gitops)
                dashboard = next(d for d in documents if d["kind"] == "ConfigMap" and
                                 d["metadata"]["name"] == "production-demo-grafana-dashboard")
                panels = json.loads(dashboard["data"]["production-demo-overview.json"])["panels"]
                self.assertEqual(len([p for p in panels if p.get("targets")]), metrics_count)


@unittest.skipUnless(shutil.which("git"), "Git is required")
class GitOpsPublicationTests(unittest.TestCase):
    def test_publish_is_fast_forward_idempotent_and_environment_scoped(self):
        # A real local bare remote tests Git behavior without any GitHub writes.
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            remote = directory / "remote.git"
            source = directory / "source"
            source.mkdir()
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "test@localhost"], check=True)
            (source / "source.txt").write_text("fixture")
            subprocess.run(["git", "-C", str(source), "add", "source.txt"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "fixture"], check=True)
            sha = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
            shutil.copytree(ROOT / "charts/webapp", source / "charts/webapp")
            release = source / ".artifacts/release"
            release.mkdir(parents=True)
            (release / "images.json").write_text(json.dumps({"commit": sha, "build": "1", "tag": ci.release_tag(sha, 1)}))
            env = support.tool_environment()
            with patch.object(ci, "ROOT", source), patch.object(ci, "repo_url", return_value=str(remote)):
                ci.publish(dict(CONFIG), env=env)
                first = (release / "gitops-revision").read_text()
                ci.publish(dict(CONFIG), env=env)
                self.assertEqual((release / "gitops-revision").read_text(), first)
                (release / "images.json").write_text(json.dumps({"commit": sha, "build": "2", "tag": ci.release_tag(sha, 2)}))
                ci.publish(dict(CONFIG), env=env)
                self.assertNotEqual((release / "gitops-revision").read_text(), first)
                with self.assertRaises(ValueError):
                    ci.publish(dict(CONFIG, cluster_name="production-demo-gitops-other"), env=env)
