"""The bug-fix workflow's Investigator: diagnose before fixing and retain questions."""

import copy
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_bug_job as bug_job
import autocode_jobs as jobs
import autocode_run_view as run_view
import autocode_workflows as workflows
import autopilot
from autocode_taskrun import TaskRun, TaskRunError
from units import autoresolver

from .test_job_failure_recovery import PROVIDER, JobHarness


def state_for(workspace="/nowhere", task="Occasionally we renew the same domain twice after a timeout. Fix it."):
    return {
        "version": 3,
        "task": task,
        "workspace": workspace,
        "status": "RUNNING",
        "stages": [],
        "workflow": {"kind": "bugfix", "then": "requirements_gather"},
        "settings": {
            "joint_planning": True,
            "roles": {
                "requirements": {"model": "r"},
                "glm": {"model": "g"},
                "plan_reviewer": {"model": "p"},
                "astra": {"model": "a", "engine": "codex"},
                "terra": {"model": "t"},
                "sol": {"model": "s"},
            },
        },
    }


CASE = {
    "id": "T1",
    "given": "the registry times out once after applying a renew",
    "when": "renew('example.com') runs",
    "then": "the registry records exactly 1 renew mutation",
}


def diagnosis(outcome="reproduced", **overrides):
    value = {
        "outcome": outcome,
        "note_path": "docs/bugs/duplicate-renew.json",
        "observed": "Renewed twice after a timeout",
        "reproduction": "fail_next('timeout-after'); renew() -> 2 mutations",
        "root_cause": "retries after an uncertain timeout with a fresh cl_trid",
        "affected_paths": ["epp/client.py"],
        "test_paths": ["tests/test_client.py"],
        "invariant": "one logical renew, at most one mutation",
        "test_cases": [CASE],
        "probe": "",
        "untestable": "The fixture has no registry to replay against",
        "conclusion": "Reconcile before resending.",
        "fix_size": "small",
        "fix_plan": ["keep one cl_trid", "poll before resending"],
        "questions": [],
        "tests_run": ["python3 -m unittest"],
        "plan_approval_requested": False,
    }
    if outcome == "not_reproduced":
        value.update(
            root_cause="",
            affected_paths=[],
            test_paths=[],
            invariant="",
            fix_size="none",
            fix_plan=[],
            test_cases=[],
            probe="",
            untestable="",
            note_path="docs/bugs/none-cells.json",
            conclusion="export() already writes None as empty.",
            questions=["Which version is the reporter running?"],
        )
    value.update(overrides)
    return value


class InvestigationRetryWorkflowTests(JobHarness):
    def test_exact_retry_provider_receives_prior_attempt_leads_at_real_admission(self):
        capture = self.workspace / ".autocode/provider-packets.jsonl"
        self.env["INVESTIGATION_PACKETS"] = str(capture)
        provider = PROVIDER.replace(
            "sys.stdin.read()",
            "text=sys.stdin.read()\n"
            "data=json.loads(text.split('CURRENT HANDOFF DATA\\n',1)[1])\n"
            "with Path(os.environ['INVESTIGATION_PACKETS']).open('a') as f:f.write(json.dumps(data)+'\\n')",
        )
        (self.workspace / ".autocode/bin/codex").write_text(provider)
        run = self.start(workflow="bugfix")
        held = run.status()
        self.assertEqual("investigate_bug", held["needs"]["stage"])
        first = json.loads(capture.read_text().splitlines()[-1])
        self.assertNotIn("prior_failed_attempt", first)
        after = run.retry_job(held["needs"]["job_retry_token"])
        retried = json.loads(capture.read_text().splitlines()[-1])
        prior = retried["prior_failed_attempt"]
        self.assertEqual(held["needs"]["attempt_id"], prior["attempt_id"])
        self.assertEqual(held["needs"]["source_identity"], prior["source_identity"])
        self.assertEqual("unverified_leads_only", prior["evidence_status"])
        self.assertTrue(Path(prior["previous_prompt"]).is_file())
        self.assertTrue(Path(prior["previous_events"]).is_file())
        self.assertNotEqual(first["investigation_workspace"], retried["investigation_workspace"])
        self.assertEqual(held["needs"]["source_identity"], after["needs"]["source_identity"])
        self.assertEqual(2, self.count())


class RoutingTests(unittest.TestCase):
    def test_a_recognized_bug_goes_to_the_investigator_and_remembers_the_build_entry(self):
        state = state_for()
        workflows.begin(state, "requirements_gather")
        workflows.apply(state, {"workflow": "bugfix", "reason": "", "signals": []}, {})
        self.assertEqual(bug_job.STAGE, state["next_stage"])
        self.assertEqual("requirements_gather", state["workflow"]["then"])
        self.assertEqual("autoresolver", autopilot.unit_for(bug_job.STAGE))
        self.assertIn(bug_job.STAGE, jobs.STAGES)


class InvestigationPromptBoundaryTests(unittest.TestCase):
    def retry_state(self):
        state = state_for("/repo")
        archive = "/repo/.autocode/runs/run/iterations/001/archived-bug-investigation-01-abc"
        state["job_failure"] = {
            "stage": "investigate_bug",
            "attempt_id": "001/bug-investigation-01",
            "job_retry_token": "jr:exact",
            "source_identity": "original-source",
            "archive": archive,
        }
        state["recovery_context"] = {
            "kind": "job_failure",
            "stage": "investigate_bug",
            "attempt_id": "001/bug-investigation-01",
            "events": archive + "/bug-investigation-01.jsonl",
            "reason": "Stage timed out after its focused probe",
        }
        state["job_retry_authorization"] = {"stage": "investigate_bug", "token": "jr:exact", "consumed": False}
        return state

    def test_exact_retry_handoff_preserves_unverified_leads_without_accepting_them(self):
        state = self.retry_state()
        before = copy.deepcopy(state)
        text, _ = bug_job.prompt(state, scratch_workspace="/repo/.autocode/investigation/new")
        data = json.loads(text.split("CURRENT HANDOFF DATA\n", 1)[1])
        prior = data["prior_failed_attempt"]
        self.assertEqual("001/bug-investigation-01", prior["attempt_id"])
        self.assertEqual("unverified_leads_only", prior["evidence_status"])
        self.assertEqual(state["recovery_context"]["events"], prior["previous_events"])
        self.assertEqual(prior["previous_events"].removesuffix(".jsonl") + ".prompt.md", prior["previous_prompt"])
        self.assertEqual("/repo/.autocode/investigation/new", data["investigation_workspace"])
        self.assertIsNone(data["prior_investigation"], "a timeout is not an accepted diagnosis")
        self.assertIn("rerun the relevant focused probe", text)
        self.assertEqual(before, state)

    def test_retry_handoff_omits_unrelated_stale_or_unauthorized_attempts(self):
        for section, key, value in (
            ("job_failure", "stage", "review_change"),
            ("recovery_context", "kind", "some_other_recovery"),
            ("recovery_context", "stage", "sol"),
            ("recovery_context", "attempt_id", "001/bug-investigation-02"),
            ("recovery_context", "events", "/other/bug-investigation-01.jsonl"),
            ("recovery_context", "events", self.retry_state()["job_failure"]["archive"] + "/other.jsonl"),
            ("job_retry_authorization", "token", "jr:stale"),
            ("job_retry_authorization", "consumed", True),
            ("job_failure", "source_identity", ""),
        ):
            with self.subTest(section=section, key=key, value=value):
                state = self.retry_state()
                state[section][key] = value
                self.assertNotIn("prior_failed_attempt", bug_job.packet(state))
        self.assertNotIn("prior_failed_attempt", bug_job.packet(state_for()))

    def test_investigation_receives_saved_window_and_prioritizes_reproduction(self):
        state = state_for("/repo")
        state["settings"]["limits"] = {"stage_timeout_seconds": 600}
        text, _ = bug_job.prompt(state, scratch_workspace="/repo/.autocode/investigation/bug-example")
        data = json.loads(text.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(600, data["configured_stage_timeout_seconds"])
        self.assertIn("Attempt the smallest behavior reproduction before broad source exploration", text)
        self.assertIn("Once the failing behavior and causal path are established", text)
        self.assertIn("never weaken the reproduction evidence", text)

    def test_opencode_investigation_uses_workspace_scratch_and_foreground_checks(self):
        text, _ = bug_job.prompt(state_for("/repo"), engine="opencode")
        self.assertIn(".autocode/investigation/", text)
        self.assertIn("Do not create scratch copies outside the workspace", text)
        self.assertIn("foreground", text)
        self.assertNotIn("scratch copy OUTSIDE the workspace", text)

    def test_scratch_copy_excludes_runner_state_and_preserves_application_source(self):
        text, _ = bug_job.prompt(state_for("/repo"), engine="opencode")
        self.assertIn("Exclude .autocode/ and .git/", text)
        self.assertIn("Do not edit application source in the original workspace", text)
        self.assertIn("never modify existing runner state or evidence", text)

    def test_prepared_scratch_handoff_requires_using_complete_copy_without_rebuilding(self):
        scratch = "/repo/.autocode/investigation/bug-example"
        text, _ = bug_job.prompt(state_for("/repo"), engine="opencode", scratch_workspace=scratch)
        data = json.loads(text.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(scratch, data["investigation_workspace"])
        self.assertIn("Use the runner-prepared investigation_workspace", text)
        self.assertIn("Do not rebuild the copy", text)
        self.assertNotIn("Try to reproduce it in a fresh scratch copy", text)
        self.assertIn("original workspace", text)


class PrepareTests(unittest.TestCase):
    def test_investigation_prefers_the_declared_external_test_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            workspace = root / "project"
            workspace.mkdir()
            environment = root / "external environment"
            subprocess.run(
                [sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True, capture_output=True
            )
            python = environment / "bin/python"
            state = state_for(str(workspace))
            command = shlex.join(["env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", str(python), "-m", "pytest", "tests"])
            state["settings"]["regression"] = {"test_command": command}
            request = autoresolver.prepare(state, bug_job.STAGE, "/run/state.json", None)
            data = json.loads(request.prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
            self.assertEqual(str(python), data["investigation_python"])
            self.assertNotIn("test_command", data)
            self.assertNotIn(command, request.prompt)
            self.assertEqual(command, state["settings"]["regression"]["test_command"])
            self.assertIn("not post-fix validation", request.prompt)

    def test_investigation_uses_virtualenv_dependencies_with_the_scratch_source(self):
        for name in (".venv", "venv"):
            with self.subTest(environment=name), tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace).resolve()
                environment = root / name
                subprocess.run(
                    [sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True, capture_output=True
                )
                python = environment / "bin/python"
                site = Path(
                    subprocess.check_output(
                        [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True
                    ).strip()
                )
                (site / "offline_dependency.py").write_text("value = 42\n")
                (root / "app.py").write_text("value = 1\n")
                request = autoresolver.prepare(state_for(str(root)), bug_job.STAGE, "/run/state.json", None)
                data = json.loads(request.prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
                scratch = Path(data["investigation_workspace"])
                self.assertEqual(str(python), data["investigation_python"])
                self.assertFalse((scratch / name).exists())
                (scratch / "app.py").write_text("value = 2\n")
                result = subprocess.run(
                    [
                        data["investigation_python"],
                        "-B",
                        "-c",
                        "import app, offline_dependency; assert app.value == 2; assert offline_dependency.value == 42",
                    ],
                    cwd=scratch,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("value = 1\n", (root / "app.py").read_text())

    def test_investigation_request_contains_complete_scratch_before_model_launch(self):
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            (root / "rule").mkdir()
            (root / "rule/rule.go").write_text("package rule")
            request = autoresolver.prepare(state_for(workspace), bug_job.STAGE, "/run/state.json", None)
            data = json.loads(request.prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
            scratch = Path(data["investigation_workspace"])
            self.assertEqual("package rule", (scratch / "rule/rule.go").read_text())
            self.assertEqual(root.resolve() / ".autocode/investigation", scratch.parent)

    def test_investigator_gets_its_own_route_and_a_scratch_copy_but_no_plan(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace)
            request = autoresolver.prepare(state, bug_job.STAGE, "/run/state.json", None)
        self.assertEqual(("astra", "investigator", True), (request.role, request.route_role, request.allow_write))
        self.assertEqual(state["settings"]["roles"]["plan_reviewer"], state["settings"]["roles"]["investigator"])
        self.assertEqual(bug_job.SCHEMA, request.schema)
        self.assertIn("renew the same domain twice", request.prompt)
        self.assertEqual("INVESTIGATING", state["phase"])

    def test_without_a_plan_reviewer_the_investigator_falls_back_to_the_resolver_route(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace)
            del state["settings"]["roles"]["plan_reviewer"]
            autoresolver.prepare(state, bug_job.STAGE, "/run/state.json", None)
        self.assertEqual(state["settings"]["roles"]["astra"], state["settings"]["roles"]["investigator"])

    def test_a_saved_investigator_route_is_kept(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace)
            state["settings"]["roles"]["investigator"] = {"model": "saved"}
            autoresolver.prepare(state, bug_job.STAGE, "/run/state.json", None)
        self.assertEqual({"model": "saved"}, state["settings"]["roles"]["investigator"])


class InvestigationQuestionValidationTests(unittest.TestCase):
    def test_duplicate_questions_are_rejected_even_with_surrounding_whitespace(self):
        question = "Which version failed?"
        for duplicate in (question, "  " + question + "  "):
            with self.subTest(duplicate=duplicate):
                report = diagnosis("not_reproduced", questions=[question, duplicate])
                with self.assertRaisesRegex(ValueError, "questions must be distinct"):
                    bug_job.check(report, [])

    def test_distinct_questions_are_accepted(self):
        report = diagnosis("not_reproduced", questions=["Which version failed?", "What input failed?"])
        bug_job.check(report, [])


class RunnerDiagnosisArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.output = self.workspace / ".autocode" / "bug-investigation-01.json"
        self.output.parent.mkdir()
        self.report = diagnosis()
        self.output.write_text(json.dumps(self.report))
        self.state = state_for(str(self.workspace))
        bug_job.apply(self.state, self.report, {"changed_files": [], "output": str(self.output)}, self.workspace)
        self.note = self.workspace / self.report["note_path"]

    def test_only_the_exact_runner_note_is_identified_without_expanding_task_scope(self):
        from autocode_util import file_hash

        self.state["current_task"] = {"affected_paths": self.report["affected_paths"] + self.report["test_paths"]}
        other = self.note.with_name("unrelated.json")
        other.write_bytes(self.note.read_bytes())
        artifact = bug_job.diagnosis_artifact(self.state, self.workspace)
        self.assertEqual(self.report["note_path"], artifact["path"])
        self.assertEqual(file_hash(self.note), artifact["sha256"])
        self.assertEqual("runner_written_diagnosis", artifact["kind"])
        self.assertNotIn(artifact["path"], self.state["current_task"]["affected_paths"])
        self.assertNotEqual(str(other.relative_to(self.workspace)), artifact["path"])

    def test_modified_or_deleted_note_is_not_classified_as_runner_evidence(self):
        rewritten = json.loads(self.note.read_text())
        rewritten["conclusion"] = "quietly replaced conclusion"
        self.note.write_text(json.dumps(rewritten, indent=2) + "\n")
        self.assertIsNone(bug_job.diagnosis_artifact(self.state, self.workspace))
        self.note.write_text(self.note.read_text()[:-2] + "garbage")
        self.assertIsNone(bug_job.diagnosis_artifact(self.state, self.workspace))
        self.note.unlink()
        self.assertIsNone(bug_job.diagnosis_artifact(self.state, self.workspace))

    def test_a_note_with_reordered_keys_is_still_the_runner_note(self):
        # Live bugfix-trivial run: the raw report's object order differs from the
        # normalized note (and a reloaded state.json sorts its keys), so the note is
        # matched by content, never by byte order.
        from autocode_util import file_hash

        original = json.loads(self.note.read_text())
        reordered = {key: original[key] for key in sorted(original, reverse=True)}
        reordered["test_cases"] = [{k: case[k] for k in sorted(case, reverse=True)} for case in original["test_cases"]]
        self.note.write_text(json.dumps(reordered, indent=2) + "\n")
        artifact = bug_job.diagnosis_artifact(self.state, self.workspace)
        self.assertEqual(self.report["note_path"], artifact["path"])
        self.assertEqual(file_hash(self.note), artifact["sha256"])

    def test_report_tampering_or_missing_provenance_cannot_exempt_a_note(self):
        self.output.write_text(json.dumps({**self.report, "root_cause": "replacement diagnosis"}))
        self.assertIsNone(bug_job.diagnosis_artifact(self.state, self.workspace))
        self.output.write_text(json.dumps(self.report))
        self.state["investigation"].pop("output_hash")
        self.assertIsNone(bug_job.diagnosis_artifact(self.state, self.workspace))

    def test_executable_note_is_not_classified_as_unchanged_runner_evidence(self):
        self.note.chmod(self.note.stat().st_mode | 0o100)
        self.assertIsNone(bug_job.diagnosis_artifact(self.state, self.workspace))

    def test_symlinked_note_or_parent_is_not_classified_as_runner_evidence(self):
        duplicate = self.workspace / "copy.json"
        duplicate.write_bytes(self.note.read_bytes())
        self.note.unlink()
        self.note.symlink_to(duplicate)
        self.assertIsNone(bug_job.diagnosis_artifact(self.state, self.workspace))
        self.note.unlink()
        self.note.parent.rmdir()
        alternative = self.workspace / "other-notes"
        alternative.mkdir()
        (alternative / self.note.name).write_bytes(duplicate.read_bytes())
        self.note.parent.symlink_to(alternative, target_is_directory=True)
        self.assertIsNone(bug_job.diagnosis_artifact(self.state, self.workspace))

    def test_review_handoff_distinguishes_note_but_preserves_all_actual_changes(self):
        import autocode_stage_context as stage_context

        paths = ["epp/client.py", self.report["note_path"], "docs/bugs/unrelated.json"]
        self.state.update(
            changed_files=paths,
            current_task={"affected_paths": ["epp/client.py"]},
            implementation={"changed_files": paths},
        )
        for stage in ("sol", "astra_checkpoint", "astra_review"):
            with (
                self.subTest(stage=stage),
                mock.patch.object(stage_context.support, "snapshot", return_value={"revision": "r1", "head": "h1"}),
            ):
                prompt, _ = stage_context.context_packet(self.state, stage, self.output.parent / "state.json")
                data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
                self.assertEqual([self.report["note_path"]], [r["path"] for r in data["runner_artifacts"]])
                self.assertEqual(paths, data["implementation"]["changed_files"])
                if stage != "astra_review":
                    self.assertEqual(paths, data["actual_changes"])
                self.assertIn("not an out-of-scope Builder edit", prompt)
                self.assertIn("grants no write permission", prompt)


class ApplyTests(unittest.TestCase):
    def apply(self, value, changed=()):
        workspace = tempfile.mkdtemp()
        state = state_for(workspace)
        bug_job.apply(
            state, value, {"changed_files": list(changed), "output": "/run/investigate_bug-01.json"}, workspace
        )
        return state, Path(workspace)

    def test_a_large_reproduced_bug_writes_the_diagnosis_and_goes_to_the_planner(self):
        state, workspace = self.apply(diagnosis(fix_size="large"))
        note = json.loads((workspace / "docs/bugs/duplicate-renew.json").read_text())
        self.assertEqual((True, []), (note["reproduced"], note["changed"]))
        for field in ("observed", "reproduction", "root_cause", "affected_paths", "invariant"):
            self.assertTrue(note[field], field)
        self.assertEqual(("RUNNING", "astra_discovery"), (state["status"], state["next_stage"]))
        self.assertIsNone(jobs.ended_in(state))
        self.assertEqual("docs/bugs/duplicate-renew.json", bug_job.large_correction(state)["note_path"])

    def test_a_report_that_does_not_reproduce_waits_for_reporter_questions(self):
        state, workspace = self.apply(diagnosis("not_reproduced"))
        note = json.loads((workspace / "docs/bugs/none-cells.json").read_text())
        self.assertEqual((False, []), (note["reproduced"], note["changed"]))
        self.assertEqual(["Which version is the reporter running?"], note["questions"])
        view = run_view.view(state)
        self.assertFalse(view["done"])
        self.assertEqual(
            ("WAITING_FOR_USER", "INVESTIGATING", bug_job.STAGE), (state["status"], state["phase"], view["next_stage"])
        )
        self.assertEqual("bugfix", view["workflow"])
        self.assertIsNone(jobs.ended_in(state))

    def test_a_report_that_does_not_reproduce_and_has_no_questions_ends_the_run(self):
        state, _ = self.apply(diagnosis("not_reproduced", questions=[]))
        view = run_view.view(state)
        self.assertTrue(view["done"])
        self.assertIsNone(view["needs"])
        self.assertIs(bug_job, jobs.ended_in(state))
        rendered = jobs.render(state, lambda _: "build completion")
        self.assertIn("NOT REPRODUCED", rendered)
        self.assertNotIn("Question for the reporter:", rendered)

    def test_an_authenticated_reporter_answer_is_not_requested_again(self):
        import autocode_bug_questions as questions

        state, workspace = self.apply(diagnosis("not_reproduced"))
        question = questions.questions(state)[0]
        answer = {"actor": "user_cli", "question": question, "text": "Version 1.2"}
        state.update(answers={question["id"]: answer}, user_events=[answer])
        retained = (workspace / "docs/bugs/none-cells.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "already.*answer|answer.*already|repeat"):
            bug_job.apply(
                state,
                diagnosis("not_reproduced"),
                {"changed_files": [], "output": "/run/investigate_bug-02.json"},
                str(workspace),
            )
        self.assertEqual(retained, (workspace / "docs/bugs/none-cells.json").read_bytes())
        self.assertFalse(run_view.view(state)["done"])
        self.assertEqual(bug_job.STAGE, state["next_stage"])

    def test_an_investigation_that_changed_the_repository_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not change the repository.*epp/client.py"):
            self.apply(diagnosis(), changed=["epp/client.py", "docs/bugs/scratch.json"])

    def test_the_note_must_live_under_docs_bugs(self):
        for path in ("notes/x.json", "docs/bugs/x.md", "docs/bugs/../../etc.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "note_path"):
                self.apply(diagnosis(note_path=path))

    def test_a_reproduced_bug_needs_a_cause_paths_and_an_invariant(self):
        with self.assertRaisesRegex(ValueError, "root cause"):
            self.apply(diagnosis(invariant=""))
        with self.assertRaisesRegex(ValueError, "root cause"):
            self.apply(diagnosis(fix_size="none"))

    def test_a_report_that_did_not_reproduce_may_not_propose_a_fix(self):
        with self.assertRaisesRegex(ValueError, "must not propose a fix"):
            self.apply(diagnosis("not_reproduced", fix_plan=["strip the text None defensively"]))

    def test_an_investigation_must_say_what_it_tried(self):
        with self.assertRaisesRegex(ValueError, "what it ran"):
            self.apply(diagnosis(reproduction="  "))

    def test_a_reproduced_bug_needs_its_regression_tests_in_plain_english(self):
        with self.assertRaisesRegex(ValueError, "needs test_cases"):
            self.apply(diagnosis(test_cases=[]))
        with self.assertRaisesRegex(ValueError, "needs given, when and then"):
            self.apply(diagnosis(test_cases=[{**CASE, "then": " "}]))
        with self.assertRaisesRegex(ValueError, "unique"):
            self.apply(diagnosis(test_cases=[CASE, {**CASE, "id": "t1"}]))
        with self.assertRaisesRegex(ValueError, "short name"):
            self.apply(diagnosis(test_cases=[{**CASE, "id": "T 1"}]))
        with self.assertRaisesRegex(ValueError, "must not propose a fix"):
            self.apply(diagnosis("not_reproduced", test_cases=[CASE]))

    def test_the_note_and_the_planner_get_the_english_tests(self):
        state, workspace = self.apply(diagnosis(fix_size="large"))
        self.assertEqual([CASE], json.loads((workspace / "docs/bugs/duplicate-renew.json").read_text())["test_cases"])
        self.assertEqual([CASE], bug_job.large_correction(state)["test_cases"])
        self.assertEqual([CASE], bug_job.test_cases(state))

    def test_paths_must_stay_inside_the_repository(self):
        for path in ("/etc/passwd", "../outside.py", ".git/config"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "inside the repository"):
                self.apply(diagnosis(affected_paths=[path]))

    def test_a_package_run_refuses_a_diagnosis_that_names_files_outside_its_package(self):
        # #711: the handoff names the package (tests.test_package_investigator_cli); the note stays under docs/bugs/.
        workspace = tempfile.mkdtemp()
        state = state_for(workspace)
        state["settings"]["package"] = "packages/api"
        record = {"changed_files": [], "output": "/run/investigate_bug-01.json"}
        for affected, tests in (
            (["packages/web/client.py"], ["packages/api/tests/"]),
            (["packages/api/client.py"], ["tests/test_client.py"]),
            (["packages/api-v2/client.py"], ["packages/api"]),
        ):
            with self.subTest(affected=affected, tests=tests):
                with self.assertRaisesRegex(ValueError, r"This run works in the package packages/api/ \(--package\)"):
                    bug_job.apply(state, diagnosis(affected_paths=affected, test_paths=tests), record, workspace)
        self.assertFalse((Path(workspace) / "docs/bugs/duplicate-renew.json").exists())
        inside = diagnosis(affected_paths=["./packages/api/client.py"], test_paths=["packages/api/", "packages/api"])
        bug_job.apply(state, inside, record, workspace)
        self.assertTrue((Path(workspace) / "docs/bugs/duplicate-renew.json").is_file())


class SmallCorrectionTests(unittest.TestCase):
    """A small reproduced bug becomes one Builder task, approved by the recorded policy, not the user.
    The short path is off by default (FullPathTests); these tests keep it working for when it returns."""

    def setUp(self):
        patcher = mock.patch.object(bug_job, "SMALL_CORRECTION_ENABLED", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start(self, **overrides):
        workspace = Path(tempfile.mkdtemp())
        (workspace / "epp").mkdir()
        (workspace / "epp" / "client.py").write_text("x = 1\n")
        for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *command], cwd=workspace, check=True
            )
        state = {
            **state_for(str(workspace)),
            "task_id": "task-under-test",
            "iteration": 1,
            "answers": {},
            "user_events": [],
            "history": [],
            "sessions": {},
        }
        autoresolver.apply_job(
            bug_job.STAGE, state, diagnosis(**overrides), {"changed_files": [], "output": "o"}, str(workspace)
        )
        return state

    def test_the_diagnosis_becomes_an_approved_one_task_contract(self):
        import autocode_goals as goals

        state = self.start()
        contract = state["goal_contract"]
        self.assertEqual(bug_job.ORIGIN, contract["origin"])
        self.assertTrue(goals.approved(state))
        self.assertEqual(
            ("workflow_policy", bug_job.SMALL_FIX_POLICY),
            (contract["approval_event"]["actor"], contract["approval_event"]["policy"]),
        )
        criterion, case = contract["body"]["acceptance_criteria"]
        self.assertEqual("one logical renew, at most one mutation", criterion["criterion"])
        self.assertEqual(bug_job.case_text(CASE), case["criterion"])
        task = state["current_task"]
        self.assertEqual(
            ["epp/client.py", "tests/test_client.py", "docs/bugs/duplicate-renew.json"], task["affected_paths"]
        )
        self.assertIn("fails on the original code", " ".join(task["requirements"]))
        self.assertIn(state["next_stage"], ("terra", "orchestrator"))
        self.assertEqual("RUNNING", state["status"])

    def test_each_english_test_becomes_a_criterion_and_a_named_test(self):
        second = {
            "id": "T2",
            "given": "no timeout",
            "when": "renew('example.com') runs",
            "then": "1 mutation",
            "kind": "preserve",
        }
        state = self.start(test_cases=[CASE, second])
        criteria = state["goal_contract"]["body"]["acceptance_criteria"]
        self.assertEqual(["C1", "C2", "C3"], [row["id"] for row in criteria])
        self.assertEqual(bug_job.case_text(CASE), criteria[1]["criterion"])
        self.assertIn("test_t1_", criteria[1]["verification_method"])
        self.assertIn("fails on the original code and passes after the fix", criteria[1]["verification_method"])
        self.assertIn("passes on the original code and after the fix", criteria[2]["verification_method"])
        task = state["current_task"]
        self.assertEqual(["C1", "C2", "C3"], task["acceptance_criteria"])
        self.assertIn("as a test named test_t2_", " ".join(task["requirements"]))
        preserved = next(row for row in task["requirements"] if "as a test named test_t2_" in row)
        self.assertIn("passes on the original code and after the fix", preserved)

    def test_a_large_fix_is_not_auto_approved(self):
        state = self.start(fix_size="large")
        self.assertNotEqual(bug_job.ORIGIN, (state.get("goal_contract") or {}).get("origin"))
        # Planned from the diagnosis: no requirements gathering, but plan review and the user's approval.
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertIsNone(
            bug_job.large_correction({**state, "investigation": {**state["investigation"], "fix_size": "small"}})
        )

    def test_a_small_fix_the_user_asked_to_approve_is_planned_and_put_to_the_user(self):
        state = self.start(fix_size="small", plan_approval_requested=True)
        self.assertNotEqual(bug_job.ORIGIN, (state.get("goal_contract") or {}).get("origin"))
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertFalse(bug_job.small_correction(state))
        self.assertIsNotNone(bug_job.large_correction(state))

    def test_the_schema_requires_the_approval_flag(self):
        self.assertIn("plan_approval_requested", bug_job.SCHEMA["required"])
        self.assertEqual({"type": "boolean"}, bug_job.SCHEMA["properties"]["plan_approval_requested"])

    def test_the_planner_plans_a_large_fix_from_the_diagnosis(self):
        from units import autoplanner

        state = self.start(fix_size="large")
        state["settings"]["roles"]["plan_reviewer"] = {"model": "p"}
        prompt, _ = autoplanner.context(state, "astra_discovery", Path(state["workspace"]) / "state.json")
        self.assertIn(autoplanner.BUG_DIAGNOSIS_RULE, prompt)
        self.assertIn('"bug_diagnosis"', prompt)
        self.assertIn(json.dumps(state["investigation"]["invariant"]), prompt)
        small, _ = autoplanner.context(
            {**state, "investigation": {**state["investigation"], "fix_size": "small"}},
            "astra_discovery",
            Path(state["workspace"]) / "state.json",
        )
        self.assertNotIn(autoplanner.BUG_DIAGNOSIS_RULE, small)

    def test_planning_keeps_restore_and_preserve_cases_provable(self):
        from units import autoplanner

        preserve = {**CASE, "id": "T2", "given": "no timeout", "kind": "preserve"}
        state = self.start(fix_size="large", test_cases=[CASE, preserve])
        state["settings"]["roles"]["plan_reviewer"] = {"model": "p"}
        prompt, _ = autoplanner.context(state, "astra_discovery", Path(state["workspace"]) / "state.json")
        instruction = prompt.split("\nCURRENT HANDOFF DATA\n")[0]
        self.assertIn("restore case (the default kind)", instruction)
        self.assertIn("fails on the original code\nbecause of the bug and passes after the fix", instruction)
        self.assertIn("must pass on the original code and after the fix", instruction)
        self.assertIn("Keep each case's kind", instruction)
        packet = json.loads(prompt.split("\nCURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual([CASE, preserve], packet["bug_diagnosis"]["test_cases"])

    def test_investigation_and_planning_keep_regression_tests_off_the_fixs_seams(self):
        # Issue #299: tests that spied on a variable the fix added could not build on the unfixed code.
        from units import autoplanner

        investigation = " ".join(bug_job.prompt(state_for("/repo"))[0].split())
        self.assertIn("never a hook, variable or helper the fix would add", investigation)
        self.assertIn("never only a log line or message", investigation)
        state = self.start(fix_size="large")
        state["settings"]["roles"]["plan_reviewer"] = {"model": "p"}
        for stage in ("astra_discovery", "astra_finalize"):
            prompt = " ".join(autoplanner.context(state, stage, Path(state["workspace"]) / "state.json")[0].split())
            self.assertIn("do not plan it around a hook, package variable or other seam the fix adds", prompt, stage)
            self.assertIn("a log line or message alone does not prove the behavior", prompt, stage)

    def test_planning_is_told_how_execution_captures_evidence(self):
        from units import autoplanner

        state = self.start(fix_size="large")
        state["settings"]["roles"]["plan_reviewer"] = {"model": "p"}
        state_path = Path(state["workspace"]) / "state.json"
        for stage in ("astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"):
            prompt, _ = autoplanner.context(state, stage, state_path)
            self.assertIn(autoplanner.EVIDENCE_FACTS, prompt, stage)
            self.assertIn('"capture_command"', prompt, stage)
            self.assertIn(" capture", json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])["capture_command"])
        requirements, _ = autoplanner.context(state, "requirements_gather", state_path)
        self.assertNotIn(autoplanner.EVIDENCE_FACTS, requirements)

    def test_the_policy_actor_cannot_approve_an_ordinary_contract(self):
        import autocode_goals as goals

        state = self.start()
        state["goal_contract"]["origin"] = "glm_draft"
        self.assertFalse(goals.approved(state))
        self.assertTrue(workflows.approval_actor_ok("anything", {"actor": "user_cli"}))
        self.assertFalse(workflows.approval_actor_ok("glm_draft", {"actor": "workflow_policy"}))


def approved_small_fix(**overrides):
    """A state whose one-task contract is approved and assigned: the short path is the quickest way there."""
    with mock.patch.object(bug_job, "SMALL_CORRECTION_ENABLED", True):
        return SmallCorrectionTests.start(SmallCorrectionTests(), **overrides)


class InvariantTests(unittest.TestCase):
    """A live bugfix-cent-drift run (2026-09-29) was accepted with refund_line returning 156.45999999999998:
    its test cases compared "to 2 decimal places" and the Validator never checked the invariant itself."""

    def test_the_validator_is_given_the_invariant_to_check_directly(self):
        state = {"investigation": diagnosis(invariant="Every refunded amount is a whole number of cents.")}
        note = bug_job.validator_note(state)
        self.assertIn("Every refunded amount is a whole number of cents.", note)
        self.assertIn("inputs the tests do not use", note)
        self.assertIn("no tolerance", note)
        for other in ({}, {"investigation": diagnosis("not_reproduced")}):
            with self.subTest(state=other):
                self.assertEqual("", bug_job.validator_note(other))

    def test_the_validator_prompt_carries_it(self):
        from units import common

        with mock.patch.object(bug_job, "SMALL_CORRECTION_ENABLED", True):  # an approved one-task contract
            state = SmallCorrectionTests.start(self, fix_size="small")
        request = common.execution_request(
            state,
            "sol",
            Path(state["workspace"]) / "state.json",
            Path(bug_job.__file__).resolve().parent / "autocode-schemas",
        )
        self.assertIn("BUG INVARIANT", request.prompt)
        self.assertIn(state["investigation"]["invariant"], request.prompt)

    def test_a_feature_followup_keeps_old_diagnosis_without_ordering_another_bug_probe(self):
        state = {
            "investigation": diagnosis(output="previous-bug.json"),
            "stages": [{"stage": "investigate_bug", "output": "previous-bug.json"}],
            "turns": [{"stage_index": 1}],
        }
        before = copy.deepcopy(state)

        self.assertEqual("", bug_job.validator_note(state))
        self.assertEqual(before, state, "the completed turn's diagnosis and evidence remain available")

    def test_a_fresh_followup_investigation_still_requires_its_own_invariant_check(self):
        for active in (False, True):
            with self.subTest(active=active):
                state = {
                    "investigation": diagnosis(output="current-bug.json"),
                    "stages": [{"stage": "investigate_bug", "output": "previous-bug.json"}],
                    "turns": [{"stage_index": 1}],
                }
                current = {"stage": "investigate_bug", "output": "current-bug.json"}
                if active:
                    state["active_stage"] = current
                else:
                    state["stages"].append(current)
                before = copy.deepcopy(state)

                note = bug_job.validator_note(state)
                self.assertIn(state["investigation"]["invariant"], note)
                self.assertIn("capture your own check", note)
                self.assertEqual(before, state)

    def test_an_unreproduced_followup_does_not_create_an_invariant_obligation(self):
        state = {
            "investigation": diagnosis("not_reproduced", output="current-bug.json"),
            "stages": [
                {"stage": "investigate_bug", "output": "previous-bug.json"},
                {"stage": "investigate_bug", "output": "current-bug.json"},
            ],
            "turns": [{"stage_index": 1}],
        }
        before = copy.deepcopy(state)

        self.assertEqual("", bug_job.validator_note(state))
        self.assertEqual(before, state)


class DiagnosisNoteTests(unittest.TestCase):
    """A live bugfix-cent-drift run (2026-09-30): the request said to write the root cause to the note, and a
    planned Builder overwrote the note the runner had already written, so the run paused for a person."""

    def builder_prompt(self, state):
        from units import common

        return common.execution_request(
            state,
            "terra",
            Path(state["workspace"]) / "state.json",
            Path(bug_job.__file__).resolve().parent / "autocode-schemas",
        ).prompt

    def test_a_builder_that_does_not_own_the_note_is_told_it_is_written(self):
        state = approved_small_fix()
        self.assertNotIn("DIAGNOSIS NOTE", self.builder_prompt(state))  # the short path's task owns the note
        state["current_task"]["affected_paths"] = ["epp/client.py", "tests/test_client.py"]  # a planned task
        prompt = self.builder_prompt(state)
        self.assertIn("DIAGNOSIS NOTE: docs/bugs/duplicate-renew.json is already written", prompt)
        self.assertEqual("", bug_job.builder_note({**state, "investigation": diagnosis("not_reproduced")}))


class FullPathTests(unittest.TestCase):
    """With the short path off, a small reproduced bug is planned and put to the user like any other."""

    def test_a_small_fix_is_planned_and_put_to_the_user(self):
        self.assertFalse(bug_job.SMALL_CORRECTION_ENABLED)
        state = SmallCorrectionTests.start(SmallCorrectionTests(), fix_size="small")
        self.assertNotEqual(bug_job.ORIGIN, (state.get("goal_contract") or {}).get("origin"))
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertFalse(bug_job.small_correction(state))
        self.assertEqual("docs/bugs/duplicate-renew.json", bug_job.large_correction(state)["note_path"])


if __name__ == "__main__":
    unittest.main()


class CaseMatchTests(unittest.TestCase):
    """The runner links an English case to its test by name, with no model: T1 -> test_t1_..."""

    def test_ids_match_whole_words_in_any_runners_test_id(self):
        cases = [{"id": "T1"}, {"id": "T2"}, {"id": "reported_row"}]
        tests = [
            "tests.test_client.RenewTests.test_t1_one_mutation",
            "tests/test_x.py::test_t12_other",
            "TestT2RenewsOnce",
            "tests/test_x.py::test_reported_row[a-b]",
        ]
        self.assertEqual(
            {
                "T1": ["tests.test_client.RenewTests.test_t1_one_mutation"],
                "T2": ["TestT2RenewsOnce"],
                "reported_row": ["tests/test_x.py::test_reported_row[a-b]"],
            },
            bug_job.match_cases(cases, tests),
        )

    def test_the_proof_fails_a_case_with_no_test_of_its_own(self):
        import autocode_regression as regression

        proof = {
            "verdict": "PASS",
            "failures": [],
            "unverified": [],
            "fail_to_pass": ["tests.test_client.RenewTests.test_t1_one_mutation"],
        }
        second = {"id": "T2", "given": "no timeout", "when": "renew() runs", "then": "1 mutation"}
        regression.check_cases(proof, [CASE, second])
        self.assertEqual("FAIL", proof["verdict"])
        self.assertEqual({"T1": ["tests.test_client.RenewTests.test_t1_one_mutation"], "T2": []}, proof["case_tests"])
        [failure] = proof["failures"]
        self.assertIn("T2: Given no timeout", failure)
        self.assertIn("test_t2_", failure)

    def test_the_proof_passes_when_every_case_has_its_test(self):
        import autocode_regression as regression

        proof = {
            "verdict": "PASS",
            "failures": [],
            "unverified": [],
            "fail_to_pass": ["tests.test_client.RenewTests.test_t1_one_mutation"],
        }
        regression.check_cases(proof, [CASE])
        self.assertEqual(("PASS", []), (proof["verdict"], proof["failures"]))

    def test_without_per_test_results_the_cases_are_unverified_not_passed(self):
        import autocode_regression as regression

        proof = {"verdict": "PASS", "failures": [], "unverified": [], "fail_to_pass": None}
        regression.check_cases(proof, [CASE])
        self.assertEqual(("UNVERIFIED", {"T1": []}), (proof["verdict"], proof["case_tests"]))

    def test_a_proof_that_already_failed_gets_no_misleading_note(self):
        import autocode_regression as regression

        proof = {
            "verdict": "FAIL",
            "failures": ["The regression tests fail on the candidate: x"],
            "unverified": [],
            "fail_to_pass": None,
        }
        regression.check_cases(proof, [CASE])
        self.assertEqual(("FAIL", [], {"T1": []}), (proof["verdict"], proof["unverified"], proof["case_tests"]))

    def test_a_preserve_case_passes_with_a_test_that_passes_before_and_after(self):
        import autocode_regression as regression

        preserve = {
            "id": "T4",
            "given": "11 items, page size 5",
            "when": "page_count(11, 5)",
            "then": "returns 2",
            "kind": "preserve",
        }
        proof = {
            "verdict": "PASS",
            "failures": [],
            "unverified": [],
            "fail_to_pass": ["tests.test_pager.PagerTests.test_t1_off_by_one"],
            "pass_to_pass": ["tests.test_pager.PagerTests.test_t4_exact_multiple"],
        }
        regression.check_cases(proof, [CASE, preserve])
        self.assertEqual(("PASS", []), (proof["verdict"], proof["failures"]))
        self.assertEqual(["tests.test_pager.PagerTests.test_t4_exact_multiple"], proof["case_tests"]["T4"])

    def test_a_preserve_case_without_a_test_fails(self):
        import autocode_regression as regression

        preserve = {
            "id": "T4",
            "given": "11 items, page size 5",
            "when": "page_count(11, 5)",
            "then": "returns 2",
            "kind": "preserve",
        }
        proof = {
            "verdict": "PASS",
            "failures": [],
            "unverified": [],
            "fail_to_pass": ["tests.test_pager.PagerTests.test_t1_off_by_one"],
            "pass_to_pass": [],
        }
        regression.check_cases(proof, [preserve])
        self.assertEqual("FAIL", proof["verdict"])
        [failure] = proof["failures"]
        self.assertIn("Preserve case T4", failure)
        self.assertIn("passes both with the change and on the original code", failure)

    def test_a_mistagged_preserve_case_fails_with_a_say_so_message(self):
        import autocode_regression as regression

        preserve = {
            "id": "T4",
            "given": "11 items, page size 5",
            "when": "page_count(11, 5)",
            "then": "returns 2",
            "kind": "preserve",
        }
        proof = {
            "verdict": "PASS",
            "failures": [],
            "unverified": [],
            "fail_to_pass": ["tests.test_pager.PagerTests.test_t4_exact_multiple"],
            "pass_to_pass": [],
        }
        regression.check_cases(proof, [preserve])
        self.assertEqual("FAIL", proof["verdict"])
        [failure] = proof["failures"]
        self.assertIn("fails on the original code", failure)
        self.assertIn("tag it restore", failure)

    def test_a_restore_case_whose_test_already_passed_on_the_original_still_fails(self):
        import autocode_regression as regression

        proof = {
            "verdict": "PASS",
            "failures": [],
            "unverified": [],
            "fail_to_pass": [],
            "pass_to_pass": ["tests.test_pager.PagerTests.test_t1_off_by_one"],
        }
        regression.check_cases(proof, [CASE])
        self.assertEqual("FAIL", proof["verdict"])
        [failure] = proof["failures"]
        self.assertIn("did not pass without it", failure)

    def test_a_case_without_a_kind_is_a_restore_case(self):
        import autocode_regression as regression

        proof = {
            "verdict": "PASS",
            "failures": [],
            "unverified": [],
            "fail_to_pass": ["tests.test_client.RenewTests.test_t1_one_mutation"],
            "pass_to_pass": ["tests.test_client.RenewTests.test_t2_never_retries_twice"],
        }
        plain = {"id": "T2", "given": "no timeout", "when": "renew() runs", "then": "1 mutation"}
        regression.check_cases(proof, [CASE, plain])
        self.assertEqual("FAIL", proof["verdict"])
        self.assertIn("T2", proof["failures"][0])

    def test_the_diagnosis_rejects_an_unknown_case_kind(self):
        bad = {"id": "T9", "given": "a", "when": "b", "then": "c", "kind": "guard"}
        with self.assertRaisesRegex(ValueError, "restore or preserve"):
            bug_job.check_cases([bad])
        bug_job.check_cases([{**bad, "kind": "preserve"}])

    def test_runs_without_english_tests_are_unchanged(self):
        import autocode_regression as regression

        proof = {"verdict": "PASS", "failures": [], "unverified": [], "fail_to_pass": ["x.test_a"]}
        regression.check_cases(proof, [])
        self.assertEqual({"verdict": "PASS", "failures": [], "unverified": [], "fail_to_pass": ["x.test_a"]}, proof)
        self.assertEqual([], bug_job.test_cases({"investigation": {"outcome": "reproduced"}}))


class ReproductionProbeTests(unittest.TestCase):
    """The runner runs the Investigator's probe in a scratch copy; "reproduced" is not taken on trust."""

    BUGGY = "def page_count(total, size):\n    return total // size\n"
    SHOWS_BUG = "python3 -c 'from pager import page_count; assert page_count(5, 2) == 2'"
    # The bug removed: the probe must fail here (#1769).
    FIXED = {"file": "pager/__init__.py", "find": "return total // size", "replace": "return -(-total // size)"}

    def apply(self, **overrides):
        root = Path(tempfile.mkdtemp(prefix="bug-probe-"))
        (root / "pager").mkdir()
        (root / "pager" / "__init__.py").write_text(self.BUGGY)
        for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *command], cwd=root, check=True
            )
        state = state_for(str(root))
        value = diagnosis(
            fix_size="large",
            affected_paths=["pager/__init__.py"],
            test_paths=["tests/test_pager.py"],
            note_path="docs/bugs/page-count.json",
            **{"counterexample": self.FIXED, **overrides},
        )
        autoresolver.apply_job(
            bug_job.STAGE, state, value, {"changed_files": [], "output": str(root / "o.json")}, str(root)
        )
        return state, root

    def test_a_probe_that_shows_the_bug_is_recorded_in_the_state_and_the_note(self):
        state, root = self.apply(probe=self.SHOWS_BUG, untestable="")
        self.assertEqual(0, state["investigation"]["probe_result"]["exit_code"])
        self.assertEqual(self.SHOWS_BUG, json.loads((root / "docs/bugs/page-count.json").read_text())["proven_by"])
        self.assertEqual(("RUNNING", "astra_discovery"), (state["status"], state["next_stage"]))
        self.assertEqual(self.BUGGY, (root / "pager" / "__init__.py").read_text())  # the workspace is untouched
        self.assertNotEqual(0, state["investigation"]["probe_result"]["counterexample"]["exit_code"])

    def test_a_probe_that_passes_with_the_bug_removed_is_not_a_reproduction(self):
        # #1769: `assert 2 + 2 == 4` exits 0 whatever the code says, so it cannot show the bug is present.
        for probe, counterexample in (
            ("python3 -c 'assert 2 + 2 == 4'", self.FIXED),
            ("python3 -c 'import pager'", self.FIXED),
            (self.SHOWS_BUG, {"file": "", "find": "", "replace": ""}),
        ):
            with self.subTest(probe=probe), self.assertRaisesRegex(ValueError, "counterexample"):
                self.apply(probe=probe, untestable="", counterexample=counterexample)

    def test_a_probe_that_does_not_show_the_bug_rejects_the_reproduction(self):
        with self.assertRaisesRegex(ValueError, "reproduction claims' probes did not exit 0"):
            self.apply(probe="python3 -c 'from pager import page_count; assert page_count(5, 2) == 3'", untestable="")

    def test_a_reproduced_bug_needs_exactly_one_of_probe_and_untestable(self):
        with self.assertRaisesRegex(ValueError, "exactly one of probe"):
            self.apply(probe="", untestable="")
        with self.assertRaisesRegex(ValueError, "exactly one of probe"):
            self.apply(probe=self.SHOWS_BUG, untestable="needs a registry")

    def test_an_untestable_reproduction_says_why_and_runs_nothing(self):
        state, root = self.apply(probe="", untestable="The race needs a real registry; see tests_run")
        self.assertIsNone(state["investigation"]["probe_result"])
        self.assertEqual("", json.loads((root / "docs/bugs/page-count.json").read_text())["proven_by"])

    def test_a_report_that_did_not_reproduce_carries_no_probe(self):
        root = Path(tempfile.mkdtemp())
        with self.assertRaisesRegex(ValueError, "must not propose a fix"):
            bug_job.apply(
                state_for(str(root)), diagnosis("not_reproduced", untestable="x"), {"changed_files": []}, str(root)
            )


class DeclaredInterpreterWorkflowTests(unittest.TestCase):
    def test_external_dependencies_reach_the_investigator_and_real_clean_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            workspace = root / "project"
            workspace.mkdir()
            (workspace / "pager.py").write_text(ReproductionProbeTests.BUGGY)
            (workspace / ".gitignore").write_text(".autocode/\n")
            for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "buggy pager"]):
                subprocess.run(
                    ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
                    cwd=workspace,
                    check=True,
                    capture_output=True,
                )
            environment = root / "external environment"
            subprocess.run(
                [sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True, capture_output=True
            )
            python = environment / "bin/python"
            site = Path(
                subprocess.check_output(
                    [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True
                ).strip()
            )
            (site / "offline_dependency.py").write_text("value = 42\n")
            report = root / "report.json"
            report.write_text(
                json.dumps(
                    diagnosis(
                        fix_size="large",
                        affected_paths=["pager.py"],
                        test_paths=["test_pager.py"],
                        note_path="docs/bugs/pager.json",
                        observed="page_count(5, 2) returns 2",
                        reproduction="page_count(5, 2) == 2",
                        invariant="round partial pages up",
                        root_cause="integer division truncates",
                        probe="",
                        untestable="",
                        test_cases=[
                            {
                                "id": "T1",
                                "given": "total=5, size=2",
                                "when": "page_count(5, 2)",
                                "then": "returns 3",
                                "kind": "restore",
                            }
                        ],
                    )
                )
            )
            provider = root / "provider.py"
            provider.write_text("""import json,subprocess,sys,shlex
from pathlib import Path
schema=json.loads(Path(sys.argv[2]).read_text())
if 'outcome' not in schema.get('properties',{}):
    raise SystemExit(2)  # This fixture exercises investigation only.
data=json.loads(sys.stdin.read().split('CURRENT HANDOFF DATA\\n',1)[1])
python=data['investigation_python']
probe_source='from pager import page_count; import offline_dependency; assert page_count(5,2)==2 and offline_dependency.value==42'
probe="from pathlib import Path; import runpy; Path('replay_fixture.py').write_text("+repr(probe_source)+"); runpy.run_path('replay_fixture.py')"
command=[python,'-B','-c',probe]
result=subprocess.run(command,cwd=data['investigation_workspace'],capture_output=True,text=True)
Path(sys.argv[3]).with_suffix('.probe.json').write_text(json.dumps({'python':python,'cwd':data['investigation_workspace'],'exit_code':result.returncode,'stderr':result.stderr}))
if result.returncode:
    print(result.stderr,file=sys.stderr)
    raise SystemExit(3)
value=json.loads(Path(sys.argv[3]).read_text())
value['probe']=shlex.join(command)
value['counterexample']={'file':'pager.py','find':'return total // size','replace':'return -(-total // size)'}
value['tests_run']=[value['probe']+' exited 0 in the prepared source copy']
Path(sys.argv[1]).write_text(json.dumps(value))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))
""")
            config_root = root / "config"
            config = config_root / "autocode/providers/offline.toml"
            config.parent.mkdir(parents=True)
            config.write_text(
                'name = "offline"\nprompt = "stdin"\noutput = "report_file"\ncommand = '
                + json.dumps([sys.executable, str(provider), "{report}", "{schema}", str(report)])
                + '\nmodels = ["producer", "verifier", "planner", "reviewer"]\n[roles]\n'
                + "\n".join(
                    f'{role} = {{ model = "{model}", effort = "medium" }}'
                    for role, model in (
                        ("astra", "reviewer"),
                        ("terra", "producer"),
                        ("sol", "verifier"),
                        ("completion", "verifier"),
                        ("glm", "planner"),
                        ("plan_reviewer", "reviewer"),
                    )
                )
                + "\n"
            )
            command = shlex.join(
                ["env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", str(python), "-m", "pytest", "test_pager.py"]
            )
            env = {
                **os.environ,
                "AUTOCODE_HOME": str(root / "registry"),
                "XDG_CONFIG_HOME": str(config_root),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            options = (
                "--provider",
                "offline",
                "--workflow",
                "bugfix",
                "--joint-planning",
                "--test-command",
                command,
                "--max-stage-seconds",
                "30",
                "--max-seconds",
                "60",
                "--max-idle-seconds",
                "0",
                "--max-tool-seconds",
                "0",
            )
            try:
                run = TaskRun.start(
                    workspace, "Fix page_count(5, 2) returning 2 instead of 3.", options=options, env=env, timeout=60
                )
            except TaskRunError as error:
                self.assertIsNotNone(error.run_dir, str(error))
                run = TaskRun(workspace, error.run_dir, options=options, env=env, timeout=30)
            view = run.status()
            note = workspace / "docs/bugs/pager.json"
            probe_receipt = report.with_suffix(".probe.json")
            self.assertTrue(
                note.is_file(),
                json.dumps(
                    {
                        "status": view["status"],
                        "stop_reason": view["stop_reason"],
                        "native_fixture_probe": json.loads(probe_receipt.read_text())
                        if probe_receipt.exists()
                        else None,
                    },
                    indent=2,
                ),
            )
            accepted = json.loads(note.read_text())
            self.assertIn(shlex.quote(str(python)), accepted["proven_by"])
            self.assertIn("offline_dependency", accepted["proven_by"])
            self.assertFalse((workspace / "replay_fixture.py").exists())
            self.assertEqual(ReproductionProbeTests.BUGGY, (workspace / "pager.py").read_text())


class BugPlanningTaskAuthorityTests(unittest.TestCase):
    STAGES = ("astra_discovery", "astra_challenge", "glm_revise", "astra_finalize")
    TASK = "Fix duplicate renews after a timeout; preserve the existing return value and callback behavior."

    def test_completed_bug_does_not_instruct_the_feature_followup_to_restore_it_again(self):
        import autocode_follow_up as follow_up
        from units import autoplanner

        with tempfile.TemporaryDirectory() as temporary:
            state = state_for(temporary, self.TASK)
            state.update(
                status="TASK_COMPLETE",
                investigation={**diagnosis(fix_size="large"), "output": "old.json"},
                stages=[{"stage": "investigate_bug", "output": "old.json"}],
            )
            follow_up.accept(state, "Add a non-destructive peek method; keep the fixed behavior.", temporary, "t1")
            workflows.apply(
                state, {"workflow": "build", "reason": "new method", "signals": []}, {"output": "kind.json"}
            )
            saved = json.loads(json.dumps(state["investigation"]))
            for stage in self.STAGES:
                with self.subTest(stage=stage):
                    prompt, _ = autoplanner.context(state, stage, Path(temporary) / "state.json")
                    instruction, raw = prompt.split("\nCURRENT HANDOFF DATA\n", 1)
                    packet = json.loads(raw)
                    self.assertNotIn("bug_diagnosis", packet)
                    self.assertNotIn(autoplanner.BUG_DIAGNOSIS_RULE, instruction)
                    self.assertIn(
                        "peek",
                        packet["task"]
                        if not packet.get("source_inventory")
                        else json.dumps(packet["source_inventory"]),
                    )
            self.assertEqual(saved, state["investigation"], "the prior diagnosis remains available as history")

    def render(self, stage, investigation):
        from units import autoplanner

        with tempfile.TemporaryDirectory() as temporary:
            state = state_for(temporary, self.TASK)
            state["investigation"] = investigation
            state["answers"] = {"Q1": "Keep the existing callback signature."}
            prompt, _ = autoplanner.context(state, stage, Path(temporary) / "state.json")
        instruction, handoff = prompt.split("\nCURRENT HANDOFF DATA\n", 1)
        return instruction, json.loads(handoff)

    def test_each_planning_stage_preserves_human_obligations_absent_from_diagnosis(self):
        investigation = diagnosis(fix_size="large")
        expected = bug_job.large_correction({"investigation": investigation})
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                instruction, packet = self.render(stage, investigation)
                self.assertEqual("see source_inventory id task:0", packet["task"])
                self.assertEqual(self.TASK, packet["source_inventory"][0]["text"])
                self.assertEqual({"Q1": "Keep the existing callback signature."}, packet["saved_answers"])
                self.assertEqual(expected, packet["bug_diagnosis"])
                self.assertIn("HUMAN TASK COVERAGE:", instruction)
                self.assertIn("A named test proves its actual assertions", instruction)
                self.assertIn("including ones absent from the diagnosis", instruction)
                self.assertIn("The task and saved user answers and events define the requested", instruction)
                self.assertIn("the diagnosis does not replace, narrow or override them", instruction)
                self.assertNotIn("It is the requirements: plan the correction", instruction)

    def test_each_planning_stage_keeps_named_guards_in_the_implementation_plan(self):
        guard = {**CASE, "id": "T2", "kind": "preserve", "given": "no timeout"}
        investigation = diagnosis(
            fix_size="large", test_cases=[CASE, guard], fix_plan=["Fix the timeout and add only test_t1_timeout."]
        )
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                instruction, packet = self.render(stage, investigation)
                self.assertEqual([CASE, guard], packet["bug_diagnosis"]["test_cases"])
                self.assertIn("Reconcile technical_approach and validation_plan", instruction)
                self.assertIn("every retained test_cases ID, including preserve cases", instruction)
                self.assertIn("A fix_plan that omits a named guard is incomplete", instruction)
                self.assertIn("Keep existing tests unchanged; do not rename", instruction)
                self.assertIn("The Plan Reviewer checks this coverage before approval", instruction)

    def test_each_planning_stage_treats_a_conflicting_diagnosis_as_a_blocking_concern(self):
        investigation = diagnosis(
            fix_size="large",
            invariant="Never call the existing callback.",
            test_cases=[{**CASE, "then": "the callback is never called"}],
            fix_plan=["remove the existing callback"],
        )
        expected = bug_job.large_correction({"investigation": investigation})
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                instruction, packet = self.render(stage, investigation)
                self.assertEqual("see source_inventory id task:0", packet["task"])
                self.assertEqual(self.TASK, packet["source_inventory"][0]["text"])
                self.assertEqual(expected, packet["bug_diagnosis"])
                self.assertIn("Check its invariant and test_cases against those", instruction)
                self.assertIn("a blocking plan concern", instruction)
                self.assertIn("The Plan Reviewer checks that comparison independently", instruction)
                self.assertIn("fix_plan is a proposed approach, not a user decision", instruction)
                self.assertIn("unresolved choice in the task and saved user answers", instruction)
                self.assertIn("missing or conflicting diagnosis text is not permission to redefine it", instruction)
