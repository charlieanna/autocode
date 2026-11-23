"""Resume corrects an authenticated Tester report after a validator update, without new verification (#3868).

The public CLI runs a bug fix with the repository's fake provider. A wrapper around the fake makes every
Tester report and Tester report repair invalid with a different error each time (until a marker file
says to stop), and records each launch with its handoff. The project's suite and regression commands run
through a counted interpreter, so every original-code baseline run and regression proof is counted. The
run is driven to the exhausted pause the issue describes: the Tester report and both of its bounded repairs
rejected, PAUSED_INVALID_OUTPUT. A validator update is then stood for by the repair's recorded validation
runtime. No model is called.

Four fast classes run first and in process: ReportCorrectionDispatch (how the resume dispatch reads the
shared decision's answer, with only the validator's verdict stubbed), ReportCorrectionDecision (the shared
decision's own edges), ReportCorrectionAuthentication (only the exact saved evidence, unchanged, authenticates)
and ReportCorrectionRecoveryEdges (the recovery steps around it keep their archive reasons, actors and status
rule). ``tests/report_correction_mutation_probe.py`` shows that they notice each named break of the change.
"""

import contextlib
import datetime as dt
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode
import autocode_action_recovery as action_recovery
import autocode_report_repair_queue as report_repair_queue
import autocode_run_status as run_status
import autocode_scratch_location as scratch_location
import autocode_stage_recovery as stage_recovery
import autocode_support as support
import autocode_util as util

from . import test_bugfix_workflow as bugfix
from . import test_report_repair, test_report_repair_compaction

SPENT = "Explicit fresh execution retry after bounded report failures"
UNLAUNCHABLE = "Unchanged repeated failure hold: the report repair cannot launch from its saved inputs"
CORRECTION_REASON = (
    "Authenticated tester report needs only a report correction: its source revision, pins, contract and "
    "handoff binding are unchanged and the current validator rejected only its contents, so a bounded "
    "report-only repair runs with the new diagnostic and the original execution evidence; no new baseline, "
    "regression proof or full attempt."
)

WRAPPER = r"""
import io, json, os, runpy, sys
from pathlib import Path
delegate = Path(__file__).with_name("ordinary_fake_codex.py")
if sys.argv[1:] == ["login", "status"]:
    os.execv(sys.executable, [sys.executable, str(delegate), *sys.argv[1:]])
prompt = sys.stdin.read()
data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
repair = bool(data.get("report_repair"))
stage = data["original"]["stage"] if repair else data["stage"]
trace = Path(os.environ["CORRECTION_TRACE"])
row = {"stage": stage, "report_repair": repair}
if repair:
    row.update(
        error=data.get("error"),
        original_output=data["original"].get("output"),
        original_events=data["original"].get("events"),
        original_executed_checks=data.get("original_executed_checks"),
    )
with trace.open("a") as stream:
    stream.write(json.dumps(row) + "\n")
sys.stdin = io.StringIO(prompt)
try:
    runpy.run_path(str(delegate), run_name="__main__")
except SystemExit as error:
    if error.code not in (None, 0):
        raise
if stage == "tester":
    output = Path(sys.argv[sys.argv.index("-o") + 1])
    report = json.loads(output.read_text())
    for key in [key for key in report if key.startswith("unexpected_")]:
        report.pop(key)
    if not Path(os.environ["CORRECTION_VALID"]).exists():
        # A different rejection each launch, so no repeated-failure streak forms.
        launches = sum(json.loads(line)["stage"] == "tester" for line in trace.read_text().splitlines())
        report[f"unexpected_{launches}"] = True
    output.write_text(json.dumps(report))
"""

COUNTER = r"""
import os, sys
with open(os.environ["CHECK_COUNTER"], "a") as stream:
    stream.write(os.environ["CHECK_LABEL"] + "\t" + os.getcwd() + "\n")
os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
"""


class ReportCorrectionDispatch(unittest.TestCase):
    """How the resume dispatch reads the shared decision, in process (autocode_action_recovery.resume).

    Only the validator's verdict on the saved report (the runner's report revalidation) is stubbed; the shared
    decision and the dispatch around it are the real ones. A report the current checks accept, or only reject
    for its contents, never reaches the fresh execution retry, so the steps that begin it are patched out and
    must stay uncalled. Anything else keeps today's path.
    """

    FRESH_RETRY = (
        "prepare_abandoned_completion_revalidation",
        "prepare_timeout_archived_completion",
        "prepare_planning_retry",
        "prepare_exhausted_execution_report_retry",
        "archive_stale_report_repair",
        "repeated_failure_resume_guard",
    )
    ACCEPTED = "saved stage output accepted without launching a retry"
    CORRECTED = "a report-only correction"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        self.at = dt.datetime.now(dt.UTC).isoformat()

    def paused(self):
        """The issue's pause: both bounded repairs spent on a report the earlier validator rejected."""
        return {
            "status": "PAUSED_INVALID_OUTPUT",
            "phase": "REPORT_REPAIR",
            "stop_reason": "Report repair rejected",
            "next_stage": "tester",
            "iteration": 1,
            "stages": [],
            "settings": {"report_repair": {"max_attempts": 2}},
            "resolver": {"attempts": {"tester": 2}},
            "pending_report_repair": {
                "attempts": 2,
                "error": "earlier validator diagnostic",
                "validation_runtime": "earlier runtime",
                "original": {"stage": "tester", "iteration": 1, "output": "report.json"},
                "revalidations": [
                    {
                        "runtime": "earlier runtime",
                        "result": "rejected",
                        "reason": "earlier validator diagnostic",
                        "at": self.at,
                    }
                ],
            },
        }

    @staticmethod
    def accepts(runner, state, run_dir, workspace):
        return True

    @staticmethod
    def rejects(runtime):
        """A validator that authenticates the report and rejects it, as the given runtime, just now."""

        def revalidate(runner, state, run_dir, workspace):
            pending = state["pending_report_repair"]
            pending["validation_runtime"] = runtime
            pending["revalidations"].append(
                {"runtime": runtime, "result": "rejected", "reason": "new validator diagnostic", "at": "now"}
            )
            return False

        return revalidate

    def resume(self, revalidate, state, retry_report=None, operational_incident=None):
        """The real resume dispatch for an explicit --resume-paused; its outcome and what it did not call."""
        args = SimpleNamespace(
            abandon_stage=None,
            resume_paused=True,
            accept_source_edit=False,
            grant_recovery=None,
            max_milestone_seconds=None,
            retry_report=retry_report,
            retry_failed_stage=False,
            diagnose_failed_stage=False,
        )
        decided = autocode.run_actions.Decided()
        decided.operational_incident = operational_incident
        stdout = io.StringIO()
        steps = {}
        with contextlib.ExitStack() as stack:
            for name in (*self.FRESH_RETRY, "reconcile_active"):
                steps[name] = stack.enter_context(patch.object(autocode, name, return_value=None))
            stack.enter_context(patch.object(autocode, "write_json", side_effect=lambda path, value: None))
            steps["revalidate"] = stack.enter_context(
                patch.object(autocode.report_revalidation, "revalidate", side_effect=revalidate)
            )
            # The explicit --retry-report owner is stubbed: only whether the dispatch hands it the resume matters.
            steps["retry_report"] = stack.enter_context(
                patch.object(action_recovery, "retry_report", return_value=None)
            )
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            code = action_recovery.resume(
                autocode, args, None, state, self.run / "state.json", self.run, self.root, decided
            )
        return code, decided, stdout.getvalue(), steps

    def fresh_retry_calls(self, steps):
        return [name for name in self.FRESH_RETRY if steps[name].called]

    def assert_resumed(self, state):
        self.assertEqual(("RUNNING", "tester"), (state["status"], state["next_stage"]))
        self.assertNotIn("stop_reason", state)

    def test_a_report_the_current_checks_accept_is_applied_without_a_fresh_retry(self):
        state = self.paused()
        code, decided, stdout, steps = self.resume(self.accepts, state)
        self.assertIsNone(code)
        self.assertTrue(decided.evidence_reconciled, "an applied report is exact reconciled evidence")
        self.assert_resumed(state)
        self.assertIn(self.ACCEPTED, stdout)
        self.assertNotIn(self.CORRECTED, stdout)
        self.assertEqual([], self.fresh_retry_calls(steps))
        # Reconciled evidence renews no allowance and is not a correction.
        self.assertEqual({"tester": 2}, state["resolver"]["attempts"])
        self.assertEqual(2, state["pending_report_repair"]["attempts"])
        self.assertNotIn("reconciliation_notes", state)

    def test_a_report_the_current_checks_only_reject_gets_a_correction_not_a_fresh_retry(self):
        state = self.paused()
        code, decided, stdout, steps = self.resume(self.rejects(util.runtime_source_digest()), state)
        self.assertIsNone(code)
        self.assertFalse(decided.evidence_reconciled, "a correction is the ordinary resume, not reconciled evidence")
        self.assert_resumed(state)
        self.assertIn(self.CORRECTED, stdout)
        self.assertNotIn(self.ACCEPTED, stdout)
        self.assertEqual([], self.fresh_retry_calls(steps))
        pending = state["pending_report_repair"]
        self.assertEqual((0, "new validator diagnostic"), (pending["attempts"], pending["error"]))
        # The Resolver's epoch renews as an ordinary resume does; the repair allowance renewed once, by the runner.
        self.assertEqual({}, state["resolver"]["attempts"])
        self.assertEqual(2, state["report_repair_lifetime_attempts"])
        epochs = [row for row in state["user_events"] if row["kind"] == "report_repair_resume_epoch"]
        self.assertEqual([("runner", 2)], [(row["actor"], row["cleared_attempts"]) for row in epochs])
        self.assertEqual(["runner"], [row["actor"] for row in state["reconciliation_notes"]])
        self.assertIn("needs only a report correction", state["reconciliation_notes"][0]["reason"])

    def test_a_report_nothing_new_rejects_keeps_todays_fresh_retry(self):
        cases = {
            "no revalidation": lambda runner, state, run_dir, workspace: False,
            "a rejection by an earlier runtime": self.rejects("earlier runtime"),
        }
        for name, revalidate in cases.items():
            with self.subTest(case=name):
                state = self.paused()
                code, decided, stdout, steps = self.resume(revalidate, state)
                self.assertIsNone(code)
                self.assertFalse(decided.evidence_reconciled)
                steps["prepare_exhausted_execution_report_retry"].assert_called_once_with(state, self.run, self.root)
                self.assertEqual(list(self.FRESH_RETRY), self.fresh_retry_calls(steps))
                for message in (self.ACCEPTED, self.CORRECTED):
                    self.assertNotIn(message, stdout)
                self.assertEqual("PAUSED_INVALID_OUTPUT", state["status"])
                self.assertNotIn("reconciliation_notes", state)

    def assert_not_corrected(self, state, stdout, steps):
        """The saved report was neither revalidated nor corrected, and the repair is as it was saved."""
        steps["revalidate"].assert_not_called()
        for message in (self.ACCEPTED, self.CORRECTED):
            self.assertNotIn(message, stdout)
        self.assertNotIn("reconciliation_notes", state)
        self.assertEqual("PAUSED_INVALID_OUTPUT", state["status"])
        pending = state["pending_report_repair"]
        self.assertEqual("earlier validator diagnostic", pending["error"])
        self.assertEqual(["earlier runtime"], [row["runtime"] for row in pending["revalidations"]])

    def test_an_explicit_report_retry_takes_its_own_owner_not_a_report_correction(self):
        # The current validator would reject the report just now, which alone qualifies for a correction.
        state = self.paused()
        code, decided, stdout, steps = self.resume(
            self.rejects(util.runtime_source_digest()), state, retry_report="tester"
        )
        self.assertIsNone(code)
        self.assertFalse(decided.evidence_reconciled)
        self.assert_not_corrected(state, stdout, steps)
        steps["retry_report"].assert_called_once()
        # The explicit retry is its own path: the fresh execution retry does not run beside it either.
        self.assertEqual([], self.fresh_retry_calls(steps))

    def test_an_operational_incident_resume_keeps_todays_path_not_a_report_correction(self):
        state = self.paused()
        code, decided, stdout, steps = self.resume(
            self.rejects(util.runtime_source_digest()), state, operational_incident="incident-1"
        )
        self.assertIsNone(code)
        self.assertFalse(decided.evidence_reconciled)
        self.assert_not_corrected(state, stdout, steps)
        steps["retry_report"].assert_not_called()
        steps["prepare_exhausted_execution_report_retry"].assert_called_once_with(state, self.run, self.root)
        self.assertEqual(list(self.FRESH_RETRY), self.fresh_retry_calls(steps))
        # The incident's own owner defers the resume; nothing renewed the repair allowance for it.
        self.assertTrue(decided.operational_resume_deferred)
        self.assertEqual(2, state["pending_report_repair"]["attempts"])


class ReportCorrectionDecision(unittest.TestCase):
    """The shared decision's own edges, in process: what never qualifies leaves the run as it was."""

    def setUp(self):
        # Imported here, so the CLI guards above load on code from before the shared decision existed.
        import autocode_report_correction as correction

        self.correction = correction
        self.at = dt.datetime.now(dt.UTC).isoformat()
        self.entry = {
            "at": self.at,
            "reason": SPENT,
            "repair": {"attempts": 2, "error": "old", "original": {"stage": "tester", "output": "report.json"}},
        }
        self.state = {"next_stage": "tester", "stages": [], "report_repair_archive": [self.entry]}
        self.saved = []

    def runner(self, revalidate):
        return type(
            "Runner",
            (),
            {
                "report_revalidation": type("Revalidation", (), {"revalidate": staticmethod(revalidate)}),
                "now": staticmethod(lambda: self.at),
                "write_json": staticmethod(lambda path, state: self.saved.append(json.loads(json.dumps(state)))),
            },
        )

    def rejects(self, runtime):
        def revalidate(runner, state, run_dir, workspace):
            pending = state["pending_report_repair"]
            pending["validation_runtime"] = runtime
            pending.setdefault("revalidations", []).append(
                {"runtime": runtime, "result": "rejected", "reason": "new diagnostic", "at": self.at}
            )
            return False

        return revalidate

    def test_a_rejection_by_this_runtime_restores_the_spent_repair_for_a_correction(self):
        runner = self.runner(self.rejects(util.runtime_source_digest()))
        self.assertEqual("corrected", self.correction.resume(runner, self.state, Path("run"), Path("work")))
        pending = self.state["pending_report_repair"]
        self.assertEqual([], self.state["report_repair_archive"])
        self.assertEqual("new diagnostic", pending["error"])
        self.assertEqual(0, pending["attempts"])
        self.assertEqual(2, self.state["report_repair_lifetime_attempts"])
        self.assertEqual(["runner"], [row["actor"] for row in self.state["reconciliation_notes"]])
        self.assertEqual(self.state, self.saved[-1])

    def test_a_report_the_current_validator_accepts_is_applied_without_recording_a_correction(self):
        pending = {"attempts": 2, "error": "old", "original": {"stage": "tester", "output": "report.json"}}
        saved = {"next_stage": "tester", "stages": [], "pending_report_repair": pending}
        seen = []

        def accepts(runner, state, run_dir, workspace):
            seen.append(state.get("pending_report_repair"))
            return True

        for name, state in (("a pending repair", saved), ("an archived spent repair", self.state)):
            with self.subTest(case=name):
                seen.clear()
                result = self.correction.resume(self.runner(accepts), state, Path("run"), Path("work"))
                self.assertEqual("accepted", result)
                self.assertEqual(1, len(seen), "the report is revalidated once, as the pending repair")
                self.assertIsNotNone(seen[0])
                self.assertEqual([], self.saved, "an applied report is not a correction and saves nothing itself")
                self.assertNotIn("reconciliation_notes", state)
                self.assertNotIn("report_repair_lifetime_attempts", state)
                kinds = {row["kind"] for row in state.get("user_events", [])}
                self.assertFalse(kinds & {"report_correction", "report_repair_resume_epoch"}, kinds)

    def test_a_stored_or_other_runtime_rejection_leaves_the_archived_repair_in_place(self):
        for revalidate in (lambda *args: False, self.rejects("earlier runtime")):
            with self.subTest(revalidate=revalidate):
                state = json.loads(json.dumps(self.state))
                self.assertIsNone(self.correction.resume(self.runner(revalidate), state, Path("run"), Path("w")))
                self.assertNotIn("pending_report_repair", state)
                self.assertEqual("old", state["report_repair_archive"][-1]["repair"]["error"])
                self.assertEqual(1, len(state["report_repair_archive"]))
                self.assertNotIn("reconciliation_notes", state)

    def test_a_failed_revalidation_puts_the_archived_repair_back(self):
        def revalidate(runner, state, run_dir, workspace):
            raise RuntimeError("the saved report changed while copying it")

        with self.assertRaises(RuntimeError):
            self.correction.resume(self.runner(revalidate), self.state, Path("run"), Path("work"))
        self.assertNotIn("pending_report_repair", self.state)
        self.assertEqual([self.entry], self.state["report_repair_archive"])

    def test_a_running_or_later_attempt_supersedes_and_other_kinds_never_return(self):
        later = (dt.datetime.fromisoformat(self.at) + dt.timedelta(seconds=1)).isoformat()
        cases = {
            "running attempt": lambda state: state.update(active_stage={"stage": "tester"}),
            "later attempt": lambda state: state["stages"].append({"stage": "tester", "started_at": later}),
            "other reason": lambda state: state["report_repair_archive"][-1].update(reason="Owning stage changed"),
            "other stage": lambda state: state.update(next_stage="builder"),
            "never launched": lambda state: state["report_repair_archive"][-1]["repair"].update(attempts=0),
            "unreadable time": lambda state: state["report_repair_archive"][-1].update(at="unknown"),
            "no repair record": lambda state: state["report_repair_archive"][-1].update(repair=None),
            "an entry that is not a record": lambda state: state.update(report_repair_archive=[SPENT]),
            "a repair without its original record": lambda state: state["report_repair_archive"][-1]["repair"].pop(
                "original"
            ),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                state = json.loads(json.dumps(self.state))
                change(state)
                self.assertIsNone(self.correction.archived(state, "spent"))
        earlier = (dt.datetime.fromisoformat(self.at) - dt.timedelta(seconds=1)).isoformat()
        for row in (
            {"stage": "tester", "started_at": earlier},
            {"stage": "tester", "started_at": later, "report_only": True},
            {"stage": "tester", "started_at": later, "runner_owned": True},
            {"stage": "builder", "started_at": later},
        ):
            with self.subTest(row=row):
                state = json.loads(json.dumps(self.state))
                state["stages"].append(row)
                self.assertEqual(self.entry, self.correction.archived(state, "spent"))
        unlaunchable = json.loads(json.dumps(self.state))
        unlaunchable["report_repair_archive"][-1].update(reason=self.correction.UNLAUNCHABLE)
        unlaunchable["report_repair_archive"][-1]["repair"]["attempts"] = 0
        self.assertIsNone(self.correction.archived(unlaunchable, "spent"))
        self.assertIsNotNone(self.correction.archived(unlaunchable, "unlaunchable"))

    def test_a_running_attempt_supersedes_but_the_rejected_attempts_own_row_does_not(self):
        repair = self.entry["repair"]
        self.assertTrue(self.correction.superseded({"active_stage": {"stage": "tester"}, "stages": []}, repair, None))
        # The row of the attempt the repair was made for starts at ``since``: it is not a later attempt.
        own = {"stages": [{"stage": "tester", "started_at": self.at}]}
        self.assertFalse(self.correction.superseded(own, repair, self.at))

    def test_a_pending_repair_or_another_running_stage_keeps_the_archived_repair_archived(self):
        self.assertEqual(self.entry, self.correction.archived(self.state, "spent"))
        cases = {
            "pending repair": lambda state: state.update(
                pending_report_repair={"attempts": 1, "original": {"stage": "tester"}}
            ),
            "another running stage": lambda state: state.update(active_stage={"stage": "builder"}),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                state = json.loads(json.dumps(self.state))
                change(state)
                self.assertIsNone(self.correction.archived(state, "spent"))

    def moment(self, seconds):
        return (dt.datetime.fromisoformat(self.at) + dt.timedelta(seconds=seconds)).isoformat()

    def pending_state(self, *rows, revalidations=()):
        """A pending repair: the original attempt at -20 s and its last report-only repair at -10 s."""
        pending = {
            "attempts": 2,
            "error": "old",
            "original": {"stage": "tester", "iteration": 3, "output": "report.json", "started_at": self.moment(-20)},
            "latest_rejected": {"stage": "tester_report_repair", "started_at": self.moment(-10)},
            "revalidations": [dict(row) for row in revalidations],
        }
        return {"next_stage": "tester", "stages": [dict(row) for row in rows], "pending_report_repair": pending}

    def test_a_runner_without_revalidation_changes_nothing(self):
        runner = type("Runner", (), {"now": staticmethod(lambda: self.at)})
        for name, state in (("pending", self.pending_state()), ("archived", self.state)):
            with self.subTest(case=name):
                before = json.loads(json.dumps(state))
                self.assertIsNone(self.correction.resume(runner, state, Path("run"), Path("work")))
                self.assertEqual(before, state)

    def test_only_a_rejection_this_resume_appends_qualifies_a_pending_repair(self):
        current = util.runtime_source_digest()
        stored = {"runtime": current, "result": "rejected", "reason": "stored diagnostic", "at": self.at}

        def not_rejected(runner, state, run_dir, workspace):
            state["pending_report_repair"]["revalidations"].append(
                {"runtime": current, "result": "unreadable", "reason": "cannot judge", "at": self.at}
            )
            return False

        cases = {
            "a stored current-runtime rejection": (self.pending_state(revalidations=[stored]), lambda *args: False),
            "a new row that is not a rejection": (self.pending_state(), not_rejected),
        }
        for name, (state, revalidate) in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(self.correction.resume(self.runner(revalidate), state, Path("run"), Path("w")))
                pending = state["pending_report_repair"]
                self.assertEqual((2, "old"), (pending["attempts"], pending["error"]))
                self.assertNotIn("reconciliation_notes", state)
                self.assertEqual([], self.saved)

    def test_a_tester_attempt_after_the_latest_rejected_repair_supersedes_the_pending_repair(self):
        state = self.pending_state({"stage": "tester", "started_at": self.moment(-5)})
        runner = self.runner(self.rejects(util.runtime_source_digest()))
        self.assertIsNone(self.correction.resume(runner, state, Path("run"), Path("work")))
        pending = state["pending_report_repair"]
        self.assertEqual((2, "old"), (pending["attempts"], pending["error"]))
        self.assertNotIn("reconciliation_notes", state)
        self.assertNotIn("report_repair_lifetime_attempts", state)
        self.assertEqual([], self.saved)

    def test_a_tester_attempt_before_the_latest_rejected_repair_does_not_supersede_it(self):
        # The original attempt's later rows (it started at -20 s) precede the repair the report was last judged in.
        state = self.pending_state({"stage": "tester", "started_at": self.moment(-15)})
        runner = self.runner(self.rejects(util.runtime_source_digest()))
        self.assertEqual("corrected", self.correction.resume(runner, state, Path("run"), Path("work")))
        self.assertEqual("new diagnostic", state["pending_report_repair"]["error"])

    def test_a_correction_records_its_whole_reason_note_and_user_event(self):
        current = util.runtime_source_digest()
        cases = (
            ("pending", self.pending_state(), 3, False),
            ("archived", self.state, None, True),
        )
        for name, state, iteration, restored in cases:
            with self.subTest(case=name):
                self.saved.clear()
                runner = self.runner(self.rejects(current))
                self.assertEqual("corrected", self.correction.resume(runner, state, Path("run"), Path("work")))
                rejection = {"runtime": current, "result": "rejected", "reason": "new diagnostic", "at": self.at}
                note = {
                    "at": runner.now(),
                    "stage": "tester",
                    "iteration": iteration,
                    "actor": "runner",
                    "reason": CORRECTION_REASON,
                    "revalidation": rejection,
                    "original_output": "report.json",
                    "restored_from_archive": restored,
                }
                self.assertEqual([note], state["reconciliation_notes"])
                event = {
                    "kind": "report_correction",
                    "actor": "runner",
                    "at": runner.now(),
                    "stage": "tester",
                    "reason": CORRECTION_REASON,
                }
                self.assertEqual([event], [row for row in state["user_events"] if row["kind"] == "report_correction"])
                self.assertEqual(state, self.saved[-1])


class ReportCorrectionAuthentication(unittest.TestCase):
    """autocode_report_correction.authenticate: only the exact saved evidence, unchanged, is still evidence."""

    def setUp(self):
        # Imported here, so the CLI guards below load on code from before the shared decision existed.
        import autocode_report_correction as correction

        self.correction = correction
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)

        def pinned(name):
            path = root / name
            path.write_text(json.dumps({"artifact": name}))
            return str(path)

        common = {"iteration": 1, "source_revision": "rev-1", "contract_hash": "contract-1", "rejected": True}
        original = {"stage": "tester", "exit_code": 0, **common, "schema": pinned("tester.schema.json")}
        for key, suffix in (("output", ".json"), ("events", ".jsonl"), ("before_ref", ".before.json")):
            original[key] = pinned("tester-01" + suffix)
        original["after_ref"] = pinned("tester-01.after.json")
        latest = {
            "stage": "tester_report_repair",
            "original_stage": "tester",
            "report_only": True,
            "exit_code": 0,
            **common,
            "output": pinned("tester_report_repair-01.json"),
            "events": pinned("tester_report_repair-01.jsonl"),
            "schema": original["schema"],
        }
        paths = {original[key] for key in ("output", "events", "schema", "before_ref", "after_ref")}
        paths |= {latest["output"], latest["events"]}
        self.pending = {
            "contract_hash": "contract-1",
            "original": original,
            "latest_rejected": latest,
            "revalidation_context": {"stage": "tester"},
            "pins": {path: util.file_hash(path) for path in sorted(paths)},
        }
        self.state = {"next_stage": "tester", "goal_contract": {"hash": "contract-1"}}
        self.complete = True
        self.completed = []
        self.runtime = SimpleNamespace(
            support=SimpleNamespace(snapshot=object(), file_hash=util.file_hash),
            stage_completed=lambda state, record: self.completed.append(record) or self.complete,
        )
        for target, name, value in (
            (correction.source_scope, "snapshot", {"revision": "rev-1"}),
            (correction.revalidation_binding, "matches", True),
        ):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def authenticate(self, pending, state=None):
        return self.correction.authenticate(self.runtime, state or self.state, Path("work"), pending)

    def valid_pending(self):
        """A copy of a freshly pinned, fully valid repair, shown to authenticate before a case changes it."""
        self.setUp()
        pending = json.loads(json.dumps(self.pending))
        self.assertIs(pending["latest_rejected"], self.authenticate(pending), "valid before the change")
        return pending

    def test_the_unchanged_saved_evidence_authenticates_its_latest_rejected_record(self):
        self.assertIs(self.pending["latest_rejected"], self.authenticate(self.pending))
        self.assertEqual([self.pending["latest_rejected"]], self.completed)

    def test_any_single_difference_from_the_saved_evidence_does_not_authenticate(self):
        def unpin(row, key):
            return lambda pending: pending["pins"].pop(pending[row][key])

        cases = {
            "latest record is not report-only": lambda pending: pending["latest_rejected"].pop("report_only"),
            "latest record of another iteration": lambda pending: pending["latest_rejected"].update(iteration=2),
            "latest record on another source": lambda pending: pending["latest_rejected"].update(
                source_revision="rev-2"
            ),
            "latest record under another contract": lambda pending: pending["latest_rejected"].update(
                contract_hash="contract-2"
            ),
            "original before_ref unpinned": unpin("original", "before_ref"),
            "latest output unpinned": unpin("latest_rejected", "output"),
            "pins held as a list": lambda pending: pending.update(pins=list(pending["pins"])),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                pending = json.loads(json.dumps(self.pending))
                change(pending)
                self.assertIsNone(self.authenticate(pending))
        with self.subTest(case="the goal contract changed"):
            state = dict(self.state, goal_contract={"hash": "contract-2"})
            self.assertIsNone(self.authenticate(json.loads(json.dumps(self.pending)), state))
        with self.subTest(case="the attempt did not complete"):
            self.complete = False
            self.assertIsNone(self.authenticate(json.loads(json.dumps(self.pending))))

    def test_each_guard_alone_keeps_otherwise_valid_evidence_from_authenticating(self):
        """Every other check passes in each case, so only the guard named keeps the report from being reused."""

        def pinned_bytes_changed(pending):
            path = Path(pending["original"]["events"])
            path.write_bytes(path.read_bytes() + b"\n")

        pending_cases = {
            "the original record is not a record": lambda pending: pending.update(original=["tester-01.json"]),
            "the original record has no source revision": lambda pending: pending["original"].pop("source_revision"),
            "the latest record was not rejected": lambda pending: pending["latest_rejected"].pop("rejected"),
            "the latest record exited non-zero": lambda pending: pending["latest_rejected"].update(exit_code=1),
            "the latest record repairs another stage": lambda pending: pending["latest_rejected"].update(
                original_stage="builder"
            ),
            "the latest record timed out": lambda pending: pending["latest_rejected"].update(timed_out=True),
            "the latest record was interrupted": lambda pending: pending["latest_rejected"].update(interrupted=True),
            "a pinned file changed": pinned_bytes_changed,
            "a pinned file is gone": lambda pending: Path(pending["latest_rejected"]["output"]).unlink(),
        }
        for name, change in pending_cases.items():
            with self.subTest(case=name):
                pending = self.valid_pending()
                change(pending)
                self.assertIsNone(self.authenticate(pending))
        state_cases = {
            "a stage is running": {"active_stage": {"stage": "builder"}},
            "a runner check is running": {"active_runner_check": {"kind": "baseline"}},
            "an artifact is uncertain": {"uncertain_artifacts": [{"path": "tester-01.json"}]},
            "another stage is next": {"next_stage": "builder"},
        }
        for name, extra in state_cases.items():
            with self.subTest(case=name):
                self.assertIsNone(self.authenticate(self.valid_pending(), dict(self.state, **extra)))
        for target, name, value, case in (
            (self.correction.revalidation_binding, "matches", False, "the handoff binding no longer holds"),
            (self.correction.source_scope, "snapshot", {"revision": "rev-2"}, "the source revision changed"),
        ):
            with self.subTest(case=case):
                pending = self.valid_pending()
                with patch.object(target, name, return_value=value):
                    self.assertIsNone(self.authenticate(pending))
        with self.subTest(case="the repair is not a record"):
            self.assertIsNone(self.authenticate(["not", "a", "repair"]))

    def test_a_saved_repair_record_without_its_original_stage_is_read_from_its_own_stage_name(self):
        """A record saved before original_stage existed names the stage it repairs only in its own stage."""
        pending = self.valid_pending()
        pending["latest_rejected"].pop("original_stage")
        self.assertIs(pending["latest_rejected"], self.authenticate(pending))
        for stage in ("builder_report_repair", "tester_report_repair_extra", "report_repair"):
            with self.subTest(stage=stage):
                pending = self.valid_pending()
                pending["latest_rejected"].pop("original_stage")
                pending["latest_rejected"]["stage"] = stage
                self.assertIsNone(self.authenticate(pending))


class _Untouchable:
    """A runner that fails the test if anything on it is used."""

    def __getattribute__(self, name):
        raise AssertionError(f"runner.{name} was used")


class ReportCorrectionRecoveryEdges(unittest.TestCase):
    """The recovery steps around the shared decision keep today's archive reasons, actors and status rules."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / "run"
        self.run.mkdir()
        self.at = dt.datetime.now(dt.UTC).isoformat()

    def test_a_resume_renews_the_repair_allowance_as_the_person_by_default(self):
        state = {"pending_report_repair": {"attempts": 2}}
        report_repair_queue.reset_for_resume(state, now=lambda: self.at)
        epochs = [row for row in state["user_events"] if row["kind"] == "report_repair_resume_epoch"]
        self.assertEqual(
            [("user_cli", 2, self.at)], [(row["actor"], row["cleared_attempts"], row["at"]) for row in epochs]
        )
        self.assertEqual((0, 2), (state["pending_report_repair"]["attempts"], state["report_repair_lifetime_attempts"]))

    def test_an_unlaunchable_repair_is_not_restored_while_the_run_is_paused(self):
        import autocode_report_correction as correction

        entry = {"at": self.at, "reason": UNLAUNCHABLE, "repair": {"attempts": 0, "original": {"stage": "tester"}}}
        state = {"status": "PAUSED_REQUESTED", "next_stage": "tester", "stages": [], "report_repair_archive": [entry]}
        # Only the status keeps it archived: the same run while running may bring it back.
        self.assertEqual(entry, correction.archived(dict(state, status="RUNNING"), "unlaunchable"))
        before = json.loads(json.dumps(state))
        self.assertFalse(stage_recovery.restore_launchable_unlaunchable_repair(_Untouchable(), state, self.run, None))
        self.assertEqual(before, state)

    def test_an_unlaunchable_repair_that_cannot_restart_again_is_held_with_the_unlaunchable_reason(self):
        def repair():
            return {"attempts": 0, "original": {"stage": "tester", "source_revision": "rev-1"}}

        earlier = [{"at": self.at, "reason": UNLAUNCHABLE, "repair": repair()}] * stage_recovery.RESTARTS_PER_SOURCE
        state = {
            "status": "RUNNING",
            "next_stage": "tester",
            "stages": [],
            "report_repair_archive": [dict(row) for row in earlier],
            "pending_report_repair": repair(),
        }
        error = support.Paused("PAUSED_REPORT_REPAIR_INPUT", "Complete report-repair handoff exceeds 262144 bytes.")
        with self.assertRaises(support.Paused) as paused:
            stage_recovery.restart_unlaunchable_repair(state, self.run, None, error)
        self.assertEqual("PAUSED_REPEATED_FAILURE", paused.exception.status)
        self.assertNotIn("pending_report_repair", state)
        self.assertEqual(len(earlier) + 1, len(state["report_repair_archive"]))
        self.assertEqual((UNLAUNCHABLE, repair()), reason_and_repair(state["report_repair_archive"][-1]))

    def test_an_exhausted_tester_repair_is_archived_for_a_fresh_retry_with_the_spent_reason(self):
        repair = {"attempts": 2, "original": {"stage": "tester", "iteration": 1, "source_revision": "rev-1"}}
        state = {
            "status": "PAUSED_INVALID_OUTPUT",
            "next_stage": "tester",
            "stages": [],
            "settings": {"report_repair": {"max_attempts": 2}},
            "pending_report_repair": json.loads(json.dumps(repair)),
        }
        self.assertTrue(stage_recovery.prepare_exhausted_execution_report_retry(state, self.run))
        self.assertNotIn("pending_report_repair", state)
        self.assertEqual((SPENT, repair), reason_and_repair(state["report_repair_archive"][-1]))


def reason_and_repair(entry):
    return entry["reason"], entry["repair"]


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised provider requires supported Linux")
class ReportCorrectionResumeCLI(unittest.TestCase):
    new_run_engine_args = ("--engine", "codex")
    setUp = bugfix.BugfixWorkflow.setUp
    launch = bugfix.BugfixWorkflow.launch
    builder_writes = bugfix.BugfixWorkflow.builder_writes

    def exhausted(self):
        """The issue's pause: the Tester report and both its repairs rejected with three different errors."""
        executable = self.root / "fixture-bin/codex"
        executable.rename(executable.with_name("ordinary_fake_codex.py"))
        executable.write_text(f"#!{sys.executable}\n" + WRAPPER)
        executable.chmod(0o755)
        self.trace = self.root / "correction-trace.jsonl"
        self.valid = self.root / "correction-valid"
        self.counter = self.root / "check-counter.txt"
        # Named like an interpreter, so the runner still reads each command as a plain unittest run.
        counted = self.root / "counted-bin/python3"
        counted.parent.mkdir()
        counted.write_text(f"#!{sys.executable}\n" + COUNTER)
        counted.chmod(0o755)
        self.env.update(
            AUTOCODE_FIXTURE_MODE="no-human", CORRECTION_TRACE=str(self.trace), CORRECTION_VALID=str(self.valid)
        )
        self.env["PATH"] = os.pathsep.join([str(executable.parent), str(Path(sys.executable).parent), self.env["PATH"]])
        self.builder_writes(
            {
                "greet.py": bugfix.references.BUGFIX_REFERENCE["greet.py"],
                "test_greet.py": bugfix.references.BUGFIX_REFERENCE["test_greet.py"],
            }
        )
        command = f"env CHECK_LABEL={{}} CHECK_COUNTER={shlex.quote(str(self.counter))} {shlex.quote(str(counted))}"
        suite = command.format("suite") + " -m unittest -v test_greet"
        regression = command.format("regression") + " -m unittest -v test_greet.TestGreet.test_blank_name_rejected"
        self.launch(
            ["Fix the blank-name greeting", "--no-chat", "--test-command", suite, "--regression-command", regression], 2
        )
        self.run_dir = next((self.project / ".autocode/runs").iterdir())
        self.launch(["--run-dir", str(self.run_dir), "--answer", "Q1=CLI"], 0)
        self.launch(["--run-dir", str(self.run_dir), "--no-chat"], 2)
        state = self.state()
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])
        self.launch(["--run-dir", str(self.run_dir), "--approve-goal", state["displayed_goal"]], 0)
        # Stop at a saved boundary to disable the Investigator, so the rejected report pauses for resume.
        self.launch(["--run-dir", str(self.run_dir), "--no-chat", "--pause-after-stage"], 2)
        state = self.state()
        self.assertEqual("PAUSED_REQUESTED", state["status"])
        self.assertEqual("bugfix", state["goal_contract"]["body"]["task_kind"])
        state["settings"]["report_repair"] = {"max_attempts": 2}
        state["settings"].setdefault("stuck_investigation", {})["max_calls_per_run"] = 0
        self.write(state)
        self.launch(["--run-dir", str(self.run_dir), "--resume-paused", "--no-chat"], 2)
        state = self.state()
        self.assertEqual("PAUSED_INVALID_OUTPUT", state["status"], state.get("stop_reason"))
        self.assertEqual("tester", state["next_stage"])
        self.assertEqual(2, state["pending_report_repair"]["attempts"])
        tester = [row for row in self.rows() if row["stage"] == "tester"]
        self.assertEqual([False, True, True], [row["report_repair"] for row in tester])
        self.assertEqual(2, len({row["error"] for row in tester if row["report_repair"]}))
        self.assertTrue(self.baselines(self.counts()), "the counter sees original-code baselines")
        self.assertTrue(any(line.startswith("regression\t") for line in self.counts()), "and regression proofs")
        return state

    def validator_update(self, state):
        """Stand for a validator update: the saved repair was last judged by another runtime."""
        pending = state["pending_report_repair"]
        pending["validation_runtime"] = "earlier runtime"
        pending["error"] = "earlier validator diagnostic"
        self.write(state)
        return state

    def resume(self):
        """``autocode --run-dir RUN --resume-paused --no-chat``; returns the launches and counted checks it made."""
        rows, counts = len(self.rows()), len(self.counts())
        subprocess.run(
            [
                *self.entry,
                "--workspace",
                str(self.project),
                "--run-dir",
                str(self.run_dir),
                "--resume-paused",
                "--no-chat",
            ],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=240,
        )
        return self.rows()[rows:], self.counts()[counts:]

    def state(self):
        return json.loads((self.run_dir / "state.json").read_text())

    def write(self, state):
        (self.run_dir / "state.json").write_text(json.dumps(state))

    def rows(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()] if self.trace.exists() else []

    def counts(self):
        return self.counter.read_text().splitlines() if self.counter.exists() else []

    def baselines(self, lines):
        """Suite runs in an original-code baseline tree (RUN/regression/baseline-<id>/scratch/baseline).

        The scratch tree is mirrored outside the workspace (autocode_scratch_location, #4057), so the
        prefix is the mirror of RUN/regression, read from the module that places it.
        """
        regression = str(scratch_location.relocate(self.run_dir / "regression")) + os.sep
        return [
            line
            for line in lines
            if line.startswith("suite\t")
            and line.split("\t", 1)[1].startswith(regression)
            and line.endswith(os.sep + os.path.join("scratch", "baseline"))
        ]

    @staticmethod
    def launches(rows, *, repair):
        return [row for row in rows if row["stage"] == "tester" and row["report_repair"] is repair]

    def correction_note(self, state):
        notes = [
            row
            for row in state.get("reconciliation_notes") or []
            if row.get("actor") == "runner" and "needs only a report correction" in str(row.get("reason"))
        ]
        self.assertEqual(1, len(notes), state.get("reconciliation_notes"))
        return notes[0]

    def corrected_twice_rejected(self):
        """AC6: after the correction is renewed, each corrected report is still rejected."""
        before = self.validator_update(self.exhausted())
        rejected = [
            row["output"] for row in before["stages"] if row["stage"].startswith("tester") and row.get("rejected")
        ]
        rows, counts = self.resume()
        return rejected, rows, counts, self.state()

    def archived_pause(self):
        """AC2: an earlier runtime's exhausted retry archived the repair, then the controller stopped."""
        state = self.validator_update(self.exhausted())
        state["pending_report_repair"].setdefault("revalidations", []).append(
            {
                "runtime": "earlier runtime",
                "output": state["pending_report_repair"]["latest_rejected"]["output"],
                "result": "rejected",
                "reason": "earlier validator diagnostic",
                "at": dt.datetime.now(dt.UTC).isoformat(),
            }
        )
        # The original exhausted execution retry, as the earlier runtime ran it on resume.
        self.assertTrue(stage_recovery.prepare_exhausted_execution_report_retry(state, self.run_dir, self.project))
        state = self.state()
        archived = state["report_repair_archive"][-1]
        self.assertEqual(SPENT, archived["reason"])
        self.assertNotIn("pending_report_repair", state)
        self.assertEqual("tester", state["next_stage"])
        self.assertEqual("earlier validator diagnostic", archived["repair"]["revalidations"][-1]["reason"])
        # A controller stopped after the redundant baseline began, before any new Tester attempt.
        run_status.pause(state, "PAUSED_REQUESTED", "Stopped after the original-code baseline began")
        state.pop("active_stage", None)
        state.pop("active_runner_check", None)
        self.write(state)
        return archived

    def test_ac1_resume_corrects_authenticated_report_without_new_verification(self):
        before = self.validator_update(self.exhausted())
        earlier = self.launches(self.rows(), repair=True)
        saved = before["pending_report_repair"]["original"]
        self.valid.write_text("the corrected report is valid")
        rows, counts = self.resume()
        repairs = self.launches(rows, repair=True)
        self.assertEqual(1, len(repairs), rows)
        self.assertEqual([], self.launches(rows, repair=False))
        state = self.state()
        revalidation = self.correction_note(state)["revalidation"]
        self.assertEqual("rejected", revalidation["result"])
        self.assertNotEqual("earlier runtime", revalidation["runtime"])
        self.assertEqual(revalidation["reason"], repairs[0]["error"])
        self.assertNotEqual("earlier validator diagnostic", repairs[0]["error"])
        self.assertEqual(saved["output"], repairs[0]["original_output"])
        self.assertEqual(saved["events"], repairs[0]["original_events"])
        self.assertTrue(repairs[0]["original_executed_checks"])
        self.assertEqual(
            [row["original_executed_checks"] for row in earlier], [repairs[0]["original_executed_checks"]] * 2
        )
        self.assertEqual([], self.baselines(counts), counts)
        self.assertEqual([], [line for line in counts if line.startswith("regression\t")], counts)
        # Nothing was asked of the person: the run finished on the corrected report.
        self.assertEqual("TASK_COMPLETE", state["status"], state.get("stop_reason"))
        self.assertFalse(state.get("pending_questions"))

    def test_ac2_resume_after_redundant_baseline_restores_report_correction(self):
        archived = self.archived_pause()
        self.valid.write_text("the corrected report is valid")
        started = dt.datetime.now(dt.UTC)
        rows, counts = self.resume()
        state = self.state()
        repairs = self.launches(rows, repair=True)
        self.assertEqual(1, len(repairs), rows)
        self.assertEqual([], self.launches(rows, repair=False))
        self.assertEqual(archived["repair"]["original"]["output"], repairs[0]["original_output"])
        self.assertNotIn(archived, (state.get("report_repair_archive") or [])[-1:])
        note = self.correction_note(state)
        self.assertTrue(note["restored_from_archive"])
        revalidation = note["revalidation"]
        self.assertEqual("rejected", revalidation["result"])
        self.assertNotEqual("earlier runtime", revalidation["runtime"])
        self.assertGreaterEqual(dt.datetime.fromisoformat(revalidation["at"]), started)
        self.assertEqual(revalidation["reason"], repairs[0]["error"])
        self.assertNotEqual("earlier validator diagnostic", repairs[0]["error"])
        self.assertEqual([], self.baselines(counts), counts)
        self.assertEqual([], [line for line in counts if line.startswith("regression\t")], counts)

    def test_ac3_changed_source_does_not_reuse_old_report(self):
        state = self.validator_update(self.exhausted())
        saved = state["pending_report_repair"]["original"]
        greet = self.project / "greet.py"
        greet.write_text(greet.read_text() + "# Reviewed while paused.\n")
        self.valid.write_text("the fresh report is valid")
        rows, _ = self.resume()
        self.assertTrue(rows)
        self.assertEqual({"stage": "tester", "report_repair": False}, rows[0])
        archive = self.state().get("report_repair_archive") or []
        self.assertIn(saved["output"], [row["repair"]["original"]["output"] for row in archive])

    def test_ac4_mismatched_pins_do_not_reuse_old_report(self):
        state = self.validator_update(self.exhausted())
        events = Path(state["pending_report_repair"]["original"]["events"])
        original_mode = events.stat().st_mode
        events.chmod(original_mode | 0o200)
        try:
            events.write_bytes(events.read_bytes() + b"\n")
        finally:
            events.chmod(original_mode)
        self.valid.write_text("the fresh report is valid")
        rows, _ = self.resume()
        full = [index for index, row in enumerate(rows) if row == {"stage": "tester", "report_repair": False}]
        self.assertTrue(full, rows)
        self.assertEqual([], self.launches(rows[: full[0]], repair=True))
        self.assertEqual(SPENT, self.state()["report_repair_archive"][-1]["reason"])

    def test_ac5_superseding_tester_attempt_does_not_reuse_old_report(self):
        archived = self.archived_pause()
        state = self.state()
        attempt = dict(next(row for row in state["stages"] if row["stage"] == "tester" and not row.get("report_only")))
        later = (dt.datetime.fromisoformat(archived["at"]) + dt.timedelta(seconds=1)).isoformat()
        attempt.update(started_at=later, finished_at=later, rejected=False, exit_code=0)
        state["stages"].append(attempt)
        self.write(state)
        self.valid.write_text("the fresh report is valid")
        rows, _ = self.resume()
        state = self.state()
        self.assertEqual(archived, state["report_repair_archive"][-1])
        restored = ((state.get("pending_report_repair") or {}).get("original") or {}).get("output")
        self.assertNotEqual(archived["repair"]["original"]["output"], restored)
        self.assertEqual([], self.launches(rows, repair=True))

    def test_ac6_corrected_report_needs_same_independent_validation(self):
        rejected, rows, counts, state = self.corrected_twice_rejected()
        self.assertEqual(2, len(self.launches(rows, repair=True)), rows)
        self.assertEqual([], self.launches(rows, repair=False))
        self.assertEqual("PAUSED_INVALID_OUTPUT", state["status"], state.get("stop_reason"))
        self.assertEqual(2, state["pending_report_repair"]["attempts"])
        self.assertEqual(2, state.get("report_repair_lifetime_attempts"))
        epochs = [row for row in state["user_events"] if row.get("kind") == "report_repair_resume_epoch"]
        self.assertEqual([("runner", 2)], [(row["actor"], row["cleared_attempts"]) for row in epochs])
        kept = {row["output"]: row for row in state["stages"] if row.get("output") in rejected}
        self.assertEqual(3, len(rejected))
        self.assertEqual(set(rejected), set(kept))
        self.assertTrue(all(row.get("rejected") for row in kept.values()))
        self.assertEqual(
            ["tester", "tester_report_repair", "tester_report_repair"], [kept[output]["stage"] for output in rejected]
        )
        self.assertEqual([], self.baselines(counts), counts)
        self.assertEqual([], [line for line in counts if line.startswith("regression\t")], counts)

    def test_ac12_second_resume_without_validator_update_takes_fresh_retry(self):
        _, rows, _, state = self.corrected_twice_rejected()
        self.assertEqual(2, len(self.launches(rows, repair=True)), rows)
        self.assertEqual([], self.launches(rows, repair=False))
        self.assertEqual("PAUSED_INVALID_OUTPUT", state["status"], state.get("stop_reason"))
        self.assertEqual(2, state["pending_report_repair"]["attempts"])
        spent = state["pending_report_repair"]["original"]["output"]
        self.valid.write_text("the fresh report is valid")
        rows, _ = self.resume()
        state = self.state()
        self.assertEqual([], self.launches(rows, repair=True))
        self.assertTrue(rows)
        self.assertEqual({"stage": "tester", "report_repair": False}, rows[0])
        last = state["report_repair_archive"][-1]
        self.assertEqual(SPENT, last["reason"])
        self.assertEqual(2, last["repair"]["attempts"])
        self.assertEqual(spent, last["repair"]["original"]["output"])
        self.assertEqual(2, state.get("report_repair_lifetime_attempts"))


# The behavior this change must keep is guarded by three existing tests in other modules. Each is selected
# here, unchanged and under its own name, so a regression proof that runs only this module runs them on the
# original code and with the change. Every other test the base class defines is hidden, so only the guard runs.
def _guard(name, base, keep):
    hidden = {attr: None for attr in dir(base) if attr.startswith("test") and attr != keep}
    return type(name, (base,), hidden)


RuntimeChangeRevalidationGuard = _guard(
    "RuntimeChangeRevalidationGuard",
    test_report_repair.RepairTests,
    "test_runtime_change_revalidates_pinned_rejected_report_without_a_new_provider_call",
)
UnlaunchableRepairRestoreGuard = _guard(
    "UnlaunchableRepairRestoreGuard",
    test_report_repair_compaction.SharedCauseHKiloDecoratedRepairAndStageLessonsTests,
    "test_retry_failed_stage_restores_attempts_zero_unlaunchable_repair_only_when_now_launchable",
)
ExhaustedExecutionRetryGuard = _guard(
    "ExhaustedExecutionRetryGuard",
    test_report_repair.RepairTests,
    "test_explicit_execution_retry_archives_exhausted_repairs_and_rotates_session",
)


if __name__ == "__main__":
    unittest.main()
