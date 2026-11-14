"""The bridge's run limit under random histories: what must hold whatever happens.

Seeded random sequences of what a deployment sees drive the real daemon loop, relay and
store against scripted runs: issues labeled, unlabeled and closed; runs working, waiting
for a person, delivered, stopped, impossible to resume, or reopened by a follow-up; starts
the host refuses; GitHub unreachable for discovery or for posting; the daemon restarted
with the limit changed or removed. Checked at every start and after every poll:

- no issue is started twice, and a refused start is never tried again;
- under a limit, an issue starts only while fewer runs than the limit are in flight;
- the issue that starts is the lowest-numbered one waiting;
- an issue that is closed or has lost its label never starts;
- no slot stays free while an open labeled issue waits, whenever GitHub could be read;
- a waiting issue is told at most once, and an issue that never waited is never told.

At the end of every history GitHub is reachable again and every run is finished: each
issue still open and labeled has then been started exactly once.

``AUTOCODE_BRIDGE_QUEUE_SEEDS`` runs more histories than the default (a deeper local sweep).
"""

import collections
import contextlib
import io
import os
import random
import tempfile
import unittest
from pathlib import Path

import autocode_bridge_cli as bridge_cli
import autocode_bridge_store as store
import autocode_bridge_worker as worker
import autocode_github as github
from autocode_bridge_github import GitHubChannel

from tests.test_bridge_cli import FakeClient, plan_body

SEEDS = int(os.environ.get("AUTOCODE_BRIDGE_QUEUE_SEEDS") or 60)
POLLS = 24
MOST_ISSUES = 9
IN_FLIGHT = ("working", "waiting")  # the test's own truth, not the admission module's


class ScriptedRun:
    def __init__(self, number):
        self.number = number
        self.state = "working"
        self.workspace = Path(f"/work/project/.autocode/issues/owner-repo-{number}")
        self.run_dir = self.workspace / ".autocode" / "runs" / f"issue-{number}"

    def status(self):
        if self.state == "working":
            return {"status": "RUNNING"}
        if self.state == "waiting":
            token = f"r1:plan-{self.number}"
            return {
                "status": "AWAITING_GOAL_APPROVAL",
                "needs": {"kind": "approve_plan", "token": token},
                "displayed_plan": plan_body(token),
            }
        if self.state == "done":
            return {"status": "COMPLETE", "done": True, "evidence": {"outcome": "delivered"}}
        if self.state == "stopped":
            return {"status": "STOPPED"}
        return {"status": "PAUSED", "needs": {"kind": "resume", "new_run_required": True, "reason": "retired"}}


class UnreliableGitHub(FakeClient):
    listing_down = False
    posting_down = False

    def open_issues(self, owner, repo, label):
        if self.listing_down:
            raise github.GitHubError("GitHub is unreachable")
        return super().open_issues(owner, repo, label)

    def issue_comment(self, ref, body):
        if self.posting_down:
            raise github.GitHubError("GitHub is unreachable")
        return super().issue_comment(ref, body)


class History:
    """One random history: the world, the daemon, and the checks made at each start and each poll."""

    def __init__(self, case, seed):
        self.case = case
        self.random = random.Random(seed)
        self.client = UnreliableGitHub({})
        self.runs = {}
        self.attempts = collections.Counter()
        self.refusing = set()
        self.waited = set()
        self.limit = self.random.choice((None, 1, 1, 2, 3))
        self.bridge = self.daemon()

    def daemon(self):
        config = bridge_cli.Config(
            repo="owner/repo",
            project=Path("/work/project"),
            label="autocode",
            actors=("dev",),
            options=(),
            poll_seconds=10.0,
            open_pr=False,
            max_active=self.limit,
        )
        return bridge_cli.Bridge(
            config,
            GitHubChannel(self.client, "owner/repo", "autocode", ("dev",)),
            launch=self.launch,
            adopt=lambda record: self.runs[record["number"]],
            prepare=lambda record, *, open_: "PR body written",
        )

    # ------------------------------------------------------------------ the world

    def wanted(self, number):
        issue = self.client.issues[number]["issue"]
        return issue["state"] == "open" and bool(issue["labels"])

    def waiting(self, number):
        record = store.load("owner", "repo", number)
        return bool(record and record.get("queued")) and self.wanted(number)

    def in_flight(self):
        return sum(1 for run in self.runs.values() if run.state in IN_FLIGHT)

    def launch(self, project, ref, issue, options, owned=False):
        number = ref.number
        self.attempts[number] += 1
        self.case.assertEqual(1, self.attempts[number], f"#{number} was started twice")
        self.case.assertTrue(self.wanted(number), f"#{number} started closed or without its label")
        if self.limit is not None:
            self.case.assertLess(self.in_flight(), self.limit, f"#{number} started with every slot in use")
            earlier = [other for other in self.client.issues if other < number and self.waiting(other)]
            self.case.assertEqual([], earlier, f"#{number} started ahead of a lower-numbered waiting issue")
        if number in self.refusing:
            raise worker.BridgeError("the host refused this start")
        run = self.runs[number] = ScriptedRun(number)
        return {
            "owner": "owner",
            "repo": "repo",
            "number": number,
            "title": issue["title"],
            "project": str(project),
            "worktree": str(run.workspace),
            "branch": f"autocode/issue-{number}",
            "run_dir": str(run.run_dir),
            "options": options,
            "receipt": {"launch_id": "a" * 32},
            "ready": True,
            "gate": {},
            "delivered": False,
            "pr_url": None,
        }

    def change_something(self):
        choice = self.random.random()
        numbers = sorted(self.client.issues)
        if choice < 0.30 and len(numbers) < MOST_ISSUES:
            number = max(numbers, default=0) + self.random.randint(1, 3)
            labels = [{"name": "autocode"}]
            self.client.issues[number] = {
                "issue": {"number": number, "state": "open", "title": f"Task {number}", "labels": labels},
                "comments": [],
            }
            if self.random.random() < 0.15:
                self.refusing.add(number)
        elif choice < 0.40 and numbers:
            issue = self.client.issues[self.random.choice(numbers)]["issue"]
            issue["labels"] = [] if issue["labels"] else [{"name": "autocode"}]
        elif choice < 0.46 and numbers:
            self.client.issues[self.random.choice(numbers)]["issue"]["state"] = "closed"
        elif choice < 0.90 and self.runs:
            run = self.runs[self.random.choice(sorted(self.runs))]
            if run.state in IN_FLIGHT:
                run.state = self.random.choice(("working", "waiting", "done", "done", "stopped", "unresumable"))
            elif run.state == "done" and self.random.random() < 0.4:
                run.state = "working"  # a follow-up reopened a delivered run
        elif choice >= 0.90:
            self.limit = self.random.choice((None, 1, 2, 3))
            self.bridge = self.daemon()  # the daemon restarted, perhaps with another limit

    # ------------------------------------------------------------------ one poll and its checks

    def poll(self, *, reliable=False):
        self.client.listing_down = not reliable and self.random.random() < 0.12
        self.client.posting_down = not reliable and self.random.random() < 0.12
        with contextlib.redirect_stderr(io.StringIO()):
            self.bridge.tick()
        still_waiting = [number for number in sorted(self.client.issues) if self.waiting(number)]
        if not self.client.listing_down:
            if self.limit is None:
                self.case.assertEqual([], still_waiting, "an issue waits though there is no limit")
            elif self.in_flight() < self.limit:
                self.case.assertEqual([], still_waiting, "a slot is free while an issue waits")
            self.waited.update(still_waiting)
        told = collections.Counter(number for number, body in self.client.posted if "queued this issue" in body)
        self.case.assertLessEqual(max(told.values(), default=0), 1, "a waiting issue was told more than once")
        self.case.assertLessEqual(set(told), self.waited, "an issue that never waited was told it waits")

    def finish(self):
        for _ in range(MOST_ISSUES + 2):  # each poll delivers what runs and starts what waits
            for run in self.runs.values():
                run.state = "done"
            self.poll(reliable=True)
        for number in self.client.issues:
            if self.wanted(number):
                self.case.assertEqual(1, self.attempts[number], f"#{number} was never started")
            self.case.assertLessEqual(self.attempts[number], 1)


class QueueInvariantTests(unittest.TestCase):
    def test_random_histories_keep_the_limit_the_order_and_the_queue_moving(self):
        saved = os.environ.get("AUTOCODE_BRIDGE_HOME")
        self.addCleanup(
            lambda: (
                os.environ.pop("AUTOCODE_BRIDGE_HOME", None)
                if saved is None
                else os.environ.__setitem__("AUTOCODE_BRIDGE_HOME", saved)
            )
        )
        started = 0
        for seed in range(SEEDS):
            with self.subTest(seed=seed), tempfile.TemporaryDirectory() as home:
                os.environ["AUTOCODE_BRIDGE_HOME"] = home
                history = History(self, seed)
                for _ in range(POLLS):
                    for _ in range(history.random.randint(1, 3)):
                        history.change_something()
                    history.poll()
                history.finish()
                started += sum(history.attempts.values())
        self.assertGreater(started, SEEDS)  # the histories did start runs, so the checks were exercised


if __name__ == "__main__":
    unittest.main()
