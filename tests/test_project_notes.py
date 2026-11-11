"""Durable, evidence-cited project notes: learned with a receipt, read as advice, corrected by a person (#2943)."""

import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_notes_cli as notes_cli
import autocode_project_notes as store
import autocode_project_notes_completion as completion
import autocode_project_notes_lifecycle as lifecycle
import autocode_regression as regression
import autocode_run_view as run_view
import autocode_stage_context as stage_context
import autocode_status as status
import autocode_stuck_job as stuck
import autocode_unattended as unattended
import autocode_util as util
import scenario_references as references
import task_scenarios as scenarios
from units import autoplanner

from tests.test_stuck_job import ProbeTests, report
from tests.test_verify import Project

AT = "2026-11-11T18:00:00+00:00"


def receipt(run="20261111-chat-abc123", **overrides):
    value = {
        "run": run,
        "stage": "investigate_stuck",
        "investigation": "builder:PAUSED_REPEATED_FAILURE",
        "observation": "The Investigator's probe exited 0 over the files it cited: test -f Makefile",
        "source_revision": None,
        "recorded_at": AT,
    }
    value.update(overrides)
    return value


def note(text="Run `make test-unit`; `make test` needs Docker.", kind="test_command", **overrides):
    return store.candidate(kind, text, receipt(**overrides))


class ProjectDirectory(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve() / "project"
        self.project.mkdir()

    def saved(self):
        return json.loads((self.project / ".autocode" / store.FILENAME).read_text())["notes"]


class StoreTests(ProjectDirectory):
    def test_a_note_is_kept_only_with_a_receipt_and_the_same_fact_is_one_note(self):
        first = note()
        again = note(text="  Run `make test-unit`;   `make test` needs Docker. ", run="20261112-chat-def456")
        self.assertEqual(first["id"], again["id"])  # kind and normalized text name the fact
        self.assertEqual([first["id"]], store.add(self.project, [first]))
        store.add(self.project, [again])
        saved = self.saved()
        self.assertEqual(1, len(saved))
        self.assertEqual("20261111-chat-abc123", saved[0]["receipt"]["run"])  # the first receipt stays
        with self.assertRaises(ValueError):
            store.add(self.project, [{**first, "receipt": {**first["receipt"], "observation": ""}}])
        with self.assertRaises(ValueError):  # an Investigator's note names the run it came from
            store.add(self.project, [{**first, "receipt": {**first["receipt"], "run": None}}])

    def test_a_secret_or_a_local_path_omits_the_note_whole(self):
        self.assertIsNone(note(text="Tests read /Users/someone/fixtures/data.json"))
        self.assertIsNone(note(text="Set api_key=sk-live-0123456789abcdef0123 before the tests"))
        self.assertIsNone(note(text="x" * (store.MAX_TEXT + 1)))
        self.assertIsNone(note(kind="permission"))
        self.assertIsNotNone(note(text="tests/test_clock.py::test_tick flakes under load; rerun it once"))

    def test_a_retracted_note_is_not_learned_again_over_the_correction(self):
        flaky = note(text="tests/test_clock.py flakes", kind="flaky_test")
        store.add(self.project, [flaky])
        retracted = store.retract(self.project, flaky["id"], reason="Fixed by a fake clock", at=AT)
        self.assertEqual({"at": AT, "reason": "Fixed by a fake clock", "replaced_by": None}, retracted["retraction"])
        store.add(self.project, [note(text="tests/test_clock.py flakes", kind="flaky_test", run="later-run")])
        self.assertEqual([], store.active(store.load(self.project)["notes"]))
        self.assertEqual(retracted["retraction"], self.saved()[0]["retraction"])
        with self.assertRaisesRegex(ValueError, "secret or a local path"):
            store.retract(self.project, flaky["id"], reason="See /home/me/notes.txt", at=AT)
        with self.assertRaisesRegex(KeyError, "note-0000"):
            store.retract(self.project, "note-0000000000000000", reason=None, at=AT)

    def test_a_correction_replaces_the_text_with_the_persons_own(self):
        wrong = note(text="Run make test")
        store.add(self.project, [wrong])
        fixed = store.correct(self.project, wrong["id"], "Run make test-unit; make test needs Docker", at=AT)
        self.assertEqual(
            ("test_command", "person", None), (fixed["kind"], fixed["receipt"]["stage"], fixed["receipt"]["run"])
        )
        self.assertEqual(f"A person corrected {wrong['id']}.", fixed["receipt"]["observation"])
        old, new = self.saved()
        self.assertEqual(fixed["id"], old["retraction"]["replaced_by"])
        self.assertEqual([new], store.active(self.saved()))
        with self.assertRaisesRegex(ValueError, "is retracted"):
            store.correct(self.project, wrong["id"], "Something else", at=AT)
        with self.assertRaisesRegex(ValueError, "current text"):
            store.correct(self.project, fixed["id"], "Run make test-unit; make test needs Docker", at=AT)
        with self.assertRaisesRegex(ValueError, "no secret or local path"):
            store.correct(self.project, fixed["id"], "Run /opt/tools/make", at=AT)

    def test_the_file_is_bounded_and_retractions_outlive_active_notes(self):
        kept = note(text="note 0")
        store.add(self.project, [kept])
        store.retract(self.project, kept["id"], reason=None, at=AT)
        store.add(self.project, [note(text=f"note {i}") for i in range(1, store.MAX_NOTES + 5)])
        saved = self.saved()
        self.assertEqual(store.MAX_NOTES, len(saved))
        self.assertEqual(kept["id"], saved[0]["id"])  # the retraction survives
        self.assertNotIn(note(text="note 1")["id"], {row["id"] for row in saved})  # the oldest active note went

    def test_an_invalid_or_linked_file_is_ignored_and_never_overwritten(self):
        directory = self.project / ".autocode"
        directory.mkdir()
        path = directory / store.FILENAME
        path.write_text('{"version": 1, "notes": [{"id": "forged"}]}')
        self.assertEqual(("invalid", []), (store.load(self.project)["status"], store.load(self.project)["notes"]))
        with self.assertRaises(ValueError):
            store.add(self.project, [note()])
        self.assertEqual('{"version": 1, "notes": [{"id": "forged"}]}', path.read_text())
        path.unlink()
        target = self.project / "elsewhere.json"
        target.write_text(json.dumps({"version": 1, "notes": []}))
        path.symlink_to(target)
        self.assertEqual("invalid", store.load(self.project)["status"])


class LifecycleTests(ProjectDirectory):
    def shown(self, counterexample_exit=1):
        return [
            {
                "id": "the diagnosed cause",
                "probe": "test -f Makefile",
                "exit_code": 0,
                "counterexample": {"exit_code": counterexample_exit},
            }
        ]

    def test_an_investigation_keeps_notes_only_with_a_probe_that_showed_its_cause(self):
        value = {"project_notes": [{"kind": "reproduce_command", "text": "python3 -m unittest tests.test_clock"}]}
        entry = {"identity": "builder:PAUSED_NO_PROGRESS", "finished_at": AT, "source_revision": "abc"}
        unproven = lifecycle.proposals(value, [], entry, run_dir="/x/.autocode/runs/run-1")
        self.assertEqual(("not_recorded", 1), (unproven["status"], unproven["proposed"]))
        self.assertIsNone(lifecycle.proposals({"project_notes": []}, self.shown(), entry, run_dir="/r/run-1"))
        kept = lifecycle.proposals(value, self.shown(), entry, run_dir="/x/.autocode/runs/run-1")
        self.assertEqual(("pending", 0), (kept["status"], kept["omitted"]))
        self.assertEqual(
            {
                "run": "run-1",
                "stage": "investigate_stuck",
                "investigation": "builder:PAUSED_NO_PROGRESS",
                "observation": "The Investigator's probe exited 0 over the files it cited and failed once one "
                "was changed: test -f Makefile",
                "source_revision": "abc",
                "recorded_at": AT,
            },
            kept["notes"][0]["receipt"],
        )
        unsafe = {
            "project_notes": [
                *value["project_notes"],
                {"kind": "other", "text": "token is ghp_secret0123456789abcdef0123456789abcd"},
            ]
        }
        self.assertEqual(1, lifecycle.proposals(unsafe, self.shown(), entry, run_dir="/r/run-1")["omitted"])

    def test_publishing_writes_the_receipted_notes_after_the_state_checkpoint(self):
        notes = [note(text="Run make test-unit")]
        state = {
            "workspace": str(self.project),
            "stuck_investigations": [
                {"identity": "a", "project_notes": {"status": "pending", "notes": notes, "omitted": 0}},
                {"identity": "b", "project_notes": {"status": "not_recorded", "proposed": 1, "reason": "no probe"}},
            ],
        }
        self.assertFalse(lifecycle.publish(self.project / "other.json", state))
        self.assertFalse((self.project / ".autocode" / store.FILENAME).exists())
        self.assertTrue(lifecycle.publish(self.project / "state.json", state))
        self.assertEqual("written", state["stuck_investigations"][0]["project_notes"]["status"])
        self.assertEqual(notes, self.saved())
        self.assertEqual(notes, lifecycle.learned(state))
        self.assertFalse(lifecycle.publish(self.project / "state.json", state))  # written once

    def test_an_unwritable_project_is_recorded_and_does_not_stop_the_run(self):
        (self.project / ".autocode").mkdir()
        (self.project / ".autocode" / store.FILENAME).write_text("not json")
        state = {
            "workspace": str(self.project),
            "stuck_investigations": [{"project_notes": {"status": "pending", "notes": [note()]}}],
        }
        self.assertTrue(lifecycle.publish(self.project / "state.json", state))
        self.assertEqual("unavailable", state["stuck_investigations"][0]["project_notes"]["status"])

    def test_a_run_reads_the_notes_frozen_at_launch_and_the_status_view_shows_them(self):
        kept, dropped = note(text="Run make test-unit"), note(text="Old fact")
        store.add(self.project, [kept, dropped])
        store.retract(self.project, dropped["id"], reason=None, at=AT)
        state = {"workspace": str(self.project)}
        lifecycle.install(state, lifecycle.load(self.project))
        store.add(self.project, [note(text="Learned after launch")])
        lifecycle.install(state, lifecycle.load(self.project))  # a resume never reloads
        self.assertEqual([kept["id"]], [row["id"] for row in lifecycle.context(state)])
        self.assertEqual([], lifecycle.for_stage(state, "builder"))
        self.assertEqual(lifecycle.context(state), lifecycle.for_stage(state, "draft_plan"))
        self.assertEqual(
            {
                "notes": [kept],
                "learned": [],
                "next_command": ["notes", "list", "--workspace", str(self.project), "--json"],
            },
            lifecycle.view(state),
        )
        self.assertEqual(lifecycle.view(state), run_view.view(state)["project_notes"])

    def test_the_task_planner_is_given_the_notes_and_the_builder_is_not(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(
            ["git", "-c", "user.name=f", "-c", "user.email=f@example.test", "commit", "--allow-empty", "-qm", "base"],
            cwd=self.project,
            check=True,
        )
        store.add(self.project, [note(text="Run make test-unit")])
        state = {
            "task": "Next task",
            "workspace": str(self.project),
            "iteration": 1,
            "stages": [],
            "acceptance_criteria": [],
            "settings": {"roles": {}},
        }
        lifecycle.install(state, lifecycle.load(self.project))
        for stage, given in (("plan_task", True), ("builder", False)):
            with self.subTest(stage=stage):
                prompt, _ = stage_context.context_packet(state, stage, self.project / "state.json")
                packet = json.loads(prompt.split("\nCURRENT HANDOFF DATA\n", 1)[1])
                self.assertEqual(given, "project_notes" in packet)
                self.assertEqual(given, lifecycle.RULE in prompt)


class InvestigatorTests(unittest.TestCase):
    """The Investigator's notes go through the real probe check and the real state checkpoint."""

    def setUp(self):
        self.fixture = ProbeTests()

    def investigate(self, **overrides):
        state, workspace, run_dir = self.fixture.setup()
        notes = [{"kind": "build_quirk", "text": "The planner must cite repository paths without trailing prose"}]
        self.fixture.apply(state, run_dir, workspace, project_notes=notes, **overrides)
        return state, workspace, run_dir

    def test_a_shown_cause_keeps_the_note_and_the_next_planner_reads_it(self):
        state, workspace, run_dir = self.investigate(probe=ProbeTests.SPACE_PROBE, example="x")
        entry = state["stuck_investigations"][0]
        self.assertEqual("pending", entry["project_notes"]["status"])
        status.persist(run_dir / "state.json", state)
        saved = json.loads((workspace / ".autocode" / store.FILENAME).read_text())["notes"]
        self.assertEqual(run_dir.name, saved[0]["receipt"]["run"])
        self.assertIn(ProbeTests.SPACE_PROBE, saved[0]["receipt"]["observation"])
        self.assertEqual(
            "written",
            json.loads((run_dir / "state.json").read_text())["stuck_investigations"][0]["project_notes"]["status"],
        )

        later = {
            "task": "Next task",
            "workspace": str(workspace),
            "run_dir": str(run_dir),
            "iteration": 1,
            "stages": [],
            "acceptance_criteria": [],
            "settings": {"joint_planning": True, "spec_first": True, "roles": {}},
        }
        lifecycle.install(later, lifecycle.load(workspace))
        for stage, given in (("requirements_gather", True), ("draft_plan", True), ("review_plan", False)):
            with self.subTest(stage=stage):
                prompt, _ = autoplanner.context(later, stage, run_dir / "state.json")
                packet = json.loads(next(line for line in prompt.splitlines() if line.startswith('{"task":')))
                self.assertEqual(given, "project_notes" in packet)
                self.assertEqual(given, lifecycle.RULE in prompt)
                if given:
                    self.assertEqual(saved[0]["text"], packet["project_notes"][0]["text"])
        investigator, _ = stuck.prompt(
            {**later, "stuck_investigation": {"identity": "i", "stage": "builder", "status": "S", "reason": "r"}},
            run_dir / "state.json",
        )
        self.assertIn(saved[0]["text"], investigator)
        self.assertIn(lifecycle.INVESTIGATOR_RULE, investigator)

    def test_an_untestable_diagnosis_keeps_nothing_and_says_why(self):
        state, workspace, run_dir = self.investigate(untestable="A judgement between two positions")
        self.assertEqual("not_recorded", state["stuck_investigations"][0]["project_notes"]["status"])
        status.persist(run_dir / "state.json", state)
        self.assertFalse((workspace / ".autocode" / store.FILENAME).exists())

    def test_the_report_field_is_optional_and_bounded(self):
        self.assertNotIn("project_notes", stuck.SCHEMA["required"])
        too_many = report(project_notes=[{"kind": "other", "text": str(i)} for i in range(4)])
        with self.assertRaisesRegex(ValueError, "too many items"):
            util.validate_schema(too_many, stuck.SCHEMA)
        util.validate_schema(report(project_notes=[{"kind": "flaky_test", "text": "x"}]), stuck.SCHEMA)
        util.validate_schema(report(), stuck.SCHEMA)


FLAKY = "test_clock.TestClock.test_tick"


def proof_file(run_dir, number, checks):
    """A verification.json as autocode_regression writes it, with only the check receipts the writer reads."""
    path = Path(run_dir) / "regression" / f"proof-{number:02d}-{number:032x}" / "verification.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"checks": checks}))
    return str(path)


def results(passed=(), failed=(), **receipt):
    return {
        "exit_code": 1 if failed else 0,
        "timed_out": False,
        "results": {"complete": True, "passed": list(passed), "failed": list(failed)},
        **receipt,
    }


class CompletionTests(ProjectDirectory):
    """The completion path, the second writer: tests a completed run's proofs saw fail and then pass."""

    def setUp(self):
        super().setUp()
        self.run_dir = self.project / ".autocode" / "runs" / "20261111-chat-abc123"
        self.run_dir.mkdir(parents=True)

    def state(self, *proofs):
        shutil.rmtree(self.run_dir / "regression", ignore_errors=True)
        state = {"workspace": str(self.project), "status": "TASK_COMPLETE", "regression_proofs": []}
        lifecycle.install(state, {})
        for verdict, revision, context, checks in proofs:
            path = proof_file(self.run_dir, len(state["regression_proofs"]) + 1, checks)
            state["regression_proofs"].append(
                {"verdict": verdict, "source_revision": revision, "path": path, "proof_context": context}
            )
            state["regression_proof"] = {"verdict": verdict, "source_revision": revision, "path": path}
        return state

    def test_only_a_failure_then_a_pass_of_the_same_source_and_context_is_a_flake(self):
        failed = {"suite_on_candidate": results(passed=["a"], failed=[FLAKY, "b"])}
        passed = {"suite_on_candidate": results(passed=["a", FLAKY]), "regression_on_base": results(passed=["b"])}
        self.assertEqual(
            [{"test": FLAKY, "failed_in": "proof 1", "passed_in": "proof 2", "source_revision": "r1"}],
            completion.flaky(self.state(("FAIL", "r1", "c", failed), ("PASS", "r1", "c", passed)), self.run_dir),
        )
        for name, proofs in (
            ("another source", (("FAIL", "r0", "c", failed), ("PASS", "r1", "c", passed))),
            ("another context", (("FAIL", "r1", "old", failed), ("PASS", "r1", "c", passed))),
            ("a pass before the failure", (("PASS", "r1", "c", passed), ("FAIL", "r1", "c", failed))),
            ("an unverified proof", (("FAIL", "r1", "c", failed), ("UNVERIFIED", "r1", "c", passed))),
            (
                "an incomplete run",
                (
                    ("FAIL", "r1", "c", {"suite_on_candidate": results(failed=[FLAKY], timed_out=True)}),
                    ("PASS", "r1", "c", passed),
                ),
            ),
            (
                "the original code",
                (("FAIL", "r1", "c", {"suite_on_base": results(failed=[FLAKY])}), ("PASS", "r1", "c", passed)),
            ),
        ):
            with self.subTest(name):
                self.assertEqual([], completion.flaky(self.state(*proofs), self.run_dir))

    def test_a_pair_before_the_delivered_source_still_counts_and_only_runner_proofs_are_read(self):
        failed = {"stability-candidate-2": results(failed=[FLAKY])}
        passed = {"regression_on_candidate": results(passed=[FLAKY])}
        state = self.state(("FAIL", "r1", "c", failed), ("PASS", "r1", "c", passed), ("PASS", "r2", "c", passed))
        self.assertEqual(["proof 1"], [row["failed_in"] for row in completion.flaky(state, self.run_dir)])
        outside = Path(self.project) / "verification.json"
        outside.write_text(json.dumps({"checks": failed}))
        state["regression_proofs"][0]["path"] = str(outside)
        state["regression_proofs"].insert(1, {"kind": "changed_code_rework", "verdict": "FAIL", "path": str(outside)})
        self.assertEqual([], completion.flaky(state, self.run_dir))

    def test_a_completed_run_keeps_its_flaky_tests_once_with_their_receipt(self):
        failed = {"suite_on_candidate": results(failed=[FLAKY, "tests/" + "x" * 500])}
        passed = {"suite_on_candidate": results(passed=[FLAKY, "tests/" + "x" * 500])}
        state = self.state(("FAIL", "r1", "c", failed), ("PASS", "r1", "c", passed))
        state["status"] = "RUNNING"
        self.assertFalse(lifecycle.publish(self.run_dir / "state.json", state))  # not completed yet
        state["status"] = "TASK_COMPLETE"
        self.assertFalse(lifecycle.publish(self.run_dir / "other.json", state))
        self.assertTrue(lifecycle.publish(self.run_dir / "state.json", state))
        [record] = state["project_notes"]["completions"]
        self.assertEqual(
            ("written", 0, state["regression_proof"]["path"]), (record["status"], record["omitted"], record["proof"])
        )
        [note] = self.saved()
        self.assertEqual("flaky_test", note["kind"])
        self.assertEqual(
            f"{FLAKY} is flaky: it failed and then passed on unchanged code, so one failure of it alone does not show "
            "a regression.",
            note["text"],
        )
        self.assertEqual(
            {
                "run": "20261111-chat-abc123",
                "stage": "regression_proof",
                "investigation": None,
                "observation": "The runner's proof 1 failed this test on the candidate and its proof 2 passed it, "
                "on the same source revision and proof context.",
                "source_revision": "r1",
            },
            {key: value for key, value in note["receipt"].items() if key != "recorded_at"},
        )
        self.assertEqual([note], lifecycle.learned(state))
        self.assertEqual([note], lifecycle.view(state)["learned"])
        self.assertFalse(lifecycle.publish(self.run_dir / "state.json", state))  # judged once per proof

    def test_no_flake_or_an_unwritable_project_is_recorded_and_the_result_stands(self):
        passed = {"suite_on_candidate": results(passed=[FLAKY])}
        state = self.state(("PASS", "r1", "c", passed))
        self.assertTrue(lifecycle.publish(self.run_dir / "state.json", state))
        self.assertEqual("not_recorded", state["project_notes"]["completions"][0]["status"])
        self.assertFalse((self.project / ".autocode" / store.FILENAME).exists())

        state = self.state(
            ("FAIL", "r1", "c", {"suite_on_candidate": results(failed=[FLAKY])}), ("PASS", "r1", "c", passed)
        )
        (self.project / ".autocode" / store.FILENAME).write_text("not json")
        self.assertTrue(lifecycle.publish(self.run_dir / "state.json", state))
        record = state["project_notes"]["completions"][0]
        self.assertEqual("unavailable", record["status"])
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual([], lifecycle.learned(state))
        self.assertEqual("not json", (self.project / ".autocode" / store.FILENAME).read_text())

        for status_name, extra in (("PAUSED_COMPLETION_GATE", {}), ("TASK_COMPLETE", {"parent_run": "p"})):
            with self.subTest(status_name, **extra):
                state = self.state(("PASS", "r1", "c", passed))
                state.update(status=status_name, **extra)
                self.assertFalse(lifecycle.publish(self.run_dir / "state.json", state))

    def test_a_saved_run_without_project_notes_writes_nothing(self):
        state = self.state(("FAIL", "r1", "c", {"suite_on_candidate": results(failed=[FLAKY])}))
        del state["project_notes"]
        self.assertFalse(lifecycle.publish(self.run_dir / "state.json", state))


class CompletionProofTests(unittest.TestCase):
    """Two real regression proofs of the same fixed code: a test the project had fails once, then passes."""

    def test_the_runners_own_proofs_show_the_flake_and_the_checkpoint_keeps_it(self):
        mark = Path(tempfile.mkdtemp(prefix="clock-")) / "flaked"
        self.addCleanup(lambda: mark.unlink(missing_ok=True) or mark.parent.rmdir())
        clock = (
            "import os\nimport unittest\n\nfrom greet import main\n\n\n"
            "class TestClock(unittest.TestCase):\n"
            "    def test_tick(self):\n"
            f"        if main(['   ']) == 2 and not os.path.exists({str(mark)!r}):\n"
            f"            open({str(mark)!r}, 'w').close()\n"
            "            self.fail('timing flake')\n"
        )
        project = Project({**scenarios.BUGFIX_SEED, "test_clock.py": clock})
        self.addCleanup(project.close)
        project.write({name: references.BUGFIX_REFERENCE[name] for name in ("greet.py", "test_greet.py")})
        run_dir = project.root / ".autocode" / "runs" / "20261111-flake-run"
        run_dir.mkdir(parents=True)
        case = {
            "id": "T1",
            "criterion": "Given a blank name; when greet.py runs; then it prints usage and exits 2",
            "verification_method": "test: test_blank_name_rejected",
        }
        state = {
            "base_commit": project.base,
            "workspace": str(project.root),
            "settings": {},
            "iteration": 1,
            "stages": [],
            "history": [],
            "goal_contract": {
                "body": {"task_kind": "bugfix", "acceptance_criteria": [case], "milestones": [{"id": "M1"}]}
            },
        }
        lifecycle.install(state, lifecycle.load(project.root))
        first = regression.prove(state, project.root, run_dir)
        self.assertEqual("FAIL", first["verdict"], first)
        self.assertTrue(any(FLAKY in reason for reason in first["failures"]), first["failures"])
        second = regression.prove(state, project.root, run_dir)
        self.assertEqual("PASS", second["verdict"], second)
        self.assertEqual(first["source_revision"], second["source_revision"])
        state["status"] = "TASK_COMPLETE"
        status.persist(run_dir / "state.json", state)
        [note] = json.loads((project.root / ".autocode" / store.FILENAME).read_text())["notes"]
        self.assertEqual(
            ("flaky_test", "20261111-flake-run", "regression_proof"),
            (note["kind"], note["receipt"]["run"], note["receipt"]["stage"]),
        )
        self.assertTrue(note["text"].startswith(FLAKY + " is flaky"))
        saved = json.loads((run_dir / "state.json").read_text())
        self.assertEqual([note], run_view.view(saved)["project_notes"]["learned"])
        later = {"workspace": str(project.root)}
        lifecycle.install(later, lifecycle.load(project.root))
        self.assertEqual([note["text"]], [row["text"] for row in lifecycle.for_stage(later, "draft_plan")])


class CommandTests(ProjectDirectory):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = notes_cli.cli([*argv, "--workspace", str(self.project)])
        return code, out.getvalue(), err.getvalue()

    def test_a_person_lists_retracts_and_corrects_notes_through_the_named_actions(self):
        wrong, other = note(text="Run make test"), note(text="tests/test_io.py flakes", kind="flaky_test")
        store.add(self.project, [wrong, other])
        code, out, _ = self.run_cli("list", "--json")
        listed = json.loads(out)
        self.assertEqual((0, "loaded", store.PATH), (code, listed["status"], listed["path"]))
        actions = {row["id"]: row["actions"] for row in listed["notes"]}
        self.assertEqual(
            ["notes", "retract", other["id"], "--workspace", str(self.project), "--json"],
            actions[other["id"]]["retract"],
        )
        self.assertEqual(0, self.take(actions[other["id"]]["retract"]))
        self.assertEqual(0, self.take([*actions[wrong["id"]]["correct"], "Run make test-unit"]))
        code, out, _ = self.run_cli("list", "--json")
        self.assertEqual(["Run make test-unit"], [row["text"] for row in json.loads(out)["notes"]])
        code, out, _ = self.run_cli("list", "--all", "--json")
        self.assertEqual({}, next(row for row in json.loads(out)["notes"] if row["id"] == other["id"])["actions"])
        code, out, _ = self.run_cli("list")
        self.assertIn("Run make test-unit", out)
        self.assertIn("a person's correction", out)

    def take(self, argv):
        """Run a named action as a client does: the argument array after `autocode`."""
        self.assertEqual("notes", argv[0])
        with contextlib.redirect_stdout(io.StringIO()):
            return notes_cli.cli(argv[1:])

    def test_refusals_say_what_is_missing(self):
        for argv, message in (
            (("retract", "--json"), "needs the note's ID"),
            (("correct", "note-0000000000000000", "--json"), "needs the corrected text"),
            (("retract", "note-0000000000000000", "--json"), "No project note note-0000000000000000"),
        ):
            with self.subTest(argv=argv):
                code, out, _ = self.run_cli(*argv)
                self.assertEqual(1, code)
                self.assertIn(message, json.loads(out)["error"])

    def test_the_unattended_wrapper_leaves_notes_to_the_operator(self):
        self.assertIn("operator-only", unattended.refused(["notes", "retract", "note-0000000000000000"]))


if __name__ == "__main__":
    unittest.main()
