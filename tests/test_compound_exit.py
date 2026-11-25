"""A Tester check run inside a longer command line (#4165).

Live Testers ran ``python3 -m pytest tests/test_number.py -q 2>&1 | tail -3`` or ``a; b`` as one Bash call
and cited the one command they cared about. The check was refused as "not supported by an exact executed
Validator event", a report-only repair could not run anything and was refused again, and a fresh Tester
attempt followed: the most common rejected Tester answer in the 2026-11-24 Claude starter sweep. These
tests drive the pure reading of the line, the check binding (autocode_support.verify_checks) and the
Tester's admission (autopilot.apply_review_result) with saved reports; no model call.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_compound_exit as compound_exit
import autocode_support as support
import autopilot

from . import test_autocode as base
from . import test_tester_probes as probes

WORKSPACE = "/work/project"


class ReadingTheLineTests(unittest.TestCase):
    """What a recorded line shows about one command it ran, read as the shell runs it."""

    def answer(self, cited, line, code):
        return compound_exit.resolve(cited, line, code, workspace=WORKSPACE)

    def test_the_last_command_of_a_sequence_has_the_line_exit_code(self):
        line = "git diff --stat HEAD; git diff --name-only HEAD"
        self.assertEqual((compound_exit.EXACT, 0), self.answer("git diff --name-only HEAD", line, 0))
        self.assertEqual((compound_exit.EXACT, 3), self.answer("git diff --name-only HEAD", line, 3))
        newline = "git diff --stat HEAD\ngit diff --name-only HEAD"
        self.assertEqual((compound_exit.EXACT, 3), self.answer("git diff --name-only HEAD", newline, 3))

    def test_every_command_of_an_and_chain_that_exited_0_exited_0(self):
        line = f"cd {WORKSPACE} && npm run lint && npm test"
        self.assertEqual((compound_exit.EXACT, 0), self.answer("npm run lint", line, 0))
        self.assertEqual((compound_exit.EXACT, 0), self.answer("npm test", line, 0))
        # A failed chain says neither which command failed nor whether the later ones ran.
        self.assertIsNone(self.answer("npm test", line, 1))
        self.assertIsNone(self.answer("npm run lint", line, 1))

    def test_a_command_whose_exit_code_the_line_hides_is_one_that_certainly_ran(self):
        for line, cited in (
            ("python3 -m pytest tests/test_number.py -q 2>&1 | tail -3", "python3 -m pytest tests/test_number.py -q"),
            ("make test 2>&1 | grep -E 'FAIL|ok' | tail -5", "make test"),
            ("sh run_tests.sh; echo EXIT $?", "sh run_tests.sh"),
            ("git diff --stat HEAD; git diff --name-only HEAD", "git diff --stat HEAD"),
            (f"cd {WORKSPACE} && npm test 2>&1 | tail -20", "npm test"),
        ):
            with self.subTest(line=line):
                self.assertEqual((compound_exit.RAN, None), self.answer(cited, line, 0))

    def test_a_line_that_could_run_the_command_differently_shows_nothing(self):
        cited = "python3 -m pytest -q"
        for line in (
            "python3 -m pytest -q | head -3",  # head can stop the command early
            "python3 -m pytest -q | grep -q passed",  # so can grep -q and -m
            "python3 -m pytest -q | grep -m1 passed",
            "python3 -m pytest -q | grep -qE passed",
            "python3 -m pytest -q || true",
            "set -e; python3 -m pytest -q; echo done",
            "X=1; python3 -m pytest -q",
            "python3 -m pytest -q & wait",
            "python3 -m pytest -q $(cat args)",
            "python3 -m pytest -q `cat args`",
            "(python3 -m pytest -q) | tail -3",
            "cd /elsewhere && python3 -m pytest -q | tail -3",
            "export X=1; python3 -m pytest -q",
            "python3 -m pytest -q <<EOF\nx\nEOF",
        ):
            with self.subTest(line=line):
                self.assertIsNone(self.answer(cited, line, 0))
        # After a && that may have failed, the command may not have run at all.
        self.assertIsNone(self.answer(cited, "make build && python3 -m pytest -q | tail -3", 1))

    def test_the_same_command_twice_with_different_answers_is_not_the_cited_one(self):
        self.assertIsNone(self.answer("pytest -q", "pytest -q; pytest -q", 0))

    def test_quoted_operators_are_text(self):
        line = "printf '%s\\n' 'a; b' && python3 -m pytest -q"
        self.assertEqual((compound_exit.EXACT, 0), self.answer("python3 -m pytest -q", line, 0))
        self.assertIsNone(self.answer("b", line, 0))

    def test_a_capture_run_is_left_to_its_receipt(self):
        capture = "autocode capture --output .autocode/evidence/x.json -- pytest -q"
        self.assertIsNone(compound_exit.part(f"{capture} 2>&1 | tail -c 500", capture, 0, WORKSPACE))


def write_events(path, *rows):
    path.write_text(
        "".join(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": ident, "type": "command_execution", "command": line, "exit_code": code},
                }
            )
            + "\n"
            for ident, line, code in rows
        )
    )


class BindingTheCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.events = self.workspace / "events.jsonl"

    def test_an_exact_answer_binds_the_check_for_every_caller(self):
        write_events(self.events, ("item_1", "git diff --stat HEAD; git diff --name-only HEAD", 0))
        checks = [{"command": "git diff --name-only HEAD", "exit_code": None, "evidence_ref": "event:"}]
        support.verify_checks(checks, self.workspace, self.events)
        self.assertEqual(
            [{"command": "git diff --name-only HEAD", "exit_code": 0, "evidence_ref": "event:item_1"}], checks
        )
        # The bound check verifies again unchanged, as later stages re-verify an accepted report.
        again = copy.deepcopy(checks)
        support.verify_checks(again, self.workspace, self.events)
        self.assertEqual(checks, again)
        # A claimed exit code the line contradicts is still refused.
        wrong = [{"command": "git diff --name-only HEAD", "exit_code": 1, "evidence_ref": "event:"}]
        with self.assertRaisesRegex(ValueError, "not supported by an exact executed Validator event"):
            support.verify_checks(wrong, self.workspace, self.events)

    def test_a_hidden_exit_code_binds_only_for_a_caller_that_replays_the_check(self):
        line = "python3 -m pytest tests/test_number.py -q 2>&1 | tail -3"
        write_events(self.events, ("item_1", line, 0))
        cited = "python3 -m pytest tests/test_number.py -q"
        check = {"command": cited, "exit_code": None, "evidence_ref": "event:"}
        with self.assertRaisesRegex(ValueError, "joined with other commands or piped into a filter"):
            support.verify_checks([dict(check)], self.workspace, self.events)
        hidden: list = []
        checks = [dict(check)]
        support.verify_checks(checks, self.workspace, self.events, hidden_exit=hidden)
        self.assertEqual([{"command": cited, "exit_code": 0, "evidence_ref": "event:item_1"}], checks)
        self.assertEqual([{"command": cited, "evidence_ref": "event:item_1", "line": line}], hidden)
        # Only a passing claim can wait for the replay, which accepts nothing but exit 0.
        failing = [{"command": cited, "exit_code": 1, "evidence_ref": "event:"}]
        with self.assertRaisesRegex(ValueError, "not supported by an exact executed Validator event"):
            support.verify_checks(failing, self.workspace, self.events, hidden_exit=[])

    def test_the_latest_run_decides(self):
        cited = "python3 -m pytest -q"
        # Run alone and failing, then fixed and run again through a filter: the later line decides.
        write_events(self.events, ("item_1", cited, 1), ("item_2", f"{cited} 2>&1 | tail -3", 0))
        checks = [{"command": cited, "exit_code": 0, "evidence_ref": "event:"}]
        support.verify_checks(checks, self.workspace, self.events, hidden_exit=[])
        self.assertEqual("event:item_2", checks[0]["evidence_ref"])
        # Run alone after the filtered run: the run alone decides, as before.
        write_events(self.events, ("item_1", f"{cited} 2>&1 | tail -3", 0), ("item_2", cited, 1))
        checks = [{"command": cited, "exit_code": None, "evidence_ref": "event:"}]
        support.verify_checks(checks, self.workspace, self.events, hidden_exit=[])
        self.assertEqual([{"command": cited, "exit_code": 1, "evidence_ref": "event:item_2"}], checks)


class ReplayObservationTests(unittest.TestCase):
    CHECK = {"command": "pytest -q", "evidence_ref": "event:item_1", "line": "pytest -q | tail -3"}

    def replay(self, **row):
        return {"checks": [{"command": "pytest -q", "evidence_ref": "event:item_1", "exit_code": 0, **row}]}

    def test_the_replay_must_show_exit_0(self):
        compound_exit.require_replayed([self.CHECK], self.replay())
        compound_exit.require_replayed([], None)
        for replay in (
            None,
            {"checks": []},
            self.replay(exit_code=1),
            self.replay(timed_out=True),
            self.replay(error="could not start"),
            self.replay(exit_code=1, base_attribution={"kind": "base"}),  # excused as the base's failure
        ):
            with self.subTest(replay=replay), self.assertRaisesRegex(ValueError, "hides its own exit code"):
                compound_exit.require_replayed([self.CHECK], replay)


class TesterAdmissionTests(unittest.TestCase):
    """The Tester's report is admitted when its replay saw the hidden exit code, and refused otherwise."""

    def setUp(self):
        self.fixture = probes.TesterProbeTests("test_ac4_cited_tester_probe_is_accepted_and_recorded")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.piped = probes.PROBE + " 2>&1 | tail -3"
        self.record = self.fixture.record(rows=[("item_1", probes.APPROVED, 0), ("item_2", self.piped, 0)])

    def report(self, verdict="PASS"):
        report = probes.event_copy(probes.ac4_report())
        report["verdict"] = verdict
        return report

    def admit(self, report, replay):
        state = self.fixture.state
        with patch.object(autopilot.check_replay, "replay", return_value=replay) as replayed:
            autopilot.apply_review_result(
                base.runner, state, "tester", copy.deepcopy(report), self.record, self.fixture.root, self.fixture.run
            )
        return replayed

    @staticmethod
    def rows(probe_exit=0):
        return {
            "checks": [
                {"command": probes.APPROVED, "evidence_ref": "event:item_1", "exit_code": 0},
                {"command": probes.PROBE, "evidence_ref": "event:item_2", "exit_code": probe_exit},
            ]
        }

    def test_a_pass_whose_replay_ran_the_piped_command_is_admitted(self):
        replayed = self.admit(self.report(), self.rows())
        replayed_checks = replayed.call_args.args[0]
        self.assertIn({"command": probes.PROBE, "exit_code": 0, "evidence_ref": "event:item_2"}, replayed_checks)
        validation = self.fixture.state["validation"]
        self.assertEqual("PASS", validation["verdict"])
        self.assertIn({"command": probes.PROBE, "exit_code": 0, "evidence_ref": "event:item_2"}, validation["checks"])

    def test_a_replay_that_did_not_see_exit_0_refuses_the_report(self):
        with self.assertRaisesRegex(ValueError, f"`{probes.PROBE}` ran only inside `{self.piped}`"):
            self.admit(self.report(), self.rows(probe_exit=1))
        self.assertNotIn("validation", self.fixture.state)

    def test_a_report_that_is_not_replayed_cannot_rely_on_a_hidden_exit_code(self):
        report = self.report(verdict="FAIL")
        report["criterion_results"][0]["status"] = "FAIL"
        report["unverified_criteria"] = []
        report["findings"] = [
            {
                "id": "F1",
                "severity": "high",
                "summary": "Record C1 is lost",
                "evidence_refs": ["check:1"],
                "criterion_ids": ["C1"],
            }
        ]
        with self.assertRaisesRegex(ValueError, "hides its own exit code"):
            self.admit(report, None)
        self.assertNotIn("validation", self.fixture.state)


if __name__ == "__main__":
    unittest.main()
