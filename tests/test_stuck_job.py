"""A stage that stops converging goes to an Investigator before the run pauses for the user."""
import copy
import tempfile
import unittest
import subprocess
import shlex
import sys
from unittest.mock import patch
from pathlib import Path

import autocode_jobs as jobs
import autocode_stuck_job as stuck
import autopilot
from units import autoresolver, common
import autocode_builder_failure as builder_failure
import autocode_util as util


class Paused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def state_for(workspace=None, next_stage="terra", status="RUNNING", **extra):
    if workspace is None:  # a real directory holding the fixture report's cited evidence file
        workspace = Path(tempfile.mkdtemp(prefix="stuck-ws-"))
        (workspace / "docs" / "bugs").mkdir(parents=True)
        (workspace / "docs" / "bugs" / "cent-drift.json").write_text("{}\n")
        workspace = str(workspace)
    state = {"version": 3, "task": "Fix the rounding drift", "workspace": workspace, "status": status,
             "phase": "EXECUTING", "next_stage": next_stage, "stages": [], "iteration": 1,
             # An OpenCode run records its transport at creation (autocode.py configure).
             "settings": {"limits": {"no_progress_batches": 3}, "transport_identities": {"opencode": {"fixture": True}},
                          "roles": {
                 "astra": {"model": "openai/gpt-6-astra", "engine": "opencode"},
                 "plan_reviewer": {"model": "openai/gpt-6-astra", "engine": "opencode", "reasoning_effort": "high"},
                 "glm": {"model": "zai-coding-plan/glm-5.3"}, "terra": {"model": "openai/gpt-6-sol"},
                 "sol": {"model": "zai-coding-plan/glm-5.3"}}}}
    state.update(extra)
    return state


def report(recommendation="retry", **overrides):
    value = {"diagnosis": "The Planner cites the diagnosis with prose after its path.", "cause": "stage_output",
             "guidance": "Cite docs/bugs/cent-drift.json exactly; put the explanation in the summary.",
             "recommendation": recommendation, "user_question": "", "evidence_refs": ["docs/bugs/cent-drift.json"],
             "example": "Given code_refs 'docs/bugs/cent-drift.json (see the note)'; when the runner checked the "
                        "path; then it rejected it as missing",
             "probe": "", "untestable": "The fixture reads no saved attempt; the cause is stated, not shown"}
    if recommendation == "pause":
        value.update(guidance="", cause="needs_user", user_question="May the fix change the public API?",
                     example="", untestable="")
    value.update(overrides)
    return value


class InterceptTests(unittest.TestCase):
    def test_a_non_convergence_pause_goes_to_the_investigator(self):
        state = state_for(status="PAUSED_REPEATED_FAILURE", stop_reason="rejected", paused_at="t",
                          pending_report_repair={"attempts": 2})
        self.assertTrue(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "rejected three times"))
        self.assertEqual(("RUNNING", "INVESTIGATING", stuck.STAGE), (state["status"], state["phase"], state["next_stage"]))
        self.assertNotIn("stop_reason", state)
        self.assertNotIn("pending_report_repair", state)  # set aside, or the before-hook replays it first
        request = state["stuck_investigation"]
        self.assertEqual(("terra", "PAUSED_REPEATED_FAILURE", {"attempts": 2}),
                         (request["stage"], request["status"], request["pending_report_repair"]))
        self.assertEqual("investigating", state["stuck_investigations"][0]["outcome"])

    def test_user_owned_and_unsafe_pauses_are_left_alone(self):
        for status, extra in (("PAUSED_BUDGET", {}), ("PAUSED_CRITERIA_CHANGE", {}), ("PAUSED_ITERATION_LIMIT", {}),
                              ("PAUSED_UNCERTAIN_STAGE", {}), ("WAITING_FOR_USER", {}),
                              ("PAUSED_REPEATED_FAILURE", {"active_stage": {"stage": "terra"}}),
                              ("PAUSED_REPEATED_FAILURE", {"version": 2}),
                              ("PAUSED_REPEATED_FAILURE", {"next_stage": stuck.STAGE}),
                              ("PAUSED_REPEATED_FAILURE", {"next_stage": "astra_diagnose"}),
                              ("PAUSED_REPEATED_FAILURE", {"next_stage": "astra_resolve"}),
                              ("PAUSED_REPEATED_FAILURE", {"settings": {"stuck_investigation": {"max_calls_per_run": 0}}})):
            with self.subTest(status=status, extra=extra):
                state = state_for(**extra)
                before = dict(state)
                self.assertFalse(stuck.intercept(state, status, "why"))
                self.assertEqual(before, state)

    def test_an_operator_authorized_retry_goes_back_to_the_operator(self):
        state = state_for(stages=[{"stage": "terra", "failure_key": "k1"}],
                          failure_retry_authorizations=[{"failure_key": "k1", "consumed": True}])
        before = copy.deepcopy(state)
        self.assertFalse(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "failed again after --retry-failed-stage"))
        self.assertEqual(before, state, "declining never touches the state")

    def test_one_investigation_per_problem_and_a_run_budget(self):
        state = state_for()
        self.assertTrue(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "x"))
        state.update(next_stage="terra", status="RUNNING")
        self.assertFalse(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "x again"))
        for stage in ("sol", "astra_review"):
            state["next_stage"] = stage
            self.assertTrue(stuck.intercept(state, "PAUSED_INVALID_OUTPUT", "x"))
        state["next_stage"] = "astra_challenge"
        self.assertFalse(stuck.intercept(state, "PAUSED_PLANNING_BUDGET", "x"), "three per run by default")


class BuilderFailureClassificationTests(unittest.TestCase):
    def fixture(self, root):
        workspace = Path(root) / 'workspace'
        workspace.mkdir()
        output = Path(root) / 'builder.json'
        output.write_text('{}\n')
        state = state_for(str(workspace), current_task={'id': 'T1'},
                          goal_contract={'hash': 'C1', 'revision': 1},
                          builder_retries={'existing': {'failures': ['earlier']}}, no_progress_batches=2)
        snapshot = patch('autocode_builder_failure.source_scope.snapshot', return_value={'revision': 'source-1'})
        snapshot.start()
        self.addCleanup(snapshot.stop)
        record = {'output': str(output), 'source_revision': 'source-1'}
        return state, builder_failure.evidence(state, record)

    def test_pre_escalation_report_is_mode_specific_and_bound_to_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            self.assertTrue(builder_failure.queue(state, evidence, 'Empty diff', enabled=True, max_calls=3))
            request = autoresolver.prepare_stuck(state, Path(root) / 'state.json')
            self.assertFalse(request.allow_write)
            self.assertIn('failure_class', request.schema['required'])
            self.assertNotIn('failure_class', stuck.SCHEMA['properties'])
            value = report(failure_class='execution', failure_id=evidence['failure_id'],
                           evidence_refs=evidence['evidence_refs'])
            result = stuck.apply(state, value, {'output': str(Path(root) / 'diagnosis.json')}, state['workspace'])
            self.assertEqual(evidence['failure_id'], result['diagnosis']['failure_id'])
            self.assertEqual(2, state['no_progress_batches'])
            self.assertEqual(['earlier'], state['builder_retries']['existing']['failures'])
            self.assertNotIn('in_force', state.get('stuck_investigation', {}))
            self.assertFalse(builder_failure.queue(state, evidence, 'Same failure', enabled=True, max_calls=3))

    def test_stale_identity_and_uncited_evidence_cannot_grant_continuation(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            builder_failure.queue(state, evidence, 'Empty diff', enabled=True, max_calls=3)
            for failure_id, refs in [('other', evidence['evidence_refs']), (evidence['failure_id'], ['other'])]:
                with self.assertRaises(ValueError):
                    stuck.apply(state, report(failure_class='execution', failure_id=failure_id, evidence_refs=refs),
                                {}, state['workspace'])
            Path(evidence['record']['output']).write_text('{"changed": true}\n')
            with self.assertRaises(util.Paused):
                autoresolver.prepare_stuck(state, Path(root) / 'state.json')

    def test_existing_call_limit_refuses_a_new_failure_without_resetting_any_lane(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            self.assertFalse(builder_failure.queue(state, evidence, 'Unknown', enabled=True, max_calls=0))
            self.assertEqual('PAUSED_BUILDER_CLASSIFICATION', state['status'])
            self.assertEqual(['earlier'], state['builder_retries']['existing']['failures'])

    def test_typed_operational_cause_retains_its_pause_without_retry_or_reassignment(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            evidence['error_class'] = 'PAUSED_PROVIDER_TIMEOUT'
            evidence['diagnosis'] = {'failure_id': evidence['failure_id'], 'failure_class': 'execution',
                                     'evidence_refs': evidence['evidence_refs']}
            before = copy.deepcopy(state['settings']['roles'])
            reassess = []
            action = builder_failure.route(state, evidence, 'Provider timed out', enabled=True, max_calls=3,
                                           reassess=lambda *args: reassess.append(args))
            self.assertEqual('recover', action)
            self.assertEqual('PAUSED_PROVIDER_TIMEOUT', state['status'])
            self.assertEqual(before, state['settings']['roles'])
            self.assertEqual([], reassess)
            self.assertNotIn('stuck_investigation', state)
            self.assertEqual(['earlier'], state['builder_retries']['existing']['failures'])

    def test_valid_execution_diagnosis_needing_user_is_an_operator_hold_not_a_retry(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            builder_failure.queue(state, evidence, 'Empty diff', enabled=True, max_calls=3)
            value = report('pause', failure_class='execution', failure_id=evidence['failure_id'],
                           evidence_refs=evidence['evidence_refs'], example='The assigned approach needs a scope decision',
                           untestable='Only the operator can approve the requested scope', user_question='May this change scope?')
            result = stuck.apply(state, value, {'output': str(Path(root) / 'diagnosis.json')}, state['workspace'])
            self.assertIsNone(result)
            self.assertEqual('PAUSED_BUILDER_CLASSIFICATION', state['status'])
            self.assertIn('May this change scope?', state['stop_reason'])
            self.assertEqual(['earlier'], state['builder_retries']['existing']['failures'])

    def test_reconstructing_evidence_through_a_file_alias_cannot_create_a_new_incident(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            alias = Path(root) / 'alias.json'
            alias.symlink_to(evidence['record']['output'])
            rebuilt = builder_failure.evidence(state, {**evidence['record'], 'output': str(alias)})
            self.assertEqual(evidence['failure_id'], rebuilt['failure_id'])
            self.assertEqual(evidence['evidence_refs'], rebuilt['evidence_refs'])
            builder_failure.queue(state, evidence, 'Empty diff', enabled=True, max_calls=3)
            self.assertFalse(builder_failure.queue(state, rebuilt, 'Alias replay', enabled=True, max_calls=3))

    def test_accepting_current_completed_attempt_defers_admission_until_normal_save(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            completed = {**evidence['record'], 'stage': 'terra', 'exit_code': 0}
            state['active_stage'] = completed
            self.assertFalse(builder_failure.queue(state, evidence, 'Empty diff', enabled=True, max_calls=3,
                                                   completed_record=completed))
            self.assertNotIn('stuck_investigations', state)
            self.assertIs(completed, state['active_stage'])
            with self.assertRaises(util.Paused):
                builder_failure.finalize(state, completed, enabled=True, max_calls=3)

    def test_foreign_active_attempt_and_uncertain_ownership_cannot_defer_or_spend_a_call(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            for extra in ({'active_stage': {'output': 'foreign.json', 'stage': 'terra', 'exit_code': 0}},
                          {'active_stage': evidence['record'], 'uncertain_artifacts': ['uncollected']}):
                held = {**copy.deepcopy(state), **extra}
                self.assertFalse(builder_failure.queue(held, evidence, 'Empty diff', enabled=True, max_calls=3,
                                                       completed_record={**evidence['record'], 'stage': 'terra', 'exit_code': 0}))
                self.assertNotIn('pending_builder_failure', held)
                self.assertNotIn('stuck_investigations', held)

    @unittest.skipUnless(sys.platform == 'darwin', 'native read-only classification probe sandbox')
    def test_classification_probe_cannot_overwrite_read_and_restore_cited_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            cited = Path(root) / 'builder.json'
            cited.write_text('original')
            scripts = ("from pathlib import Path; assert Path('builder.json').read_text() == 'original'",
                       "from pathlib import Path; p=Path('builder.json'); old=p.read_text(); "
                       "p.write_text('invented'); assert p.read_text() == 'invented'; p.write_text(old)")
            for index, script in enumerate(scripts):
                command = builder_failure.readonly_probe(shlex.join([sys.executable, '-c', script]))
                receipt = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True, timeout=10)
                self.assertEqual(index == 0, receipt.returncode == 0, receipt.stderr)
                self.assertEqual('original', cited.read_text())

    def test_probe_without_enforceable_read_only_boundary_fails_closed(self):
        with patch.object(builder_failure.sys, 'platform', 'unsupported'):
            with self.assertRaisesRegex(ValueError, 'containment is unavailable'):
                builder_failure.readonly_probe('true')

    def test_unchanged_held_binding_refuses_writer_and_diagnostician_without_renewing_authority(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            builder_failure.hold(state, evidence, 'PAUSED_BUILDER_CLASSIFICATION', 'May this change scope?')
            before = copy.deepcopy(state['builder_retries'])
            for stage in ('terra', 'orchestrator', 'astra_resolve', 'investigate_stuck'):
                with self.assertRaisesRegex(util.Paused, 'May this change scope'):
                    builder_failure.dispatch_guard(state, stage, state['workspace'])
            self.assertEqual(before, state['builder_retries'])
            self.assertIn('builder_failure_hold', state)
            state['current_task'] = {**state['current_task'], 'id': 'genuinely-new-assignment'}
            builder_failure.dispatch_guard(state, 'terra', state['workspace'])
            self.assertNotIn('builder_failure_hold', state)
            self.assertEqual(before, state['builder_retries'])

    def test_check_classification_uses_executed_facts_not_model_authored_operational_labels(self):
        checks = [{'command': 'check', 'exit_code': 1, 'evidence_ref': 'event:C1', 'timed_out': True}]
        event = {'type': 'item.completed', 'item': {'id': 'C1', 'type': 'command_execution', 'exit_code': 1}}
        facts = builder_failure.check_facts(checks, {'events': 'executed.jsonl'}, '.', read_events=lambda _: [event])
        self.assertNotIn('timed_out', facts[0])
        event['item']['timed_out'] = True
        facts = builder_failure.check_facts([{key: value for key, value in checks[0].items() if key != 'timed_out'}],
                                           {'events': 'executed.jsonl'}, '.', read_events=lambda _: [event])
        self.assertTrue(facts[0]['timed_out'])


class ApplyTests(unittest.TestCase):
    def investigated(self, status, stage="terra", **extra):
        state = state_for(next_stage=stage, **extra)
        self.assertTrue(stuck.intercept(state, status, "the original reason"))
        return state

    def test_retry_after_repeated_failure_keeps_the_failure_history(self):
        history = {"k1": {"identity": {"stage": "terra"}, "count": 3}, "k2": {"identity": {"stage": "sol"}, "count": 1}}
        state = self.investigated("PAUSED_REPEATED_FAILURE", pending_report_repair={"attempts": 2},
                                  failure_history=copy.deepcopy(history),
                                  stages=[{"stage": "terra", "failure_key": "k1"}, {"stage": "sol", "failure_key": "k2"}])
        stuck.apply(state, report(), {"output": "o"}, state["workspace"])
        # One more attempt, not a fresh failure budget: another failure counts on top of these.
        self.assertEqual(history, state["failure_history"])
        self.assertEqual(["k1", "k2"], [row.get("failure_key") for row in state["stages"]])
        self.assertEqual({"attempts": 2}, state["report_repair_archive"][-1]["repair"])
        self.assertEqual(("RUNNING", "EXECUTING", "terra"), (state["status"], state["phase"], state["next_stage"]))
        self.assertTrue(state["stuck_investigation"]["in_force"])
        self.assertEqual("retried", state["stuck_investigations"][0]["outcome"])

    def test_the_route_exists_only_while_the_investigator_runs(self):
        state = self.investigated("PAUSED_REPEATED_FAILURE")
        state["settings"]["roles"][stuck.ROUTE] = {"model": "openai/gpt-6-astra", "engine": "opencode",
                                                   "reasoning_effort": "xhigh"}
        stuck.apply(state, report(), {"output": "o"}, state["workspace"])
        self.assertNotIn(stuck.ROUTE, state["settings"]["roles"])
        self.assertEqual(("openai/gpt-6-astra", "opencode"),
                         (state["stuck_investigations"][0]["model"], state["stuck_investigations"][0]["engine"]))

    def test_pinned_route_from_the_cli(self):
        from types import SimpleNamespace
        settings = {"engine": "codex"}
        self.assertIs(settings, stuck.configure(settings, SimpleNamespace()))
        self.assertNotIn("stuck_investigation", settings)
        stuck.configure(settings, SimpleNamespace(investigator_model="openai/gpt-6-sol", investigator_reasoning_effort=None))
        self.assertEqual({"model": "openai/gpt-6-sol", "reasoning_effort": "high", "provider": None, "engine": "opencode"},
                         settings["stuck_investigation"]["route"])
        stuck.configure(settings, SimpleNamespace(investigator_model=None, investigator_reasoning_effort="max"))
        self.assertEqual(("openai/gpt-6-sol", "max"), (stuck.pinned_route(settings)["model"],
                                                     stuck.pinned_route(settings)["reasoning_effort"]))
        stuck.configure(settings, SimpleNamespace(investigator_model="gpt-6-astra", investigator_reasoning_effort=None))
        self.assertEqual("codex", settings["stuck_investigation"]["route"]["engine"], "a bare name keeps the run's engine")
        with self.assertRaises(ValueError):
            stuck.configure({"engine": "codex"}, SimpleNamespace(investigator_model=None, investigator_reasoning_effort="high"))

    def test_retry_grants_exactly_one_more_review_round_or_batch(self):
        for stage, used, expected in (("astra_challenge", 2, 4), ("astra_finalize", 2, 3)):
            state = self.investigated("PAUSED_PLANNING_BUDGET", stage=stage,
                                      planning={"review_call_limit": 2, "astra_calls": used})
            stuck.apply(state, report(), {}, state["workspace"])
            self.assertEqual((expected, "PLANNING"), (state["planning"]["review_call_limit"], state["phase"]), stage)
        state = self.investigated("PAUSED_NO_PROGRESS", no_progress_batches=3)
        stuck.apply(state, report(), {}, state["workspace"])
        self.assertEqual(2, state["no_progress_batches"])

    def test_pause_restores_the_original_pause_with_the_diagnosis(self):
        state = self.investigated("PAUSED_REPEATED_FAILURE", pending_report_repair={"attempts": 2})
        stuck.apply(state, report("pause"), {}, state["workspace"])
        self.assertEqual(("PAUSED_REPEATED_FAILURE", "PAUSED_OR_BLOCKED", "terra"),
                         (state["status"], state["phase"], state["next_stage"]))
        self.assertIn("the original reason", state["stop_reason"])
        self.assertIn("Investigator (paused): The Planner cites", state["stop_reason"])
        self.assertIn("Needs you: May the fix change the public API?", state["stop_reason"])
        self.assertEqual({"attempts": 2}, state["pending_report_repair"])
        self.assertNotIn("stuck_investigation", state)

    def test_a_diagnose_only_pause_is_never_retried(self):
        state = self.investigated("PAUSED_BUILDER_RETRY_LIMIT")
        stuck.apply(state, report(), {}, state["workspace"])
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", state["status"])
        self.assertIn("Investigator (paused)", state["stop_reason"])

    def test_reports_the_runner_cannot_act_on_are_rejected(self):
        for name, value, changed in (("wrote to the workspace", report(), ["billing/tax.py"]),
                                     ("found nothing", report(diagnosis=" "), []),
                                     ("retry without guidance", report(guidance=""), []),
                                     ("asks the user nothing", report("pause", user_question=""), [])):
            with self.subTest(name):
                state = self.investigated("PAUSED_REPEATED_FAILURE")
                with self.assertRaises(ValueError):
                    stuck.apply(state, value, {"changed_files": changed}, state["workspace"])

    def test_a_failed_investigation_restores_the_original_pause(self):
        state = self.investigated("PAUSED_INVALID_OUTPUT", pending_report_repair={"attempts": 2})
        status, reason = stuck.abandon(state, "provider timed out")
        self.assertEqual(("PAUSED_INVALID_OUTPUT", "terra"), (status, state["next_stage"]))
        self.assertIn("could not finish: provider timed out", reason)
        self.assertEqual({"attempts": 2}, state["pending_report_repair"])
        self.assertEqual("investigation_failed", state["stuck_investigations"][0]["outcome"])


class GuidanceTests(unittest.TestCase):
    def history(self, **overrides):
        return {"identity": "astra_discovery:PAUSED_INVALID_OUTPUT", "stage": "astra_discovery",
                "requested_at": "2026-09-30T08:00:00+00:00", "outcome": "retried",
                "status": "PAUSED_INVALID_OUTPUT", "trigger": "rejected_output",
                "cause": "stage_output", "diagnosis": "The Planner cited runner state instead of source.",
                "guidance": "Cite README.md and app/__init__.py, never .autocode/state.json.", **overrides}

    def test_report_corrections_reach_a_fresh_planning_request_after_clarification(self):
        state = {"stuck_investigations": [self.history()], "answers": {"Q1": {"text": "Use the existing API"}}}
        before = copy.deepcopy(state)
        for stage in ("astra_discovery", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                text = stuck.with_guidance(state, stage, self.request()).prompt
                self.assertIn(self.history()["guidance"], text)
                self.assertLess(text.index(self.history()["guidance"]), text.index("CURRENT HANDOFF DATA"))
        self.assertEqual(before, state, "Remembering a correction must not replenish a retry or budget")
        self.assertEqual(self.request(), stuck.with_guidance(state, "terra", self.request()))

    def test_pauses_and_decision_diagnoses_are_not_reused_as_report_corrections(self):
        for row in (self.history(outcome="paused"), self.history(cause="needs_user"),
                    self.history(cause="environment"), self.history(stage="terra"),
                    self.history(guidance="")):
            with self.subTest(row=row):
                self.assertEqual(self.request(), stuck.with_guidance(
                    {"stuck_investigations": [row]}, "astra_discovery", self.request()))

    def test_report_lessons_survive_boundaries_that_do_not_reset_investigation_identity(self):
        state = {"turns": [{"at": "2026-09-30T09:00:00+00:00"}],
                 "clarification_episode": {"started_by": "answer:Q1"}, "stuck_investigations": [self.history()]}
        self.assertIn(self.history()["guidance"], stuck.with_guidance(state, "astra_discovery", self.request()).prompt)

    def test_active_guidance_keeps_earlier_lessons_and_wins_on_conflict(self):
        first = self.history()
        active = self.history(identity="glm_revise:PAUSED_INVALID_OUTPUT", stage="glm_revise", guidance="Use a valid field")
        state = {"stuck_investigations": [first, active], "stuck_investigation": dict(active, in_force=True)}
        text = stuck.with_guidance(state, "astra_discovery", self.request()).prompt
        self.assertEqual(1, text.count(active["guidance"]))
        self.assertLess(text.index(first["guidance"]), text.index(active["guidance"]))
        self.assertIn("takes precedence", text)
        self.assertNotIn("PREVIOUSLY VERIFIED", text)

    def test_configured_lesson_limit_and_rejection_trigger(self):
        rows = [self.history(identity=str(i), guidance=f"Correction {i}", status="PAUSED_REPEATED_FAILURE") for i in range(5)]
        rows.append(self.history(trigger="non_convergence", status="PAUSED_PLANNING_BUDGET", guidance="Accept M3"))
        state = {"settings": {"stuck_investigation": {"max_calls_per_run": 5}}, "stuck_investigations": rows}
        text = stuck.with_guidance(state, "astra_discovery", self.request()).prompt
        for i in range(5):
            self.assertIn(f"Correction {i}", text)
        self.assertNotIn("Accept M3", text)
        state["settings"]["stuck_investigation"]["max_calls_per_run"] = 0
        self.assertEqual(self.request(), stuck.with_guidance(state, "astra_discovery", self.request()))

    def request(self):
        return common.ModelRequest("glm", "glm", "Do the stage.\nCURRENT HANDOFF DATA\n{}", {}, {}, False)

    def in_force(self, stage):
        state = state_for(next_stage=stage)
        stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "x")
        stuck.apply(state, report(), {}, state["workspace"])
        return state

    def test_guidance_reaches_the_stuck_stage_before_its_handoff_data(self):
        state = self.in_force("terra")
        prompt = stuck.with_guidance(state, "terra", self.request()).prompt
        self.assertIn("INVESTIGATOR GUIDANCE", prompt)
        self.assertLess(prompt.index("Cite docs/bugs/cent-drift.json exactly"), prompt.index("CURRENT HANDOFF DATA"))
        self.assertNotIn("INVESTIGATOR GUIDANCE", stuck.with_guidance(state, "sol", self.request()).prompt)

    def test_execution_guidance_is_spent_when_its_stage_completes(self):
        state = self.in_force("terra")
        stuck.settle(state, "sol")
        self.assertIn("stuck_investigation", state)
        stuck.settle(state, "terra")
        self.assertNotIn("stuck_investigation", state)

    def test_planning_guidance_covers_the_whole_planning_cycle_until_the_run_stops(self):
        state = self.in_force("astra_challenge")
        for stage in ("glm_revise", "astra_finalize", "astra_discovery"):
            self.assertIn("INVESTIGATOR GUIDANCE", stuck.with_guidance(state, stage, self.request()).prompt, stage)
            stuck.settle(state, stage)
        self.assertNotIn("INVESTIGATOR GUIDANCE", stuck.with_guidance(state, "terra", self.request()).prompt)
        stuck.retire(state)
        self.assertNotIn("stuck_investigation", state)

    def test_autopilot_prepares_every_stage_through_the_guidance(self):
        state = self.in_force("astra_discovery")
        original = autopilot.unit_module
        autopilot.unit_module = lambda stage: type("Unit", (), {"prepare": staticmethod(lambda *a: self.request())})
        try:
            prompt = autopilot.prepare_request(state, "glm_revise", Path("/run/state.json"), None).prompt
        finally:
            autopilot.unit_module = original
        self.assertIn("INVESTIGATOR GUIDANCE", prompt)


class RouteTests(unittest.TestCase):
    def test_default_models_never_astra_and_never_the_stuck_stages_model(self):
        roles = state_for()["settings"]["roles"]
        pick = lambda roles, stuck_model, provider=None: tuple(
            stuck.route(roles, stuck_model, provider)[k] for k in ("model", "reasoning_effort"))
        # kilocode runs reach Claude through the Kilo Gateway.
        self.assertEqual((stuck.CLAUDE, "high"), pick(roles, "zai-coding-plan/glm-5.3", "kilocode"))
        self.assertEqual(("openai/gpt-6-sol", "high"), pick(roles, stuck.CLAUDE, "kilocode"))
        # OpenCode runs: Sol, or GLM when the stuck stage runs on Sol.
        self.assertEqual(("openai/gpt-6-sol", "high"), pick(roles, "zai-coding-plan/glm-5.3"))
        self.assertEqual(("zai-coding-plan/glm-5.3", "high"), pick(roles, "openai/gpt-6-sol"))
        self.assertEqual("opencode", stuck.route(roles, "x")["engine"])
        # Native Codex runs have GPT models only: Sol, or Luna when the stuck stage runs on Sol.
        codex = {"plan_reviewer": {"model": "gpt-6-astra", "engine": "codex"}}
        self.assertEqual(("gpt-6-sol", "high"), pick(codex, "gpt-6-astra"))
        self.assertEqual(("gpt-6-luna", "high"), pick(codex, "gpt-6-sol"))
        # A custom provider keeps its reviewer model.
        self.assertEqual(("custom/model", "high"), pick({"astra": {"model": "custom/model"}}, "x", "mytool"))
        for provider in (None, "opencode", "kilocode"):
            self.assertNotIn("astra", pick(roles, "openai/gpt-6-sol", provider)[0])
        self.assertEqual("high", roles["plan_reviewer"]["reasoning_effort"], "the Plan Reviewer's own route is untouched")

    def test_the_autoresolver_prepares_a_fresh_investigator(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace, next_stage="astra_challenge", sessions={stuck.ROUTE: "old"})
            stuck.intercept(state, "PAUSED_PLANNING_BUDGET", "2/2 plan-review calls used")
            request = autoresolver.prepare(state, stuck.STAGE, Path(workspace) / "state.json", None)
        self.assertEqual(("astra", stuck.ROUTE, False, stuck.SCHEMA),
                         (request.role, request.route_role, request.allow_write, request.schema))
        self.assertEqual("openai/gpt-6-sol", state["settings"]["roles"][stuck.ROUTE]["model"])
        self.assertNotIn(stuck.ROUTE, state["sessions"])
        self.assertIn('"status": "PAUSED_PLANNING_BUDGET"', request.prompt)
        self.assertIn(str(Path(workspace) / "state.json"), request.prompt)
        self.assertEqual("autoresolver", autopilot.unit_for(stuck.STAGE))
        self.assertIn(stuck.STAGE, jobs.STAGES)


class DriveTests(unittest.TestCase):
    """The loop end to end with a scripted stage and investigator."""

    def run_loop(self, state, script, investigate=True):
        calls = []

        def dispatch(current, stage):
            calls.append(stage)
            action = script[stage].pop(0)
            return action(current) if callable(action) else action

        def apply(current, stage, outcome):
            if stage == stuck.STAGE:
                return stuck.apply(current, outcome, {}, state["workspace"])
            current.update(next_stage=None, status="TASK_COMPLETE")

        def fail(status):
            def raise_(current):
                current["stages"].append({"stage": current["next_stage"], "rejected": True})
                raise Paused(status, f"{status} at {current['next_stage']}")
            return raise_

        for stage, actions in script.items():
            script[stage] = [fail(a[1:]) if isinstance(a, str) and a.startswith("!") else a for a in actions]
        finished = stuck.drive(state, dispatch, apply=apply, persist=lambda _: None,
                                      active=lambda s: s["status"] == "RUNNING", skip=object(), paused=Paused,
                                      investigate=investigate)
        return finished, calls

    def test_a_stuck_stage_is_investigated_retried_and_finishes(self):
        state = state_for()
        done, calls = self.run_loop(state, {"terra": ["!PAUSED_REPEATED_FAILURE", "built"],
                                            stuck.STAGE: [report()]})
        self.assertEqual(["terra", stuck.STAGE, "terra"], calls)
        self.assertEqual("TASK_COMPLETE", done["status"])

    def test_the_same_problem_twice_pauses_with_the_diagnosis(self):
        state = state_for()
        with self.assertRaises(Paused) as caught:
            self.run_loop(state, {"terra": ["!PAUSED_REPEATED_FAILURE", "!PAUSED_REPEATED_FAILURE"],
                                  stuck.STAGE: [report()]})
        self.assertEqual("PAUSED_REPEATED_FAILURE", caught.exception.status)
        self.assertIn("Investigator (retried): The Planner cites", str(caught.exception))

    def test_an_investigation_that_fails_restores_the_original_pause(self):
        state = state_for()
        with self.assertRaises(Paused) as caught:
            self.run_loop(state, {"terra": ["!PAUSED_REPEATED_FAILURE"], stuck.STAGE: ["!PAUSED_INVALID_OUTPUT"]})
        self.assertEqual("PAUSED_REPEATED_FAILURE", caught.exception.status)
        self.assertIn("could not finish", str(caught.exception))
        self.assertEqual(("PAUSED_REPEATED_FAILURE", "terra"), (state["status"], state["next_stage"]))

    def test_a_pause_set_on_the_state_is_investigated_too(self):
        state = state_for(next_stage="astra_review")
        calls = []

        def dispatch(current, stage):
            calls.append(stage)
            if stage == stuck.STAGE:
                return report()
            current["stages"].append({"stage": stage})
            if calls.count("astra_review") == 1:
                current.update(status="PAUSED_COMPLETION_REVIEW", stop_reason="All required criteria already pass")
                return "paused"
            current.update(status="TASK_COMPLETE", next_stage=None)
            return "done"

        def apply(current, stage, outcome):
            if stage == stuck.STAGE:
                stuck.apply(current, outcome, {}, state["workspace"])

        stuck.drive(state, dispatch, apply=apply, active=lambda s: s["status"] == "RUNNING", skip=object(),
                    paused=Paused, investigate=True)
        self.assertEqual(["astra_review", stuck.STAGE, "astra_review"], calls)
        self.assertEqual("TASK_COMPLETE", state["status"])

    def test_a_pause_reasserted_on_resume_launches_nothing(self):
        # A guard re-raising a known pause before any stage runs is not a new failure to converge.
        state = state_for(status="RUNNING")
        calls = []

        def dispatch(current, stage):
            calls.append(stage)
            raise Paused("PAUSED_BUILDER_RETRY_LIMIT", "Builder lane paused")

        with self.assertRaises(Paused) as caught:
            stuck.drive(state, dispatch, active=lambda s: s["status"] == "RUNNING", skip=object(), paused=Paused,
                        investigate=True)
        self.assertEqual((["terra"], "Builder lane paused"), (calls, str(caught.exception)))
        self.assertNotIn("stuck_investigation", state)

    def test_a_runner_owned_receipt_is_not_a_fresh_attempt(self):
        state = state_for(status="RUNNING")

        def dispatch(current, stage):
            current["stages"].append({"stage": "resolver", "runner_owned": True})
            raise Paused("PAUSED_REPEATED_FAILURE", "known failure re-asserted")

        with self.assertRaises(Paused):
            stuck.drive(state, dispatch, active=lambda s: s["status"] == "RUNNING", skip=object(), paused=Paused,
                        investigate=True)
        self.assertNotIn("stuck_investigation", state)

    def test_an_uncertain_investigation_pauses_like_any_stage(self):
        state = state_for()

        def dispatch(current, stage):
            if stage == stuck.STAGE:
                current["active_stage"] = {"stage": stuck.STAGE}
                raise Paused("PAUSED_UNCERTAIN_STAGE", "provider result unknown")
            current["stages"].append({"stage": stage})
            raise Paused("PAUSED_REPEATED_FAILURE", "rejected again")

        with self.assertRaises(Paused) as caught:
            stuck.drive(state, dispatch, active=lambda s: s["status"] == "RUNNING", skip=object(), paused=Paused,
                        investigate=True)
        self.assertEqual("PAUSED_UNCERTAIN_STAGE", caught.exception.status)
        self.assertEqual(stuck.STAGE, state["next_stage"], "the investigation reruns after reconciliation")
        self.assertIn("stuck_investigation", state)

    def test_without_investigate_the_loop_is_unchanged(self):
        state = state_for()
        with self.assertRaises(Paused) as caught:
            self.run_loop(state, {"terra": ["!PAUSED_REPEATED_FAILURE"]}, investigate=False)
        self.assertEqual("PAUSED_REPEATED_FAILURE at terra", str(caught.exception))
        self.assertNotIn("stuck_investigations", state)


if __name__ == "__main__":
    unittest.main()


class ProbeTests(unittest.TestCase):
    """A retry's diagnosed cause is shown by a probe the runner runs over the cited files only; no model."""

    REJECTED = {"code_refs": ["docs/bugs/cent-drift.json (see the note)"], "summary": "plan"}

    def setup(self):
        import json, subprocess
        workspace = Path(tempfile.mkdtemp(prefix="stuck-probe-ws-"))
        (workspace / "docs" / "bugs").mkdir(parents=True)
        (workspace / "docs" / "bugs" / "cent-drift.json").write_text("{}\n")
        for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *command],
                           cwd=workspace, check=True)
        run_dir = Path(tempfile.mkdtemp(prefix="stuck-probe-run-"))
        (run_dir / "astra_discovery-03.json").write_text(json.dumps(self.REJECTED))
        (run_dir / "secret-uncited.json").write_text("{}")
        state = state_for(workspace=str(workspace), next_stage="astra_discovery")
        self.assertTrue(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "rejected three times"))
        return state, workspace, run_dir

    def apply(self, state, run_dir, workspace, **overrides):
        value = report(**{"evidence_refs": [str(run_dir / "astra_discovery-03.json")], "untestable": "", **overrides})
        autoresolver.apply_job(stuck.STAGE, state, value,
                               {"changed_files": [], "output": str(run_dir / "investigate_stuck-01.json")}, str(workspace))
        return state

    def test_a_probe_over_the_cited_run_file_is_accepted_and_recorded(self):
        state, workspace, run_dir = self.setup()
        probe = "python3 -c \"import json; r = json.load(open('run/astra_discovery-03.json')); assert ' ' in r['code_refs'][0]\""
        self.apply(state, run_dir, workspace, probe=probe, example="Given code_refs 'docs/bugs/cent-drift.json (see "
                   "the note)'; when the runner checked the path; then it was rejected as missing")
        entry = state["stuck_investigations"][0]
        self.assertEqual(("retried", 0, probe), (entry["outcome"], entry["probe_result"]["exit_code"], entry["probe"]))
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertTrue((run_dir / "secret-uncited.json").is_file())  # the real run directory is untouched

    def test_an_archived_output_is_probed_at_its_file_name_as_the_prompt_says(self):
        # Live run 2026-09-29: the rejected output had been moved to archived-sol-01-*/ and a probe
        # that opened run/sol-01.json, as the prompt told it to, found nothing.
        state, workspace, run_dir = self.setup()
        archived = run_dir / "archived-astra_discovery-03-1a2b3c"
        archived.mkdir()
        (run_dir / "astra_discovery-03.json").rename(archived / "astra_discovery-03.json")
        probe = "python3 -c \"import json; r = json.load(open('run/astra_discovery-03.json')); assert ' ' in r['code_refs'][0]\""
        self.apply(state, run_dir, workspace, probe=probe, example="x",
                   evidence_refs=[str(archived / "astra_discovery-03.json")])
        self.assertEqual("retried", state["stuck_investigations"][0]["outcome"])

    def test_two_cited_run_files_with_one_name_are_refused(self):
        state, workspace, run_dir = self.setup()
        (run_dir / "archived").mkdir()
        (run_dir / "archived" / "astra_discovery-03.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "two run files named astra_discovery-03.json"):
            stuck.cited_files(report(evidence_refs=[str(run_dir / "astra_discovery-03.json"),
                                                    str(run_dir / "archived" / "astra_discovery-03.json")]),
                              workspace, run_dir)

    def test_a_probe_that_does_not_show_the_cause_rejects_the_report(self):
        state, workspace, run_dir = self.setup()
        with self.assertRaisesRegex(ValueError, "diagnosed causes' probes did not exit 0"):
            self.apply(state, run_dir, workspace, example="x",
                       probe="python3 -c \"import json; r = json.load(open('run/astra_discovery-03.json')); assert ' ' not in r['code_refs'][0]\"")

    def test_a_probe_sees_only_the_cited_files(self):
        state, workspace, run_dir = self.setup()
        with self.assertRaisesRegex(ValueError, "did not exit 0"):
            self.apply(state, run_dir, workspace, example="x", probe="test -f run/secret-uncited.json")

    def test_cited_files_must_exist(self):
        state, workspace, run_dir = self.setup()
        with self.assertRaisesRegex(ValueError, "not found there"):
            self.apply(state, run_dir, workspace, example="x", untestable="judgement",
                       evidence_refs=["docs/bugs/no-such-note.json"])
        self.assertEqual({"run/astra_discovery-03.json": (run_dir / "astra_discovery-03.json").resolve()},
                         stuck.cited_files(report(evidence_refs=["docs/bugs/cent-drift.json:3",
                                                                 str(run_dir / "astra_discovery-03.json")]),
                                           workspace, run_dir))

    def test_a_retry_needs_an_example_and_exactly_one_of_probe_and_untestable(self):
        state, workspace, run_dir = self.setup()
        with self.assertRaisesRegex(ValueError, "example in plain English"):
            self.apply(state, run_dir, workspace, example=" ", untestable="judgement")
        with self.assertRaisesRegex(ValueError, "exactly one of probe"):
            self.apply(state, run_dir, workspace, example="x")
        with self.assertRaisesRegex(ValueError, "must cite the files"):
            self.apply(state, run_dir, workspace, example="x", untestable="judgement", evidence_refs=[])
