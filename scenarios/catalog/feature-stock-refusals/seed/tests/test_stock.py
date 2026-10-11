import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import traceback
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

STOCK = Path(__file__).resolve().parents[1] / "stock.py"


class StockCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.cwd = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def run_cli(self, *args):
        command = [sys.executable, str(STOCK), *args]
        stdout, stderr = io.StringIO(), io.StringIO()
        previous_argv, previous_cwd = sys.argv, Path.cwd()
        returncode = 0
        try:
            sys.argv = command[1:]
            os.chdir(self.cwd)
            with redirect_stdout(stdout), redirect_stderr(stderr):
                try:
                    # Fresh script globals and __main__ for every stock command.
                    runpy.run_path(str(STOCK), run_name="__main__")
                except SystemExit as error:
                    if error.code is None:
                        returncode = 0
                    elif isinstance(error.code, int):
                        if not -(1 << 31) <= error.code < (1 << 31):
                            raise OverflowError("Stock fixture cannot establish this exit status")
                        # Match the POSIX shell-visible status for this fixture's CLI.
                        returncode = error.code & 0xff
                    else:
                        print(error.code, file=sys.stderr)
                        returncode = 1
                except Exception:
                    # An actual script failure remains a failing command, never PASS.
                    traceback.print_exc()
                    returncode = 1
        finally:
            sys.argv = previous_argv
            os.chdir(previous_cwd)
        return subprocess.CompletedProcess(command, returncode, stdout.getvalue(), stderr.getvalue())

    def write_store(self, data):
        (self.cwd / "stock.json").write_text(json.dumps(data) if not isinstance(data, str) else data)

    def store_bytes(self):
        path = self.cwd / "stock.json"
        return path.read_bytes() if path.exists() else None


class ReceiveTests(StockCase):
    def test_receive_adds_units(self):
        self.write_store({"A1": {"bolt": 3}})
        result = self.run_cli("receive", "bolt", "2", "A1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_cli("show").stdout, "A1 bolt 5\n")

    def test_receive_zero_is_refused_and_store_unchanged(self):
        self.write_store({"A1": {"bolt": 3}})
        before = self.store_bytes()
        result = self.run_cli("receive", "bolt", "0", "A1")
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.strip())
        self.assertEqual(self.store_bytes(), before)


if __name__ == "__main__":
    unittest.main()
