"""A project's require-approver: the setting's values, the refusal, where provenance enforces it, and how a new run
takes it from the project file (#722).

The public CLI runs are in test_required_approver_cli.
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import autocode_approval_provenance as provenance
import autocode_args
import autocode_doctor as doctor
import autocode_required_approver as required_approver
from providers import opencode

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
        for event in (plan_event(), {**plan_event(), "kind": "human_review", "criterion": "C1"}):
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
        self.assertIn(f"{self.config}: require-approver 'Lee Lead' is not an email address", stderr.getvalue())

    def test_doctor_shows_who_may_approve_and_where_it_was_set(self):
        rows = {row["key"]: (row["value"], row["source"]) for row in doctor.project_settings(self.root)[1]}
        self.assertEqual((None, "built-in default"), rows["require-approver"])
        self.config.write_text('require-approver = "lead@example.test"\n')
        check, found = doctor.project_settings(self.root)
        rows = {row["key"]: (row["value"], row["source"]) for row in found}
        self.assertEqual((["lead@example.test"], str(self.config)), rows["require-approver"])
        self.assertEqual(doctor.OK, check.status)


if __name__ == "__main__":
    unittest.main()
