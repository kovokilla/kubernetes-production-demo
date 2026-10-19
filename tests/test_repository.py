from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RepositoryTests(unittest.TestCase):
    def test_readme_script_references_exist(self):
        readme = (ROOT / "README.md").read_text()
        references = set(re.findall(r"\bscripts/[\w.-]+\.(?:py|sh)\b", readme))
        self.assertTrue(references)
        for reference in references:
            with self.subTest(reference=reference):
                self.assertTrue((ROOT / reference).is_file())

    def test_readme_navigation_matches_headings(self):
        readme = (ROOT / "README.md").read_text()
        headings = re.findall(r"^#{1,6} (.+)$", readme, flags=re.M)
        anchors = {re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-") for heading in headings}
        for anchor in re.findall(r"\]\(#([^)]+)\)", readme):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, anchors)

    @unittest.skipUnless((ROOT / ".git").exists() and shutil.which("git"), "Requires a Git checkout")
    def test_git_ignores_credentials_and_generated_files(self):
        paths = {
            ".env", ".env.production", ".secrets/token", "development.kubeconfig",
            "kubeconfig", "jenkins/nested/kubeconfig", ".tools/helm",
            ".artifacts/release/values.yaml", ".venv/pyvenv.cfg", "tests/__pycache__/test.pyc",
        }
        result = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "--no-index", "--stdin"],
            input="\n".join(sorted(paths)) + "\n", text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.splitlines()), paths)
        example = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "--no-index", ".env.example"],
            text=True, capture_output=True,
        )
        self.assertEqual(example.returncode, 1, "Non-secret example templates should remain committable")
