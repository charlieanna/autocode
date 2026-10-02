"""Declared public input preflight tests (AC1-AC4, AC13, AC14).

Synthetic Git fixtures: the original workspace is a real repository, the copy
root stands in for the actual Git-enumerated source copy a run would use.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_input_preflight as preflight

PROOF = b"proof instructions v1\n"
TAMPERED = b"tampered\n"
PATCH = b"--- a/x\n+++ b/x\n"


def write(root, relpath, data, mode=0o644):
    path = Path(root) / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)


def commit_fixture(root, tracked):
    for args in (["init", "-q"], ["add", *tracked],
                 ["-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "fixture"]):
        subprocess.run(["git", "-C", str(root), *args], check=True)


class InputPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="input-preflight-")
        self.addCleanup(self.temp.cleanup)
        self.original = Path(self.temp.name) / "original"
        self.copy = Path(self.temp.name) / "copy"
        self.original.mkdir()
        self.copy.mkdir()

    def manifest(self, entries):
        path = Path(self.temp.name) / "manifest.json"
        path.write_text(json.dumps({"inputs": entries}))
        return path

    def entry(self, path, data=PROOF):
        return {"path": path, "type": "file", "mode": "0644", "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def ignored_fixture(self):
        """The original failure shape: a declared input under an ignored path."""
        write(self.original, ".gitignore", b".pilot-env/\n")
        write(self.original, "README.md", b"readme\n")
        commit_fixture(self.original, [".gitignore", "README.md"])
        write(self.original, ".pilot-env/public/PROOF.md", PROOF)
        write(self.copy, ".gitignore", b".pilot-env/\n")
        write(self.copy, "README.md", b"readme\n")
        return self.manifest([self.entry(".pilot-env/public/PROOF.md")])

    def test_ac1_preflight_flags_ignored_required_input(self):
        manifest = self.ignored_fixture()
        report = preflight.preflight(manifest, self.original, self.copy)
        self.assertEqual([], report["ok"])
        self.assertEqual(1, len(report["errors"]))
        message = report["errors"][0]
        self.assertIn(".pilot-env/public/PROOF.md", message)
        self.assertIn("ignored", message)
        self.assertIn("pilot-public/", message)

    def test_ac2_preflight_flags_identity_mismatch(self):
        manifest = self.ignored_fixture()
        write(self.copy, ".pilot-env/public/PROOF.md", TAMPERED)
        report = preflight.preflight(manifest, self.original, self.copy)
        self.assertEqual(1, len(report["errors"]))
        message = report["errors"][0]
        self.assertIn(".pilot-env/public/PROOF.md", message)
        self.assertIn("size", message)
        self.assertIn("sha256", message)

        write(self.copy, ".pilot-env/public/PROOF.md", PROOF, mode=0o600)
        report = preflight.preflight(manifest, self.original, self.copy)
        self.assertEqual(1, len(report["errors"]))
        message = report["errors"][0]
        self.assertIn(".pilot-env/public/PROOF.md", message)
        self.assertIn("mode", message)
        self.assertNotIn("size", message)
        self.assertNotIn("sha256", message)

    def test_ac3_preflight_accepts_exact_inputs(self):
        write(self.original, "README.md", b"readme\n")
        write(self.original, "pilot-public/PUBLIC-PROOF.md", PROOF)
        commit_fixture(self.original, ["README.md", "pilot-public/PUBLIC-PROOF.md"])
        write(self.original, "pilot-public/pr42.patch", PATCH)
        write(self.copy, "pilot-public/PUBLIC-PROOF.md", PROOF)
        write(self.copy, "pilot-public/pr42.patch", PATCH)
        manifest = self.manifest([self.entry("pilot-public/PUBLIC-PROOF.md"),
                                  self.entry("pilot-public/pr42.patch", data=PATCH)])
        report = preflight.preflight(manifest, self.original, self.copy)
        self.assertEqual([], report["errors"])
        self.assertEqual({"pilot-public/PUBLIC-PROOF.md", "pilot-public/pr42.patch"},
                         {record["path"] for record in report["ok"]})

    def test_ac4_manifest_rejects_non_project_relative_paths(self):
        manifest = self.manifest([self.entry("/tmp/PROOF.md"), self.entry("../PROOF.md")])
        entries, errors = preflight.load_manifest(manifest)
        self.assertEqual([], entries)
        self.assertEqual(2, len(errors))
        for path in ("/tmp/PROOF.md", "../PROOF.md"):
            matches = [error for error in errors if path in error]
            self.assertEqual(1, len(matches), errors)
            self.assertIn("project-relative", matches[0])
        report = preflight.preflight(manifest, self.original, self.copy)
        self.assertEqual([], report["ok"])
        self.assertEqual(sorted(errors), sorted(report["errors"]))

    def test_ac13_preflight_flags_type_mismatch(self):
        manifest = self.manifest([self.entry("pilot-public/PROOF.md")])
        write(self.copy, "pilot-public/PROOF.md", PROOF)

        directory = self.original / "pilot-public" / "PROOF.md"
        directory.mkdir(parents=True)
        write(self.original, "pilot-public/PROOF.md/keep.txt", b"keep\n")
        report = preflight.preflight(manifest, self.original, self.copy)
        self.assertEqual(1, len(report["errors"]))
        self.assertIn("pilot-public/PROOF.md", report["errors"][0])
        self.assertIn("type", report["errors"][0])

        shutil.rmtree(directory)
        write(self.original, "pilot-public/PROOF.md.bak", PROOF)
        os.symlink("PROOF.md.bak", self.original / "pilot-public" / "PROOF.md")
        report = preflight.preflight(manifest, self.original, self.copy)
        self.assertEqual(1, len(report["errors"]))
        self.assertIn("pilot-public/PROOF.md", report["errors"][0])
        self.assertIn("type", report["errors"][0])

    def test_symlink_ancestor_cannot_escape_original_or_copy_root(self):
        outside = Path(self.temp.name) / "outside"
        write(outside, "PROOF.md", PROOF)
        for root in (self.original, self.copy):
            (root / "pilot-public").symlink_to(outside, target_is_directory=True)
        manifest = self.manifest([self.entry("pilot-public/PROOF.md")])
        report = preflight.preflight(manifest, self.original, self.copy)
        self.assertEqual([], report["ok"])
        self.assertEqual(2, len(report["errors"]))
        for label in ("original", "copy"):
            self.assertTrue(any(label in error and "symlink ancestor" in error
                                and "pilot-public/PROOF.md" in error for error in report["errors"]))

    def test_ac14_preflight_flags_original_root_failures(self):
        manifest = self.manifest([self.entry("pilot-public/PROOF.md")])
        write(self.copy, "pilot-public/PROOF.md", PROOF)

        report = preflight.preflight(manifest, self.original, self.copy)
        self.assertEqual(1, len(report["errors"]))
        self.assertIn("pilot-public/PROOF.md", report["errors"][0])
        self.assertIn("missing from the original workspace", report["errors"][0])

        write(self.original, "pilot-public/PROOF.md", TAMPERED)
        report = preflight.preflight(manifest, self.original, self.copy)
        self.assertEqual(1, len(report["errors"]))
        message = report["errors"][0]
        self.assertIn("pilot-public/PROOF.md", message)
        self.assertIn("size", message)
        self.assertIn("sha256", message)


if __name__ == "__main__":
    unittest.main()
