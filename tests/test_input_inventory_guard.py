"""Guard for the Git-enumerated inventory seam (AC5): ignored paths stay excluded.

Every source copy AutoCode builds starts from autocode_util.snapshot, which
enumerates tracked plus ordinary untracked files. This guard imports only
pre-existing code (autocode_util and the standard library), so it runs
unchanged before and after the declared-input preflight change and keeps the
original ignored-path gap detectable at the seam it actually lives in.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_util

PROOF = b"proof instructions v1\n"
PATCH = b"--- a/x\n+++ b/x\n"


def write(root, relpath, data, mode=0o644):
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)


class GitInventoryGuardTests(unittest.TestCase):
    def test_ac5_git_inventory_excludes_ignored_paths(self):
        with tempfile.TemporaryDirectory(prefix="inventory-guard-") as name:
            root = Path(name)
            write(root, ".gitignore", b".pilot-env/\nsecrets.env\nnode_modules/\ndist/\n")
            write(root, "README.md", b"readme\n")
            write(root, "pilot-public/PUBLIC-PROOF.md", PROOF)
            for args in (["init", "-q"], ["add", ".gitignore", "README.md", "pilot-public/PUBLIC-PROOF.md"],
                         ["-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "fixture"]):
                subprocess.run(["git", "-C", str(root), *args], check=True)
            write(root, "pilot-public/pr42.patch", PATCH)
            write(root, ".pilot-env/public/PROOF.md", PROOF)
            write(root, "secrets.env", b"token=1\n")
            write(root, "node_modules/dep.js", b"dep\n")
            write(root, "dist/out.js", b"out\n")
            files = autocode_util.snapshot(root)["files"]
            self.assertEqual(
                [".gitignore", "README.md", "pilot-public/PUBLIC-PROOF.md", "pilot-public/pr42.patch"],
                sorted(files),
            )


if __name__ == "__main__":
    unittest.main()
