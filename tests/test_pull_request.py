"""A finished run's pull request: `autocode pr` and a follow-up that asks for one (autocode_pull_request)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import autocode_chat_history as chat_history
import autocode_publish_turn as publish_turn
import autocode_pull_request as pull_request
from autocode_taskrun import TaskRun

from tests import GIT_TEST_CONFIG
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS  # the offline fixture provider's greeting task

TOOLS = Path(__file__).resolve().parents[1] / "tools"


class _FakeGitHub(BaseHTTPRequestHandler):
    """The three calls `autocode pr` makes: the repository, the branch's pull requests, and opening one."""

    pulls: list[dict] = []

    def _send(self, status, value):
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/repos/acme/widgets":
            return self._send(200, {"default_branch": "main"})
        if url.path == "/repos/acme/widgets/pulls":
            head = parse_qs(url.query)["head"][0]
            return self._send(200, [row for row in _FakeGitHub.pulls if f"acme:{row['head']}" == head])
        return self._send(404, {"message": "Not Found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        number = len(_FakeGitHub.pulls) + 1
        row = {
            **body,
            "number": number,
            "state": "open",
            "html_url": f"https://github.example/acme/widgets/pull/{number}",
        }
        _FakeGitHub.pulls.append(row)
        return self._send(201, row)

    def log_message(self, *args):
        pass


class OfferTests(unittest.TestCase):
    """The status view's `pull_request`: offered only for a done run on a branch of its own with a GitHub remote."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="pr-offer-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(
            ["git", "-C", str(self.project), "remote", "add", "origin", "git@github.com:acme/widgets.git"], check=True
        )
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.place = {"kind": "worktree", "folder": str(self.project), "branch": "autocode/greet"}

    def offer(self, **changes):
        values = {"done": True, "place": self.place, "workspace": "/w", "run_dir": self.run_dir, **changes}
        return pull_request.offer(**values)

    def test_a_done_run_on_its_own_branch_offers_the_command(self):
        self.assertEqual(
            {
                "branch": "autocode/greet",
                "repository": "acme/widgets",
                "url": None,
                "label": "Open pull request",
                "next_command": ["pr", "--workspace", "/w", "--run-dir", str(self.run_dir), "--open", "--json"],
            },
            self.offer(),
        )

    def test_an_opened_pull_request_is_updated_rather_than_opened_again(self):
        (self.run_dir / pull_request.RECORD).write_text(
            json.dumps({"url": "https://github.example/acme/widgets/pull/3"})
        )
        offered = self.offer()
        self.assertEqual(
            ("https://github.example/acme/widgets/pull/3", "Update pull request"), (offered["url"], offered["label"])
        )

    def test_nothing_is_offered_otherwise(self):
        subprocess.run(["git", "-C", str(self.project), "remote", "remove", "origin"], check=True)
        self.assertIsNone(self.offer())  # no remote to open it on
        subprocess.run(
            ["git", "-C", str(self.project), "remote", "add", "origin", "https://github.com/acme/widgets"], check=True
        )
        self.assertIsNotNone(self.offer())
        self.assertIsNone(self.offer(done=False))
        self.assertIsNone(self.offer(place={**self.place, "kind": "folder"}))
        self.assertIsNone(self.offer(place=None))
        self.assertIsNone(self.offer(run_dir=None))


class CheckedTests(unittest.TestCase):
    """Only the run's own report, over the source its validation checked, may become a pull request body."""

    REPORT = {
        "availability": "recorded",
        "reasons": ["Historical record"],
        "document": {"revision": {"source_revision": "r1"}},
    }
    FRESH = {"verification": {"freshness": "current", "source_revision": "r1", "inspected_source_revision": "r1"}}

    def test_a_current_report_counts(self):
        pull_request.checked({}, {"availability": "current"})

    def test_a_recorded_report_counts_while_the_source_is_what_validation_checked(self):
        pull_request.checked(self.FRESH, self.REPORT)

    def test_anything_else_is_refused(self):
        for view, report in (
            ({"verification": {**self.FRESH["verification"], "freshness": "stale"}}, self.REPORT),
            ({"verification": {**self.FRESH["verification"], "inspected_source_revision": "r2"}}, self.REPORT),
            (self.FRESH, {**self.REPORT, "document": {"revision": {"source_revision": "r0"}}}),
            (self.FRESH, {**self.REPORT, "availability": "stale"}),
            (self.FRESH, {**self.REPORT, "availability": "invalid"}),
            ({}, self.REPORT),
        ):
            with self.subTest(view=view, report=report), self.assertRaises(pull_request.PullRequestError):
                pull_request.checked(view, report)


class PublishTurnTests(unittest.TestCase):
    """Requirements' `publish` ends the turn with no plan and records the pull request it asked for."""

    REPORT = {
        "job": "publish",
        "required_behaviors": [],
        "acceptance_tests": [],
        "summary": "Opening it.",
        "source_refs": [],
    }

    def test_only_a_publish_report_with_nothing_to_build_is_named(self):
        self.assertTrue(publish_turn.named(self.REPORT))
        self.assertFalse(publish_turn.named({**self.REPORT, "job": "build"}))
        self.assertFalse(publish_turn.named({**self.REPORT, "required_behaviors": ["Print a farewell"]}))
        self.assertFalse(publish_turn.named(None))

    def test_a_follow_up_finishes_answered_and_records_its_request(self):
        state = {"status": "RUNNING", "turns": [{"event_id": "feedback-1", "say": "Open a pull request."}]}
        publish_turn.finish(state, self.REPORT, {"output": "/r/out.json"})
        self.assertEqual(("TASK_COMPLETE", "discuss"), (state["status"], state["workflow"]["kind"]))
        self.assertEqual("Opening it.", state["answer"]["answer"])
        self.assertEqual("feedback-1", state[publish_turn.KEY]["event_id"])
        self.assertEqual("", publish_turn.outcome_text(state))
        state[publish_turn.KEY]["outcome"] = {"kind": "opened", "url": "https://x/pull/1", "branch": "autocode/b"}
        self.assertIn("Opened pull request https://x/pull/1 for branch autocode/b.", chat_history.response(state))
        state[publish_turn.KEY]["outcome"] = {"kind": "refused", "reason": "the evidence is stale"}
        self.assertIn("No pull request was opened: the evidence is stale", chat_history.response(state))

    def test_a_first_message_is_only_answered(self):
        state = {"status": "RUNNING"}
        publish_turn.finish(state, self.REPORT, {})
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertNotIn(publish_turn.KEY, state)

    def test_only_the_turn_that_asked_opens_one(self):
        state = {
            "status": "TASK_COMPLETE",
            "turns": [{"event_id": "feedback-1"}, {"event_id": "feedback-2"}],
            publish_turn.KEY: {"event_id": "feedback-1"},
        }
        saved = []
        self.assertEqual("", pull_request.deliver_requested(state, "/w", "/r", saved.append))
        self.assertEqual([], saved)


class PullRequestCliTests(unittest.TestCase):
    """A run builds in its own worktree, completes, and its pull request opens with no model call."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="pr-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.remote = root / "acme" / "widgets.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        for key, value in GIT_TEST_CONFIG.items():  # the push's receive-pack does not see tests/__init__.py's config
            subprocess.run(["git", "-C", str(self.remote), "config", key, value], check=True)
        self.project = root / "project"
        self.project.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "base")
        self.git("remote", "add", "origin", str(self.remote))
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(TOOLS / "live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        server = HTTPServer(("127.0.0.1", 0), _FakeGitHub)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        _FakeGitHub.pulls = []
        self.env = {
            key: value for key, value in os.environ.items() if key not in ("OPENAI_API_KEY", "CODEX_API_KEY")
        } | {
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@example.test",
            "PATH": f"{bindir}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "AUTOCODE_GITHUB_API": f"http://127.0.0.1:{server.server_port}",
            "GITHUB_TOKEN": "t",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", "-C", str(cwd or self.project), "-c", "user.name=T", "-c", "user.email=t@example.test", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def autocode(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(TOOLS / "autocode.py"), *args],
            cwd=cwd or self.project,
            capture_output=True,
            text=True,
            env=self.env,
            timeout=300,
        )

    def build(self) -> TaskRun:
        """The greeting CLI, built and completed in a worktree and branch of its own."""
        started = self.autocode("--brief", BRIEF, "--build-in-worktree", *FIXTURE_OPTIONS)
        self.assertEqual(2, started.returncode, started.stdout + started.stderr)
        (worktree,) = (self.project / ".autocode/worktrees").iterdir()
        (run_dir,) = (worktree / ".autocode/runs").iterdir()
        run = TaskRun(worktree, run_dir, env=self.env)
        run.advance_until_input()
        run.approve_plan(run.status()["needs"]["token"])
        run.advance_until_input()
        self.assertTrue(run.status()["done"])
        return run

    def pr(self, run: TaskRun, *args):
        return self.autocode("pr", "--workspace", str(run.workspace), "--run-dir", str(run.run_dir), *args)

    def remote_branch(self, branch):
        return self.git("ls-remote", str(self.remote), f"refs/heads/{branch}").split("\t")[0]

    def test_a_finished_run_opens_its_pull_request_from_the_named_command(self):
        run = self.build()
        view = run.status()
        offered = view["pull_request"]
        branch = view["worktree"]["branch"]
        self.assertEqual(
            ("Open pull request", None, "acme/widgets"), (offered["label"], offered["url"], offered["repository"])
        )
        tip = self.git("rev-parse", f"refs/heads/{branch}")

        checked = self.pr(run)
        self.assertEqual(0, checked.returncode, checked.stderr)
        self.assertIn("Nothing has been pushed", checked.stdout)
        self.assertEqual(("", []), (self.remote_branch(branch), _FakeGitHub.pulls))

        opened = self.autocode(*offered["next_command"])  # the command the view names, exactly
        self.assertEqual(0, opened.returncode, opened.stdout + opened.stderr)
        result = json.loads(opened.stdout.splitlines()[-1])
        self.assertEqual(("https://github.example/acme/widgets/pull/1", True), (result["url"], result["opened"]))
        self.assertEqual(tip, self.remote_branch(branch))
        (pull,) = _FakeGitHub.pulls
        self.assertEqual((branch, "main", True), (pull["head"], pull["base"], pull["draft"]))
        self.assertEqual("Provide a deterministic greeting CLI", pull["title"])
        self.assertIn(run.evidence_report()["markdown"].rstrip("\n"), pull["body"])
        self.assertIn("greet.py", pull["body"])
        after = run.status()["pull_request"]
        self.assertEqual((pull["html_url"], "Update pull request"), (after["url"], after["label"]))

        # Pressing again pushes to the same pull request; it never opens a second one.
        again = self.autocode(*offered["next_command"])
        self.assertEqual(0, again.returncode, again.stderr)
        self.assertFalse(json.loads(again.stdout.splitlines()[-1])["opened"])
        self.assertEqual(1, len(_FakeGitHub.pulls))

        # Source edited after completion is not what was checked: refused, and nothing is pushed.
        (Path(run.workspace) / "greet.py").write_text("print('edited')\n")
        refused = self.autocode(*offered["next_command"])
        self.assertEqual(1, refused.returncode, refused.stdout)
        self.assertIn("error", json.loads(refused.stdout.splitlines()[-1]))
        self.assertEqual(tip, self.remote_branch(branch))

    def test_create_a_pr_after_the_task_opens_it_with_no_planning(self):
        run = self.build()
        stages_before = len(json.loads((Path(run.run_dir) / "state.json").read_text())["stages"])
        said = self.autocode(
            "--workspace",
            str(run.workspace),
            "--run-dir",
            str(run.run_dir),
            "--follow-up",
            "Open a pull request for this.",
        )
        self.assertEqual(0, said.returncode, said.stderr)
        run.advance_until_input()
        view = run.status()
        self.assertTrue(view["done"], f"{view['status']}: {view.get('stop_reason')}")
        self.assertEqual("https://github.example/acme/widgets/pull/1", view["pull_request"]["url"])
        (pull,) = _FakeGitHub.pulls
        self.assertEqual(view["worktree"]["branch"], pull["head"])
        self.assertEqual(self.git("rev-parse", f"refs/heads/{pull['head']}"), self.remote_branch(pull["head"]))
        state = json.loads((Path(run.run_dir) / "state.json").read_text())
        # Requirements alone ran for the follow-up: no Planner, Builder or review was spent on it.
        self.assertEqual(["requirements_gather"], [row["stage"] for row in state["stages"][stages_before:]])
        self.assertEqual("opened", state[publish_turn.KEY]["outcome"]["kind"])
        self.assertIn("Opened pull request https://github.example/acme/widgets/pull/1", chat_history.response(state))


if __name__ == "__main__":
    unittest.main()
