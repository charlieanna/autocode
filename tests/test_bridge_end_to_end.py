"""The channel bridge through the real CLI: a labeled issue becomes an evidence pull request.

A loopback GitHub stands in for the channel; the bridge, the run, the worktree,
the approvals and the pull-request preparation are the real ones, with the
offline fixture provider and the explicit owned mode (the same no-systemd mode
the GitHub Action uses). This is the flow docs/channels.md promises, driven the
way a person would drive it: label, comment, approve, read the result. With a
run limit, three labeled issues are worked one at a time, lowest number first,
each to its own evidence pull request; every poll is a new daemon process, so
the queue is also carried across restarts.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

TOOLS = Path(__file__).resolve().parents[1] / "tools"
BOT = "autocode-bot"
ACTOR = "dev"


class FakeGitHub(BaseHTTPRequestHandler):
    """The three reads and one write the bridge makes, over in-memory state."""

    issues = {}
    comments = {}
    next_id = 5000

    @classmethod
    def add_comment(cls, number, login, body):
        cls.next_id += 1
        cls.comments[number].append({"id": cls.next_id, "user": {"login": login}, "body": body})
        return cls.next_id

    def _send(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        one = re.fullmatch(r"/repos/acme/widgets/issues/(\d+)(/comments.*)?", self.path)
        if self.path.startswith("/repos/acme/widgets/issues?"):
            self._send([row for row in FakeGitHub.issues.values() if row.get("state") == "open"])
        elif one and int(one.group(1)) in FakeGitHub.issues and one.group(2):
            self._send(FakeGitHub.comments[int(one.group(1))][-30:])
        elif one and int(one.group(1)) in FakeGitHub.issues:
            number = int(one.group(1))
            self._send({**FakeGitHub.issues[number], "comments": len(FakeGitHub.comments[number])})
        else:
            self.send_error(404)

    def do_POST(self):
        number = int(re.fullmatch(r"/repos/acme/widgets/issues/(\d+)/comments", self.path).group(1))
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        identity = FakeGitHub.add_comment(number, BOT, body.get("body") or "")
        data = json.dumps({"id": identity}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class BridgeEndToEndTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="bridge-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.remote = root / "acme" / "widgets.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        self.project = root / "project"
        self.project.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "base")
        self.git("remote", "add", "origin", str(self.remote))
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(TOOLS / "live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        FakeGitHub.issues = {
            7: {
                "number": 7,
                "title": "Add a greeting CLI",
                "state": "open",
                "body": BRIEF,
                "labels": [{"name": "autocode"}],
                "html_url": "https://github.com/acme/widgets/issues/7",
            }
        }
        FakeGitHub.comments = {7: []}
        FakeGitHub.next_id = 5000
        server = HTTPServer(("127.0.0.1", 0), FakeGitHub)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@example.test",
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "AUTOCODE_BRIDGE_HOME": str(root / "bridge"),
            "AUTOCODE_GITHUB_API": f"http://127.0.0.1:{server.server_port}",
            "GITHUB_TOKEN": "t",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "AUTOCODE_BRIDGE_GITHUB_REPO": "acme/widgets",
            "AUTOCODE_BRIDGE_PROJECT": str(self.project),
            "AUTOCODE_BRIDGE_ACTORS": json.dumps([ACTOR]),
            "AUTOCODE_BRIDGE_OPTIONS": json.dumps(list(FIXTURE_OPTIONS)),
            "AUTOCODE_BRIDGE_OWNED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", "-C", str(cwd or self.project), "-c", "user.name=T", "-c", "user.email=t@example.test", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def bridge_once(self):
        result = subprocess.run(
            [sys.executable, str(TOOLS / "autocode.py"), "bridge", "run", "--once"],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=300,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return result

    def posted(self, number=7):
        return [row["body"] for row in FakeGitHub.comments[number] if row["user"]["login"] == BOT]

    def reply(self, text, number=7):
        FakeGitHub.add_comment(number, ACTOR, text)

    def said(self, number, words):
        return any(words in body for body in self.posted(number))

    def test_a_labeled_issue_becomes_an_evidence_pull_request(self):
        for _ in range(10):
            self.bridge_once()
            bodies = self.posted()
            if any("Task complete" in body for body in bodies):
                break
            if any("Plan for approval" in body for body in bodies) and not any(
                "Plan approved" in body for body in bodies
            ):
                self.reply("approve")
            elif any("questions for you" in body for body in bodies) and not any("Answered" in body for body in bodies):
                proposed = re.search(r"proposed: (.+)", "\n".join(bodies))
                self.reply(proposed.group(1).strip() if proposed else "sqlite")
        bodies = self.posted()
        self.assertTrue(any("took this issue" in body for body in bodies))
        self.assertTrue(any("Plan for approval" in body for body in bodies))
        self.assertTrue(any("Plan approved" in body for body in bodies))
        self.assertTrue(any("Task complete" in body for body in bodies))

        worktree = self.project / ".autocode" / "issues" / "acme-widgets-7"
        committed = self.git("show", "--name-only", "--format=%s", "HEAD", cwd=worktree).splitlines()
        self.assertEqual("Add a greeting CLI (#7)", committed[0])
        self.assertIn("greet.py", committed)
        body = (self.project / ".autocode" / "issues" / "acme-widgets-7-pr.md").read_text()
        self.assertTrue(body.startswith("Resolves acme/widgets#7."))
        self.assertIn("## Acceptance criteria", body)

        status = subprocess.run(
            [sys.executable, str(TOOLS / "autocode.py"), "bridge", "status", "--json"],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=60,
        )
        rows = json.loads(status.stdout)["records"]
        self.assertEqual(1, len(rows))
        self.assertEqual("done", rows[0]["gate"]["key"])
        self.assertTrue(rows[0]["pr_prepared"])
        self.assertTrue(rows[0]["owned"])

    def test_an_actors_words_before_the_plan_cannot_approve_it(self):
        self.reply("approve")  # written into the issue's history before the bridge ever takes it
        for _ in range(8):
            self.bridge_once()
            if any("Plan for approval" in body for body in self.posted()):
                break
        self.assertTrue(any("Plan for approval" in body for body in self.posted()))  # the run waits, untouched
        self.assertFalse(any("Plan approved" in body for body in self.posted()))
        worktree = self.project / ".autocode" / "issues" / "acme-widgets-7"
        self.assertNotIn("Add a greeting CLI (#7)", self.git("log", "--format=%s", cwd=worktree))
        self.assertFalse((self.project / ".autocode" / "issues" / "acme-widgets-7-pr.md").exists())

    def test_three_labeled_issues_are_worked_one_at_a_time_to_three_pull_requests(self):
        numbers = (7, 8, 9)
        for number in (9, 8):  # listed newest first, as GitHub does
            FakeGitHub.issues = {
                number: {
                    **FakeGitHub.issues[7],
                    "number": number,
                    "html_url": f"https://github.com/acme/widgets/issues/{number}",
                },
                **FakeGitHub.issues,
            }
            FakeGitHub.comments[number] = []
        self.env["AUTOCODE_BRIDGE_MAX_ACTIVE"] = "1"
        issues = self.project / ".autocode" / "issues"
        early_approval_checked = False

        for poll in range(40):
            self.bridge_once()
            took = [number for number in numbers if self.said(number, "took this issue")]
            done = [number for number in numbers if self.said(number, "Task complete")]
            # One slot: issues start lowest number first, and never two before one is delivered.
            self.assertEqual(list(numbers[: len(took)]), took, f"poll {poll}")
            self.assertLessEqual(len(took) - len(done), 1, f"poll {poll}")
            for number in numbers:
                self.assertEqual(number in took, (issues / f"acme-widgets-{number}").exists(), f"poll {poll}")
                if number not in took:  # a waiting issue is told once, and nothing else
                    self.assertEqual(1, len(self.posted(number)), f"poll {poll}")
                    self.assertIn("queued this issue", self.posted(number)[0])
            if poll == 0:
                self.reply("approve", 8)  # written while #8 waits: it must never approve #8's plan
            if len(done) == len(numbers):
                break
            for number in took:
                if number in done:
                    continue
                if self.said(number, "Plan for approval") and not self.said(number, "Plan approved"):
                    if number == 8 and not early_approval_checked:
                        self.bridge_once()  # one more poll with nothing new said: the early word stays unused
                        self.assertFalse(self.said(8, "Plan approved"))
                        early_approval_checked = True
                    self.reply("approve", number)
                elif self.said(number, "questions for you") and not self.said(number, "Answered"):
                    proposed = re.search(r"proposed: (.+)", "\n".join(self.posted(number)))
                    self.reply(proposed.group(1).strip() if proposed else "sqlite", number)

        self.assertTrue(early_approval_checked)
        for number in numbers:
            self.assertTrue(self.said(number, "Task complete"), number)
            self.assertEqual(0 if number == 7 else 1, sum("queued this issue" in body for body in self.posted(number)))
            worktree = issues / f"acme-widgets-{number}"
            committed = self.git("show", "--name-only", "--format=%s", "HEAD", cwd=worktree).splitlines()
            self.assertEqual(f"Add a greeting CLI (#{number})", committed[0])
            self.assertIn("greet.py", committed)
            self.assertEqual(
                f"autocode/issue-{number}-add-a-greeting-cli", self.git("branch", "--show-current", cwd=worktree)
            )
            body = (issues / f"acme-widgets-{number}-pr.md").read_text()
            self.assertTrue(body.startswith(f"Resolves acme/widgets#{number}."))

        status = subprocess.run(
            [sys.executable, str(TOOLS / "autocode.py"), "bridge", "status", "--json"],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=60,
        )
        rows = json.loads(status.stdout)["records"]
        self.assertEqual([7, 8, 9], [row["number"] for row in rows])
        for row in rows:
            self.assertEqual("done", row["gate"]["key"])
            self.assertTrue(row["pr_prepared"])
            self.assertTrue(row["settled"])
            self.assertNotIn("queued", row)


if __name__ == "__main__":
    unittest.main()
