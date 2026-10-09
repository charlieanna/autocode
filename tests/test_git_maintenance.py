"""A commit in a repository a test creates starts no background Git maintenance.

tests/__init__.py turns it off through the environment; see the comment there.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class BackgroundMaintenanceTests(unittest.TestCase):
    def test_a_commit_in_a_new_repository_starts_no_maintenance(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, trace = Path(temp) / "repo", Path(temp) / "trace2.json"
            repo.mkdir()
            git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.test"]
            subprocess.run([*git, "init", "-q"], check=True, capture_output=True)
            (repo / "a.txt").write_text("a\n")
            subprocess.run([*git, "add", "a.txt"], check=True, capture_output=True)
            subprocess.run([*git, "commit", "-qm", "a"], check=True, capture_output=True,
                           env={**os.environ, "GIT_TRACE2_EVENT": str(trace)})
            children = [event["argv"] for event in map(json.loads, trace.read_text().splitlines())
                        if event.get("event") == "child_start"]
        self.assertEqual([], [argv for argv in children if {"maintenance", "gc"} & set(argv)], children)


if __name__ == "__main__":
    unittest.main()
