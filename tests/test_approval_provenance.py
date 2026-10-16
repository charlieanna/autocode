"""Who approved, accepted completion, edited the plan or resumed a pause: capture, stamping, the status view rows
and the completion lines, and the pause a resume is found in (#722).

The public CLI runs that launch a fake provider are in test_approval_provenance_cli; --accept-completion and
--goal-file with --as, which launch nothing, run here through the real CLI entry in-process
(CompletionAcceptanceTests, GoalFileTests).
"""

import contextlib
import copy
import io
import json
import socket
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

import autocode_approval_provenance as provenance
import autocode_args
import autocode_configure as configure
import autocode_contract_identity as contract_identity
import autocode_goal_lifecycle as lifecycle
import autocode_jobs as jobs
import autocode_plan_file as plan_file
import autocode_run_setup as run_setup
import autocode_run_view as run_view
import autocode_support as support

from . import test_goals

IDENTITY = {"git_user_name": "Ana Example", "git_user_email": "ana@example.test"}


def plan_event(token="r1:abc", **extra):
    return {"kind": "goal_approval", "actor": "user_cli", "at": "2026-10-15T08:00:00+00:00", "token": token, **extra}


def review_event(criterion="C1", **extra):
    return {
        "kind": "human_review",
        "actor": "user_cli",
        "at": "2026-10-15T09:00:00+00:00",
        "criterion": criterion,
        "token": "r1:abc@src:val",
        **extra,
    }


def completion_event(**extra):
    return {
        "kind": "completion_accept",
        "actor": "user_cli",
        "at": "2026-10-15T10:00:00+00:00",
        "basis": "runner-verified gates; 2 completion-report attempts failed",
        **extra,
    }


def recorded(workspace, called):
    return {**IDENTITY, "host": "desk-7", "command": called["command"], "claimed_as": called["claimed_as"]}


class InvocationTests(unittest.TestCase):
    def test_the_command_keeps_option_names_and_never_their_values(self):
        called = provenance.invocation(
            ["--run-dir", "/runs/r", "--approve-goal=r1:secret", "--review-token", "tok", "--", "--not-an-option"],
            claimed_as="  Bo  ",
        )
        self.assertEqual({"command": "autocode --run-dir --approve-goal --review-token", "claimed_as": "Bo"}, called)
        self.assertNotIn("secret", called["command"])
        self.assertIsNone(provenance.invocation(["resume"])["claimed_as"])

    def test_capture_reads_the_workspace_git_identity_and_the_host(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", directory], check=True)
            for key, value in (("user.name", "Ana Example"), ("user.email", "ana@example.test")):
                subprocess.run(["git", "-C", directory, "config", key, value], check=True)
            captured = provenance.capture(
                directory, provenance.invocation(["--approve-goal"]), hostname=lambda: "desk-7"
            )
        self.assertEqual(
            {**IDENTITY, "host": "desk-7", "command": "autocode --approve-goal", "claimed_as": None}, captured
        )

    def test_an_unset_identity_or_a_failing_git_is_recorded_as_unknown(self):
        unset = lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="")

        def missing(*args, **kwargs):
            raise FileNotFoundError("git")

        for run in (unset, missing):
            with self.subTest(run=run):
                captured = provenance.capture("/nowhere", {"command": "autocode"}, run=run, hostname=lambda: "")
                self.assertEqual(
                    (None, None, None), (captured["git_user_name"], captured["git_user_email"], captured["host"])
                )

    def test_claims_are_single_line_bounded_names(self):
        self.assertIsNone(provenance.claim_refusal(None))
        self.assertIsNone(provenance.claim_refusal("Bo at the shared desk"))
        for name in ("", "   ", "Bo\nEve", "x" * (provenance.CLAIM_LIMIT + 1)):
            with self.subTest(name=name):
                self.assertTrue(provenance.claim_refusal(name))


class StampTests(unittest.TestCase):
    def test_only_the_approvals_this_decision_added_are_stamped(self):
        earlier = review_event("C0")
        saved = {"workspace": "/w", "user_events": [earlier]}
        new_plan, answer = plan_event(), {"kind": "answer", "actor": "user_cli", "question_id": "Q1"}
        candidate = {"workspace": "/w", "user_events": [dict(earlier), answer, new_plan]}
        candidate["goal_contract"] = {"approval_event": new_plan}  # the same object, as lifecycle.approve saves it
        called = provenance.invocation(["--approve-goal", "--as"], claimed_as="Bo")
        self.assertEqual(1, provenance.stamp(saved, candidate, called, record=recorded))
        self.assertEqual("Bo", new_plan["provenance"]["claimed_as"])
        self.assertEqual(new_plan["provenance"], candidate["goal_contract"]["approval_event"]["provenance"])
        self.assertNotIn("provenance", candidate["user_events"][0])  # a repeated approval keeps its first record
        self.assertNotIn("provenance", answer)

    def test_a_completion_acceptance_is_stamped_like_an_approval(self):
        accepted = completion_event()
        candidate = {"workspace": "/w", "user_events": [plan_event(), accepted]}
        called = provenance.invocation(["--run-dir", "/r", "--accept-completion", "--as", "Bo"], claimed_as="Bo")
        self.assertEqual(1, provenance.stamp({"user_events": [plan_event()]}, candidate, called, record=recorded))
        self.assertEqual("autocode --run-dir --accept-completion --as", accepted["provenance"]["command"])
        self.assertEqual("Bo", accepted["provenance"]["claimed_as"])
        self.assertNotIn("provenance", candidate["user_events"][0])

    def test_nothing_is_captured_when_no_approval_was_added(self):
        def refuse(*args):
            raise AssertionError("no approval, no capture")

        saved = {"user_events": [plan_event()]}
        candidate = {"user_events": [plan_event(), {"kind": "answer", "actor": "user_cli"}]}
        self.assertEqual(0, provenance.stamp(saved, candidate, {"command": "autocode"}, record=refuse))
        self.assertEqual(0, provenance.stamp({}, {"user_events": [plan_event(actor="runner")]}, {}, record=refuse))

    def test_a_goal_edit_records_who_edited_on_the_draft_it_installed(self):
        before = {"revision": 1, "hash": "a", "origin": "astra_finalize", "created_at": "2026-10-15T07:00:00+00:00"}
        saved = {"workspace": "/w", "user_events": [], "goal_contract": before}
        edited = {"revision": 2, "hash": "b", "origin": "user_cli_edit", "created_at": "2026-10-15T07:30:00+00:00"}
        candidate = {"workspace": "/w", "user_events": [], "goal_contract": edited, "contract_history": [dict(before)]}
        called = provenance.invocation(["--run-dir", "/r", "--edit-goal", "body.json", "--as", "Bo"], claimed_as="Bo")
        self.assertEqual(1, provenance.stamp(saved, candidate, called, record=recorded))
        self.assertEqual("autocode --run-dir --edit-goal --as", edited["provenance"]["command"])
        self.assertEqual("Bo", edited["provenance"]["claimed_as"])
        self.assertNotIn("provenance", candidate["contract_history"][0])

    def test_a_saved_edit_or_a_draft_no_person_wrote_is_never_stamped(self):
        def refuse(*args):
            raise AssertionError("no new edit, no capture")

        # An edit saved before AutoCode recorded editors, then approved: the approver did not write it.
        legacy = {"revision": 2, "hash": "b", "origin": "user_cli_edit"}
        saved = {"user_events": [], "goal_contract": dict(legacy)}
        self.assertEqual(0, provenance.stamp(saved, {"user_events": [], "goal_contract": legacy}, {}, record=refuse))
        planned = {"revision": 3, "hash": "c", "origin": "astra_finalize"}
        self.assertEqual(0, provenance.stamp(saved, {"goal_contract": planned}, {}, record=refuse))
        self.assertNotIn("provenance", legacy)

    def test_a_terminal_decision_is_applied_then_stamped(self):
        candidate = {"workspace": "/w", "user_events": []}
        provenance.decide(
            lambda value: value["user_events"].append(review_event()),
            {"user_events": []},
            candidate,
            ["--workspace", "/w", "--chat"],
            record=recorded,
        )
        self.assertEqual("autocode --workspace --chat", candidate["user_events"][0]["provenance"]["command"])


class ResumeTests(unittest.TestCase):
    def test_a_resume_that_released_its_pause_records_who_resumed(self):
        state = {"workspace": "/w", "status": "RUNNING"}
        called = provenance.invocation(["--workspace", "/w", "--no-chat", "--as", "Cy"], claimed_as="Cy", word="resume")
        at = "2026-10-15T10:00:00+00:00"
        self.assertTrue(provenance.acknowledge(state, "PAUSED_TIME_LIMIT", called, at=at, record=recorded))
        self.assertEqual(
            [
                {
                    "at": at,
                    "pause_status": "PAUSED_TIME_LIMIT",
                    "provenance": {
                        **IDENTITY,
                        "host": "desk-7",
                        "command": "autocode resume --workspace --no-chat --as",
                        "claimed_as": "Cy",
                    },
                }
            ],
            state["pause_acknowledgements"],
        )

    def test_nothing_is_recorded_without_a_released_pause(self):
        def refuse(*args):
            raise AssertionError("no released pause, no capture")

        called = provenance.invocation(["--resume-paused"])
        for state, pause in (
            ({"status": "RUNNING"}, None),  # the resume found no pause: the run was running or waiting
            ({"status": "PAUSED_TIME_LIMIT"}, "PAUSED_TIME_LIMIT"),  # the pause still holds the run
        ):
            with self.subTest(state=state):
                self.assertFalse(provenance.acknowledge(state, pause, called, at="now", record=refuse))
                self.assertNotIn("pause_acknowledgements", state)


class ResumedPauseTests(unittest.TestCase):
    def test_the_pause_a_resume_finds_holding_the_run(self):
        operational = {
            "status": "WAITING_FOR_USER",
            "resolver_human_proposal": {
                "scope": "operational_exhaustion",
                "origin": {"pause_status": "PAUSED_ITERATION_LIMIT"},
            },
        }
        cases = (
            ({"status": "PAUSED_TIME_LIMIT"}, "PAUSED_TIME_LIMIT"),
            ({"status": "BLOCKED_ENVIRONMENT"}, "BLOCKED_ENVIRONMENT"),
            ({"status": "PAUSED_DESIGN_CONFLICT"}, "PAUSED_DESIGN_CONFLICT"),  # waits for an edit, then resumes
            (operational, "PAUSED_ITERATION_LIMIT"),  # the pause behind AutoResolver's operational request
            ({"status": "WAITING_FOR_USER"}, None),  # waiting on a person's answer or approval
            ({"status": "AWAITING_GOAL_APPROVAL"}, None),
            ({"status": "RUNNING"}, None),
        )
        for state, pause in cases:
            with self.subTest(status=state["status"]):
                self.assertEqual(pause, run_setup.resumed_pause(state))

    def test_a_pause_intervention_names_the_pause_it_interrupted(self):
        intent = {"held_pause": {"status": "PAUSED_TIME_LIMIT", "stop_reason": "Saved active-time limit reached"}}
        state = {"status": "PAUSED_INTERVENTION", "pause_intent": intent}
        self.assertEqual("PAUSED_TIME_LIMIT", run_setup.resumed_pause(state))
        # One that interrupted nothing, or was already acknowledged, is the pause itself.
        self.assertEqual("PAUSED_INTERVENTION", run_setup.resumed_pause({"status": "PAUSED_INTERVENTION"}))
        acknowledged = {"status": "PAUSED_INTERVENTION", "pause_intent": {**intent, "acknowledged_at": "now"}}
        self.assertEqual("PAUSED_INTERVENTION", run_setup.resumed_pause(acknowledged))


class ViewTests(unittest.TestCase):
    def test_every_approval_is_listed_oldest_first_with_its_provenance(self):
        stamped = {**IDENTITY, "host": "desk-7", "command": "autocode --approve-goal --as", "claimed_as": "Bo"}
        state = {
            "user_events": [
                plan_event("r1:old"),  # saved before AutoCode recorded approvers
                {"kind": "answer", "actor": "user_cli"},
                plan_event("r2:new", provenance=stamped),
                review_event(provenance={**stamped, "command": "autocode --approve-review", "claimed_as": None}),
            ]
        }
        rows = provenance.view(state)
        self.assertEqual(
            [("plan", "r1:old", None), ("plan", "r2:new", None), ("review", None, "C1")],
            [(row["kind"], row["contract_token"], row["criterion"]) for row in rows],
        )
        self.assertIsNone(rows[0]["provenance"])
        self.assertEqual(stamped, rows[1]["provenance"])
        self.assertEqual("user_cli", rows[2]["actor"])
        self.assertEqual([None, None, None], [row["pause_status"] for row in rows])
        self.assertEqual([], provenance.view({}))

    def test_goal_edits_and_resumes_are_listed_in_time_order_with_the_approvals(self):
        stamped = {**IDENTITY, "host": "desk-7", "command": "autocode --edit-goal", "claimed_as": None}
        resumed = {**stamped, "command": "autocode resume --workspace"}
        edited = {"revision": 3, "hash": "b", "origin": "user_cli_edit", "created_at": "2026-10-15T07:30:00+00:00"}
        state = {
            "contract_history": [
                {"revision": 1, "hash": "a", "origin": "astra_finalize", "created_at": "2026-10-15T07:00:00+00:00"},
                # An edit saved before AutoCode recorded editors, replaced by the stamped one below.
                {"revision": 2, "hash": "old", "origin": "user_cli_edit", "created_at": "2026-10-15T07:10:00+00:00"},
            ],
            "goal_contract": {**edited, "provenance": stamped},
            "user_events": [plan_event("r3:b"), review_event()],
            "pause_acknowledgements": [
                {"at": "2026-10-15T08:30:00+00:00", "pause_status": "PAUSED_REQUESTED", "provenance": resumed}
            ],
        }
        rows = provenance.view(state)
        self.assertEqual(
            [
                ("edit", "2026-10-15T07:10:00+00:00", "r2:old", None),
                ("edit", "2026-10-15T07:30:00+00:00", "r3:b", None),
                ("plan", "2026-10-15T08:00:00+00:00", "r3:b", None),
                ("resume", "2026-10-15T08:30:00+00:00", None, "PAUSED_REQUESTED"),
                ("review", "2026-10-15T09:00:00+00:00", None, None),
            ],
            [(row["kind"], row["at"], row["contract_token"], row["pause_status"]) for row in rows],
        )
        self.assertEqual([None, stamped, None, resumed, None], [row["provenance"] for row in rows])
        self.assertEqual({"user_cli"}, {row["actor"] for row in rows})
        text = "\n".join(provenance.lines(state))
        self.assertIn("Edited plan r2:old saved 2026-10-15T07:10:00+00:00 by someone AutoCode did not record", text)
        self.assertIn(
            "Edited plan r3:b saved 2026-10-15T07:30:00+00:00 by Ana Example <ana@example.test> on desk-7 "
            "via `autocode --edit-goal`.",
            text,
        )
        self.assertIn(
            "Resumed from PAUSED_REQUESTED 2026-10-15T08:30:00+00:00 by Ana Example <ana@example.test> on desk-7 "
            "via `autocode resume --workspace`.",
            text,
        )

    def test_a_completion_acceptance_is_listed_and_named_in_the_completion_lines(self):
        stamped = {**IDENTITY, "host": "desk-7", "command": "autocode --accept-completion --as", "claimed_as": "Bo"}
        older = completion_event(at="2026-10-14T10:00:00+00:00")  # accepted before AutoCode recorded who accepted
        state = {"user_events": [older, plan_event(), completion_event(provenance=stamped)]}
        rows = provenance.view(state)
        self.assertEqual(["completion", "plan", "completion"], [row["kind"] for row in rows])
        self.assertEqual(
            {
                "kind": "completion",
                "at": "2026-10-15T10:00:00+00:00",
                "actor": "user_cli",
                "contract_token": None,
                "criterion": None,
                "pause_status": None,
                "provenance": stamped,
            },
            rows[-1],
        )
        self.assertIsNone(rows[0]["provenance"])
        text = "\n".join(provenance.lines(state))
        self.assertIn("Completion accepted 2026-10-14T10:00:00+00:00 by someone AutoCode did not record", text)
        self.assertIn(
            "Completion accepted 2026-10-15T10:00:00+00:00 by Ana Example <ana@example.test> on desk-7 via "
            "`autocode --accept-completion --as`, who gave the name 'Bo' (a claim, not verified).",
            text,
        )

    def test_an_edit_the_history_also_holds_is_listed_once(self):
        stamped = {**IDENTITY, "host": "desk-7", "command": "autocode --edit-goal", "claimed_as": None}
        edited = {
            "revision": 2,
            "hash": "b",
            "origin": "user_cli_edit",
            "created_at": "2026-10-15T07:30:00+00:00",
            "provenance": stamped,
        }
        state = {"contract_history": [dict(edited)], "goal_contract": edited}
        self.assertEqual(
            [("edit", "r2:b", stamped)],
            [(row["kind"], row["contract_token"], row["provenance"]) for row in provenance.view(state)],
        )

    def test_the_completion_lines_name_who_where_how_and_the_claim(self):
        stamped = {**IDENTITY, "host": "desk-7", "command": "autocode --approve-goal --as", "claimed_as": "Bo"}
        text = "\n".join(provenance.lines({"user_events": [plan_event("r1:old"), plan_event(provenance=stamped)]}))
        self.assertIn("Approvals:", text)
        self.assertIn("Plan r1:old approved 2026-10-15T08:00:00+00:00 by someone AutoCode did not record", text)
        self.assertIn(
            "Plan r1:abc approved 2026-10-15T08:00:00+00:00 by Ana Example <ana@example.test> on desk-7 "
            "via `autocode --approve-goal --as`, who gave the name 'Bo' (a claim, not verified).",
            text,
        )

    def test_a_run_that_recorded_no_approval_says_so_before_its_cost_line(self):
        # Such as a run saved before AutoCode kept approval events, or a bug fix or design that ended before a plan.
        for state in (
            {},
            {"user_events": [{"kind": "answer", "actor": "user_cli"}]},
            {"workflow": {"kind": "build"}},
            {"workflow": {"kind": "bugfix"}},
            {"workflow": {"kind": "design"}},
        ):
            with self.subTest(state=state):
                summary = jobs.render(state, lambda _: "TASK COMPLETE\n")
                self.assertIn(
                    "TASK COMPLETE\n\nApprovals: none recorded (no plan or review approval, plan edit or resume "
                    "from a pause was saved).\nCost: ",
                    summary,
                )

    def test_a_review_or_discussion_never_asks_for_an_approval_so_its_summary_does_not_say_none(self):
        resumed = {
            "at": "2026-10-15T08:30:00+00:00",
            "pause_status": "PAUSED_TIME_LIMIT",
            "provenance": {**IDENTITY, "host": "desk-7", "command": "autocode resume", "claimed_as": None},
        }
        for state, last in (
            ({"workflow": {"kind": "review"}, "review": {"verdict": "approve"}}, "Findings: "),
            ({"workflow": {"kind": "discuss"}, "answer": {"answer": "Yes."}}, "Evidence:"),
        ):
            with self.subTest(workflow=state["workflow"]["kind"]):
                summary = jobs.render(state, lambda _: "TASK COMPLETE\n")
                self.assertNotIn("Approvals", summary)
                self.assertIn(last, summary.splitlines()[-2])  # the job's own last line, just before the cost line
                self.assertTrue(summary.splitlines()[-1].startswith("Cost: "))
                # A resume is still the run's own record: the local summary lists it, as --status does.
                text = jobs.render({**state, "pause_acknowledgements": [resumed]}, lambda _: "TASK COMPLETE\n")
                self.assertIn("\n\nApprovals:\n  Resumed from PAUSED_TIME_LIMIT 2026-10-15T08:30:00+00:00 by Ana", text)


class ClaimArgumentTests(unittest.TestCase):
    def parse(self, *flags):
        with contextlib.redirect_stderr(io.StringIO()):
            return autocode_args.parse(None, ["--run-dir", "/saved/run", *flags], configure.DEFAULT_ROLE_MODELS)[0]

    def test_as_names_who_approves_accepts_edits_or_resumes_only(self):
        self.assertEqual("Bo", self.parse("--approve-goal", "r1:abc", "--as", "Bo").claimed_as)
        self.assertEqual("Bo", self.parse("--approve-review", "C1", "--review-token", "t", "--as", "Bo").claimed_as)
        self.assertEqual("Bo", self.parse("--accept-completion", "--as", "Bo").claimed_as)
        self.assertEqual("Bo", self.parse("--edit-goal", "body.json", "--as", "Bo").claimed_as)
        self.assertEqual("Bo", self.parse("--goal-file", "plan.md", "--as", "Bo").claimed_as)
        self.assertEqual("Bo", self.parse("--resume-paused", "--as", "Bo").claimed_as)
        self.assertIsNone(self.parse("--approve-goal", "r1:abc").claimed_as)
        for flags in (
            ("--as", "Bo"),
            ("--feedback", "Smaller", "--as", "Bo"),
            ("--approve-goal", "r1:abc", "--as", " "),
            ("--approve-goal", "r1:abc", "--as", "Bo\nEve"),
        ):
            with self.subTest(flags=flags), self.assertRaises(SystemExit) as refused:
                self.parse(*flags)
            self.assertEqual(2, refused.exception.code)


class CompletionAcceptanceTests(unittest.TestCase):
    """--accept-completion records who closed the run, through the real CLI entry in-process: a Git fixture
    workspace paused after a passing validation, and no agent may launch (test_goals.GoalTests.invoke)."""

    # The goal tests' Git fixture and CLI driver; binding the class here would run its tests again.
    setUp, approve, draft, decision, validation, invoke = (
        getattr(test_goals.GoalTests, name)
        for name in ("setUp", "approve", "draft", "decision", "validation", "invoke")
    )

    def paused_at_completion(self, email="ana@example.test"):
        """An approved run whose validation passed and whose completion report could not be produced, in a
        workspace whose Git identity is Ana's (or ``email``)."""
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), support.snapshot(self.root))
        self.validation()
        self.state.update(
            status="PAUSED_INVALID_OUTPUT",
            phase="PAUSED_OR_BLOCKED",
            next_stage="astra_review",
            stop_reason="Completion report could not be produced",
        )
        for key, value in (("user.name", "Ana Example"), ("user.email", email)):
            subprocess.run(["git", "-C", str(self.root), "config", key, value], check=True)

    def test_accepting_completion_records_who_accepted_in_status_and_the_evidence_report(self):
        self.paused_at_completion()
        self.assertEqual(0, self.invoke("--accept-completion", "--as", "Bo"), self.stderr)
        self.assertEqual("TASK_COMPLETE", self.state["status"])
        row = run_view.view(self.state)["approvals"][-1]
        self.assertEqual(
            {
                "kind": "completion",
                "at": row["at"],
                "actor": "user_cli",
                "contract_token": None,
                "criterion": None,
                "pause_status": None,
                "provenance": {
                    "git_user_name": "Ana Example",
                    "git_user_email": "ana@example.test",
                    "host": socket.gethostname() or None,
                    "command": "autocode --workspace --run-dir --accept-completion --as",
                    "claimed_as": "Bo",
                },
            },
            row,
        )
        self.assertTrue(row["at"])
        evidence = json.loads((self.run / "evidence.json").read_text())
        self.assertEqual(row, evidence["approvals"][-1])
        markdown = (self.run / "evidence.md").read_text()
        self.assertIn("| Completion accepted | Ana Example &lt;ana@example.test&gt; |", markdown)
        self.assertIn("resumed the run from a pause or accepted its completion, oldest first.", markdown)
        # The completion summary a completed run prints names who accepted it.
        self.assertEqual(0, self.invoke(), self.stderr)
        self.assertIn(
            f"  Completion accepted {row['at']} by Ana Example <ana@example.test> on "
            f"{socket.gethostname() or 'an unnamed host'} via `autocode --workspace --run-dir --accept-completion "
            "--as`, who gave the name 'Bo' (a claim, not verified).\n",
            self.stdout,
        )


class GoalFileTests(unittest.TestCase):
    """--goal-file PATH --as NAME records who loaded the plan file, through the real CLI entry in-process: the
    loaded revision is a goal edit, and ``--status`` lists it with the name given (test_goals.GoalTests.invoke)."""

    setUp, draft, invoke = (getattr(test_goals.GoalTests, name) for name in ("setUp", "draft", "invoke"))

    def test_a_plan_file_loaded_with_as_lists_the_claim_on_its_edit_row(self):
        self.draft()
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.state["status"])
        for key, value in (("user.name", "Ana Example"), ("user.email", "ana@example.test")):
            subprocess.run(["git", "-C", str(self.root), "config", key, value], check=True)
        exported = self.state["goal_contract"]
        edited = copy.deepcopy(exported["body"])
        edited["constraints"].append("Keep the greeting on one line")
        path = self.run / "plan.md"  # as `autocode plan --export` writes it, then edited and rendered
        path.write_text(
            plan_file.document(
                {
                    "autocode_plan": plan_file.FORMAT,
                    "goal_hash": plan_file.goal_hash(exported["body"]),
                    "revision": exported["revision"],
                    "approval_status": "not approved",
                    "contract": edited,
                }
            )
        )
        self.assertEqual(0, self.invoke("--goal-file", str(path), "--as", "Bo"), self.stderr)
        self.assertEqual(edited, self.state["goal_contract"]["body"])
        self.assertEqual(0, self.invoke("--status"), self.stderr)
        rows = [row for row in json.loads(self.stdout)["view"]["approvals"] if row["kind"] == "edit"]
        self.assertEqual(
            [
                {
                    "git_user_name": "Ana Example",
                    "git_user_email": "ana@example.test",
                    "host": socket.gethostname() or None,
                    "command": "autocode --workspace --run-dir --goal-file --as",
                    "claimed_as": "Bo",
                }
            ],
            [row["provenance"] for row in rows],
        )
        self.assertEqual(contract_identity.token(self.state["goal_contract"]), rows[0]["contract_token"])
