"""Full command-line flow with real processes and an explicitly fake provider."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class SubprocessFlow(unittest.TestCase):
    new_run_engine_args = ("--engine", "codex")

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / "unrelated-project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Fixture", "-c", "user.email=f@example.test",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        bin_dir = self.root / "fixture-bin"
        bin_dir.mkdir()
        source = Path(__file__).resolve().parent
        for filename in ("fake_codex.py", "goal_fixtures.py"):
            shutil.copy2(source / filename, bin_dir / ("codex" if filename == "fake_codex.py" else filename))
        (bin_dir / "codex").chmod(0o755)
        # Hermetic provider/model resolution: the child autocode.py process
        # must never read a contributor's own ~/.config/autocode or ~/.codex.
        config_home = self.root / "xdg-config"
        config_home.mkdir()
        codex_home = self.root / "codex-home"
        codex_home.mkdir()
        self.env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                    "PYTHONDONTWRITEBYTECODE": "1", "AUTOCODE_HOME": str(self.root / "registry-home"),
                    "XDG_CONFIG_HOME": str(config_home), "CODEX_HOME": str(codex_home)}
        self.env.pop("AUTOCODE_PROVIDER", None)
        self.entry = [os.environ["AUTOCODE_TEST_CLI"]] if os.environ.get("AUTOCODE_TEST_CLI") else [sys.executable, str(source / "autocode.py")]

    def launch(self, args, expected, *, answers=None):
        if "--run-dir" not in args and "--engine" not in args:
            args = [*self.new_run_engine_args, *args]
        if "--run-dir" not in args and "--in-place" not in args:
            args = [*args, "--in-place"]
        result = subprocess.run([*self.entry, "--workspace", str(self.project), *args], cwd=self.root, env=self.env,
                                input=answers, capture_output=True, text=True, timeout=60)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return result

    def saved(self):
        run = next((self.project / ".autocode/runs").iterdir())
        return run, json.loads((run / "state.json").read_text())

    def test_two_milestones_require_independent_evidence_before_advancing(self):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'milestones'
        self.launch(['Build greeting and goodbye', '--chat'], 0, answers='CLI\nyes\n')
        run, state = self.saved()
        self.assertEqual('TASK_COMPLETE', state['status'])
        execution = [r['stage'] for r in state['stages'] if r['stage'] != 'astra_discovery']
        self.assertEqual(['astra_plan', 'terra', 'sol', 'astra_review', 'terra', 'sol', 'astra_review'], execution)
        self.assertEqual({'M1', 'M2'}, {r['id'] for r in state['milestone_progress'].values() if r['accepted']})
        first = next(r for r in state['milestone_progress'].values() if r['id'] == 'M1')
        self.assertEqual('NOT_VERIFIED', first['accepted_validation']['criterion_results'][1]['status'])
        unchanged = (run / 'state.json').read_bytes()
        status = json.loads(self.launch(['--run-dir', str(run), '--status'], 0).stdout)
        self.assertTrue(status['milestone_checkpoint']['enabled'])
        self.assertGreater(status['milestone_checkpoint']['seconds_by_role']['sol'], 0)
        self.assertEqual(unchanged, (run / 'state.json').read_bytes())

    def test_changing_code_with_repeated_failed_checks_exhausts_builder_policy(self):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'stalled'
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nyes\n')
        _, state = self.saved()
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT', state['status'])
        self.assertEqual(['retry','escalate','pause'], [r['action'] for r in state['builder_retry_decisions']])
        self.assertEqual(3, sum(r['stage'] == 'terra' for r in state['stages']))

    def test_queued_checkpoint_migration_preserves_work_and_never_launches_on_activation(self):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nno\n')
        run, state = self.saved()
        args = ['--run-dir', str(run), '--no-chat']
        self.launch([*args, '--approve-goal', state['displayed_goal']], 0)
        self.launch([*args, '--pause-after-stage'], 2)
        self.launch([*args, '--resume-paused', '--pause-after-stage'], 2)
        _, state = self.saved()
        state['settings'].pop('milestone_checkpoints')
        state['settings']['workflow'] = {'mode': 'glm_final_audit_v2'}
        (run / 'state.json').write_text(json.dumps(state))
        original = (run / 'state.json').read_bytes()
        self.launch([*args, '--request-milestone-checkpoints'], 0)
        self.assertEqual(original, (run / 'state.json').read_bytes())
        status = json.loads(self.launch([*args, '--status'], 0).stdout)
        self.assertTrue(status['milestone_activation_pending'])
        self.launch([*args, '--show-goal'], 0)
        _, migrated = self.saved()
        self.assertEqual(state['goal_contract'], migrated['goal_contract'])
        self.assertEqual(len(state['stages']), len(migrated['stages']))
        self.assertEqual('sol', migrated['next_stage'])
        self.assertNotIn('workflow', migrated['settings'])
        self.launch([*args, '--resume-paused'], 0)
        self.assertEqual('TASK_COMPLETE', self.saved()[1]['status'])

    def test_chat_brief_feedback_approval_and_autonomous_rework(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "rework"
        result = self.launch(["Build a greeting tool", "--chat"], 0,
                             answers="CLI\nKeep Unicode support\nyes\n")
        run, state = self.saved()
        self.assertEqual("COMPLETE", state["phase"])
        self.assertEqual(["CONTINUE", "REWORK", "COMPLETE"], [d["report"]["status"] for d in state["decisions"]])
        self.assertEqual(2, sum(r["stage"] == "terra" for r in state["stages"]))
        self.assertEqual(2, sum(r["stage"] == "sol" for r in state["stages"]))
        self.assertEqual(["FAIL"], [row["validation"]["verdict"] for row in state["validation_archive"]])
        self.assertIn("Keep Unicode support", state["goal_contract"]["body"]["constraints"])
        self.assertEqual(1, sum(e["kind"] == "goal_approval" for e in state["user_events"]))
        # The Validator's failing finding and the Plan Reviewer's structured REWORK finding share one ledger,
        # were linked to the correction task, and were closed by the reviewers' next reports.
        ledger = state["findings_ledger"]
        self.assertEqual({"sol", "astra"}, {row["source"] for row in ledger})
        self.assertTrue(all(row["status"] == "resolved" and row["resolved_in"] for row in ledger))
        rework_task = [t for t in [*state.get("task_archive", []), state["current_task"]] if t.get("decision") == "REWORK"][0]
        self.assertTrue(all(row["assigned_task"] == rework_task["id"] for row in ledger))
        self.assertEqual(sorted(row["id"] for row in ledger), sorted(rework_task["findings"]))
        rework_prompt = Path(next(r for r in state["stages"] if r["stage"] == "terra" and r.get("task_id") == rework_task["id"])["prompt"]).read_text()
        handoff = json.loads(rework_prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual({"sol", "astra"}, {row["source"] for row in handoff["open_findings"]})
        self.assertIn("Acceptance evidence:", result.stdout)
        self.assertIn("End-to-end flow: PASS", result.stdout)
        executed = [row for row in state["stages"] if row["stage"] != "astra_discovery" and not row.get('runner_owned')]
        for record in executed:
            prompt = Path(record["prompt"]).read_text()
            data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
            self.assertEqual(state["goal_contract"], data["goal_contract"])
            self.assertEqual(str(self.project), data["workspace"])
            if record["stage"] == "sol":
                self.assertEqual(data["current_task"]["id"], data["implementation"]["task_id"])
                self.assertIn("commands_run", data["implementation"])
                self.assertEqual(data["source_revision"], data["implementation"]["source_revision"])
        before = (run / "state.json").read_bytes()
        self.launch(["--run-dir", str(run)], 0)
        self.assertEqual(before, (run / "state.json").read_bytes())

    def test_new_and_resumed_runs_register_without_launch_flags(self):
        probe = self.root / "registry-launches.jsonl"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(probe)
        self.launch(["Build a greeting tool"], 2)
        run, _ = self.saved()
        listed = subprocess.run([*self.entry, "registry", "list", "--json"], cwd=self.root, env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(0, listed.returncode, listed.stdout + listed.stderr)
        runs = json.loads(listed.stdout)["runs"]
        self.assertEqual([str(run.resolve())], [item["run_dir"] for item in runs])
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        # A different isolated registry proves that an ordinary resume re-registers
        # the saved checkpoint before it can consider another provider stage.
        resumed_home = self.root / "resumed-registry-home"
        self.env["AUTOCODE_HOME"] = str(resumed_home)
        self.launch(["--run-dir", str(run)], 2)
        resumed = subprocess.run([*self.entry, "registry", "list"], cwd=self.root, env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(0, resumed.returncode, resumed.stdout + resumed.stderr)
        self.assertEqual([str(run.resolve())], [item["run_dir"] for item in json.loads(resumed.stdout)["runs"]])
        observed = [json.loads(line) for line in probe.read_text().splitlines()]
        self.assertEqual(2, len(observed))
        self.assertTrue(all(str(run.resolve()) in item["runs"] for item in observed))

    def test_registry_failure_preserves_new_run_for_retry_without_a_stage_launch(self):
        blocked_home = self.root / "blocked-registry"
        blocked_home.write_text("not a directory")
        self.env["AUTOCODE_HOME"] = str(blocked_home)
        self.launch(["Build a greeting tool"], 2)
        run, state = self.saved()
        self.assertEqual("PAUSED_REGISTRY", state["status"])
        self.assertEqual([], list((run / "iterations").glob("*")) if (run / "iterations").exists() else [])
        self.env["AUTOCODE_HOME"] = str(self.root / "retry-registry-home")
        probe = self.root / "retry-launches.jsonl"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(probe)
        self.launch(["--run-dir", str(run), "--resume-paused"], 2)
        registered = subprocess.run([*self.entry, "registry", "list"], cwd=self.root, env=self.env,
                                   capture_output=True, text=True, timeout=30)
        self.assertEqual(0, registered.returncode, registered.stdout + registered.stderr)
        self.assertEqual(str(run.resolve()), json.loads(registered.stdout)["runs"][0]["run_dir"])
        self.assertEqual(["astra_discovery"], [json.loads(line)["stage"] for line in probe.read_text().splitlines()])

    def test_read_only_commands_do_not_create_or_register_storage(self):
        run = self.project / ".autocode/runs/read-only"
        run.mkdir(parents=True)
        state = {"version": 2, "workspace": str(self.project), "task": "Read only", "status": "RUNNING",
                 "iteration": 1, "sessions": {}, "history": [], "stages": [], "next_stage": "astra_plan"}
        state_path = run / "state.json"
        state_path.write_text(json.dumps(state))
        before = state_path.read_bytes()
        self.assertFalse(Path(self.env["AUTOCODE_HOME"]).exists())
        self.launch(["--run-dir", str(run), "--status"], 0)
        self.launch(["--run-dir", str(run), "--dry-run"], 0)
        help_result = subprocess.run([*self.entry, "--help"], cwd=self.root, env=self.env,
                                     capture_output=True, text=True, timeout=30)
        self.assertEqual(0, help_result.returncode, help_result.stdout + help_result.stderr)
        self.assertFalse(Path(self.env["AUTOCODE_HOME"]).exists())
        self.assertEqual(before, state_path.read_bytes())

    def test_chat_pause_before_approval_resumes_same_brief(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.launch(["Build a greeting tool", "--chat"], 2, answers="CLI\nno\n")
        run, state = self.saved()
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["phase"])
        self.assertFalse((self.project / "greet.py").exists())
        token = state["displayed_goal"]
        self.launch(["--run-dir", str(run), "--chat"], 0, answers="yes\n")
        _, final = self.saved()
        self.assertEqual(token, final["goal_contract"]["approval_event"]["token"])
        self.assertEqual("COMPLETE", final["phase"])

    def test_completed_run_rechecks_changed_source_and_evidence(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.launch(["Build a greeting tool", "--chat"], 0, answers="CLI\nyes\n")
        run, state = self.saved()
        args = ["--run-dir", str(run)]
        stage_count = len(state["stages"])
        source = self.project / "greet.py"
        source.write_text(source.read_text() + "\n# external edit\n")
        unchanged = (run / "state.json").read_bytes()
        status = self.launch([*args, "--status"], 0)
        self.assertFalse(json.loads(status.stdout)["completion_current"])
        self.assertEqual(unchanged, (run / "state.json").read_bytes())
        self.launch(args, 2)
        _, paused = self.saved()
        self.assertEqual("PAUSED_STALE_VALIDATION", paused["status"])
        self.assertEqual(stage_count, len(paused["stages"]))
        self.launch([*args, "--resume-paused"], 0)
        _, final = self.saved()
        self.assertEqual(["sol", "astra_review"], [r["stage"] for r in final["stages"][stage_count:]])
        evidence = Path(next(iter(final["validation"]["evidence_hashes"])))
        evidence.write_text(evidence.read_text() + "\n")
        self.launch(args, 2)
        self.assertEqual("PAUSED_STALE_VALIDATION", self.saved()[1]["status"])

    def test_chat_completes_artifact_review_without_busy_loop(self):
        result = self.launch(["Build a greeting tool", "--chat"], 0, answers="CLI\nyes\nyes\n")
        _, state = self.saved()
        self.assertEqual("COMPLETE", state["phase"])
        self.assertEqual(1, len(state["human_reviews"]))
        self.assertIn("Approve artifact criterion C1", result.stdout)

    def test_abandoned_completion_revalidates_before_completing(self):
        self.check_abandoned_completion_recovery()

    def test_legacy_completion_failure_loop_recovers_on_explicit_resume(self):
        self.check_abandoned_completion_recovery(legacy=True)

    def check_abandoned_completion_recovery(self, *, legacy=False):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.env['AUTOCODE_FIXTURE_QUOTA_STAGE'] = 'astra_review'
        self.launch(['Build a greeting tool', '--chat'], 2, answers='CLI\nyes\n')
        run, interrupted = self.saved()
        self.assertEqual('PASS', interrupted['validation']['verdict'])
        self.assertEqual('astra_review', interrupted['active_stage']['stage'])
        source = (self.project / 'greet.py').read_bytes()
        contract = interrupted['goal_contract']
        args = ['--run-dir', str(run), '--no-chat']
        status = json.loads(self.launch([*args, '--status'], 0).stdout)
        del self.env['AUTOCODE_FIXTURE_QUOTA_STAGE']

        self.launch([*args, '--abandon-stage', status['attempt_id']], 0)
        _, abandoned = self.saved()
        self.assertEqual('PAUSED_STAGE_ABANDONED', abandoned['status'])
        self.assertEqual('sol', abandoned['next_stage'])
        self.assertNotIn('validation', abandoned)
        self.assertEqual(interrupted['validation'], abandoned['validation_archive'][-1]['validation'])
        self.assertTrue(abandoned['stages'][-1]['abandoned'])
        self.assertTrue(Path(abandoned['stages'][-1]['events']).is_file())
        self.assertEqual(source, (self.project / 'greet.py').read_bytes())
        if legacy:
            from .test_autocode import runner, s
            # Recreate the durable state written by the old completion router.
            abandoned.update(status='PAUSED_REPEATED_FAILURE', next_stage='astra_review')
            error = s.Paused('PAUSED_COMPLETION_GATE',
                'Completion rejected: missing, stale, failed or unverified independent evidence')
            for attempt in range(3):
                record = {'stage': 'astra_review', 'role': 'astra', 'iteration': 1,
                          'output': str(run / f'failed-completion-{attempt}.json'),
                          'source_revision': abandoned['recovery_context']['source_revision'],
                          'rejected': True, 'rejection_reason': str(error)}
                runner.failures.record(abandoned, record, error, s.now())
                abandoned['stages'].append(record)
            (run / 'state.json').write_text(json.dumps(abandoned))
        count = len(abandoned['stages'])
        # Merely inspecting or launching a paused run must not authorize recovery.
        self.launch(args, 2)
        self.assertEqual(count, len(self.saved()[1]['stages']))

        self.launch([*args, '--resume-paused', '--unit', 'autoreview'], 0)
        _, final = self.saved()
        self.assertEqual('TASK_COMPLETE', final['status'])
        self.assertEqual(['sol', 'astra_review'], [r['stage'] for r in final['stages'][count:]])
        self.assertEqual('PASS', final['validation']['verdict'])
        self.assertEqual(1, sum(r['stage'] == 'terra' for r in final['stages']))
        self.assertEqual(contract, final['goal_contract'])
        self.assertEqual(source, (self.project / 'greet.py').read_bytes())
        if legacy:
            self.assertEqual(abandoned['failure_history'], final['failure_history'])
        self.assertTrue(json.loads(self.launch([*args, '--status'], 0).stdout)['completion_current'])

    def test_unexpected_session_pauses_and_can_be_explicitly_abandoned(self):
        self.launch(["Build a greeting tool"], 2)
        run, initial = self.saved()
        args = ["--run-dir", str(run)]
        self.launch([*args, "--answer", "Q1=CLI"], 0)
        self.env["AUTOCODE_FIXTURE_SESSION_DRIFT"] = "1"
        self.launch(args, 2)
        _, paused = self.saved()
        self.assertEqual("PAUSED_UNCERTAIN_STAGE", paused["status"])
        self.assertEqual(initial["sessions"], paused["sessions"])
        self.assertEqual(1, len(paused["stages"]))
        status = json.loads(self.launch([*args, "--status"], 0).stdout)
        unchanged = (run / "state.json").read_bytes()
        self.launch([*args, "--abandon-stage", "001/wrong-01"], 2)
        self.assertEqual(unchanged, (run / "state.json").read_bytes())
        self.launch([*args, "--abandon-stage", status["attempt_id"]], 0)
        _, abandoned = self.saved()
        self.assertEqual("PAUSED_STAGE_ABANDONED", abandoned["status"])
        self.assertEqual(2, len(abandoned["stages"]))
        self.assertTrue(abandoned["stages"][-1]["abandoned"])
        self.assertNotIn("astra", abandoned["sessions"])
        self.assertFalse((self.project / "greet.py").exists())
        del self.env["AUTOCODE_FIXTURE_SESSION_DRIFT"]
        self.launch([*args, "--resume-paused"], 2)
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.saved()[1]["phase"])

    def test_rework_pauses_at_saved_iteration_limit_and_can_resume(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "rework"
        self.launch(["Build a greeting tool", "--chat", "--max-iterations", "1"], 2, answers="CLI\nyes\n")
        run, state = self.saved()
        self.assertEqual("PAUSED_ITERATION_LIMIT", state["status"])
        self.assertEqual("PAUSED_OR_BLOCKED", state["phase"])
        self.assertEqual("REWORK", state["last_decision"]["report"]["status"])
        task_id = state["current_task"]["id"]
        self.assertEqual(1, sum(r["stage"] == "terra" for r in state["stages"]))
        self.launch(["--run-dir", str(run), "--resume-paused", "--max-iterations", "3"], 0)
        _, final = self.saved()
        self.assertEqual("COMPLETE", final["phase"])
        self.assertEqual(task_id, final["current_task"]["id"])
        self.assertEqual(2, sum(r["stage"] == "terra" for r in final["stages"]))

    def test_standalone_cli_full_interview_approval_review_and_completion(self):
        project, launch = self.project, self.launch
        launch(["Build a useful greeting tool", "--reasoning-effort", "high", "--terra-provider", "ZAI"], 2)
        expected_models = {"astra": "gpt-5.6-sol", "terra": "gpt-5.6-terra",
                           "sol": "gpt-5.6-sol", "completion": "gpt-5.6-sol"}
        run = next((project / ".autocode/runs").iterdir())
        args = ["--run-dir", str(run)]
        def state(): return json.loads((run / "state.json").read_text())
        self.assertEqual("DISCOVERING", state()["phase"])
        self.assertEqual("WAITING_FOR_USER", state()["status"])
        self.assertEqual(expected_models, {role:settings["model"]
                                          for role,settings in state()["settings"]["roles"].items()})
        self.assertFalse((project / "greet.py").exists())
        launch([*args, "--answer", "Q1=CLI"], 0)
        self.assertEqual(1, len(state()["stages"]))
        launch(args, 2)
        self.assertEqual("AWAITING_GOAL_APPROVAL", state()["phase"])
        self.assertEqual("CLI", state()["answers"]["Q1"]["text"])
        launch([*args, "--approve-goal", "r1:stale"], 2)
        self.assertFalse((project / "greet.py").exists())
        launch([*args, "--approve-goal", state()["displayed_goal"]], 0)
        self.assertEqual("READY_TO_EXECUTE", state()["phase"])
        self.assertEqual(2, len(state()["stages"]))
        launch(args, 2)
        self.assertEqual("WAITING_FOR_USER", state()["phase"])
        self.assertEqual("human_review", state()["user_request"]["kind"])
        launch([*args, "--resume-paused"], 2)
        before = (project / "greet.py").read_bytes()
        launch([*args, "--approve-review", "C1", "--review-token", state()["displayed_review"]], 0)
        launch(args, 0)
        final = state()
        self.assertEqual("COMPLETE", final["phase"])
        self.assertEqual(expected_models, {role:settings["model"]
                                          for role,settings in final["settings"]["roles"].items()})
        self.assertEqual(before, (project / "greet.py").read_bytes())
        self.assertEqual(1, sum(r["stage"] == "terra" for r in final["stages"]))
        self.assertEqual(["Optional web UI"], final["deferred_backlog"])
        launch(args, 0)
        self.assertEqual(len(final["stages"]), len(state()["stages"]))
        for record in final["stages"]:
            if record.get('runner_owned'):
                self.assertEqual('runner', record['engine'])
                self.assertNotIn('command', record)
                continue
            command = record["command"]
            expected = "workspace-write" if record["role"] == "terra" else "read-only"
            self.assertEqual(expected, command[command.index("--sandbox") + 1])
            self.assertEqual(expected_models[record["role"]], command[command.index("--model") + 1])
            self.assertNotIn("--last", command)
            if record["role"] == "terra":
                self.assertIn('model_provider="ZAI"', command)
            else:
                self.assertFalse(any(str(item).startswith("model_provider=") for item in command))

if __name__ == "__main__":
    unittest.main()
