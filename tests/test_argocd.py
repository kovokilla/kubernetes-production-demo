import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
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


setup = load("argocd-setup")
ci = load("gitops-ci")
CONFIG = setup.configuration()


class ArgoMigrationTests(unittest.TestCase):
    def test_targets_only_existing_cluster_and_ports(self):
        self.assertEqual(CONFIG["cluster_name"], "production-demo")
        self.assertEqual([CONFIG[k] for k in ("http_port", "https_port", "jenkins_port")], [8080, 8443, 8090])
        for key, value in [("cluster_name", "production-demo-gitops"), ("http_port", 18080),
                           ("gitops_branch", "main"), ("source_branch", "gitops/production-demo"),
                           ("gitops_branch", "gitops/../main")]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    support.validate_config(dict(CONFIG, **{key: value}))

    def test_handoff_requires_explicit_external_write_flag(self):
        with patch.object(setup, "source_is_published") as preflight, patch.object(setup, "run") as run:
            with self.assertRaises(SystemExit):
                setup.migrate(CONFIG, False)
        preflight.assert_not_called()
        run.assert_not_called()

    def test_unpublished_source_cannot_pause_jenkins(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(setup, "ARGO_STATE", Path(directory)), \
                patch.object(setup, "source_is_published", side_effect=ValueError("not published")), \
                patch.object(setup, "reuse_credentials") as reuse, patch.object(setup, "load") as load:
            with self.assertRaises(ValueError):
                setup.migrate(CONFIG, True)
            reuse.assert_not_called()
            load.assert_not_called()

    def test_install_does_not_adopt_workloads_or_change_jenkins(self):
        with tempfile.TemporaryDirectory() as directory:
            def command(args, **kwargs):
                if args[:3] == ["gh", "api", "meta"]:
                    return json.dumps({"ssh_keys": ["ssh-ed25519 public-test-host-key"]})
                if "argocd-initial-admin-secret" in args:
                    return json.dumps({"data": {"password": base64.b64encode(b"fixture").decode()}})
                return ""
            with patch.object(setup, "ARGO_STATE", Path(directory)), \
                    patch.object(setup, "run", side_effect=command) as run, \
                    patch.object(setup, "apply") as apply, patch.object(setup, "helm") as helm:
                setup.install(CONFIG)
            self.assertEqual([item["kind"] for item in apply.call_args.args[1]], ["Namespace"])
            self.assertEqual(helm.call_args.args[:4], (CONFIG, "argocd", "argo-cd", "argocd"))
            self.assertFalse(any("docker" == call.args[0][0] for call in run.call_args_list))
            self.assertEqual((Path(directory) / "admin_password").stat().st_mode & 0o777, 0o600)

    def test_adoption_rejects_any_workload_spec_change(self):
        desired = {"apiVersion": "apps/v1", "kind": "Deployment",
                   "metadata": {"name": "demo-frontend"}, "spec": {"replicas": 2}}
        live = dict(desired)
        manifest = yaml.safe_dump(desired)
        with patch.object(setup, "get", return_value=live), \
                patch.object(setup, "run", return_value=json.dumps(desired)):
            setup.check_live_equivalence(CONFIG, manifest)
        simulated = dict(desired, spec={"replicas": 3})
        with patch.object(setup, "get", return_value=live), \
                patch.object(setup, "run", return_value=json.dumps(simulated)):
            with self.assertRaises(ValueError):
                setup.check_live_equivalence(CONFIG, manifest)

    def test_adoption_rejects_secret_manifests(self):
        secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "demo-app"}}
        with patch.object(setup, "get") as get:
            with self.assertRaises(ValueError):
                setup.check_live_equivalence(CONFIG, yaml.safe_dump(secret))
            get.assert_not_called()

    def test_safe_chart_extraction_rejects_links_and_traversal(self):
        for name, kind in [("../escape", tarfile.REGTYPE), ("/webapp/file", tarfile.REGTYPE),
                           ("webapp/link", tarfile.SYMTYPE), ("webapp/hardlink", tarfile.LNKTYPE)]:
            payload = io.BytesIO()
            with tarfile.open(fileobj=payload, mode="w:gz") as archive:
                entry = tarfile.TarInfo(name)
                entry.type = kind
                archive.addfile(entry)
            with tempfile.TemporaryDirectory() as directory, self.subTest(name=name):
                with self.assertRaises(ValueError):
                    setup.extract_chart(payload.getvalue(), Path(directory))

    def test_no_cluster_or_cni_lifecycle_operations(self):
        source = (ROOT / "scripts/argocd-setup.py").read_text()
        self.assertNotIn("kind create", source)
        self.assertNotIn("kind delete", source)
        self.assertNotIn("calico.yaml", source)
        self.assertNotIn("helm uninstall", source)
        self.assertFalse((ROOT / "terraform").exists())

    def test_same_jenkins_init_filename_replaces_legacy_job(self):
        dockerfile = (ROOT / "jenkins/gitops/Dockerfile").read_text()
        self.assertIn("init.groovy.d/production-demo.groovy.override", dockerfile)
        self.assertNotIn("init.groovy.d/gitops.groovy", dockerfile)
        override = yaml.safe_load((ROOT / "jenkins/compose.argocd.yaml").read_text())
        self.assertNotIn("volumes", override)
        self.assertNotIn("ports", override["services"]["controller"])
        pipeline = (ROOT / "Jenkinsfile.gitops").read_text()
        self.assertNotIn("kind-deployer", pipeline)
        self.assertNotIn("upgrade --install", pipeline)

    def test_project_and_observer_are_restricted(self):
        project = yaml.safe_load((ROOT / "infrastructure/gitops-project.yaml").read_text())["spec"]
        self.assertEqual(project["destinations"][0]["namespace"], "demo")
        self.assertEqual(project["clusterResourceWhitelist"], [])
        self.assertNotIn("Secret", [item["kind"] for item in project["namespaceResourceWhitelist"]])
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(setup, "JENKINS_STATE", Path(directory)), \
                patch.object(setup, "apply") as apply, \
                patch.object(setup, "run", side_effect=RuntimeError("stop before signing")):
            with self.assertRaises(RuntimeError):
                setup.observer(CONFIG)
            role = apply.call_args.args[1][0]
            self.assertEqual(role["rules"][0]["verbs"], ["get"])
            self.assertEqual(role["rules"][0]["resourceNames"], ["demo"])

    def test_renewal_uses_recorded_source_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "ownership.json"
            expected = dict(CONFIG, source_branch="feat/argocd")
            marker.write_text(json.dumps(expected))
            with patch.object(setup, "ARGO_STATE", Path(directory)), \
                    patch.object(sys, "argv", ["argocd-setup.py", "renew-observer"]), \
                    patch.object(setup, "renew_observer") as renew:
                setup.main()
                renew.assert_called_once_with(expected)

    def test_partial_handoff_can_select_rollback_before_active_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = dict(CONFIG, source_branch="feat/argocd")
            (Path(directory) / "handoff.json").write_text(json.dumps({"config": expected, "phase": "checked"}))
            with patch.object(setup, "ARGO_STATE", Path(directory)), \
                    patch.object(sys, "argv", ["argocd-setup.py", "rollback"]), \
                    patch.object(setup, "rollback") as rollback:
                setup.main()
                rollback.assert_called_once_with(expected)

    def test_legacy_setup_is_blocked_after_adoption(self):
        with tempfile.TemporaryDirectory() as home:
            marker = Path(home) / ".local/share/kubernetes-production-demo/argocd/ownership.json"
            marker.parent.mkdir(parents=True)
            marker.write_text("{}")
            for name in ("jenkins-setup.sh", "jenkins-refresh-credentials.sh", "deploy-local.sh"):
                result = subprocess.run(["bash", str(ROOT / "scripts" / name)],
                                        env=dict(os.environ, HOME=home), capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Argo CD owns deployment", result.stderr)

    def test_monitoring_modes_preserve_legacy_and_remove_obsolete_expiry(self):
        helm = shutil.which("helm", path=support.tool_environment()["PATH"])
        for enabled, count in [(False, 10), (True, 7)]:
            command = [helm, "template", "monitoring-resources", str(ROOT / "charts/monitoring-resources")]
            if enabled:
                command += ["--set", "gitops.enabled=true"]
            docs = list(yaml.safe_load_all(subprocess.check_output(command, text=True)))
            rules = next(doc for doc in docs if doc["kind"] == "PrometheusRule")["spec"]["groups"][0]["rules"]
            self.assertEqual(len(rules), count)


class PublicationTests(unittest.TestCase):
    def test_exact_live_snapshot_can_seed_gitops_without_matching_local_head(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            remote = directory / "remote.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            snapshot = directory / "snapshot"
            shutil.copytree(ROOT / "charts/webapp", snapshot / "charts/webapp")
            release = snapshot / ".artifacts/release"
            release.mkdir(parents=True)
            sha = "a" * 40
            (release / "images.json").write_text(json.dumps({"commit": sha, "build": "19", "tag": ci.release_tag(sha, 19)}))
            with patch.object(ci, "repo_url", return_value=str(remote)):
                first = ci.publish(CONFIG, support.tool_environment(), release_root=snapshot, expected_source=sha)
                second = ci.publish(CONFIG, support.tool_environment(), release_root=snapshot, expected_source=sha)
                self.assertEqual(first, second)
                with self.assertRaises(ValueError):
                    ci.publish(CONFIG, support.tool_environment(), release_root=snapshot, expected_source="b" * 40)
            message = subprocess.check_output(["git", "--git-dir", str(remote), "log", "-1", "--format=%B",
                                               "refs/heads/gitops/production-demo"], text=True)
            self.assertIn("Co-Authored-By: Warp <agent@warp.dev>", message)
