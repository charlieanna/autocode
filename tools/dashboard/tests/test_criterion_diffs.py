"""Each requirement's saved-step diffs, read from the run's public status view (#730)."""

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console import Console, Handler, LoopbackHTTPServer

FAKE = r"""
import json,sys
from pathlib import Path
root=Path(__file__).parent
args=sys.argv[1:]
def read(name): return json.loads((root/name).read_text())
if args[:2]==['registry','location']:
 print(json.dumps({'registry_version':1,'operation':'location','registry_path':str(root/'registry.json')}))
elif args[:2]==['registry','list']:
 print(json.dumps(read('registry.json')))
elif '--status' in args:
 (root/'status-calls').open('a').write('1\n')
 print(json.dumps(read('status.json')))
elif args[:2]==['intervention','inspect']:
 print(json.dumps({'version':1,'operation':'inspect','requests':[],'pending_count':0}))
else:
 print('recorded')
"""


class RunFixture:
    """A real Git project with two saved steps and a fake runner whose --status names them."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.git("init", "-q")
        base = self.commit({"a.py": "a0\n", "b.py": "b0\n"})
        first = self.commit({"a.py": "a1\n"})
        second = self.commit({"b.py": "b1\n"})
        (self.workspace / "a.py").write_text("uncommitted working tree\n")
        self.run = self.workspace / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        (self.run / "state.json").write_text(json.dumps({"version": 3, "workspace": str(self.workspace)}))
        rows = [
            {"id": "code-1", "milestone_id": "M1", "label": "Change a", "changed_files": ["a.py"], "commit": first},
            {"id": "code-2", "milestone_id": "M2", "label": "Change b", "changed_files": ["b.py"], "commit": second},
        ]
        self.view = {
            "evidence": {"base_commit": base},
            "code_checkpoints": {"rows": rows},
            "changes_by_criterion": {
                "meaning": "attribution",
                "criteria": [
                    {"id": "C1", "milestones": ["M1"], "checkpoints": ["code-1"], "files": ["a.py"]},
                    {"id": "C2", "milestones": ["M2"], "checkpoints": ["code-2"], "files": ["b.py"]},
                ],
                "unattributed": [],
            },
        }
        self.fake = self.root / "runner.py"
        self.fake.write_text(FAKE)
        self.save(
            "registry.json",
            {
                "operation": "list",
                "registry_version": 1,
                "workspaces": [{"workspace": str(self.workspace)}],
                "runs": [{"workspace": str(self.workspace), "run_dir": str(self.run), "availability": "available"}],
            },
        )
        self.save("status.json", {"view": self.view})
        isolation = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "home")})
        isolation.start()
        self.addCleanup(isolation.stop)
        self.console = Console((), self.fake, lambda: None)
        self.addCleanup(self.console.pool.shutdown, wait=True)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.workspace), *args], text=True, stderr=subprocess.PIPE)

    def commit(self, files):
        for name, text in files.items():
            (self.workspace / name).write_text(text)
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "step")
        return self.git("rev-parse", "HEAD").strip()

    def save(self, name, value):
        (self.root / name).write_text(json.dumps(value))


class CriterionDiffs(RunFixture, unittest.TestCase):
    def test_every_requirement_gets_its_own_saved_step_diffs(self):
        shown = self.console.criterion_diffs(self.workspace, self.run)
        self.assertEqual("available", shown["availability"])
        self.assertEqual(["C1", "C2"], [row["id"] for row in shown["criteria"]])
        self.assertEqual(["code-1"], shown["criteria"][0]["checkpoints"])
        self.assertIn("-a0\n+a1", shown["steps"]["code-1"]["files"][0]["text"])
        self.assertIn("-b0\n+b1", shown["steps"]["code-2"]["files"][0]["text"])
        self.assertNotIn("uncommitted", json.dumps(shown))
        self.assertEqual("M1", shown["steps"]["code-1"]["milestone_id"])

    def test_the_status_view_it_reads_is_the_one_the_task_detail_carries(self):
        self.console.task_view(self.workspace, self.run)
        self.console.criterion_diffs(self.workspace, self.run)
        self.assertEqual(1, len((self.root / "status-calls").read_text().split()))

    def test_without_a_status_view_it_says_so_and_reads_nothing(self):
        with patch.object(self.console, "_json_command", return_value=(None, {"message": "gone"})):
            shown = self.console.criterion_diffs(self.workspace, self.run)
        self.assertEqual("unavailable", shown["availability"])
        self.assertIn("status", shown["reason"])
        self.assertEqual({}, shown["steps"])
        self.assertEqual([], shown["criteria"])


class CriterionDiffRoute(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
        self.server.console = self.console
        self.server.hosts = {"127.0.0.1:" + str(self.server.server_port)}
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def route(self, run=None):
        return "/api/criterion-diffs?" + urlencode({"workspace": str(self.workspace), "run": str(run or self.run)})

    def test_route_returns_each_requirements_diffs_for_a_listed_task_only(self):
        code, data = self.request(self.route())
        self.assertEqual(200, code)
        self.assertIn("+a1", data["steps"]["code-1"]["files"][0]["text"])
        self.assertEqual(404, self.request(self.route(self.workspace / ".autocode/runs/other"))[0])
        self.assertEqual(403, self.request(self.route(), {"Origin": "http://evil.test"})[0])
        self.console.task_archive_action({"workspace": str(self.workspace), "run": str(self.run), "action": "archive"})
        self.assertEqual(404, self.request(self.route())[0])


if __name__ == "__main__":
    unittest.main()
