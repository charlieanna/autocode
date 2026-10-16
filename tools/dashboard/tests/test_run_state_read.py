import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard_run_state as run_state


class RunStateReadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name)

    def test_read_returns_the_saved_object(self):
        run_state.path(self.run).write_text(json.dumps({"status": "RUNNING"}))
        self.assertEqual({"status": "RUNNING"}, run_state.read(self.run))

    def test_a_missing_malformed_or_non_object_state_is_refused(self):
        with self.assertRaises(OSError):
            run_state.read(self.run)
        for text in ("{", "[1]"):
            with self.subTest(text=text):
                run_state.path(self.run).write_text(text)
                with self.assertRaises(ValueError):
                    run_state.read(self.run)


if __name__ == "__main__":
    unittest.main()
