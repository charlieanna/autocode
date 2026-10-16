"""A project's require-approver: the setting's values, the refusal, where provenance enforces it, how a new run
takes it from the project file, how the terminal conversation stops at a refusal, and --accept-completion (#722).

The public CLI runs that launch a fake provider are in test_required_approver_cli.
"""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_approval_provenance as provenance
import autocode_args
import autocode_doctor as doctor
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_required_approver as required_approver
from goal_fixtures import body
from providers import opencode

from . import test_approval_provenance, test_goals

ANA = {"git_user_name": "Ana Example", "git_user_email": "ana@example.test", "host": "desk-7", "claimed_as": None}
SOURCE = "/w/.autocode/config.toml"


def run_requiring(*addresses):
    return {"workspace": "/w", "user_events": [], "settings": {required_approver.SETTING: list(addresses)}}


def plan_event(**extra):
    return {"kind": "goal_approval", "actor": "user_cli", "at": "2026-10-16T08:00:00+00:00", "token": "r1:a", **extra}


def as_ana(workspace, called):
    return {**ANA, "command": called["command"], "claimed_as": called["claimed_as"]}


class SettingTests(unittest.TestCase):
    def test_one_address_or_a_list_is_kept_in_order_without_repeats(self):
        self.assertEqual(["lead@example.test"], required_approver.checked(" lead@example.test ", SOURCE))
        self.assertEqual(
            ["Lead@Example.test", "deputy@example.test"],
            required_approver.checked(["Lead@Example.test", "deputy@example.test", "lead@example.TEST"], SOURCE),
        )

    def test_anything_but_email_addresses_is_refused_naming_the_file(self):
        for value in ([], 3, True, {"email": "a@b"}, ["a@b", 4], "", "Lead", "Lee <lee@x>", "a@b c", "a@b@c", "a\n@b"):
            with self.subTest(value=value), self.assertRaises(ValueError) as refused:
                required_approver.checked(value, SOURCE)
            self.assertIn(f"{SOURCE}: require-approver", str(refused.exception))
        with self.assertRaises(ValueError):
            required_approver.checked("a@" + "b" * required_approver.ADDRESS_LIMIT, SOURCE)

    def test_only_a_plain_address_passes(self):
        """One @ between a non-empty local part and domain, with no name, list, comment or quoting around it."""
        for value in (
            "<lee@x>",
            "Lee<lee@x>",
            "lee@x>",
            '"Lee"<lee@x>',
            '"lee"@x',
            "mailto:lee@x",
            "lee@x,dee@y",
            "lee@x,",
            "lee@x;",
            "(Lee)lee@x",
            "lee@[x]",
            "le\\e@x",
            "@x",
            "lee@",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as refused:
                    required_approver.checked(value, SOURCE)
                self.assertIn("is not a plain email address", str(refused.exception))
        self.assertEqual(
            ["o'neil+ci@example.test", "root@localhost"],
            required_approver.checked(["o'neil+ci@example.test", "root@localhost"], SOURCE),
        )

    def test_a_run_without_the_setting_lets_anyone_approve(self):
        for state in ({}, {"settings": {}}, {"settings": {required_approver.SETTING: []}}):
            with self.subTest(state=state):
                self.assertEqual([], required_approver.required(state))
                self.assertIsNone(required_approver.refusal(state, {**ANA, "git_user_email": None}))


class RefusalTests(unittest.TestCase):
    def test_a_listed_address_approves_whatever_its_case(self):
        state = run_requiring("deputy@example.test", "Ana@Example.TEST")
        self.assertIsNone(required_approver.refusal(state, ANA))
        required_approver.enforce(state, ANA)

    def test_another_identity_is_refused_naming_the_required_and_the_actual_identity(self):
        state = run_requiring("lead@example.test", "deputy@example.test")
        text = required_approver.refusal(state, {**ANA, "claimed_as": "Lee Lead"})
        self.assertIn("only from Git user.email lead@example.test or deputy@example.test", text)
        self.assertIn("this checkout's Git identity is Ana Example <ana@example.test>", text)
        self.assertIn("Nothing was approved and the run is still waiting at the same stop.", text)
        self.assertIn("The --as name 'Lee Lead' is a claim and does not count.", text)
        with self.assertRaises(required_approver.Refused):
            required_approver.enforce(state, ANA)
        self.assertNotIn("--as name", required_approver.refusal(state, ANA))

    def test_an_unset_identity_is_named_as_unset(self):
        state = run_requiring("lead@example.test")
        self.assertIn(
            "Git identity is Ana Example, with no user.email set",
            required_approver.refusal(state, {**ANA, "git_user_email": None}),
        )
        self.assertIn(
            "Git identity is unset: neither user.name nor user.email is set",
            required_approver.refusal(state, {"git_user_name": None, "git_user_email": None}),
        )


class EnforcementTests(unittest.TestCase):
    """autocode_approval_provenance.stamp, which every approval path calls, refuses before stamping."""

    def test_a_new_approval_from_another_identity_is_refused_and_nothing_is_stamped(self):
        for event in (
            plan_event(),
            {**plan_event(), "kind": "human_review", "criterion": "C1"},
            {"kind": "completion_accept", "actor": "user_cli", "at": "2026-10-16T09:00:00+00:00"},
        ):
            with self.subTest(kind=event["kind"]):
                candidate = run_requiring("lead@example.test")
                candidate["user_events"].append(event)
                with self.assertRaises(required_approver.Refused):
                    provenance.stamp(
                        run_requiring(), candidate, {"command": "autocode", "claimed_as": None}, record=as_ana
                    )
                self.assertNotIn("provenance", event)

    def test_the_named_approver_is_stamped_as_before(self):
        candidate = run_requiring("ana@example.test")
        candidate["user_events"].append(plan_event())
        self.assertEqual(1, provenance.stamp({}, candidate, {"command": "autocode", "claimed_as": None}, record=as_ana))
        self.assertEqual("ana@example.test", candidate["user_events"][0]["provenance"]["git_user_email"])

    def test_an_edit_a_policy_approval_or_a_repeated_approval_is_not_checked(self):
        edit = {"revision": 2, "hash": "b", "origin": "user_cli_edit"}
        candidate = {**run_requiring("lead@example.test"), "goal_contract": edit}
        self.assertEqual(1, provenance.stamp({}, candidate, {"command": "autocode", "claimed_as": None}, record=as_ana))
        self.assertEqual("ana@example.test", edit["provenance"]["git_user_email"])
        policy = plan_event(actor="workflow_policy")
        candidate = {**run_requiring("lead@example.test"), "user_events": [policy]}
        self.assertEqual(0, provenance.stamp({}, candidate, {}, record=as_ana))
        earlier = plan_event()
        saved = {**run_requiring("lead@example.test"), "user_events": [earlier]}
        candidate = {**run_requiring("lead@example.test"), "user_events": [dict(earlier)]}
        self.assertEqual(0, provenance.stamp(saved, candidate, {}, record=as_ana))

    def test_a_refused_terminal_decision_prints_why_and_reports_false(self):
        candidate = run_requiring("lead@example.test")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            decided = provenance.decide(
                lambda value: value["user_events"].append(plan_event()), {}, candidate, ["--chat"], record=as_ana
            )
        self.assertFalse(decided)
        self.assertIn("Approval refused", out.getvalue())
        allowed = run_requiring("ana@example.test")
        self.assertTrue(
            provenance.decide(lambda value: value["user_events"].append(plan_event()), {}, allowed, [], record=as_ana)
        )


class ProjectFileTests(unittest.TestCase):
    """A new run takes require-approver from the project file, with no flag to type it away."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / ".autocode").mkdir()
        self.config = self.root / ".autocode" / "config.toml"

    def parse(self, *argv):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            args, _ = autocode_args.parse(None, [*argv, "--workspace", str(self.root)], opencode.DEFAULT_MODELS)
        return args, stderr.getvalue()

    def test_a_new_run_takes_the_list_and_a_saved_run_reads_no_file(self):
        self.config.write_text('engine = "codex"\nrequire-approver = ["lead@example.test"]\n')
        args, stderr = self.parse("Build it")
        self.assertEqual(["lead@example.test"], args.require_approver)
        self.assertIn("engine, require-approver", stderr)
        run = self.root / ".autocode" / "runs" / "r1"
        run.mkdir(parents=True)
        args, _ = self.parse("--run-dir", str(run), "--resume-paused")
        self.assertIsNone(getattr(args, "require_approver", None))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parse("Build it", "--require-approver", "me@example.test")  # no flag sets or overrides it

    def test_a_value_that_is_not_an_address_stops_a_new_run(self):
        self.config.write_text('require-approver = "Lee Lead"\n')
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as stop:
            autocode_args.parse(None, ["Build it", "--workspace", str(self.root)], opencode.DEFAULT_MODELS)
        self.assertEqual(2, stop.exception.code)
        self.assertIn(f"{self.config}: require-approver 'Lee Lead' is not a plain email address", stderr.getvalue())

    def test_a_new_run_from_a_design_handoff_with_no_typed_task_takes_the_list(self):
        """--ui-run alone starts a new run; its task comes from the handoff only after parsing (autocode_run_setup)."""
        self.config.write_text('require-approver = ["lead@example.test"]\n')
        args, stderr = self.parse("--ui-run", str(self.root / ".autocode-ui" / "runs" / "accepted"))
        self.assertIsNone(args.task)
        self.assertEqual(["lead@example.test"], getattr(args, "require_approver", None))
        self.assertIn("require-approver", stderr)

    def test_a_replay_never_takes_the_list_nor_says_it_did(self):
        """A replay re-applies its recording's decisions with no person approving, and keeps its recording's settings."""
        self.config.write_text('engine = "codex"\nrequire-approver = ["lead@example.test"]\n')
        recording = str(self.root / ".autocode" / "replay" / "recording" / "recording.json")
        args, stderr = self.parse("Build it", "--replay-recording", recording)
        self.assertIsNone(getattr(args, "require_approver", None))
        self.assertIn(f"Project settings from {self.config}: engine\n", stderr)
        self.assertNotIn("require-approver", stderr)

    def test_doctor_shows_who_may_approve_and_where_it_was_set(self):
        rows = {row["key"]: (row["value"], row["source"]) for row in doctor.project_settings(self.root)[1]}
        self.assertEqual((None, "built-in default"), rows["require-approver"])
        self.config.write_text('require-approver = "lead@example.test"\n')
        check, found = doctor.project_settings(self.root)
        rows = {row["key"]: (row["value"], row["source"]) for row in found}
        self.assertEqual((["lead@example.test"], str(self.config)), rows["require-approver"])
        self.assertEqual(doctor.OK, check.status)


class TerminalConversationTests(unittest.TestCase):
    """chat_checkpoint, the terminal conversation, stops at a refused approval and reports that nothing changed: a
    refused plan approval returns False, and with two artifact criteria a refusal of the first asks nothing about the
    second."""

    setUp = test_goals.GoalTests.setUp
    validation = test_goals.GoalTests.validation
    decision = test_goals.GoalTests.decision

    def identify(self, email):
        """The Git identity of the run's workspace, in the fixture repository's own config."""
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", email], check=True)

    def converse(self, *replies):
        """Run one conversation turn with these replies; a prompt beyond them reads end of input."""
        prompts = []

        def reply(prompt):
            prompts.append(prompt)
            if len(prompts) > len(replies):
                raise EOFError
            return replies[len(prompts) - 1]

        with (
            patch("builtins.input", side_effect=reply),
            contextlib.redirect_stdout(io.StringIO()) as out,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = runner.chat_checkpoint(self.state)
        return result, prompts, out.getvalue()

    def test_a_refusal_stops_at_the_same_question_with_two_human_review_criteria(self):
        self.state["settings"][required_approver.SETTING] = ["lead@example.test"]
        draft = body(human=True)
        draft["acceptance_criteria"].append(
            {
                "id": "C2",
                "criterion": "The greeting reads naturally",
                "verification_method": "A person reads the greeting",
                "human_review": True,
            }
        )
        draft["milestones"][0]["acceptance_criteria"].append("C2")
        lifecycle.install_draft(self.state, draft, origin="test")
        lifecycle.human.evaluate(self.state)
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.state["status"])

        self.identify("ana@example.test")
        approved, prompts, out = self.converse("yes")
        self.assertFalse(approved)  # the caller then saves the run and stops; True would carry on as approved
        self.assertEqual(1, len(prompts))
        self.assertIn("Approval refused", out)
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.state["status"])
        self.assertFalse(goals.approved(self.state))
        self.identify("lead@example.test")
        approved, _, _ = self.converse("yes")
        self.assertTrue(approved)
        self.assertTrue(goals.approved(self.state))

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.validation()
            runner.apply_result(
                self.state, "astra_review", self.decision("COMPLETE"), {"output": "review"}, self.root, self.run
            )
        lifecycle.human.evaluate(self.state)  # publish the artifact review, as the runner's writer boundary does
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual(["C1", "C2"], goals.missing_human_reviews(self.state))
        self.identify("ana@example.test")
        approved, prompts, out = self.converse("y", "y")
        self.assertFalse(approved)
        # Refused at C1, the conversation asks nothing about C2.
        self.assertEqual(["Approve artifact criterion C1 after reviewing its evidence? [y/N]: "], prompts)
        self.assertEqual(1, out.count("Approval refused"))
        self.assertEqual(["C1", "C2"], goals.missing_human_reviews(self.state))
        self.identify("lead@example.test")
        approved, prompts, _ = self.converse("y", "y")
        self.assertTrue(approved)
        self.assertEqual(2, len(prompts))
        self.assertEqual([], goals.missing_human_reviews(self.state))


class CompletionAcceptanceTests(unittest.TestCase):
    """--accept-completion through the real CLI entry in-process on a run started with require-approver: an
    unlisted Git identity is refused before anything is saved, and a listed one closes the run."""

    setUp, approve, draft, decision, validation, invoke = (
        getattr(test_goals.GoalTests, name)
        for name in ("setUp", "approve", "draft", "decision", "validation", "invoke")
    )
    paused_at_completion = test_approval_provenance.CompletionAcceptanceTests.paused_at_completion

    def status(self):
        """The saved run as ``--status`` shows it."""
        self.assertEqual(0, self.invoke("--status"), self.stderr)
        return json.loads(self.stdout)["view"]

    def test_an_unlisted_identity_cannot_accept_completion_and_the_run_stays_paused(self):
        self.paused_at_completion()
        self.state["settings"][required_approver.SETTING] = ["lead@example.test"]
        self.assertEqual(2, self.invoke("--accept-completion"))
        self.assertIn("Approval refused", self.stderr)
        self.assertIn("this checkout's Git identity is Ana Example <ana@example.test>", self.stderr)
        view = self.status()
        self.assertEqual("PAUSED_INVALID_OUTPUT", view["status"])
        self.assertNotIn("completion", [row["kind"] for row in view["approvals"]])
        self.assertEqual(2, self.invoke("--accept-completion", "--as", "Lead"))
        self.assertIn("The --as name 'Lead' is a claim and does not count.", self.stderr)
        view = self.status()
        self.assertEqual("PAUSED_INVALID_OUTPUT", view["status"])
        self.assertNotIn("completion", [row["kind"] for row in view["approvals"]])
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "LEAD@example.test"], check=True)
        self.assertEqual(0, self.invoke("--accept-completion"), self.stderr)
        view = self.status()
        self.assertEqual("TASK_COMPLETE", view["status"])
        accepted = [row for row in view["approvals"] if row["kind"] == "completion"]
        self.assertEqual(["LEAD@example.test"], [row["provenance"]["git_user_email"] for row in accepted])


if __name__ == "__main__":
    unittest.main()
