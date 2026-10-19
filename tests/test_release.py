import importlib.util
from pathlib import Path
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release", ROOT / "scripts/update-release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for relative in ("environments/production/values.yaml", "charts/webapp/Chart.yaml", "charts/webapp/values.yaml"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((ROOT / relative).read_text())
        for component in ("frontend", "backend"):
            (self.root / f"{component}.digest").write_text("sha256:" + "a" * 64)

    def test_release_preserves_tls_and_pins_both_digests(self):
        release.update_release(self.root, "Example/Project", "b" * 40, "42", self.root)
        values = yaml.safe_load((self.root / "environments/production/values.yaml").read_text())
        chart = yaml.safe_load((self.root / "charts/webapp/Chart.yaml").read_text())
        defaults = yaml.safe_load((self.root / "charts/webapp/values.yaml").read_text())
        self.assertTrue(values["ingress"]["tls"]["enabled"])
        self.assertEqual(chart["version"], "0.1.42")
        self.assertEqual(chart["appVersion"], "b" * 40)
        for component in ("frontend", "backend"):
            self.assertEqual(values[component]["image"]["repository"], f"ghcr.io/example/project/{component}")
            self.assertEqual(values[component]["image"]["digest"], "sha256:" + "a" * 64)
            self.assertEqual(defaults[component]["image"]["digest"], "sha256:" + "a" * 64)
            self.assertEqual(defaults[component]["image"]["pullPolicy"], "IfNotPresent")

    def test_invalid_digest_does_not_change_files(self):
        target = self.root / "environments/production/values.yaml"
        original = target.read_text()
        (self.root / "backend.digest").write_text("not-a-digest")
        with self.assertRaises(ValueError):
            release.update_release(self.root, "owner/repo", "b" * 40, "42", self.root)
        self.assertEqual(target.read_text(), original)

    def test_rejects_invalid_release_parameters(self):
        for repository, sha, number in [
            ("bad", "b" * 40, "42"),
            ("owner/repo", "short", "42"),
            ("owner/repo", "b" * 40, "0"),
        ]:
            with self.assertRaises(ValueError):
                release.update_release(self.root, repository, sha, number, self.root)
