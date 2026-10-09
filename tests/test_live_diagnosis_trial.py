"""Offline tests for the astra_diagnose live-validation trial driver.

These run for free, with no model calls, and prove the trial script's own
code: the seeded defect's regression proof, and the mechanical repeat-count
escalation. They do not exercise astra_diagnose itself -- that mechanic
(admission -> astra_diagnose -> a model's retry recommendation ->
re-dispatch) is already proven offline, through the real CLI, in
tests/test_resolver_runtime.py's OperationalDiagnosisTests. What is new here
is specific to live_diagnosis_trial.py: its fixture harness must not crash,
and it must not misattribute a non-target pause to astra_diagnose's trigger.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import contextlib

import live_diagnosis_trial as trial  # noqa: E402
from autopilot_testkit import Bundle  # noqa: E402


class SeedIntegrityTests(unittest.TestCase):
    """The plan requires a labeled, reproducible defect: seed fails, reference passes."""

    def test_prove_seed_and_reference_passes_on_the_real_fixture(self):
        bundle = Bundle("DIAGNOSIS-TRIAL-SELFTEST")
        trial.prove_seed_and_reference(bundle)  # raises TrialError on failure
        bundle.finish(trial.base.scenarios.PASS, "seed/reference regression proof holds")

    def test_seed_module_actually_fails_its_own_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "convert.py").write_text(trial.SEED_MODULE)
            (root / "test_convert.py").write_text(trial.SEED_TEST)
            proc = trial.subprocess.run([sys.executable, "test_convert.py"], capture_output=True, text=True, cwd=root)
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("32", proc.stderr + proc.stdout)

    def test_reference_module_passes_the_seed_test_unmodified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "convert.py").write_text(trial.REFERENCE_MODULE)
            (root / "test_convert.py").write_text(trial.SEED_TEST)
            proc = trial.subprocess.run([sys.executable, "test_convert.py"], capture_output=True, text=True, cwd=root)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_ground_truth_names_the_actual_single_character_defect(self):
        # The comparison file this script writes is read by a human, so the
        # ground truth it states must actually match the seeded source.
        self.assertIn("+ 31", trial.SEED_MODULE)
        self.assertIn("31", trial.GROUND_TRUTH)
        self.assertIn("32", trial.GROUND_TRUTH)


class MechanicalEscalationTests(unittest.TestCase):
    """escalate_to_repeat_threshold: real evidence, zero model calls."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name)
        self.bundle = Bundle("DIAGNOSIS-TRIAL-ESCALATION-SELFTEST")

    def write_state(self, state):
        (self.run_dir / "state.json").write_text(json.dumps(state))

    def test_requires_a_real_terra_report_repair_identity(self):
        self.write_state({"pending_report_repair": None, "stages": [], "failure_history": {}})
        with self.assertRaisesRegex(trial.TrialError, r"no real rejected Builder \(terra\) report"):
            trial.escalate_to_repeat_threshold(self.run_dir, self.bundle)

    def test_refuses_a_non_terra_identity(self):
        # A genuine rejection at a different stage must never be treated as
        # this trial's terra-scoped target, even if it has a failure_key.
        self.write_state(
            {
                "pending_report_repair": {"original": {"stage": "requirements_gather", "failure_key": "k1"}},
                "stages": [],
                "failure_history": {},
            }
        )
        with self.assertRaisesRegex(trial.TrialError, r"no real rejected Builder \(terra\) report"):
            trial.escalate_to_repeat_threshold(self.run_dir, self.bundle)

    def test_raises_a_real_single_occurrence_to_the_policy_threshold(self):
        self.write_state(
            {
                "pending_report_repair": {"original": {"stage": "terra", "role": "terra", "failure_key": "k1"}},
                "stages": [{"stage": "terra", "failure_key": "k1"}],
                "failure_history": {"k1": {"count": 1, "last_error": "Missing summary field"}},
                "status": "RUNNING",
            }
        )
        trial.escalate_to_repeat_threshold(self.run_dir, self.bundle)
        saved = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(3, saved["failure_history"]["k1"]["count"])
        self.assertEqual(3, saved["failure_history"]["k1"]["streak"])
        self.assertEqual("PAUSED_REPEATED_FAILURE", saved["status"])
        # The real error text is preserved; escalation only touches the count.
        self.assertEqual("Missing summary field", saved["failure_history"]["k1"]["last_error"])

    def test_falls_back_to_a_stages_row_when_report_repair_already_cleared(self):
        self.write_state(
            {
                "pending_report_repair": None,
                "stages": [{"stage": "terra", "failure_key": "k2"}],
                "failure_history": {"k2": {"count": 1}},
            }
        )
        trial.escalate_to_repeat_threshold(self.run_dir, self.bundle)
        saved = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(3, saved["failure_history"]["k2"]["count"])


class JudgeFinalVerdictTests(unittest.TestCase):
    """Negative controls for the false-PASS defect: the verdict must score
    against a frozen test file, never the delivered (editable) workspace copy."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.run_dir = root / "run"
        self.run_dir.mkdir()
        (self.run_dir / "state.json").write_text(json.dumps({"status": "TASK_COMPLETE"}))
        self.frozen = root / "frozen_test_convert.py"
        self.frozen.write_text(trial.SEED_TEST)

    def test_a_neutered_delivered_test_does_not_mask_the_real_bug(self):
        (self.project / "convert.py").write_text(trial.SEED_MODULE)  # bug still present
        (self.project / "test_convert.py").write_text("pass\n")  # neutered by the candidate
        verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
        self.assertEqual("FAIL", trial.verdict_result(verdict))

    def test_a_genuine_fix_passes_the_frozen_test(self):
        (self.project / "convert.py").write_text(trial.REFERENCE_MODULE)
        (self.project / "test_convert.py").write_text(trial.SEED_TEST)
        verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
        self.assertEqual(0, verdict["independent_test_exit"])

    def test_a_missing_convert_module_is_reported_not_silently_passed(self):
        (self.project / "test_convert.py").write_text(trial.SEED_TEST)
        verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
        self.assertIsNone(verdict["independent_test_exit"])
        self.assertIn("missing", verdict["note"])

    def test_a_module_level_system_exit_does_not_produce_a_false_pass(self):
        # A bare exit code of 0 is not proof anything was checked: this
        # module never even defines celsius_to_fahrenheit.
        (self.project / "convert.py").write_text("raise SystemExit(0)\n")
        (self.project / "test_convert.py").write_text(trial.SEED_TEST)
        verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
        self.assertFalse(verdict["verified_pass"])
        self.assertEqual("FAIL", trial.verdict_result(verdict))

    def test_a_modified_protected_test_fails_even_with_correct_code(self):
        (self.project / "convert.py").write_text(trial.REFERENCE_MODULE)  # genuinely correct
        (self.project / "test_convert.py").write_text("pass\n")  # but the protected test was edited
        verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
        self.assertEqual("modified", verdict["protected_test_status"])
        self.assertEqual("FAIL", trial.verdict_result(verdict))

    def test_an_unmodified_protected_test_with_correct_code_passes(self):
        (self.project / "convert.py").write_text(trial.REFERENCE_MODULE)
        (self.project / "test_convert.py").write_text(trial.SEED_TEST)
        verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
        self.assertEqual("unmodified", verdict["protected_test_status"])
        self.assertEqual("PASS", trial.verdict_result(verdict))

    def test_grading_a_hung_module_times_out_rather_than_stalling(self):
        (self.project / "convert.py").write_text(
            "import time\nprint('before timeout', flush=True)\n"
            "def celsius_to_fahrenheit(celsius):\n    time.sleep(3600)\n"
        )
        (self.project / "test_convert.py").write_text(trial.SEED_TEST)
        with patch.object(trial, "GRADING_SUBPROCESS_TIMEOUT", 1):
            verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
        self.assertTrue(verdict["timed_out"])
        self.assertIn("before timeout", verdict["independent_test_tail"])
        json.dumps(verdict)
        self.assertEqual("FAIL", trial.verdict_result(verdict))

    def test_stdout_markers_skips_and_import_failures_cannot_forge_pass(self):
        candidates = [
            "import unittest\nraise unittest.SkipTest('skip everything')\n",
            "raise ImportError('broken dependency')\n",
            "import os\nos._exit(0)\n",
            "print('TRIAL_RESULT_MARKER tests_run=0 failures=0 errors=0 expected=0', flush=True)\nimport os\nos._exit(0)\n",
            "print('TRIAL_RESULT_MARKER tests_run=3 failures=0 errors=0 expected=3', flush=True)\nimport os\nos._exit(0)\n",
            'print(\'{"contract_valid": true, "values": [32, 212, 98.6]}\', flush=True)\nimport os\nos._exit(0)\n',
            trial.SEED_MODULE,
        ]
        (self.project / "test_convert.py").write_text(trial.SEED_TEST)
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                (self.project / "convert.py").write_text(candidate)
                verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
                self.assertFalse(verdict["verified_pass"])
                self.assertEqual("FAIL", trial.verdict_result(verdict))

    def test_signature_and_numeric_contract_are_enforced(self):
        candidates = [
            "celsius_to_fahrenheit = 32\n",
            "def celsius_to_fahrenheit(c): return c * 9 / 5 + 32\n",
            "def celsius_to_fahrenheit(celsius=0): return celsius * 9 / 5 + 32\n",
            "def celsius_to_fahrenheit(*celsius): return celsius[0] * 9 / 5 + 32\n",
            "def celsius_to_fahrenheit(celsius): return float('nan')\n",
            "def celsius_to_fahrenheit(celsius): return '32'\n",
            "def celsius_to_fahrenheit(celsius): return True\n",
            "def celsius_to_fahrenheit(celsius):\n import unittest\n raise unittest.SkipTest('skip')\n",
        ]
        (self.project / "test_convert.py").write_text(trial.SEED_TEST)
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                (self.project / "convert.py").write_text(candidate)
                self.assertEqual(
                    "FAIL", trial.verdict_result(trial.judge_final_verdict(self.project, self.run_dir, self.frozen))
                )

    def test_stdlib_numeric_results_preserve_the_frozen_test_contract(self):
        candidates = [
            "from fractions import Fraction\ndef celsius_to_fahrenheit(celsius): return Fraction(9, 5) * celsius + 32\n",
            "from decimal import Decimal\ndef celsius_to_fahrenheit(celsius): return Decimal.from_float(celsius * 9 / 5 + 32)\n",
        ]
        (self.project / "test_convert.py").write_text(trial.SEED_TEST)
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                (self.project / "convert.py").write_text(candidate)
                reference = subprocess.run(
                    [sys.executable, "test_convert.py"], cwd=self.project, capture_output=True, text=True, timeout=10
                )
                self.assertEqual(0, reference.returncode, reference.stdout + reference.stderr)
                self.assertEqual(
                    "PASS", trial.verdict_result(trial.judge_final_verdict(self.project, self.run_dir, self.frozen))
                )

    def test_numeric_tolerance_does_not_exceed_the_frozen_test_contract(self):
        (self.project / "test_convert.py").write_text(trial.SEED_TEST)
        (self.project / "convert.py").write_text(
            "def celsius_to_fahrenheit(celsius): return 98.609 if celsius == 37 else celsius * 9 / 5 + 32\n"
        )
        reference = subprocess.run(
            [sys.executable, "test_convert.py"], cwd=self.project, capture_output=True, text=True, timeout=10
        )
        self.assertNotEqual(0, reference.returncode)
        self.assertIn("FAIL", reference.stderr)
        self.assertEqual(
            "FAIL", trial.verdict_result(trial.judge_final_verdict(self.project, self.run_dir, self.frozen))
        )

    def test_reference_with_noisy_non_utf8_output_still_passes(self):
        (self.project / "test_convert.py").write_text(trial.SEED_TEST)
        (self.project / "convert.py").write_text(
            "import os\nos.write(1, b'noise\\xff')\nos.write(2, b'warning\\xff')\n" + trial.REFERENCE_MODULE
        )
        verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
        self.assertEqual("PASS", trial.verdict_result(verdict))
        self.assertIn("noise", verdict["independent_test_tail"])
        self.assertIn("warning", verdict["independent_test_stderr_tail"])
        json.dumps(verdict)

    def test_modified_frozen_bytes_and_missing_protected_test_fail(self):
        (self.project / "convert.py").write_text(trial.REFERENCE_MODULE)
        self.assertEqual(
            "FAIL", trial.verdict_result(trial.judge_final_verdict(self.project, self.run_dir, self.frozen))
        )
        self.frozen.write_text("pass\n")
        (self.project / "test_convert.py").write_text("pass\n")
        verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
        self.assertEqual("invalid_frozen_test", verdict["protected_test_status"])
        self.assertEqual("FAIL", trial.verdict_result(verdict))

    def test_grader_kills_descendants_on_timeout_and_success(self):
        (self.project / "test_convert.py").write_text(trial.SEED_TEST)
        for hang in (True, False):
            with self.subTest(hang=hang):
                pidfile = self.project / "child.pid"
                pidfile.unlink(missing_ok=True)
                candidate = (
                    "import subprocess, sys\n"
                    "from pathlib import Path\n"
                    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                    f"Path({str(pidfile)!r}).write_text(str(child.pid) + '\\n')\n" + trial.REFERENCE_MODULE
                )
                if hang:
                    candidate += "import time\ntime.sleep(60)\n"
                (self.project / "convert.py").write_text(candidate)
                # Expire the fake clock after the real descendant publishes its
                # complete PID. A 0.3s wall deadline could kill the interpreter
                # before it spawned anything, leaving cleanup untested under load.
                real_clock = trial.grader_process.time
                clock = Mock(wraps=real_clock)
                deadline_started, jump = False, 0

                def now():
                    nonlocal deadline_started, jump
                    observed = real_clock.monotonic()
                    if deadline_started and hang and pidfile.exists() and pidfile.read_text().endswith("\n"):
                        jump = trial.GRADING_SUBPROCESS_TIMEOUT + 1
                    deadline_started = True
                    return observed + jump

                clock.monotonic.side_effect = now
                with patch.object(trial.grader_process, "time", clock):
                    verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
                self.assertEqual(hang, bool(jump))
                self.assertEqual(hang, verdict["timed_out"])
                pid = int(pidfile.read_text())
                try:
                    # A killed orphan may briefly remain a zombie until reaped.
                    status = subprocess.run(
                        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
                    ).stdout.strip()
                    self.assertTrue(not status or status.startswith("Z"), status)
                finally:
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(pid, signal.SIGKILL)

    def test_grading_obeys_the_shared_deadline(self):
        (self.project / "convert.py").write_text(trial.REFERENCE_MODULE)
        with self.assertRaisesRegex(trial.TrialError, "wall-clock budget"):
            trial.judge_final_verdict(self.project, self.run_dir, self.frozen, trial.time.monotonic() - 1)


class SharedDeadlineTests(unittest.TestCase):
    """Negative control for the renewed-timeout defect: one deadline shared
    across every phase, not a fresh budget passed to each subprocess call."""

    def test_drive_step_passes_remaining_time_not_the_full_budget(self):
        recorded = {}

        def fake_invoke(cmd, env, cwd, timeout):
            recorded["timeout"] = timeout
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            (project / ".autocode").mkdir()
            (project / ".autocode" / "runs").mkdir()
            run_dir = project / ".autocode" / "runs" / "fixture"
            run_dir.mkdir()
            (run_dir / "state.json").write_text(json.dumps({"status": "TASK_COMPLETE"}))
            bundle = Bundle("DIAGNOSIS-TRIAL-DEADLINE-SELFTEST")
            profile = {"provider": "fixture"}
            # 1200s budget, but only ~1 second of it remains: the per-call
            # timeout passed to subprocess must reflect that, not renew to 1200.
            deadline = trial.time.monotonic() + 1.0
            with (
                patch.object(trial.base, "invoke", fake_invoke),
                patch.object(trial.base, "_discover_run_dir", return_value=run_dir),
            ):
                trial.drive_to_first_verdict(project, root, profile, trial.TrialBudget(40, deadline), bundle)
        self.assertLess(recorded["timeout"], 1.5)

    def test_step_refuses_to_launch_once_the_shared_deadline_has_passed(self):
        bundle = Bundle("DIAGNOSIS-TRIAL-DEADLINE-SELFTEST-2")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with self.assertRaisesRegex(trial.TrialError, "wall-clock budget exceeded"):
                trial.drive_to_first_verdict(
                    project,
                    project,
                    {"provider": "fixture"},
                    trial.TrialBudget(40, trial.time.monotonic() - 1.0),
                    bundle,
                )

    def test_timeout_bytes_are_logged_and_raised_as_trial_error(self):
        bundle = Mock()
        budget = trial.TrialBudget(3, trial.time.monotonic() + 1)
        error = subprocess.TimeoutExpired("autocode", 1, output=b"printed\xff", stderr=b"error")
        with patch.object(trial.base, "invoke", side_effect=error):
            with self.assertRaises(trial.TrialError):
                budget.invoke("start", [], {}, Path("."), bundle)
        self.assertEqual(1, budget.used)
        event = bundle.log.call_args
        self.assertEqual("cli_timeout", event.args[0])
        self.assertIsInstance(event.kwargs["stdout_tail"], str)
        json.dumps(event.kwargs)

    def test_one_cli_budget_covers_first_attempt_admission_diagnosis_and_retry(self):
        budget = trial.TrialBudget(3, trial.time.monotonic() + 30)
        with patch.object(trial.base, "invoke", return_value=subprocess.CompletedProcess([], 0, "", "")) as invoke:
            for kind in ("start", "admit", "diagnose"):
                budget.invoke(kind, [], {}, Path("."), Mock())
            with self.assertRaisesRegex(trial.TrialError, "invocation budget"):
                budget.invoke("retry", [], {}, Path("."), Mock())
        self.assertEqual(3, invoke.call_count)


class DiagnosisOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "diagnosis.json"
        self.output.write_text(
            json.dumps({"diagnosis": "The report is missing summary", "recommendation": {"action": "retry"}})
        )
        self.request = {"blocker_id": "b1", "failure_key": "f1", "original_stage": "terra"}
        self.admitted = {
            "status": "RUNNING",
            "next_stage": "astra_diagnose",
            "diagnosis_request": self.request,
            "stages": [],
        }
        self.diagnosis_row = {"stage": "astra_diagnose", "output": str(self.output)}
        self.receipt = {
            "stage": "resolver",
            "runner_owned": True,
            "decision": {"action": "retry"},
            "receipt": {"blocker_id": "b1", "action": "retry", "in_scope_reason": "proposal within boundaries"},
        }

    def diagnose(self, states, budget=None):
        with (
            patch.object(trial.base, "load_state", side_effect=states),
            patch.object(trial.base, "invoke", return_value=subprocess.CompletedProcess([], 0, "", "")) as invoke,
            patch.object(trial.base, "autocode_command", return_value=["fake-cli"]),
            patch.object(trial.base, "_serve_gate", return_value=False),
            patch.object(trial.base, "_progressed", return_value=True),
        ):
            result = trial.diagnose_and_retry(
                self.root,
                self.root,
                {"provider": "fake"},
                self.root,
                budget or trial.TrialBudget(10, trial.time.monotonic() + 30),
                Mock(),
            )
        return result, invoke.call_count

    def test_model_retry_without_policy_acceptance_never_dispatches(self):
        for rows in ([self.diagnosis_row], [self.receipt, self.diagnosis_row]):
            with self.subTest(rows=rows):
                rejected = {
                    "status": "PAUSED_REPEATED_FAILURE",
                    "stages": rows,
                    "diagnosis_request": self.request,
                    "failure_history": {"f1": {}},
                }
                result, calls = self.diagnose([self.admitted, rejected])
                self.assertFalse(result["policy_accepted_retry"])
                self.assertFalse(result["retry_dispatched"])
                self.assertEqual(2, calls)

    def test_policy_retry_needs_a_new_original_stage_record(self):
        accepted = {"status": "TASK_COMPLETE", "stages": [self.receipt, self.diagnosis_row]}
        result, calls = self.diagnose([self.admitted, accepted, accepted, accepted])
        self.assertTrue(result["policy_accepted_retry"])
        self.assertFalse(result["retry_dispatched"])
        self.assertEqual(2, calls)

    def test_accepted_retry_and_observed_dispatch_are_recorded_separately(self):
        accepted = {"status": "RUNNING", "next_stage": "terra", "stages": [self.receipt, self.diagnosis_row]}
        final = {
            "status": "TASK_COMPLETE",
            "stages": accepted["stages"] + [{"stage": "terra", "output": "new-builder-report.json"}],
        }
        result, calls = self.diagnose([self.admitted, accepted, accepted, final, final, final])
        self.assertTrue(result["policy_accepted_retry"])
        self.assertTrue(result["retry_dispatched"])
        self.assertEqual(3, calls)

    def test_admission_receipt_or_wrong_identity_cannot_stand_in_for_retry_acceptance(self):
        for receipt in (
            dict(self.receipt, receipt=dict(self.receipt["receipt"], blocker_id="other")),
            dict(self.receipt, receipt=dict(self.receipt["receipt"], in_scope_reason="invalid proposal")),
        ):
            state = {"status": "RUNNING", "next_stage": "terra", "stages": [receipt, self.diagnosis_row]}
            result, calls = self.diagnose([self.admitted, state])
            self.assertFalse(result["policy_accepted_retry"])
            self.assertEqual(2, calls)
        admitted = dict(self.admitted, stages=[self.receipt])
        state = {"status": "RUNNING", "next_stage": "terra", "stages": [self.receipt, self.diagnosis_row]}
        result, calls = self.diagnose([admitted, state])
        self.assertFalse(result["policy_accepted_retry"])
        self.assertEqual(2, calls)

    def test_retry_does_not_renew_the_budget_used_before_diagnosis(self):
        budget = trial.TrialBudget(3, trial.time.monotonic() + 30)
        budget.used = 1  # first driving invocation already spent
        accepted = {"status": "RUNNING", "next_stage": "terra", "stages": [self.receipt, self.diagnosis_row]}
        with self.assertRaisesRegex(trial.TrialError, "invocation budget"):
            self.diagnose([self.admitted, accepted, accepted], budget)
        self.assertEqual(3, budget.used)


class MainOutcomeTests(unittest.TestCase):
    def run_main(self, outcome="complete", diagnosis=None, error=None, code_pass=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            evidence = root / "evidence"
            evidence.mkdir()
            bundle = Mock(dir=evidence)
            first = {"outcome": outcome, "state": {"status": "PAUSED_REPEATED_FAILURE"}, "run_dir": root}
            with (
                patch.object(trial, "Bundle", return_value=bundle),
                patch.object(trial, "prove_seed_and_reference"),
                patch.object(trial.base, "make_workspace", return_value=project),
                patch.object(trial.subprocess, "run"),
                patch.object(trial, "source_revision", return_value={}),
                patch.object(trial, "drive_to_first_verdict", return_value=first, side_effect=error) as drive,
                patch.object(trial, "diagnose_and_retry", return_value=diagnosis) as diagnose,
                patch.object(
                    trial,
                    "judge_final_verdict",
                    return_value={"protected_test_status": "unmodified", "verified_pass": code_pass},
                ) as judge,
                patch("builtins.print"),
            ):
                code = trial.main(["--workspace", str(root)])
            report = evidence / "diagnosis-comparison.json"
            payload = json.loads(report.read_text()) if report.exists() else None
            return code, payload, bundle, drive, diagnose, judge

    def test_unexercised_good_code_is_not_a_diagnosis_pass(self):
        code, payload, bundle, _, _, _ = self.run_main()
        self.assertEqual(3, code)
        self.assertEqual("PASS", payload["code_verdict"])
        self.assertEqual("NOT_EXERCISED", payload["astra_diagnose"])
        self.assertEqual("NEEDS_HUMAN_REVIEW", payload["result"])
        self.assertEqual("RECORDED", bundle.finish.call_args.args[0])

    def test_fixed_code_after_retry_still_needs_human_diagnosis_assessment(self):
        diagnosis = {
            "admitted": True,
            "policy_accepted_retry": True,
            "retry_dispatched": True,
            "recommendation": {"action": "retry"},
            "diagnosis": "missing report summary",
        }
        code, payload, _, drive, diagnose, _ = self.run_main("rejected", diagnosis)
        self.assertEqual(3, code)
        self.assertEqual("PASS", payload["code_verdict"])
        self.assertEqual("HUMAN_ASSESSMENT_PENDING", payload["diagnosis_quality"])
        self.assertEqual("NEEDS_HUMAN_REVIEW", payload["result"])
        self.assertIn("seeded_implementation_bug", payload)
        self.assertIn("report_rejection_evidence", payload)
        self.assertIs(drive.call_args.args[3], diagnose.call_args.args[4])

    def test_bad_code_remains_a_failure_even_when_the_runner_says_complete(self):
        code, payload, bundle, _, _, _ = self.run_main(code_pass=False)
        self.assertEqual(1, code)
        self.assertEqual("FAIL", payload["code_verdict"])
        self.assertEqual("FAIL", payload["result"])
        self.assertEqual(trial.base.scenarios.FAIL, bundle.finish.call_args.args[0])

    def test_rejected_retry_recommendation_does_not_grade_code(self):
        diagnosis = {"admitted": True, "policy_accepted_retry": False, "recommendation": {"action": "retry"}}
        code, payload, _, _, _, judge = self.run_main("rejected", diagnosis)
        self.assertEqual(3, code)
        self.assertEqual("NOT_GRADED", payload["code_verdict"])
        judge.assert_not_called()

    def test_premerge_base_timeout_finishes_error_evidence_without_traceback(self):
        error = subprocess.TimeoutExpired("cli", 1, output=b"printed\xff", stderr=b"error")
        code, _, bundle, _, _, _ = self.run_main(error=error)
        self.assertEqual(1, code)
        self.assertEqual(trial.base.scenarios.ERROR, bundle.finish.call_args.args[0])
        event = bundle.log.call_args
        self.assertEqual("trial_error", event.args[0])
        self.assertIsInstance(event.kwargs["stdout_tail"], str)


class ParseArgsTests(unittest.TestCase):
    def test_default_profile_is_the_free_fixture(self):
        args = trial.parse_args([])
        self.assertEqual("fixture", args.profile)
        self.assertFalse(args.i_authorize_live_model_spend)

    def test_live_profile_without_authorization_is_refused_before_any_spend(self):
        with patch.object(sys, "stderr"):
            code = trial.main(["--profile", "glm53-openai"])
        self.assertEqual(2, code)


if __name__ == "__main__":
    unittest.main()
