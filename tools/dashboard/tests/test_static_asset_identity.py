"""Unchanged source must serve identical JavaScript across server restarts."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class StaticAssetIdentityTests(unittest.TestCase):
    def test_javascript_identity_is_independent_of_python_hash_seed(self):
        root = Path(__file__).resolve().parents[3]
        command = [sys.executable, '-c',
                   "import sys,hashlib;sys.path[:0]=['tools/dashboard','tools'];"
                   "from agent_console import APP;print(hashlib.sha256(APP.encode()).hexdigest())"]
        identities = [subprocess.check_output(
            command, cwd=root, text=True, timeout=10,
            env={**os.environ, 'PYTHONHASHSEED': seed, 'PYTHONDONTWRITEBYTECODE': '1'})
            for seed in ('1', '2')]
        self.assertEqual(identities[0], identities[1])
