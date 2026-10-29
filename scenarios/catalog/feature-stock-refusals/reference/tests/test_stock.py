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
                            raise OverflowError("Stock fixture cannot establish this exit status") from error
                        # Match the POSIX shell-visible status for this fixture's CLI.
                        returncode = error.code & 0xFF
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


class MoveRemoveTests(StockCase):
    def assert_refused(self, result, before):
        self.assertEqual(result.returncode, 2)
        # The refusal must come from move/remove itself. On the original code argparse also exits 2
        # ("invalid choice", after a "usage:" line) and never touches the store.
        self.assertTrue(result.stderr.startswith("stock.py: "), result.stderr)
        self.assertNotIn("invalid choice", result.stderr)
        self.assertEqual(self.store_bytes(), before)

    def test_c1_move_transfers_units(self):
        self.write_store({"A1": {"bolt": 5}})
        result = self.run_cli("move", "bolt", "2", "A1", "B2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_cli("show").stdout, "A1 bolt 3\nB2 bolt 2\n")

    def test_c2_remove_takes_units_and_drops_empty_holdings(self):
        self.write_store({"A1": {"bolt": 2, "nut": 1}})
        result = self.run_cli("remove", "bolt", "2", "A1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_cli("show").stdout, "A1 nut 1\n")

    def test_remove_last_holding_drops_the_empty_location(self):
        self.write_store({"A1": {"bolt": 2}})
        result = self.run_cli("remove", "bolt", "2", "A1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_cli("show").stdout, "")
        self.assertEqual(json.loads(self.store_bytes()), {})

    def test_c3_move_more_than_on_hand_is_refused(self):
        self.write_store({"A1": {"bolt": 1}})
        before = self.store_bytes()
        result = self.run_cli("move", "bolt", "2", "A1", "B2")
        self.assert_refused(result, before)

    def test_c4_move_to_same_location_is_refused(self):
        self.write_store({"A1": {"bolt": 1}})
        before = self.store_bytes()
        result = self.run_cli("move", "bolt", "1", "A1", "A1")
        self.assert_refused(result, before)

    def test_c5_malformed_store_is_refused(self):
        self.write_store("{not json")
        before = self.store_bytes()
        result = self.run_cli("remove", "bolt", "1", "A1")
        self.assert_refused(result, before)

    def test_c6_non_positive_quantity_is_refused(self):
        self.write_store({"A1": {"bolt": 1}})
        before = self.store_bytes()
        result = self.run_cli("move", "bolt", "0", "A1", "B2")
        self.assert_refused(result, before)

    def test_c7_remove_more_than_on_hand_is_refused(self):
        self.write_store({"A1": {"bolt": 1}})
        before = self.store_bytes()
        result = self.run_cli("remove", "bolt", "2", "A1")
        self.assert_refused(result, before)


if __name__ == "__main__":
    unittest.main()
