"""The example Action, real issue CLI, fake provider, loopback API and bare remote.

No child private state is inspected. These tests are discovered by the normal
suite, including PR --all-fast; no credentials or public GitHub writes are used.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from autocode_taskrun import TaskRun

from tests import GIT_TEST_CONFIG
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / "examples/github-issue/issue_action.py"


class _LocalGitHub(BaseHTTPRequestHandler):
    def request(self):
        api = self.server
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        payload = json.loads(data) if data else None
        api.requests.append((self.command, self.path, self.headers.get("Authorization"), payload))
        if self.headers.get("Authorization") != "Bearer offline-token":
            return self.respond(401, {"message": "Offline token required"})
        parsed = urlsplit(self.path)
        path = parsed.path
        prefix = "/repos/acme/widgets"
        if self.command == "GET" and path.startswith(prefix + "/collaborators/"):
            actor = path.split("/")[-2]
            return self.respond(200, {"permission": {"maintainer": "write", "reader": "read"}.get(actor, "none")})
        if self.command == "GET" and path == prefix + "/issues/7":
            return self.respond(200, {"title": "Add a greeting CLI", "body": BRIEF, "state": "open",
                                      "number": 7, "labels": [{"name": "autocode"}],
                                      "comments": len(api.comments),
                                      "html_url": "https://github.example/acme/widgets/issues/7"})
        if self.command == "GET" and path == prefix + "/issues/7/comments":
            query = parse_qs(parsed.query)
            page, size = int(query.get("page", [1])[0]), int(query.get("per_page", [100])[0])
            return self.respond(200, api.comments[(page - 1) * size:page * size])
        if self.command == "POST" and path == prefix + "/issues/7/comments":
            comment = {"id": 100 + len(api.comments), "body": payload["body"],
                       "user": {"login": "github-actions[bot]"}}
            api.comments.append(comment)
            return self.respond(201, comment)
        if path.startswith(prefix + "/issues/comments/"):
            identifier = int(path.rsplit("/", 1)[1])
            comment = next((row for row in api.comments if row["id"] == identifier), None)
            if comment is not None and self.command in {"GET", "PATCH"}:
                if self.command == "PATCH":
                    comment["body"] = payload["body"]
                return self.respond(200, comment)
        if self.command == "POST" and path == prefix + "/pulls":
            api.pulls.append(payload)
            return self.respond(201, {"html_url": "https://github.example/acme/widgets/pull/1"})
        return self.respond(404, {"message": "Unexpected offline API request"})

    do_GET = request
    do_POST = request
    do_PATCH = request
    do_PUT = request
    do_DELETE = request

    def respond(self, code, body):
        encoded = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass


class IssueActionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="issue-action-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.remote = root / "acme/widgets.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        for key, value in GIT_TEST_CONFIG.items():
            subprocess.run(["git", "-C", str(self.remote), "config", key, value], check=True)
        self.project = root / "project"
        self.project.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.project / ".gitignore").write_text(".autocode/\n")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "base")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "main")
        self.git("symbolic-ref", "HEAD", "refs/heads/main", cwd=self.remote)
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(ROOT / "tools/live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        self.event_file = root / "event.json"
        self.record_path = self.project / ".autocode/issues/acme-widgets-7.json"
        self.api = ThreadingHTTPServer(("127.0.0.1", 0), _LocalGitHub)
        self.api.requests, self.api.comments, self.api.pulls = [], [], []
        threading.Thread(target=self.api.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(self.api.server_close)
        self.addCleanup(self.api.shutdown)
        self.env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                    "AUTOCODE_HOME": str(root / "registry"), "PYTHONDONTWRITEBYTECODE": "1",
                    "AUTOCODE_GITHUB_API": f"http://127.0.0.1:{self.api.server_port}",
                    "GITHUB_TOKEN": "offline-token", "GH_TOKEN": "offline-token",
                    "GITHUB_REPOSITORY": "acme/widgets", "GITHUB_ACTOR": "maintainer",
                    "GITHUB_TRIGGERING_ACTOR": "maintainer",
                    "AUTOCODE_ISSUE_ACTORS": json.dumps(["maintainer", "reader"]),
                    "AUTOCODE_ISSUE_OPTIONS": json.dumps(FIXTURE_OPTIONS),
                    "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
                    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.test",
                    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.test"}

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.project), "-c", "user.name=T",
                               "-c", "user.email=t@example.test", *args], capture_output=True,
                              text=True, check=True).stdout.strip()

    def action(self, name="issues", *, label="autocode", actor="maintainer", options=None):
        event = {"repository": {"full_name": "acme/widgets"}}
        if name == "issues":
            event.update(action="labeled", label={"name": label}, issue={"number": 7, "state": "open"})
        else:
            event["inputs"] = {"issue": "7"}
        self.event_file.write_text(json.dumps(event))
        env = {**self.env, "GITHUB_EVENT_NAME": name, "GITHUB_TRIGGERING_ACTOR": actor}
        if options is not None:
            env["AUTOCODE_ISSUE_OPTIONS"] = json.dumps(options)
        return subprocess.run([sys.executable, str(HANDLER), "--event", str(self.event_file),
                               "--project", str(self.project)], env=env, capture_output=True,
                              text=True, timeout=300)

    def succeeded(self, result):
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        return result.stdout

    def saved_run(self):
        record = json.loads(self.record_path.read_text())  # supported issue attachment, not child state
        run = TaskRun(Path(record["worktree"]), Path(record["run_dir"]),
                      options=tuple(record["options"]), env=self.env)
        return record, run

    def assert_no_issue_work(self):
        self.assertFalse(self.record_path.exists())
        self.assertEqual([], self.api.comments)
        self.assertEqual([], self.api.pulls)
        self.assertEqual("main", self.git("branch", "--format=%(refname:short)"))

    def test_label_approval_continuation_and_exact_canonical_draft(self):
        initial = self.succeeded(self.action())
        self.assertIn("waiting for you to approve its plan", initial)
        record, run = self.saved_run()
        self.assertIn("--no-adaptive-planning", record["options"])
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"])
        token = view["needs"]["token"]
        attempts = view["usage"]["accounting"]["attempts"]
        self.assertFalse((run.workspace / "greet.py").exists())
        self.assertEqual([], self.api.pulls)
        self.assertEqual(1, len(self.api.comments))
        self.assertIn(f"--approve-goal {token}", self.api.comments[0]["body"])
        self.assertIn("Dispatching the workflow does not approve anything", self.api.comments[0]["body"])

        self.succeeded(self.action())
        self.assertEqual(record, self.saved_run()[0])
        self.assertEqual(attempts, run.status()["usage"]["accounting"]["attempts"])
        self.assertEqual(1, len(self.api.comments))
        # Simulate interruption after GitHub accepted a comment but before its receipt.
        (self.project / ".autocode/issue-action/acme-widgets-7.json").unlink()
        self.succeeded(self.action())
        self.assertEqual(1, len(self.api.comments))
        self.succeeded(self.action("workflow_dispatch"))
        paused = run.status()
        self.assertEqual(("approve_plan", token), (paused["needs"]["kind"], paused["needs"]["token"]))
        self.assertEqual(attempts, paused["usage"]["accounting"]["attempts"])
        self.assertEqual([], self.api.pulls)

        # A separately created attachment must not import an automatic approval policy.
        saved_attachment = self.record_path.read_text()
        unsafe = {**record, "options": [word for word in record["options"] if word != "--no-adaptive-planning"]}
        self.record_path.write_text(json.dumps(unsafe))
        refused = self.action("workflow_dispatch")
        self.assertEqual(1, refused.returncode)
        self.assertIn("lacks --no-adaptive-planning", refused.stderr)
        self.assertEqual(attempts, run.status()["usage"]["accounting"]["attempts"])
        self.assertEqual([], self.api.pulls)
        self.record_path.write_text(saved_attachment)

        # This is the explicit test human. The handler has no approval code path.
        self.assertIn("greet.py", run.show_goal())
        run.approve_plan(token)
        self.succeeded(self.action("workflow_dispatch"))
        self.assertTrue(run.status()["done"])
        # The delivery commit changed HEAD after cmd_pr authenticated current
        # evidence. Read its authenticated historical pair without relabelling it current.
        report = run.evidence_report(require_current=False)
        self.assertEqual("fake", report["document"]["provenance"]["kind"])
        self.assertEqual(report["markdown"].encode("utf-8"), (run.run_dir / "evidence.md").read_bytes())
        [pull] = self.api.pulls
        self.assertTrue(pull["draft"])
        self.assertEqual((record["branch"], "main"), (pull["head"], pull["base"]))
        self.assertTrue(pull["body"].startswith("Resolves acme/widgets#7."))
        self.assertEqual(1, pull["body"].count(report["markdown"]))
        self.assertEqual(pull["body"], (self.project / ".autocode/issues/acme-widgets-7-pr.md").read_text())
        head = self.git("rev-parse", "HEAD", cwd=run.workspace)
        self.assertEqual(head, self.git("rev-parse", "refs/heads/" + record["branch"], cwd=self.remote))
        completed_attempts = run.status()["usage"]["accounting"]["attempts"]
        self.succeeded(self.action("workflow_dispatch"))
        self.assertEqual(1, len(self.api.pulls))
        self.assertEqual(1, len(self.api.comments))
        self.assertEqual(head, self.git("rev-parse", "HEAD", cwd=run.workspace))
        self.assertEqual(completed_attempts, run.status()["usage"]["accounting"]["attempts"])
        self.assertFalse(any("/merge" in path for _, path, _, _ in self.api.requests))
        self.assertTrue(all(auth == "Bearer offline-token" for _, _, auth, _ in self.api.requests))

    def test_unlisted_actor_is_refused_before_api_or_git_work(self):
        result = self.action(actor="outsider")
        self.assertEqual(1, result.returncode)
        self.assertIn("not in AUTOCODE_ISSUE_ACTORS", result.stderr)
        self.assertEqual([], self.api.requests)
        self.assert_no_issue_work()

    def test_rerun_actor_needs_write_permission_even_if_original_actor_has_it(self):
        result = self.action(actor="reader")
        self.assertEqual(1, result.returncode)
        self.assertIn("write or admin", result.stderr)
        self.assertEqual(1, len(self.api.requests))
        self.assertIn("/collaborators/reader/permission", self.api.requests[0][1])
        self.assert_no_issue_work()

    def test_wrong_label_does_not_start_or_authorize_work(self):
        self.assertIn("Ignored event", self.succeeded(self.action(label="bug", actor="outsider")))
        self.assertEqual([], self.api.requests)
        self.assert_no_issue_work()

    def test_saved_options_cannot_replay_a_human_approval(self):
        for option in ("--approve-goal=token", "--delegate-all", "--delegate=q1", "--no-requirements",
                       "--adaptive-planning", "--resume-paused", "--accept-completion"):
            with self.subTest(option=option):
                result = self.action(options=[*FIXTURE_OPTIONS, option])
                self.assertEqual(1, result.returncode)
                self.assertIn("human/run action", result.stderr)
                self.assert_no_issue_work()

    def test_concurrent_trigger_does_not_take_over_the_owned_issue(self):
        directory = self.project / ".autocode/issue-action"
        directory.mkdir(parents=True)
        with (directory / "acme-widgets-7.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.action()
        self.assertEqual(1, result.returncode)
        self.assertIn("Another Action owns this issue", result.stderr)
        self.assert_no_issue_work()


if __name__ == "__main__":
    unittest.main()
