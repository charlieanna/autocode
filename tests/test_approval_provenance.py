"""Who approved each plan and review: capture, stamping, the status view rows and the completion lines (#722).

The public CLI runs are in test_approval_provenance_cli.
"""

import contextlib
import io
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

import autocode_approval_provenance as provenance
import autocode_args
import autocode_configure as configure

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
        self.assertEqual([], provenance.lines({"user_events": [{"kind": "answer"}]}))


class ClaimArgumentTests(unittest.TestCase):
    def parse(self, *flags):
        with contextlib.redirect_stderr(io.StringIO()):
            return autocode_args.parse(None, ["--run-dir", "/saved/run", *flags], configure.DEFAULT_ROLE_MODELS)[0]

    def test_as_names_the_approver_of_an_approval_only(self):
        self.assertEqual("Bo", self.parse("--approve-goal", "r1:abc", "--as", "Bo").claimed_as)
        self.assertEqual("Bo", self.parse("--approve-review", "C1", "--review-token", "t", "--as", "Bo").claimed_as)
        self.assertEqual("Bo", self.parse("--edit-goal", "body.json", "--as", "Bo").claimed_as)
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
