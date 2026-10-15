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
        self.assertEqual([], provenance.view({}))

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
