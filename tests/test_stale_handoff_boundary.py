"""The pre-relaunch stale-handoff boundary for issue #3288 (spec-first acceptance tests).

A relaunch that follows an admitted stage-level failure/timeout recovery must compare the
source, task, settings, contract and approved scope bound to the failed attempt's launch
with the current ones, and pause PAUSED_STALE_HANDOFF before any provider process is
launched when they moved on outside the run. The run's own retained Builder work is not
drift. The packet-bound admission pause and its exact reason are guarded unchanged.

Every drift regression builds its original attempt genuinely: the r0 before-snapshot is
written once, before_ref set to it, and the attempt admitted through the real shared
launch admission exactly as the caller tools/autocode.py:640-650 does. That record and
its original before_ref then ride the real stage_recovery.automatically_recover_timed_out_stage,
which only reads the before-snapshot and copies the attempt's captured launch binding onto
the recovery row instead of recomputing one at admission. The stale-pause observation
precreates the relaunch's own directory and watches provider outputs and attempt records,
so the fixture cannot fail its own assertion on a correct refusal, and the recorded
decision is read from the state the core itself saved.
"""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_accepted_source as accepted
import autocode_network_retry as network_retry
import autocode_resolver_recovery as recovery
import autocode_role_launch as role_launch
import autocode_run_records as records
import autocode_source_scope as source_scope
import autocode_stage_recovery as stage_recovery
import autocode_support as support
import autocode_util as util
import autocode_workflow as workflow

BOUND_FILES = {"README.md": "d0", "stock.py": "s0", "tests/test_stock.py": "t0"}
BINDING_KEYS = ("contract_hash", "contract_revision", "task_id", "source_revision", "settings_hash")
PACKET_REASON = "Recovery packet: current source, task, settings, contract or scope changed before admission"
THREAD_STARTED = '{"type":"thread.started","thread_id":"expired-session"}\n'
UNBOUND_REASON = "Stale handoff before relaunch: the failed attempt preserved no launch binding to compare"
CAPACITY_EVENTS = [
    THREAD_STARTED,
    '{"type":"turn.failed","error":"selected model is at capacity"}\n',
]
PERMISSION_EVENTS = [
    THREAD_STARTED,
    '{"type":"permission.requested","message":"permission requested: external_directory (/tmp/scratch)"}\n',
]


def binding_dicts(value):
    """Every dict in ``value`` shaped like a drift binding (recovery ``_binding``)."""
    if isinstance(value, dict):
        if all(key in value for key in BINDING_KEYS):
            yield value
        for item in value.values():
            yield from binding_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from binding_dicts(item)


class StaleHandoffBoundaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self._git("init", "-q")
        self._git("config", "user.name", "Fixture")
        self._git("config", "user.email", "fixture@example.test")
        for path, content in BOUND_FILES.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self._git("add", "-A")
        self._git("commit", "-qm", "fixture")
        self.run = self.root / ".autocode/runs/fixture"
        (self.run / "iterations").mkdir(parents=True)

    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def fresh_state(self):
        body = {"acceptance_criteria": [{"id": "C1", "criterion": "Keep the exact course contract"}]}
        self.h1 = support.digest({"task_id": "T1", "revision": 4, "body": body})
        return {
            "version": 2,
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "status": "RUNNING",
            "phase": "EXECUTING",
            "iteration": 5,
            "sessions": {"builder": "expired-session"},
            "stages": [],
            "settings": {
                "roles": {
                    role: {"model": f"model-{role}", "reasoning_effort": "high"}
                    for role in ("task_planner", "builder", "tester")
                },
                "workflow": {"mode": workflow.FINAL_MODE},
                "milestone_checkpoints": {"max_seconds": 100},
                "budget_origins": {"milestone_max_seconds": "user_explicit"},
            },
            "goal_contract": {
                "task_id": "T1",
                "hash": self.h1,
                "revision": 4,
                "body": body,
            },
            "current_task": {
                "id": "T1",
                "kind": "implement",
                "milestone_id": "M1",
                "acceptance_criteria": ["C1"],
                "affected_paths": ["stock.py"],
            },
            "next_stage": "builder",
        }

    def admit_original(self, state, role="tester", iteration=5, events_lines=None, timed_out=True, exit_code=-15):
        """Build the original attempt through the real shared launch admission.

        Exactly as the caller tools/autocode.py:640-650 does: the r0 before-snapshot is
        written once, before_ref set to it, launch authority bound, and the attempt
        admitted through role_launch.admit_launch.  Nothing re-snapshots over that
        before_ref.  The setup deliberately does not assert on or index the
        ``launch_binding`` capture so the behavioral regressions tolerate its absence
        on the pre-fix base and fail at the relaunch refusal instead.
        """
        base = self.run / f"iterations/{iteration:03d}/{role}-01"
        base.parent.mkdir(parents=True, exist_ok=True)
        before = source_scope.snapshot(self.root, state, base_snapshot=support.snapshot)
        support.atomic_json(base.with_suffix(".before.json"), before)
        base.with_suffix(".jsonl").write_text("".join(events_lines or [THREAD_STARTED]))
        record = {
            "role": role,
            "stage": role,
            "iteration": iteration,
            "output": str(base.with_suffix(".json")),
            "events": str(base.with_suffix(".jsonl")),
            "before_ref": str(base.with_suffix(".before.json")),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "processes": [],
            "task_id": (state.get("current_task") or {}).get("id"),
        }
        goal = state.get("goal_contract") or {}
        if goal:
            record.update(contract_revision=goal["revision"], contract_hash=goal["hash"])
        record["context"] = state.pop("pending_context_metrics", {})
        network_retry.bind_attempt(state, record, before, self.root)
        self.admit(state, record, before, role)
        return record, before

    def admit(self, state, record, before, role):
        role_launch.admit_launch(
            state,
            record,
            before,
            self.root,
            self.run,
            native=None,
            native_isolation=None,
            worker_context={},
            command=[],
            environment={},
            child_environment={},
            visual_context=None,
            original_stage=role,
            report_only=False,
            joint_stage=False,
            fallback_route=None,
            retry_authorization=None,
            controller=None,
        )

    def admit_timeout_recovery(self, state, record, before):
        """Carry the original attempt and its before_ref through the real timeout recovery.

        Deliberately does not assert on or index ``launch_binding`` so the behavioral
        regressions tolerate its absence on the pre-fix base; the copied-not-recomputed
        binding equality lives in its own separately-reported check.
        """
        state["active_stage"] = record
        error = support.Paused("PAUSED_PROVIDER_TIMEOUT", "timed out")
        self.assertTrue(stage_recovery.automatically_recover_timed_out_stage(state, self.run, self.root, error))
        self.assertNotIn("active_stage", state)
        row = state["automatic_timeout_recoveries"][-1]
        saved_before = records.read_json(Path(record["before_ref"]))
        self.assertEqual(
            before["revision"], saved_before["revision"], "the recovery only reads the original before_ref"
        )
        return row

    def admit_capacity_recovery(self, state, record, before):
        """Carry the admitted attempt through the real capacity recovery."""
        state["active_stage"] = record
        error = support.Paused("PAUSED_PROVIDER_CAPACITY", "model at capacity")
        self.assertTrue(stage_recovery.automatically_recover_capacity_stage(state, self.run, self.root, error))
        self.assertNotIn("active_stage", state)
        return state["automatic_capacity_recoveries"][-1]

    def admit_permission_recovery(self, state, record, before):
        """Carry the admitted attempt through the real external-directory denial recovery."""
        state["active_stage"] = record
        error = support.Paused("PAUSED_PROVIDER_UNCERTAIN", "OpenCode denied external_directory")
        self.assertTrue(
            stage_recovery.automatically_recover_external_directory_denial(state, self.run, self.root, error)
        )
        self.assertNotIn("active_stage", state)
        return state["automatic_permission_recoveries"][-1]

    def strip_legacy_keys(self, state, attempt, row):
        """Model an old saved shape: no launch_binding capture, no key on the row/context."""
        attempt.pop("launch_binding", None)
        row.pop("launch_binding", None)
        context = state.get("recovery_context")
        if isinstance(context, dict):
            context.pop("launch_binding", None)

    def admit_relaunch(self, state, role="tester", iteration=6):
        """Admit the failed stage's relaunch at the one shared launch admission."""
        base = self.run / f"iterations/{iteration:03d}/{role}-01"
        base.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "role": role,
            "stage": role,
            "iteration": iteration,
            "output": str(base.with_suffix(".json")),
            "events": str(base.with_suffix(".jsonl")),
            "processes": [],
            "task_id": (state.get("current_task") or {}).get("id"),
        }
        goal = state.get("goal_contract") or {}
        if goal:
            record.update(contract_revision=goal["revision"], contract_hash=goal["hash"])
        self.admit(state, record, source_scope.snapshot(self.root, state, base_snapshot=support.snapshot), role)
        return record

    def assert_stale_pause(self, state, role="tester", iteration=6):
        """The relaunch pauses as stale before any provider spend.

        The fixture precreates the relaunch's own directory before the observation, and the
        observation covers provider output artifacts and attempt records only, so the
        fixture's own setup cannot fail the assertion on a correct stale refusal.
        """
        (self.run / f"iterations/{iteration:03d}").mkdir(parents=True, exist_ok=True)
        stages_before = len(state.get("stages", []))
        artifacts_before = {path for path in (self.run / "iterations").rglob("*") if path.is_file()}
        with self.assertRaises(util.Paused) as caught:
            self.admit_relaunch(state, role=role, iteration=iteration)
        self.assertEqual("PAUSED_STALE_HANDOFF", caught.exception.status)
        self.assertEqual("PAUSED_STALE_HANDOFF", state.get("status"))
        self.assertTrue(state.get("stop_reason"))
        self.assertEqual(stages_before, len(state.get("stages", [])))
        self.assertEqual(artifacts_before, {path for path in (self.run / "iterations").rglob("*") if path.is_file()})
        return caught.exception

    def test_ac1_external_source_drift_pauses_before_relaunch_spend(self):
        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="tester")
        self.admit_timeout_recovery(state, attempt, before)
        (self.root / "README.md").write_text("d1")
        error = self.assert_stale_pause(state, role="tester")
        self.assertNotIn(str(error), accepted.SOURCE_REASONS)
        self.assertIsNone(accepted.resume_action(state))

    def test_ac11_before_admission_drift_pauses_before_relaunch_spend(self):
        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="tester")
        r0 = before["revision"]
        (self.root / "README.md").write_text("d1")
        row = self.admit_timeout_recovery(state, attempt, before)
        self.assertNotEqual(
            r0,
            row["source_revision"],
            "the admission-time snapshot saw the drift; only the preserved launch binding is r0",
        )
        error = self.assert_stale_pause(state, role="tester")
        self.assertNotIn(str(error), accepted.SOURCE_REASONS)
        self.assertIsNone(accepted.resume_action(state))
        preserved = [
            binding
            for binding in binding_dicts(state)
            if binding["source_revision"] == r0 and binding["task_id"] == "T1"
        ]
        self.assertTrue(
            preserved,
            "the compared binding is the failed attempt's preserved launch binding at r0, "
            "not the admission-time snapshot of the drifted source",
        )

    def test_ac2_task_settings_contract_scope_drift_pauses_relaunch(self):
        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="tester")
        self.admit_timeout_recovery(state, attempt, before)
        cases = {
            "task id": lambda s: s["current_task"].update(id="T2"),
            "criteria": lambda s: s["current_task"].update(acceptance_criteria=["C1", "C2"]),
            "settings": lambda s: s["settings"]["roles"]["builder"].update(reasoning_effort="max"),
            "contract": lambda s: s["goal_contract"].update(hash="h2"),
            "milestone": lambda s: s["current_task"].update(milestone_id="M2"),
        }
        for name, drift in cases.items():
            with self.subTest(name):
                drifted = copy.deepcopy(state)
                drift(drifted)
                self.assert_stale_pause(drifted, role="tester")
        admitted = copy.deepcopy(state)
        admitted["settings"]["milestone_checkpoints"]["max_seconds"] = 600
        self.admit_relaunch(admitted, role="tester")

    def test_ac3_own_retained_builder_work_is_not_external_drift(self):
        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="builder")
        (self.root / "tests/test_stock.py").write_text("t1")
        (self.root / "stock_current.py").write_text("s0")
        self.admit_timeout_recovery(state, attempt, before)
        (self.root / "stock_current.py").unlink()
        self.admit_relaunch(state, role="builder")
        (self.root / "stock_current.py").write_text("s0")
        self.admit_relaunch(state, role="builder")
        (self.root / "README.md").write_text("d1")
        self.assert_stale_pause(state, role="builder")

    def test_run_extended_stage_timeout_is_not_settings_drift(self):
        """AutoResolver's own stage-timeout extension after the timeout is not drift; a person's route is."""
        state = self.fresh_state()
        state["settings"]["limits"] = {"stage_timeout_seconds": 3600}
        attempt, before = self.admit_original(state, role="tester")
        self.admit_timeout_recovery(state, attempt, before)
        # What autocode_default_budget_runtime.recover writes once the timed-out attempt shows progress.
        state["settings"]["limits"]["stage_timeout_seconds"] = 7200
        rerouted = copy.deepcopy(state)
        rerouted["settings"]["roles"]["tester"]["model"] = "model-named-by-person"
        self.admit_relaunch(state, role="tester")
        self.assert_stale_pause(rerouted, role="tester")

    def resume_past_stale_pause(self, drift):
        """Pause on ``drift``, resume, and relaunch: --resume-paused keeps the recovery note."""
        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="tester")
        row = self.admit_timeout_recovery(state, attempt, before)
        drift(state, attempt, row)
        self.assert_stale_pause(state, role="tester")
        state["status"] = "RUNNING"
        self.admit_relaunch(state, role="tester", iteration=7)
        discarded = [event for event in state["user_events"] if event.get("kind") == "stale_handoff_discarded"]
        self.assertEqual(1, len(discarded), "the core records that the resumed run discarded the handoff")
        self.assertEqual("006/tester-01", discarded[0]["paused_attempt_id"])

    def test_resume_past_a_source_stale_pause_discards_the_handoff(self):
        """--resume-paused discards the stale handoff, as its explanation says; it is not refused again."""
        self.resume_past_stale_pause(lambda state, attempt, row: (self.root / "README.md").write_text("d1"))

    def test_resume_past_a_legacy_unbound_pause_discards_the_handoff(self):
        """A run saved before launch bindings is never left unable to continue."""
        self.resume_past_stale_pause(self.strip_legacy_keys)

    def test_rejected_relaunch_keeps_its_own_builder_work(self):
        """A relaunched Builder set aside with its edits is still the run's own work for the next attempt."""
        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="builder")
        (self.root / "stock.py").write_text("s1")
        self.admit_timeout_recovery(state, attempt, before)
        relaunch = self.admit_relaunch(state, role="builder", iteration=6)
        base = Path(relaunch["output"]).with_suffix("")
        start = source_scope.snapshot(self.root, state, base_snapshot=support.snapshot)
        support.atomic_json(base.with_suffix(".before.json"), start)
        (self.root / "stock.py").write_text("s2")
        (self.root / "tests/test_stock.py").write_text("t2")
        left = source_scope.snapshot(self.root, state, base_snapshot=support.snapshot)
        support.atomic_json(base.with_suffix(".after.json"), left)
        base.with_suffix(".json").write_text("{}")
        relaunch.update(
            before_ref=str(base.with_suffix(".before.json")), after_ref=str(base.with_suffix(".after.json"))
        )
        records.archive_rejected_stage(state, self.run, relaunch, "report rejected")
        self.assertIn("recovery_context", state, "a rejected attempt does not retire the recovery note")
        self.admit_relaunch(state, role="builder", iteration=7)
        (self.root / "README.md").write_text("d1")
        self.assert_stale_pause(state, role="builder", iteration=8)

    def test_ac4_stale_pause_decision_recorded_in_saved_state(self):
        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="tester")
        r0 = before["revision"]
        (self.root / "README.md").write_text("d1")
        self.admit_timeout_recovery(state, attempt, before)
        self.assert_stale_pause(state, role="tester")
        saved = json.loads((self.run / "state.json").read_text())
        self.assertEqual("PAUSED_STALE_HANDOFF", saved["status"])
        self.assertIn("source", str(saved["stop_reason"]).lower())
        decisions = [event for event in saved.get("user_events", []) if event.get("kind") == "stale_handoff_drift"]
        self.assertTrue(decisions, "the core records its drift decision in the saved state")
        decision = decisions[-1]
        self.assertEqual(["source"], decision["changed"])
        compared = decision["compared_binding"]
        self.assertEqual(r0, compared["source_revision"])
        self.assertEqual("T1", compared["task_id"])
        self.assertEqual(self.h1, compared["contract_hash"])
        self.assertEqual(attempt["launch_binding"]["settings_hash"], compared["settings_hash"])
        self.assertEqual(["M1"], compared["scope"]["milestones"])
        self.assertEqual(["C1"], compared["scope"]["criteria"])

    def test_launch_binding_copied_not_recomputed(self):
        """Capture-shape check: the recovery copies the launch binding, never recomputes it."""
        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="tester")
        self.assertIn("launch_binding", attempt)
        row = self.admit_timeout_recovery(state, attempt, before)
        self.assertEqual(
            attempt["launch_binding"],
            row["launch_binding"],
            "the recovery copies the launch binding from the attempt record, never recomputing it at admission",
        )
        self.assertEqual(before["revision"], row["launch_binding"]["source_revision"])
        self.assertEqual("T1", row["launch_binding"]["task_id"])
        self.assertEqual(self.h1, row["launch_binding"]["contract_hash"])
        self.assertTrue(row["launch_binding"]["settings_hash"])
        saved_before = records.read_json(Path(attempt["before_ref"]))
        self.assertEqual(before["revision"], saved_before["revision"])

    def test_legacy_unbound_attempt_settings_scope_drift_pauses(self):
        """A legacy attempt with no captured launch_binding must refuse on settings and scope drift."""
        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="tester")
        attempt.pop("launch_binding", None)
        state["settings"]["roles"]["builder"]["reasoning_effort"] = "max"
        state["current_task"]["milestone_id"] = "M2"
        self.admit_timeout_recovery(state, attempt, before)
        self.assert_stale_pause(state, role="tester")
        saved = json.loads((self.run / "state.json").read_text())
        self.assertEqual("PAUSED_STALE_HANDOFF", saved["status"])
        decisions = [event for event in saved.get("user_events", []) if event.get("kind") == "stale_handoff_drift"]
        self.assertTrue(decisions, "the core records its drift decision in the saved state")
        decision = decisions[-1]
        self.assertIn("settings", decision["changed"])
        self.assertIn("scope", decision["changed"])

    def test_captured_binding_missing_scope_drift_pauses(self):
        """A captured binding dictionary lacking 'scope' must refuse on settings and scope drift."""
        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="tester")
        binding = attempt.get("launch_binding")
        if isinstance(binding, dict):
            binding.pop("scope", None)
        state["settings"]["roles"]["builder"]["reasoning_effort"] = "max"
        state["current_task"]["milestone_id"] = "M2"
        self.admit_timeout_recovery(state, attempt, before)
        self.assert_stale_pause(state, role="tester")
        saved = json.loads((self.run / "state.json").read_text())
        self.assertEqual("PAUSED_STALE_HANDOFF", saved["status"])
        decisions = [event for event in saved.get("user_events", []) if event.get("kind") == "stale_handoff_drift"]
        self.assertTrue(decisions, "the core records its drift decision in the saved state")
        decision = decisions[-1]
        self.assertIn("settings", decision["changed"])
        self.assertIn("scope", decision["changed"])

    def test_ac6_legacy_saved_recovery_without_launch_binding_pauses_before_relaunch_spend(self):
        """An old-runtime timeout recovery row (no launch_binding) still governs the relaunch."""
        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="tester")
        r0 = before["revision"]
        row = self.admit_timeout_recovery(state, attempt, before)
        self.strip_legacy_keys(state, attempt, row)
        (self.root / "README.md").write_text("d1")
        error = self.assert_stale_pause(state, role="tester")
        self.assertNotIn(str(error), accepted.SOURCE_REASONS)
        self.assertIsNone(accepted.resume_action(state))
        saved = json.loads((self.run / "state.json").read_text())
        decisions = [event for event in saved.get("user_events", []) if event.get("kind") == "stale_handoff_drift"]
        self.assertTrue(decisions, "the core records its drift decision in the saved state")
        decision = decisions[-1]
        self.assertIn("source", decision["changed"])
        compared = decision["compared_binding"]
        self.assertEqual(r0, compared["source_revision"], "authority is the original r0 before-snapshot revision")
        self.assertEqual("T1", compared["task_id"])
        self.assertEqual(self.h1, compared["contract_hash"])
        self.assertIsNone(compared.get("settings_hash"), "no original evidence proves settings; none is blessed")
        self.assertIsNone(compared.get("scope"), "no original evidence proves scope; none is blessed")

    def test_ac14_legacy_capacity_recovery_without_launch_binding_pauses_before_relaunch_spend(self):
        """An old-runtime capacity recovery row (no launch_binding) still governs the relaunch."""
        state = self.fresh_state()
        attempt, before = self.admit_original(
            state, role="tester", events_lines=CAPACITY_EVENTS, timed_out=False, exit_code=1
        )
        row = self.admit_capacity_recovery(state, attempt, before)
        self.strip_legacy_keys(state, attempt, row)
        (self.root / "README.md").write_text("d1")
        error = self.assert_stale_pause(state, role="tester")
        self.assertNotIn(str(error), accepted.SOURCE_REASONS)
        self.assertIsNone(accepted.resume_action(state))

    def test_ac15_legacy_permission_recovery_without_launch_binding_pauses_before_relaunch_spend(self):
        """An old-runtime permission recovery row (no launch_binding) still governs the relaunch."""
        state = self.fresh_state()
        state["settings"]["workflow"]["mode"] = "standard"  # no reviewer routing to approve here
        attempt, before = self.admit_original(
            state, role="tester", events_lines=PERMISSION_EVENTS, timed_out=False, exit_code=1
        )
        row = self.admit_permission_recovery(state, attempt, before)
        self.strip_legacy_keys(state, attempt, row)
        (self.root / "README.md").write_text("d1")
        error = self.assert_stale_pause(state, role="tester")
        self.assertNotIn(str(error), accepted.SOURCE_REASONS)
        self.assertIsNone(accepted.resume_action(state))

    def test_ac17_legacy_recovery_missing_attempt_record_refuses_unbound(self):
        """A legacy row whose failed attempt left no provable evidence is refused unbound."""
        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="tester")
        row = self.admit_timeout_recovery(state, attempt, before)
        self.strip_legacy_keys(state, attempt, row)
        row_id = row.get("attempt_id")
        state["stages"] = [r for r in state.get("stages", []) if records.attempt_id(r) != row_id]
        error = self.assert_stale_pause(state, role="tester")
        self.assertEqual(UNBOUND_REASON, str(error))
        saved = json.loads((self.run / "state.json").read_text())
        decisions = [event for event in saved.get("user_events", []) if event.get("kind") == "stale_handoff_drift"]
        self.assertTrue(decisions, "the core records its refusal in the saved state")
        self.assertEqual(["launch_binding"], decisions[-1]["changed"])
        self.assertIsNone(accepted.resume_action(saved))

    def test_ac16_unrecognized_recovery_context_keeps_returning_early(self):
        """A recovery_context of an unrecognized kind never broadens the brake."""
        state = self.fresh_state()
        state["recovery_context"] = {"attempt_id": "004/ghost-01", "stage": "tester", "kind": "unrelated"}
        (self.root / "README.md").write_text("d1")
        self.admit_relaunch(state, role="tester")

    def test_ac18_report_only_and_dry_run_relaunches_return_without_pause(self):
        """Report-only and dry-run relaunches keep their exemption ahead of the brake."""
        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="tester")
        self.admit_timeout_recovery(state, attempt, before)
        (self.root / "README.md").write_text("d1")
        for flag in ("report_only", "dry_run"):
            with self.subTest(flag):
                record = {
                    "role": "tester",
                    "stage": "tester",
                    "iteration": 6,
                    "output": str(self.run / "iterations/006/tester-01.json"),
                    "events": str(self.run / "iterations/006/tester-01.jsonl"),
                    "processes": [],
                    flag: True,
                }
                self.admit(state, record, source_scope.snapshot(self.root, state), "tester")

    def test_ac19_recovery_packet_builder_relaunch_returns_without_pause(self):
        """A builder dispatch whose held request carries a recovery_packet defers to that path."""
        import autocode_stale_handoff as stale_handoff

        state = self.fresh_state()
        attempt, before = self.admit_original(state, role="builder")
        self.admit_timeout_recovery(state, attempt, before)
        (self.root / "README.md").write_text("d1")
        state["repair_plan"] = {
            "tasks": [{"id": "T1"}],
            "recovery_packet": {"pointer": "recovery-packet.json"},
        }
        record = {
            "role": "builder",
            "stage": "builder",
            "iteration": 6,
            "output": str(self.run / "iterations/006/builder-01.json"),
            "events": str(self.run / "iterations/006/builder-01.jsonl"),
            "processes": [],
        }
        stale_handoff.admit_relaunch(state, record, self.root, self.run)


class PacketPauseGuardTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        self.run = self.root / ".autocode/runs/owned"
        self.run.mkdir(parents=True)
        self.source = self.root / "app.py"
        self.source.write_text("def answer():\n    return 2\n")
        self.events = self.run / "validator.jsonl"
        self.events.write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "id": "check",
                        "command": "python -m unittest test_app",
                        "exit_code": 1,
                        "aggregated_output": "AssertionError: 2 != 3\nRan 1 test in 0.002s\nFAILED",
                    },
                }
            )
            + "\n"
        )
        self.output = self.run / "review.json"
        self.output.write_text('{"status":"REWORK"}')
        self.validator = self.run / "validator.json"
        self.validator.write_text('{"verdict":"FAIL"}')
        self.state = {
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "settings": {"roles": {"builder": {"model_pinned": True}}},
            "goal_contract": {
                "task_id": "approved-job",
                "hash": "h",
                "revision": 1,
                "body": {"acceptance_criteria": [{"id": "C1", "criterion": "Return 3"}]},
            },
            "current_task": {
                "id": "T1",
                "kind": "implement",
                "milestone_id": "M1",
                "acceptance_criteria": ["C1"],
                "affected_paths": ["app.py"],
            },
            "stages": [{"stage": "tester", "output": str(self.validator), "events": str(self.events)}],
            "validation": {
                "task_id": "T1",
                "contract_hash": "h",
                "contract_revision": 1,
                "source_revision": "s1",
                "reviewer_role": "tester",
                "output": str(self.validator),
                "verdict": "FAIL",
                "checks": [{"command": "python -m unittest test_app", "exit_code": 1, "evidence_ref": "event:check"}],
            },
        }
        self.request = {
            "source_revision": "s1",
            "source_output": str(self.output),
            "evidence_hashes": {
                str(self.output): util.file_hash(self.output),
                str(self.events): util.file_hash(self.events),
            },
        }
        self.state["resolution_request"] = self.request
        self.snapshot = patch.object(util, "snapshot", return_value={"revision": "s1", "files": {"app.py": "hash"}})
        self.snapshot.start()
        self.addCleanup(self.snapshot.stop)
        recovery.prepare_resolution(
            self.state,
            {"summary": "Wrong answer"},
            {"stage": "review_completion", "output": str(self.output), "source_revision": "s1"},
        )

    def test_ac5_packet_pause_keeps_the_saved_status_and_reason(self):
        self.state["settings"]["roles"]["builder"]["model_pinned"] = False
        with self.assertRaises(util.Paused) as caught:
            recovery.admit_dispatch(self.state, {"stage": "resolve"}, self.root, self.run)
        self.assertEqual("PAUSED_STALE_HANDOFF", caught.exception.status)
        self.assertEqual(PACKET_REASON, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
