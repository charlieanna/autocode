"""Owner loss after an Investigator writes output, through TaskRun and the CLI."""

import fcntl
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import autocode
import autocode_job_report_recovery as recovery
from autocode_taskrun import TaskRun, TaskRunError

from .test_bug_job import diagnosis
from .test_job_failure_recovery import BROKEN, ORIGINAL, WRAPPER, JobHarness

PROVIDER = r"""import json,os,signal,socket,sys
from pathlib import Path
sys.stdin.read()
with Path(os.environ['JOB_CALLS']).open('a') as log:log.write('request\n')
Path(sys.argv[1]).write_text(Path(os.environ['JOB_REPORT']).read_text())
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':20,'output_tokens':30}}),flush=True)
connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
connection.connect(os.environ['JOB_SOCKET'])
connection.sendall(b'written')
signal.pause()
"""

FAULT = r"""
import socket
real_popen=autocode.supervision.subprocess.Popen
def watched_keeper(command,*args,**kwargs):
 if len(command)>2 and command[2].endswith('autocode_supervision_keeper.py'):
  command=[*command[:2],os.environ['JOB_KEEPER'],*command[3:]]
 return real_popen(command,*args,**kwargs)
autocode.supervision.subprocess.Popen=watched_keeper
listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
listener.bind(os.environ['JOB_SOCKET']);listener.listen(1)
def lose_owner(child,seconds,checkpoint,**kwargs):
 connection,_=listener.accept()
 assert connection.recv(7)==b'written'
 # The keeper observes EOF, stops the blocked provider, and seals cleanup.
 # No exit/result checkpoint or source witness is fabricated.
 os._exit(0)
autocode.processes.wait_for_stage=lose_owner
"""

KEEPER = r"""import os,socket,sys
sys.path.insert(0,os.environ['JOB_RUNTIME']+'/tools')
import autocode_supervision_keeper as keeper
result=keeper.main(int(sys.argv[1]),int(sys.argv[2]))
connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
connection.connect(os.environ['JOB_SEAL_SOCKET'])
connection.sendall(b'sealed')
raise SystemExit(result)
"""


class InvestigatorReportRecoveryTests(JobHarness):
    def setUp(self):
        super().setUp()
        self.workspace = self.workspace.resolve()
        private = self.workspace / ".autocode"
        provider = private / "provider.py"
        provider.write_text(PROVIDER)
        self.report = private / "fixture-report.json"
        self.value = diagnosis(
            note_path="docs/bugs/double.json",
            affected_paths=["calc.py"],
            test_paths=["tests/test_calc.py"],
            observed="double(5) returns 10 rather than 15",
            root_cause="multiplier is two",
            reproduction="double(5) == 10",
            invariant="double(5) returns 15",
            fix_size="large",
            probe=f'{sys.executable} -B -c "from calc import double; assert double(5) == 10"',
            untestable="",
            test_cases=[{"id": "T1", "given": "n=5", "when": "double(5)", "then": "returns 15", "kind": "restore"}],
        )
        self.report.write_text(json.dumps(self.value))
        config_root = private / "config"
        config = config_root / "autocode/providers/offline.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            'name = "offline"\ncommand = '
            + json.dumps([sys.executable, str(provider), "{report}"])
            + '\nmodels = ["gpt-6-astra", "gpt-6-terra", "gpt-6-sol", "gpt-6-glm"]\n[roles]\n'
            + "\n".join(
                f'{role} = {{ model = "gpt-6-{role if role in ("astra", "terra", "glm") else "sol"}", effort = "high" }}'
                for role in ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")
            )
            + "\n"
        )
        sockets = tempfile.TemporaryDirectory(prefix="jrr-", dir="/tmp")
        self.addCleanup(sockets.cleanup)
        self.env.update(
            XDG_CONFIG_HOME=str(config_root),
            JOB_REPORT=str(self.report),
            JOB_SOCKET=str(Path(sockets.name) / "ready.sock"),
        )
        self.sealed = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sealed.bind(str(Path(sockets.name) / "sealed.sock"))
        self.sealed.listen(1)
        self.sealed.settimeout(15)
        self.addCleanup(self.sealed.close)
        keeper = private / "keeper.py"
        keeper.write_text(KEEPER)
        self.env.update(JOB_KEEPER=str(keeper), JOB_SEAL_SOCKET=str(Path(sockets.name) / "sealed.sock"))
        self.options = (
            "--provider",
            "offline",
            "--max-stage-seconds",
            "900",
            "--max-seconds",
            "1200",
            "--max-idle-seconds",
            "0",
            "--max-tool-seconds",
            "0",
        )
        self.wrapper = private / "wrapper.py"
        self.wrapper.write_text(
            WRAPPER.replace("raise SystemExit(autocode.main())", FAULT + "\nraise SystemExit(autocode.main())")
        )

    def lost_run(self):
        # The fault intentionally loses the CLI controller. Do not let the
        # client's outer tree cleanup kill the independent keeper before sealing.
        with patch(
            "autocode_taskrun.run_captured",
            side_effect=lambda command, **kw: subprocess.run(command, capture_output=True, text=True, **kw),
        ):
            run = self.start("success", workflow="bugfix")
        connection, _ = self.sealed.accept()
        with connection:
            self.assertEqual(b"sealed", connection.recv(6))
            self.assertEqual(b"", connection.recv(1))  # keeper exited, not merely wrote its receipt
        # Further invocations do not inject another launch fault.
        self.wrapper.write_text(WRAPPER)
        self.assertEqual(1, self.count(), (run.last_advance.stdout, run.last_advance.stderr))
        return run

    def test_inspected_report_is_applied_once_without_a_second_provider_call(self):
        run = self.lost_run()
        before = (run.run_dir / "state.json").read_bytes()
        offer = run.status()["job_report_recovery"]
        if offer is None:
            recovery.inspect(autocode, json.loads(before), run.run_dir, self.workspace)
        self.assertIsNotNone(offer)
        self.assertEqual(before, (run.run_dir / "state.json").read_bytes())
        self.assertEqual(offer, run.status()["job_report_recovery"])
        view = run.recover_job_report(offer["token"])
        self.assertIsNone(view["job_report_recovery"])
        self.assertEqual("astra_discovery", view["next_stage"])
        self.assertTrue((self.workspace / self.value["note_path"]).is_file())
        self.assertEqual(ORIGINAL, (self.workspace / "calc.py").read_text())
        self.assertEqual(1, self.count())
        saved = json.loads((run.run_dir / "state.json").read_text())
        stage = next(row for row in saved["stages"] if row["stage"] == "investigate_bug")
        self.assertIsNone(stage.get("exit_code"))
        self.assertEqual("explicit_operator_adoption", stage["report_recovery"]["authority"])
        self.assertEqual(offer["sha256"], stage["report_recovery"]["sha256"])
        with self.assertRaises(TaskRunError):
            run.recover_job_report(offer["token"])
        self.assertEqual(1, self.count())

    def test_plain_attach_and_resume_do_not_adopt_or_launch(self):
        run = self.lost_run()
        offer = run.status()["job_report_recovery"]
        self.assertIsNotNone(offer)
        view = run.advance()
        self.assertIn(view["status"], ("PAUSED_UNCERTAIN_STAGE", "WAITING_FOR_USER"))
        self.assertEqual("investigate_bug", view["next_stage"])
        self.assertFalse(view["done"])
        self.assertEqual(offer, view["job_report_recovery"])
        run.resume_paused()
        self.assertFalse((self.workspace / self.value["note_path"]).exists())
        self.assertEqual(1, self.count())
        run.recover_job_report(offer["token"])
        self.assertEqual(1, self.count())

    def test_changed_report_source_configuration_and_wrong_token_are_rejected(self):
        run = self.lost_run()
        offer = run.status()["job_report_recovery"]
        with self.assertRaisesRegex(TaskRunError, "token"):
            run.recover_job_report("jrr:wrong")
        path = Path(offer["report"])
        original = path.read_bytes()
        path.write_text(json.dumps({**self.value, "conclusion": "different inspected bytes"}))
        with self.assertRaisesRegex(TaskRunError, "token"):
            run.recover_job_report(offer["token"])
        path.write_bytes(original)
        (self.workspace / "calc.py").write_text(BROKEN)
        with self.assertRaisesRegex(TaskRunError, "Source changed"):
            run.recover_job_report(offer["token"])
        (self.workspace / "calc.py").write_text(ORIGINAL)
        changed = TaskRun(
            self.workspace,
            run.run_dir,
            command=run.command,
            options=("--max-stage-seconds", "901"),
            env=run.env,
            timeout=60,
        )
        saved = (run.run_dir / "state.json").read_bytes()
        with self.assertRaisesRegex(TaskRunError, "configuration|limits"):
            changed.recover_job_report(offer["token"])
        self.assertEqual(saved, (run.run_dir / "state.json").read_bytes())
        self.assertEqual(offer, run.status()["job_report_recovery"])
        self.assertFalse((self.workspace / self.value["note_path"]).exists())
        self.assertEqual(1, self.count())

    def test_missing_malformed_and_symlink_reports_reject_without_adoption(self):
        run = self.lost_run()
        offer = run.status()["job_report_recovery"]
        path = Path(offer["report"])
        original = path.read_bytes()
        for contents in ("{", "{}"):
            path.write_text(contents)
            self.assertIsNone(run.status()["job_report_recovery"])
            with self.assertRaises(TaskRunError):
                run.recover_job_report(offer["token"])
        path.unlink()
        with self.assertRaises(TaskRunError):
            run.recover_job_report(offer["token"])
        path.symlink_to(self.report)
        with self.assertRaisesRegex(TaskRunError, "non-symlink"):
            run.recover_job_report(offer["token"])
        path.unlink()
        path.write_bytes(original)
        self.assertEqual(1, self.count())

    def test_probe_failure_is_not_bypassed_by_report_adoption(self):
        self.value["probe"] = f'{sys.executable} -B -c "raise SystemExit(1)"'
        self.report.write_text(json.dumps(self.value))
        run = self.lost_run()
        offer = run.status()["job_report_recovery"]
        with self.assertRaisesRegex(TaskRunError, "probe|reproduce"):
            run.recover_job_report(offer["token"])
        charged = json.loads((run.run_dir / "state.json").read_text())["active_seconds"]
        with self.assertRaisesRegex(TaskRunError, "probe|reproduce"):
            run.recover_job_report(offer["token"])
        self.assertEqual(charged, json.loads((run.run_dir / "state.json").read_text())["active_seconds"])
        self.assertFalse((self.workspace / self.value["note_path"]).exists())
        self.assertEqual("investigate_bug", run.status()["next_stage"])
        self.assertEqual(1, self.count())

    def test_admission_rejects_foreign_context_and_uncertain_cleanup_or_liveness(self):
        run = self.lost_run()
        state = json.loads((run.run_dir / "state.json").read_text())
        # Inject boundary observations in memory; public fault setup above owns the actual receipt.
        for field, value in (
            ("output", str(self.report)),
            ("report_only", True),
            ("events", str(self.report)),
            ("before_ref", str(self.report)),
            ("output_mode", "opencode_events"),
            ("supports_sessions", True),
            ("timed_out", True),
            ("cleanup_error", "uncertain"),
        ):
            candidate = json.loads(json.dumps(state))
            candidate["active_stage"][field] = value
            self.assertIsNone(recovery.offer(autocode, candidate, run.run_dir, self.workspace))
        candidate = json.loads(json.dumps(state))
        candidate["active_stage"]["capture_context"]["nonce"] = "different"
        with self.assertRaisesRegex(ValueError, "token"):
            recovery.apply(
                autocode, candidate, run.run_dir, self.workspace, run.status()["job_report_recovery"]["token"]
            )
        observation = autocode.supervision.observe(state["active_stage"]["supervision"])
        for key in ("owner", "keeper", "provider"):
            for live in ({"checked": False, "alive": None}, {"checked": True, "alive": True}):
                with patch.object(autocode.supervision, "observe", return_value={**observation, key: live}):
                    self.assertIsNone(recovery.offer(autocode, state, run.run_dir, self.workspace))
        for change in ({"cleanup_error": "denied"}, {"phase": "uncertain"}, {"cause": "stage_deadline"}):
            observed = {**observation, "receipt": {**observation["receipt"], **change}}
            with patch.object(autocode.supervision, "observe", return_value=observed):
                self.assertIsNone(recovery.offer(autocode, state, run.run_dir, self.workspace))
        candidate = json.loads(json.dumps(state))
        candidate["active_stage"]["supervision"]["nonce"] = "foreign"
        self.assertIsNone(recovery.offer(autocode, candidate, run.run_dir, self.workspace))
        for field, value in (("capture_hash", "corrupt"), ("before_identity", "foreign")):
            candidate = json.loads(json.dumps(state))
            candidate["active_stage"]["job_source"][field] = value
            self.assertIsNone(recovery.offer(autocode, candidate, run.run_dir, self.workspace))
        for field, value in (("attempt", str(self.report)), ("source_revision", "foreign")):
            candidate = json.loads(json.dumps(state))
            candidate["active_stage"]["capture_context"][field] = value
            self.assertIsNone(recovery.offer(autocode, candidate, run.run_dir, self.workspace))
        for workers in ({"checked": False, "alive": None}, {"checked": True, "alive": True}):
            with patch.object(autocode.processes, "recorded_worker_state", return_value=workers):
                self.assertIsNone(recovery.offer(autocode, state, run.run_dir, self.workspace))
        self.assertEqual(1, self.count())

    def test_report_loading_does_not_follow_an_auxiliary_response_symlink(self):
        run = self.lost_run()
        offer = run.status()["job_report_recovery"]
        response = Path(offer["report"]).with_suffix(".response.txt")
        response.symlink_to(self.workspace / "calc.py")
        run.recover_job_report(offer["token"])
        self.assertEqual(ORIGINAL, (self.workspace / "calc.py").read_text())
        self.assertTrue(response.is_symlink())
        self.assertEqual(1, self.count())

    def test_checkout_writer_exclusion_blocks_recovery_without_changing_state(self):
        run = self.lost_run()
        offer = run.status()["job_report_recovery"]
        before = (run.run_dir / "state.json").read_bytes()
        with (self.workspace / ".autocode/writer.lock").open("a+") as locked:
            fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(TaskRunError, "Another AutoCode run"):
                run.recover_job_report(offer["token"])
        self.assertEqual(before, (run.run_dir / "state.json").read_bytes())
        self.assertFalse((self.workspace / self.value["note_path"]).exists())
        self.assertEqual(ORIGINAL, (self.workspace / "calc.py").read_text())
        run.recover_job_report(offer["token"])
        self.assertEqual(1, self.count())

    def test_pending_durable_stop_wins_over_attach_resume_and_report_adoption(self):
        run = self.lost_run()
        offer = run.status()["job_report_recovery"]
        stopped = subprocess.run(
            [
                *run.command,
                "intervention",
                "submit",
                "--workspace",
                str(self.workspace),
                "--run-dir",
                str(run.run_dir),
                "--request-id",
                "stop-recovery",
                "--kind",
                "stop",
                "--text",
                "",
                "--json",
            ],
            env={**os.environ, **run.env},
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(0, stopped.returncode, stopped.stderr)
        self.assertEqual("PAUSED_INTERVENTION", run.advance()["status"])
        self.assertEqual("PAUSED_INTERVENTION", run.resume_paused()["status"])
        with self.assertRaises(TaskRunError):
            run.recover_job_report(offer["token"])
        self.assertIsNone(run.status()["job_report_recovery"])
        self.assertFalse((self.workspace / self.value["note_path"]).exists())
        self.assertEqual(1, self.count())

    def test_report_or_source_change_during_loading_cannot_reach_result_application(self):
        run = self.lost_run()
        offer = run.status()["job_report_recovery"]
        state = json.loads((run.run_dir / "state.json").read_text())
        report = Path(offer["report"])
        original = report.read_bytes()
        for target, replacement in ((report, original + b" "), (self.workspace / "calc.py", BROKEN.encode())):
            before = target.read_bytes()

            def changed_loader(*args, **kwargs):
                target.write_bytes(replacement)
                return self.value

            with (
                patch.object(autocode, "load_stage_report", side_effect=changed_loader),
                patch.object(autocode, "commit_stage_result") as commit,
            ):
                with self.assertRaisesRegex(ValueError, "changed"):
                    recovery.apply(autocode, state, run.run_dir, self.workspace, offer["token"])
                commit.assert_not_called()
            target.write_bytes(before)
        self.assertEqual(1, self.count())

    def test_report_or_source_change_during_real_probe_cannot_publish_note_or_result(self):
        run = self.lost_run()
        offer = run.status()["job_report_recovery"]
        charged = []
        self.wrapper.write_text(
            WRAPPER.replace(
                "raise SystemExit(autocode.main())",
                """
import autocode_verify as verify
real_probe=verify.scratch_run
def mutate_after_probe(*args,**kwargs):
 result=real_probe(*args,**kwargs)
 Path(os.environ['JOB_MUTATE_TARGET']).write_text(os.environ['JOB_MUTATE_BYTES'])
 return result
verify.scratch_run=mutate_after_probe
raise SystemExit(autocode.main())
""",
            )
        )
        for target, replacement in (
            (Path(offer["report"]), json.dumps({**self.value, "conclusion": "replacement"})),
            (self.workspace / "calc.py", BROKEN),
        ):
            original = target.read_bytes()
            run.env.update(JOB_MUTATE_TARGET=str(target), JOB_MUTATE_BYTES=replacement)
            with self.assertRaisesRegex(TaskRunError, "changed"):
                run.recover_job_report(offer["token"])
            self.assertEqual(replacement, target.read_text())
            self.assertFalse((self.workspace / self.value["note_path"]).exists())
            self.assertEqual("investigate_bug", run.status()["next_stage"])
            saved = json.loads((run.run_dir / "state.json").read_text())
            self.assertFalse(saved.get("investigation"))
            self.assertFalse(any(row.get("stage") == "investigate_bug" for row in saved["stages"]))
            self.assertNotIn("report_recovery", saved["active_stage"])
            charged.append(saved["active_seconds"])
            if target == Path(offer["report"]):
                self.assertEqual(ORIGINAL, (self.workspace / "calc.py").read_text())
            target.write_bytes(original)
            self.assertEqual(offer, run.status()["job_report_recovery"])
        self.assertEqual(charged[0], charged[1])
        self.assertEqual(1, self.count())
