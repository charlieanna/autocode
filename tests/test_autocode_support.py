"""Tests for autocode_support helpers that have no module of their own."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_support as support


class LocalSettingsTests(unittest.TestCase):
    def test_a_missing_codex_is_refused_in_words_not_an_errno(self):
        """#2307: `--engine codex` without Codex installed printed "[Errno 2] No such file or directory: 'codex'"."""
        with tempfile.TemporaryDirectory() as empty:
            environment = {**os.environ, "PATH": empty, "CODEX_HOME": str(Path(empty) / "codex")}
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(RuntimeError) as refused:
                    support.local_settings()
        self.assertTrue(str(refused.exception).startswith("Codex is not on PATH; no provider request was launched"))
        self.assertIn("autocode doctor", str(refused.exception))
        self.assertIn("--engine claude or --engine opencode", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
