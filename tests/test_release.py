import importlib.util
import json
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
        for relative in ("environments/jenkins/values.yaml", "charts/webapp/Chart.yaml", "charts/webapp/values.yaml"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((ROOT / relative).read_text())
        self.ids = {name: "sha256:" + "a" * 64 for name in ("frontend", "backend")}

    def test_release_packages_both_unique_local_images(self):
        release.update_release(self.root, "b" * 40, "42", self.ids)
        values = yaml.safe_load((self.root / ".artifacts/release/values.yaml").read_text())
        chart = yaml.safe_load((self.root / "charts/webapp/Chart.yaml").read_text())
        defaults = yaml.safe_load((self.root / "charts/webapp/values.yaml").read_text())
        self.assertFalse(values["ingress"]["tls"]["enabled"])
        self.assertEqual(chart["version"], "0.2.42")
        self.assertEqual(chart["appVersion"], "b" * 40)
        for component in ("frontend", "backend"):
            self.assertEqual(values[component]["image"]["repository"], f"demo/{component}")
            self.assertEqual(values[component]["image"]["tag"], "sha-" + "b" * 40 + "-build-42")
            self.assertEqual(defaults[component]["image"]["digest"], "")
            self.assertEqual(defaults[component]["image"]["pullPolicy"], "Never")
        images = json.loads((self.root / ".artifacts/release/images.json").read_text())
        self.assertEqual(images["imageIds"], self.ids)

    def test_invalid_image_id_does_not_change_files(self):
        target = self.root / "charts/webapp/values.yaml"
        original = target.read_text()
        self.ids["backend"] = "not-an-image"
        with self.assertRaises(ValueError):
            release.update_release(self.root, "b" * 40, "42", self.ids)
        self.assertEqual(target.read_text(), original)

    def test_rejects_invalid_release_parameters(self):
        for sha, number in [
            ("short", "42"),
            ("b" * 40, "0"),
        ]:
            with self.assertRaises(ValueError):
                release.update_release(self.root, sha, number, self.ids)
