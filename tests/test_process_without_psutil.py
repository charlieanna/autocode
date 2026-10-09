"""`autocode --version` and `autocode doctor` work where psutil cannot be imported (#67).

autocode.py loads autocode_process when it starts, but only supervising a run
needs psutil. A first-time user whose install lost the dependency must get the
version and doctor's report naming the fix, not a ModuleNotFoundError. The CLI
runs with a directory first on PYTHONPATH whose psutil.py raises ImportError, both
as the script (tools/autocode.py) and as a package, as the installed
``autocode_cli.autocode`` entry point imports it.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_doctor as doctor
import autocode_util as util

REPO_ROOT = Path(__file__).resolve().parents[1]
# The installed entry point imports autocode_cli.autocode; ``tools`` is the same package.
ENTRIES = {
    "script": [str(REPO_ROOT / "tools" / "autocode.py")],
    "package": ["-c", "import tools.autocode as autocode; raise SystemExit(autocode.cli())"],
}


class WithoutPsutilTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="no-psutil-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "shim").mkdir()
        (self.root / "shim" / "psutil.py").write_text("raise ImportError('psutil is not installed (test shim)')\n")
        self.env = {key: value for key, value in os.environ.items() if key != "AUTOCODE_PROVIDER"}
        # No engine on PATH and no user config: doctor's report depends on psutil alone.
        self.env.update(PYTHONPATH=str(self.root / "shim"), PATH=os.defpath, XDG_CONFIG_HOME=str(self.root))

    def autocode(self, entry, *args):
        proc = subprocess.run(
            [sys.executable, *ENTRIES[entry], *args],
            cwd=REPO_ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertNotIn("Traceback", proc.stderr)
        return proc

    def test_version_needs_no_psutil(self):
        for entry in ENTRIES:
            with self.subTest(entry=entry):
                proc = self.autocode(entry, "--version")
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertTrue(proc.stdout.startswith("autocode "), proc.stdout)

    def test_doctor_reports_psutil_missing_with_its_fix(self):
        for entry in ENTRIES:
            with self.subTest(entry=entry):
                proc = self.autocode(entry, "doctor", "--json", "--workspace", str(self.root))
                self.assertEqual(1, proc.returncode, proc.stderr)
                psutil = {c["name"]: c for c in json.loads(proc.stdout)["checks"]}["psutil"]
                self.assertEqual(doctor.MISSING, psutil["status"])
                self.assertIn("reinstall AutoCode", psutil["fix"])
        proc = self.autocode("script", "doctor", "--workspace", str(self.root))
        self.assertEqual(1, proc.returncode, proc.stderr)
        self.assertIn("✖ psutil", proc.stdout)

    def test_using_the_missing_module_names_the_fix(self):
        missing = util.MissingModule("psutil", ImportError("No module named 'psutil'"))
        with self.assertRaisesRegex(ModuleNotFoundError, "autocode doctor"):
            missing.pids()
        self.assertFalse(hasattr(missing, "__file__"))


if __name__ == "__main__":
    unittest.main()
