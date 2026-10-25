"""The bridge daemon end to end, against a scripted run and a scripted channel.

The real relay, worker action code, store and daemon loop run; only the channel
transport, the durable launcher and the CLI's own process calls are fakes. What
these tests pin is the contract the docs promise: a labeled issue is taken once,
each gate is posted once, a reply becomes exactly the action the fresh view
names, words written before a gate cannot answer it, and a changed plan token
refuses an approval instead of approving the new plan unseen.
"""

import tempfile
import unittest
from pathlib import Path

import autocode_bridge_cli as bridge_cli
import autocode_bridge_relay as bridge_relay
import autocode_bridge_store as store
import autocode_bridge_worker as worker
import autocode_github as github

PLAN_TOKEN = "r1:plan"
SECOND_TOKEN = "r2:revised"


def plan_body(token):
    return {
        "revision": 2 if token == SECOND_TOKEN else 1,
        "token": token,
        "acceptance_criteria": [
            {"id": "c1", "criterion": "greets by name", "verification_method": "unittest", "human_review": False}
        ],
        "constraints": [],
        "permission_boundaries": [],
    }


class FakeRun:
    # revise_on_feedback: a plan-gate reply marked as feedback moves the run to a revised plan.
    """The run as the bridge sees it: questions, then a plan, then done — moved only by actions."""

    def __init__(self, *, revise_after_answer=False, revise_on_feedback=False):
        self.revise_after_answer = revise_after_answer
        self.revise_on_feedback = revise_on_feedback
        self.state = "questions"
        self.calls = []
        self.workspace = Path("/work/project/.autocode/issues/owner-repo-7")
        self.run_dir = self.workspace / ".autocode" / "runs" / "issue-7"

    def status(self):
        if self.state == "questions":
            return {
                "status": "WAITING_FOR_USER",
                "needs": {
                    "kind": "answer",
                    "resolver_request_id": "req-1",
                    "resolver_token": "t1",
                    "questions": [
                        {
                            "id": "q-1",
                            "question": "Which database?",
                            "options": ["sqlite", "postgres"],
                            "proposed_default": "sqlite",
                        }
                    ],
                },
            }
        if self.state in ("plan", "plan2"):
            token = SECOND_TOKEN if self.state == "plan2" else PLAN_TOKEN
            return {
                "status": "AWAITING_GOAL_APPROVAL",
                "needs": {"kind": "approve_plan", "token": token},
                "displayed_plan": plan_body(token),
            }
        return {"status": "COMPLETE", "done": True, "evidence": {"outcome": "delivered"}}

    def answer(self, question_id, text, *, resolver_token=None):
        self.calls.append(("answer", question_id, text))
        self.state = "plan2" if self.revise_after_answer else "plan"

    def approve_plan(self, token):
        self.calls.append(("approve", token))
        if token != (SECOND_TOKEN if self.state == "plan2" else PLAN_TOKEN):
            raise AssertionError("a stale token was submitted")
        self.state = "done"

    def feedback(self, text):
        self.calls.append(("feedback", text))
        if self.revise_on_feedback and self.state in ("plan", "plan2"):
            self.state = "plan2"

    def follow_up(self, text):
        self.calls.append(("follow_up", text))

    def resume_paused(self):
        self.calls.append(("resume",))

    def advance(self):
        self.calls.append(("advance",))

    def dispatch_durable(self, command, *, view):
        self.calls.append(("dispatch", tuple(command.argv), dict(command.env)))


class FakeClient:
    def __init__(self, issues):
        self.issues = issues
        self.posted = []
        self.next_id = 1000

    def open_issues(self, owner, repo, label):
        return [
            row["issue"]
            for row in self.issues.values()
            if row["issue"].get("state") == "open"
            and label in [item.get("name") for item in row["issue"].get("labels") or []]
        ]

    def issue(self, ref):
        row = self.issues[ref.number]
        return {**row["issue"], "comments_list": list(row["comments"])}

    def issue_comment(self, ref, body):
        self.next_id += 1
        self.posted.append((ref.number, body))
        return {"id": self.next_id}


def comment(client, number, login, body):
    client.next_id += 1
    client.issues[number]["comments"].append({"id": client.next_id, "user": {"login": login}, "body": body})
    return client.next_id


class BridgeTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        import os

        self.saved_home = os.environ.get("AUTOCODE_BRIDGE_HOME")
        os.environ["AUTOCODE_BRIDGE_HOME"] = home.name
        self.home = home
        self.client = FakeClient(
            {
                7: {
                    "issue": {"number": 7, "state": "open", "title": "Greeting CLI", "labels": [{"name": "autocode"}]},
                    "comments": [],
                }
            }
        )
        self.prepared = []

    def tearDown(self):
        import os

        if self.saved_home is None:
            os.environ.pop("AUTOCODE_BRIDGE_HOME", None)
        else:
            os.environ["AUTOCODE_BRIDGE_HOME"] = self.saved_home

    def bridge(self, run, *, launch=None):
        from autocode_bridge_github import GitHubChannel

        config = bridge_cli.Config(
            repo="owner/repo",
            project=Path("/work/project"),
            label="autocode",
            actors=("dev",),
            options=(),
            poll_seconds=10.0,
            open_pr=False,
        )
        started = launch or (
            lambda project, ref, issue, options, owned=False: {
                "owner": "owner",
                "repo": "repo",
                "number": 7,
                "title": "Greeting CLI",
                "project": str(project),
                "worktree": str(run.workspace),
                "branch": "autocode/issue-7-greeting-cli",
                "run_dir": str(run.run_dir),
                "options": options,
                "receipt": {"launch_id": "a" * 32},
                "ready": True,
                "gate": {},
                "delivered": False,
                "pr_url": None,
            }
        )
        return bridge_cli.Bridge(
            config,
            GitHubChannel(self.client, "owner/repo", "autocode", ("dev",)),
            launch=started,
            adopt=lambda record: run,
            prepare=lambda record, *, open_: (
                self.prepared.append((record["number"], open_)) or "PR body written; draft PR #12"
            ),
        )

    def bodies(self):
        return [body for _, body in self.client.posted]

    def test_a_labeled_issue_runs_to_a_pull_request_through_the_channel(self):
        run = FakeRun()
        bridge = self.bridge(run)
        bridge.tick()
        self.assertTrue(any("took this issue" in body for body in self.bodies()))
        self.assertTrue(any("[q-1]" in body for body in self.bodies()))

        comment(self.client, 7, "dev", "postgres")
        bridge.tick()
        self.assertIn(("answer", "q-1", "postgres"), run.calls)
        self.assertTrue(any("Answered q-1" in body for body in self.bodies()))

        bridge.tick()  # the plan gate relays with its criteria
        self.assertTrue(any("greets by name" in body for body in self.bodies()))

        comment(self.client, 7, "dev", "approve")
        bridge.tick()
        self.assertEqual([("answer", "q-1", "postgres"), ("approve", PLAN_TOKEN)], run.calls)
        self.assertTrue(any("Plan approved" in body for body in self.bodies()))

        bridge.tick()  # delivery announces completion with the prepared pull request
        self.assertEqual([(7, False)], self.prepared)
        self.assertTrue(any("Task complete" in body for body in self.bodies()))
        self.assertTrue(any("draft PR #12" in body for body in self.bodies()))
        record = store.load("owner", "repo", 7)
        self.assertEqual("done", record["gate"]["key"])
        self.assertTrue(record["pr_prepared"])

        posted = len(self.bodies())
        bridge.tick()  # nothing new to say
        self.assertEqual(posted, len(self.bodies()))

    def test_an_approval_matching_no_plan_shown_here_is_refused(self):
        run = FakeRun()
        run.state = "plan"
        action = bridge_relay.ReplyAction("approve", shown_token="")
        with self.assertRaises(worker.BridgeError):
            worker.take(run, run.status(), action)
        stale = bridge_relay.ReplyAction("approve", shown_token="r0:other")
        with self.assertRaises(worker.BridgeError):
            worker.take(run, run.status(), stale)
        self.assertEqual([], run.calls)  # nothing reached the run

    def test_plan_feedback_revises_and_the_new_plan_is_relayed_under_its_token(self):
        run = FakeRun(revise_on_feedback=True)
        bridge = self.bridge(run)
        bridge.tick()
        comment(self.client, 7, "dev", "sqlite")
        bridge.tick()
        bridge.tick()  # plan v1
        comment(self.client, 7, "dev", "make the greeting shout")
        bridge.tick()  # taken as plan feedback, never as an approval
        self.assertIn(("feedback", "make the greeting shout"), run.calls)
        bridge.tick()  # the revised plan posts as a new gate with its own token
        plans = [body for body in self.bodies() if "Plan for approval" in body]
        self.assertEqual(2, len(plans))
        comment(self.client, 7, "dev", "approve")
        bridge.tick()
        self.assertIn(("approve", SECOND_TOKEN), run.calls)  # the approval bound to the plan the person saw
        bridge.tick()
        self.assertTrue(any("Task complete" in body for body in self.bodies()))

    def test_words_written_before_a_gate_cannot_answer_it(self):
        run = FakeRun()
        early = comment(self.client, 7, "dev", "approve")  # history before the bridge took the issue
        bridge = self.bridge(run)
        bridge.tick()
        bridge.tick()
        bridge.tick()
        self.assertEqual([], run.calls)  # the early approval is never even interpreted
        record = store.load("owner", "repo", 7)
        self.assertGreaterEqual(record["initial_comment_id"], early)

    def test_only_allowlisted_actors_steer_the_run(self):
        run = FakeRun()
        bridge = self.bridge(run)
        bridge.tick()
        comment(self.client, 7, "stranger", "postgres")
        bridge.tick()
        bridge.tick()
        self.assertEqual([], run.calls)

    def test_a_refused_start_is_recorded_and_never_retried(self):
        run = FakeRun()

        def refused(project, ref, issue, options, owned=False):
            raise worker.BridgeError("durable execution is unavailable on this host")

        bridge = self.bridge(run, launch=refused)
        bridge.tick()
        self.assertTrue(any("could not start this run" in body for body in self.bodies()))
        record = store.load("owner", "repo", 7)
        self.assertTrue(record["failed"])
        bridge.tick()
        self.assertEqual(1, sum(1 for body in self.bodies() if "could not start" in body))  # posted once

    def test_a_second_daemon_is_refused_while_the_lock_is_held(self):
        bridge = self.bridge(FakeRun())
        with open(store.root() / "bridge.lock", "a+") as lock:
            bridge_cli.util.lock_file(lock)
            with self.assertRaisesRegex(bridge_cli.ConfigError, "another bridge daemon already holds"):
                bridge.run(once=True)

    def test_config_comes_from_the_environment_and_names_what_is_missing(self):

        with self.assertRaises(bridge_cli.ConfigError):
            bridge_cli.Config.from_env({})
        config = bridge_cli.Config.from_env(
            {
                "AUTOCODE_BRIDGE_GITHUB_REPO": "owner/repo",
                "AUTOCODE_BRIDGE_ACTORS": '["dev", "lead"]',
                "AUTOCODE_BRIDGE_PROJECT": "/work/project",
                "AUTOCODE_BRIDGE_OPTIONS": '["--engine", "codex"]',
                "AUTOCODE_BRIDGE_OPEN_PR": "1",
            }
        )
        self.assertEqual(("dev", "lead"), config.actors)
        self.assertEqual(("--engine", "codex"), config.options)
        self.assertTrue(config.open_pr)
        with self.assertRaises(bridge_cli.ConfigError):
            bridge_cli.Config.from_env({"AUTOCODE_BRIDGE_GITHUB_REPO": "owner/repo", "AUTOCODE_BRIDGE_ACTORS": "dev"})


class ParserTests(unittest.TestCase):
    def test_the_subcommands_parse(self):
        parsed = bridge_cli.parser().parse_args(["run", "--once"])
        self.assertTrue(parsed.once)
        self.assertTrue(bridge_cli.parser().parse_args(["status", "--json"]).json)
        self.assertTrue(bridge_cli.parser().parse_args(["check"]))
        with self.assertRaises(SystemExit):
            bridge_cli.parser().parse_args(["nonsense"])


class ProvisionTests(unittest.TestCase):
    """The git and record work of taking an issue, with the launch itself faked."""

    def setUp(self):
        import subprocess

        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.project = Path(home.name) / "project"
        self.project.mkdir()
        identity = ["-c", "user.name=T", "-c", "user.email=t@example.test"]
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(
            ["git", "-C", str(self.project), *identity, "commit", "--allow-empty", "-qm", "Start"], check=True
        )
        store_home = tempfile.TemporaryDirectory()
        self.addCleanup(store_home.cleanup)
        import os

        self.saved_home = os.environ.get("AUTOCODE_BRIDGE_HOME")
        os.environ["AUTOCODE_BRIDGE_HOME"] = store_home.name

    def tearDown(self):
        import os

        if self.saved_home is None:
            os.environ.pop("AUTOCODE_BRIDGE_HOME", None)
        else:
            os.environ["AUTOCODE_BRIDGE_HOME"] = self.saved_home

    def launch(self, worktree, run_dir, brief, options):
        from autocode_taskrun import TaskRun

        self.brief = brief
        return TaskRun(Path(worktree), Path(run_dir), options=tuple(options)), {"launch_id": "b" * 32, "ready": True}

    def test_taking_an_issue_creates_the_worktree_and_both_records(self):
        import autocode_issue as issue_tool

        ref = github.IssueRef("acme", "widgets", 12)
        issue = {
            "number": 12,
            "title": "Greeting!",
            "body": "Say hi",
            "html_url": "https://github.com/acme/widgets/issues/12",
        }
        record = worker.start_issue_run(self.project, ref, issue, ["--engine", "codex"], launch=self.launch)
        self.assertTrue(record["worktree"].startswith(str(self.project.resolve())))
        self.assertTrue((self.project / ".autocode" / "issues" / "acme-widgets-12" / ".git").exists())
        self.assertEqual("autocode/issue-12-greeting", record["branch"])
        saved = issue_tool.load(self.project, ref)  # so autocode issue pr/status keep working for a person
        self.assertEqual(record["run_dir"], saved["run_dir"])
        self.assertIn("Resolve GitHub issue acme/widgets#12: Greeting!", self.brief)
        self.assertIn("--engine", " ".join(saved["options"]))
        again = store.load("acme", "widgets", 12)
        self.assertEqual(record["run_dir"], again["run_dir"])

    def test_launch_options_never_carry_a_human_action(self):
        ref = github.IssueRef("acme", "widgets", 13)
        issue = {"number": 13, "title": "Fix", "body": "b"}
        for forbidden in ("--approve-goal", "--answer", "--resume-paused", "--feedback"):
            with self.assertRaises(worker.BridgeError):
                worker.provider_options([forbidden, "x"])
        with self.assertRaises(worker.BridgeError):
            worker.start_issue_run(self.project, ref, issue, ["--resume-paused"], launch=self.launch)
        self.assertIsNone(store.load("acme", "widgets", 13))


if __name__ == "__main__":
    unittest.main()
