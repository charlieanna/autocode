"""`autocode issue list` and `autocode issue queue`: many issues on one project, a few working at a time."""

from __future__ import annotations

import io
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
from unittest.mock import patch
from urllib.parse import urlsplit

import autocode_github as github
import autocode_issue as issue_cli
import autocode_issue_queue as issue_queue

from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def issue_json(number: int, title: str, labels=()) -> dict:
    return {
        "number": number,
        "title": title,
        "body": BRIEF,
        "state": "open",
        "html_url": f"https://github.com/acme/widgets/issues/{number}",
        "labels": [{"name": name} for name in labels],
    }


class Project:
    """A Git checkout whose origin is acme/widgets."""

    def __init__(self, root: Path):
        self.root = root
        self.path = root / "project"
        self.path.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "base")
        self.git("remote", "add", "origin", "https://github.com/acme/widgets.git")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            check=True,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "T",
                "GIT_AUTHOR_EMAIL": "t@example.test",
                "GIT_COMMITTER_NAME": "T",
                "GIT_COMMITTER_EMAIL": "t@example.test",
            },
        ).stdout.strip()


class FakeChild:
    def __init__(self, on_done):
        self.returncode = None
        self.on_done = on_done

    def poll(self):
        if self.returncode is None:
            self.returncode = self.on_done()
        return self.returncode


class SchedulerTests(unittest.TestCase):
    """The scheduler starts a queued issue only while fewer than the limit of issue runs work."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="issue-queue-")
        self.addCleanup(temp.cleanup)
        self.project = Project(Path(temp.name)).path
        self.working = {"acme-widgets-1"}  # an issue started by hand earlier, still working
        self.waiting_rows = set()
        self.started = []
        self.peak = 0

    def rows(self, project):
        rows = [
            {
                "workspace": str(project / issue_queue.STORE / slug),
                "run_dir": str(project / issue_queue.STORE / slug / ".autocode/runs/r"),
                "bucket": "working",
            }
            for slug in sorted(self.working)
        ]
        rows += [
            {"workspace": str(project / issue_queue.STORE / slug), "bucket": "needs you"}
            for slug in sorted(self.waiting_rows)
        ]
        rows.append({"workspace": str(project), "bucket": "working"})  # the project's own chat: not an issue run
        return rows

    def start(self, project, entry, options):
        self.started.append((entry["ref"], tuple(options)))
        self.working.add(entry["slug"])
        self.peak = max(self.peak, len(self.working))

        def done():
            issue_queue.record_path(project, github.parse_ref(entry["ref"])).parent.mkdir(parents=True, exist_ok=True)
            issue_queue.record_path(project, github.parse_ref(entry["ref"])).write_text("{}")
            return 0

        return FakeChild(done)

    def tick(self, seconds):
        # Between rounds, the oldest working issue run reaches its plan and waits for the person.
        if self.working:
            slug = sorted(self.working)[0]
            self.working.discard(slug)
            self.waiting_rows.add(slug)

    def test_starts_no_more_than_the_limit_and_a_waiting_run_frees_its_slot(self):
        with patch.object(issue_cli, "default_repo", return_value=("acme", "widgets")):
            out = io.StringIO()
            with patch("sys.stdout", out):
                issue_cli.main(
                    [
                        "queue",
                        "add",
                        "7",
                        "8",
                        "9",
                        "--max-active",
                        "2",
                        "--project",
                        str(self.project),
                        "--",
                        "--engine",
                        "codex",
                    ]
                )
        queue = issue_queue.load(self.project)
        self.assertEqual(["queued"] * 3, [entry["state"] for entry in queue["entries"]])
        self.assertEqual(2, queue["max_active"])
        self.assertEqual(
            ["issue", "queue", "run", "--project", str(self.project)],
            issue_queue.queue_view(self.project)["next_command"],
        )
        with (
            patch.object(issue_queue, "runs", self.rows),
            patch.object(issue_queue, "_start", self.start),
            patch.object(issue_queue.util, "sleep", self.tick),
        ):
            self.assertEqual(0, issue_queue.schedule(self.project, out=io.StringIO()))
        self.assertEqual(
            [
                ("acme/widgets#7", ("--engine", "codex")),
                ("acme/widgets#8", ("--engine", "codex")),
                ("acme/widgets#9", ("--engine", "codex")),
            ],
            self.started,
        )
        self.assertLessEqual(self.peak, 2)
        self.assertEqual(["started"] * 3, [entry["state"] for entry in issue_queue.load(self.project)["entries"]])
        self.assertIsNone(issue_queue.queue_view(self.project)["next_command"])

    def test_a_running_scheduler_is_not_named_again_and_a_second_one_steps_aside(self):
        issue_queue.save(
            self.project,
            {
                **issue_queue._empty(),
                "entries": [{"ref": "acme/widgets#7", "slug": "acme-widgets-7", "number": 7, "state": "queued"}],
            },
        )
        lock = self.project / issue_queue.LOCK
        with lock.open("a+") as handle:
            issue_queue.util.lock_file(handle)
            self.assertTrue(issue_queue.queue_view(self.project)["runner_active"])
            self.assertIsNone(issue_queue.queue_view(self.project)["next_command"])
            out = io.StringIO()
            with patch.object(issue_queue, "_start", side_effect=AssertionError("started twice")):
                self.assertEqual(0, issue_queue.schedule(self.project, out=out))
            self.assertIn("already running", out.getvalue())
            issue_queue.util.unlock_file(handle)
        self.assertFalse(issue_queue.queue_view(self.project)["runner_active"])

    def test_a_failed_start_is_kept_with_its_output_and_can_be_queued_again(self):
        issue_queue.save(
            self.project,
            {
                **issue_queue._empty(),
                "entries": [{"ref": "acme/widgets#7", "slug": "acme-widgets-7", "number": 7, "state": "queued"}],
            },
        )

        def fail(project, entry, options):
            log = project / ".autocode" / "issue-queue" / f"{entry['slug']}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("autocode issue: GET /repos/acme/widgets/issues/7 returned 404: Not Found\n")
            return FakeChild(lambda: 1)

        with (
            patch.object(issue_queue, "runs", lambda project: []),
            patch.object(issue_queue, "_start", fail),
            patch.object(issue_queue.util, "sleep", lambda seconds: None),
        ):
            self.assertEqual(1, issue_queue.schedule(self.project, out=io.StringIO()))
        entry = issue_queue.load(self.project)["entries"][0]
        self.assertEqual("failed", entry["state"])
        self.assertIn("404", entry["error"])
        with (
            patch.object(issue_cli, "default_repo", return_value=("acme", "widgets")),
            patch("sys.stdout", io.StringIO()),
        ):
            issue_cli.main(["queue", "add", "7", "--project", str(self.project)])
        self.assertEqual(["queued"], [e["state"] for e in issue_queue.load(self.project)["entries"]])

    def test_an_issue_with_a_run_is_not_queued_and_remove_keeps_a_started_one(self):
        record = issue_queue.record_path(self.project, github.IssueRef("acme", "widgets", 8))
        record.parent.mkdir(parents=True)
        record.write_text("{}")
        out = io.StringIO()
        with patch.object(issue_cli, "default_repo", return_value=("acme", "widgets")), patch("sys.stdout", out):
            issue_cli.main(["queue", "add", "7", "8", "--project", str(self.project)])
            self.assertIn("acme/widgets#8 already has a run", out.getvalue())
            queue = issue_queue.load(self.project)
            queue["entries"][0]["state"] = "started"
            issue_queue.save(self.project, queue)
            issue_cli.main(["queue", "remove", "7", "--project", str(self.project)])
        self.assertEqual(["acme/widgets#7"], [e["ref"] for e in issue_queue.load(self.project)["entries"]])

    def test_options_go_only_to_start_and_queue_add(self):
        with patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(2, issue_cli.main(["queue", "run", "--project", str(self.project), "--", "--engine", "x"]))
        self.assertIn("queue add", err.getvalue())


class ListingTests(unittest.TestCase):
    """What `issue list --json` says about each open issue, read from the issue records and `autocode runs`."""

    def test_each_issue_shows_its_run_queue_place_and_named_pull_request_action(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            done = project / issue_queue.STORE / "acme-widgets-7"
            record = {
                "owner": "acme",
                "repo": "widgets",
                "number": 7,
                "title": "Done one",
                "worktree": str(done),
                "run_dir": str(done / ".autocode/runs/r7"),
                "branch": "autocode/issue-7",
                "pr_url": None,
            }
            issue_queue.record_path(project, github.IssueRef("acme", "widgets", 7)).parent.mkdir(parents=True)
            issue_queue.record_path(project, github.IssueRef("acme", "widgets", 7)).write_text(json.dumps(record))
            issue_queue.save(
                project,
                {
                    **issue_queue._empty(),
                    "entries": [{"ref": "acme/widgets#9", "slug": "acme-widgets-9", "number": 9, "state": "queued"}],
                },
            )
            rows = [
                {"run_dir": record["run_dir"], "workspace": str(done), "bucket": "settled", "status": "TASK_COMPLETE"}
            ]
            view = issue_queue.listing(
                project,
                "acme",
                "widgets",
                [issue_json(9, "Queued one"), issue_json(8, "Untouched", ["bug"]), issue_json(7, "Done one")],
                rows,
            )
            started = issue_queue.queue_view(project, rows=rows)["issues"]
        self.assertEqual(
            [("acme/widgets#7", "Done one", "settled")], [(r["ref"], r["title"], r["bucket"]) for r in started]
        )
        self.assertEqual(
            ["issue", "pr", "acme/widgets#7", "--project", str(project), "--open"],
            started[0]["actions"]["open_pr"]["next_command"],
        )
        by_number = {row["number"]: row for row in view["issues"]}
        self.assertEqual("queued", by_number[9]["queue_state"])
        self.assertIsNone(by_number[9]["run"])
        self.assertEqual(["bug"], by_number[8]["labels"])
        self.assertEqual({}, by_number[8]["actions"])
        self.assertEqual("settled", by_number[7]["run"]["bucket"])
        self.assertEqual(
            ["issue", "pr", "acme/widgets#7", "--project", str(project), "--open"],
            by_number[7]["actions"]["open_pr"]["next_command"],
        )
        self.assertEqual(
            ["issue", "queue", "add", "--project", str(project), "--max-active", "LIMIT", "ISSUES"],
            view["queue_add"]["next_command"],
        )
        self.assertEqual(0, view["queue"]["working"])  # the finished run uses no model

    def test_a_finished_run_with_a_pull_request_names_no_action(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            path = issue_queue.record_path(project, github.IssueRef("acme", "widgets", 7))
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({"run_dir": str(project / "r"), "pr_url": "https://github.com/acme/widgets/pull/3"})
            )
            rows = [{"run_dir": str(project / "r"), "bucket": "settled", "status": "TASK_COMPLETE"}]
            view = issue_queue.listing(project, "acme", "widgets", [issue_json(7, "Done")], rows)
        self.assertEqual({}, view["issues"][0]["actions"])
        self.assertEqual("https://github.com/acme/widgets/pull/3", view["issues"][0]["run"]["pr_url"])


class _FakeGitHub(BaseHTTPRequestHandler):
    issues = {}

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/repos/acme/widgets/issues":
            payload = list(self.issues.values())
        elif path.startswith("/repos/acme/widgets/issues/") and path.rsplit("/", 1)[1].isdigit():
            payload = self.issues.get(int(path.rsplit("/", 1)[1]))
        else:
            payload = None
        body = json.dumps(payload).encode()
        self.send_response(200 if payload is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class QueueCliTests(unittest.TestCase):
    """Two issues queued with a limit of one both start through the real CLI and the offline fixture provider:
    a run waiting for its plan's approval uses no model, so it gives its slot to the next issue."""

    def test_queue_starts_every_issue_and_list_shows_each_waiting_for_its_plan(self):
        with tempfile.TemporaryDirectory(prefix="issue-queue-cli-") as temp:
            root = Path(temp)
            project = Project(root).path
            bindir = root / "bin"
            bindir.mkdir()
            shutil.copy2(TOOLS / "live_fixture_provider.py", bindir / "codex")
            (bindir / "codex").chmod(0o755)
            _FakeGitHub.issues = {7: issue_json(7, "Add a greeting CLI"), 8: issue_json(8, "Add a greeting CLI too")}
            server = HTTPServer(("127.0.0.1", 0), _FakeGitHub)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            env = {
                **os.environ,
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

            for key in ("OPENAI_API_KEY", "CODEX_API_KEY"):  # Codex refuses to start beside an API key
                env.pop(key, None)

            def cli(*args):
                return subprocess.run(
                    [sys.executable, str(TOOLS / "autocode.py"), "issue", *args],
                    capture_output=True,
                    text=True,
                    env=env,
                    cwd=project,
                    timeout=600,
                )

            added = cli("queue", "add", "7", "8", "--max-active", "1", "--", *FIXTURE_OPTIONS)
            self.assertEqual(0, added.returncode, added.stderr)
            ran = cli("queue", "run")
            self.assertEqual(0, ran.returncode, ran.stdout + ran.stderr + cli("queue", "status", "--json").stdout)
            status = json.loads(cli("queue", "status", "--json").stdout)
            self.assertEqual(["started", "started"], [entry["state"] for entry in status["entries"]])
            self.assertIsNone(status["next_command"])
            listed = cli("list", "--json")
            self.assertEqual(0, listed.returncode, listed.stderr)
            rows = {row["number"]: row for row in json.loads(listed.stdout)["issues"]}
        # The queue's own view lists the same runs without reading GitHub, for a board to show.
        self.assertEqual({7, 8}, {run["number"] for run in status["issues"]})
        self.assertTrue(all(run["bucket"] == "needs you" and not run["actions"] for run in status["issues"]))
        for number in (7, 8):
            self.assertEqual("needs you", rows[number]["run"]["bucket"], rows[number])
            self.assertEqual("approve_plan", rows[number]["run"]["needs"])
            self.assertTrue(rows[number]["run"]["branch"].startswith(f"autocode/issue-{number}-"))


if __name__ == "__main__":
    unittest.main()
