"""Isolated tests: no model calls, credentials, learner data or course writes."""
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_support as s
import autocode_goals as goals
from goal_fixtures import approve_fixture, envelope


class DetachedOutputTest(unittest.TestCase):
    def test_closed_progress_pipe_does_not_stop_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "stage-finished"
            read_fd, write_fd = os.pipe()
            os.close(read_fd)
            disconnected = os.fdopen(write_fd, "w", buffering=1)
            try:
                def run_stage():
                    print("sol: still reviewing", flush=True)
                    marker.write_text("finished")
                    return 0

                with patch.object(sys, "stdout", disconnected), patch.object(runner, "main", side_effect=run_stage):
                    self.assertEqual(0, runner.cli())
            finally:
                with contextlib.suppress(BrokenPipeError):
                    disconnected.close()
            self.assertEqual("finished", marker.read_text())


class RetrofitTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.registry_environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "registry-home")})
        self.registry_environment.start()
        self.addCleanup(self.registry_environment.stop)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        self.evidence = self.run / "check.log"
        self.evidence.write_text("3 tests passed\n")
        self.criteria = [{"id":"C1", "criterion":"Contract holds", "status":"verified", "evidence":str(self.evidence)}]
        self.settings = {"roles": {r:{"model":f"model-{r}","reasoning_effort":"high"} for r in ("astra","terra","sol")},
                         "context_soft_tokens":1000, "rotation_after_input_tokens":1000000,
                         "headroom":{"enabled":False}}
        self.state = {"version":2,"workspace":str(self.root),"task":"Keep the exact course contract", "status":"RUNNING", "iteration":5,
                      "sessions":{"astra":"a-session","terra":"t-session","sol":"s-session"}, "stages":[],"history":[],
                      "acceptance_criteria":self.criteria, "criteria_revision":s.digest(s.criteria_definition(self.criteria)),
                      "settings":self.settings,"next_stage":"terra","next_action":"repair the full cohort"}

    def decision(self, status="CONTINUE"):
        return {"status":status,"acceptance_criteria":self.criteria,"next_objective":"next coherent batch",
                "evidence":[str(self.evidence)],"blocker":"", "plan":["batch"],"affected_paths":["source.rb"]}

    def legacy_final(self, role, value, iteration=5):
        base = self.run / "iterations" / f"{iteration:03d}" / role
        base.parent.mkdir(parents=True, exist_ok=True)
        base.with_suffix(".json").write_text(json.dumps(value))
        base.with_suffix(".jsonl").write_text(json.dumps({"type":"thread.started","thread_id":f"{role}-explicit"})+"\n"+json.dumps({"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}})+"\n")

    def test_atomic_failure_keeps_old_checkpoint(self):
        p=self.run/"state.json"
        s.atomic_json(p,{"old":True})
        with patch.object(s.os,"replace",side_effect=OSError("interruption")):
            with self.assertRaises(OSError): s.atomic_json(p,{"new":True})
        self.assertEqual({"old":True},s.read(p))
        self.assertEqual([],list(self.run.glob(".checkpoint-*")))

    def test_builder_no_progress_advances_automatic_ladder(self):
        self.state["settings"]["engine"] = "opencode"
        self.state["settings"]["roles"]["terra"].update(
            engine="opencode", provider=None, model="xiaomi-token-plan-sgp/mimo-v2.6-pro", reasoning_effort="medium")
        record = {"role":"terra", "stage":"terra", "output":str(self.run/"terra.json"),
                  "events":str(self.run/"terra.jsonl"), "source_revision":"same",
                  "changed_files":[], "after_ref":str(self.run/"after.json"), "diff_ref":None}
        runner._apply_result(self.state, "terra", {"evidence_refs":[str(self.evidence)]}, record, self.root, self.run)
        self.assertEqual("high", self.state["settings"]["roles"]["terra"]["reasoning_effort"])
        self.assertEqual("Mimo Pro High", self.state["reasoning_escalations"][-1]["selected"]["profile"])
        self.assertNotIn("terra", self.state["sessions"])

    def test_failed_validation_rework_advances_builder_ladder(self):
        self.state["settings"]["engine"] = "opencode"
        self.state["settings"]["roles"]["terra"].update(
            engine="opencode", provider=None, model="xiaomi-token-plan-sgp/mimo-v2.6-pro", reasoning_effort="high")
        self.state["validation"] = {"verdict":"FAIL"}
        record = {"role":"astra", "route_role":"completion", "stage":"astra_review",
                  "output":str(self.run/"decision.json")}
        runner._apply_result(self.state, "astra_review", self.decision(), record, self.root, self.run)
        self.assertEqual("xhigh", self.state["settings"]["roles"]["terra"]["reasoning_effort"])
        self.assertEqual("Mimo Pro XHigh", self.state["reasoning_escalations"][-1]["selected"]["profile"])

    def test_process_guard_targets_only_real_runner_processes(self):
        marker=".autocode/runs/run-x"
        # Wrapper shells carry the invocation text but are not runners themselves.
        self.assertFalse(s.duplicate_runner_command(f"zsh -lc 'python3 /ws/autocode/tools/autocode.py --run-dir {marker}'"))
        self.assertFalse(s.duplicate_runner_command(f"sh /tmp/dsa-run.sh --show-goal {marker}"))
        # Helper apps embedding runner prompt text in argv are not runners.
        self.assertFalse(s.duplicate_runner_command(
            "/Applications/SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient turn-ended "
            '{"cwd":"/ws/dsa-tutor","input-messages":["python3 autocode.py ... codex exec ..."]}'))
        # Real duplicate runners and orphaned codex exec children are detected.
        self.assertTrue(s.duplicate_runner_command(f"python3 /ws/autocode/tools/autocode.py task --run-dir {marker}"))
        self.assertTrue(s.duplicate_runner_command(f"/usr/bin/python3 /ws/autocode/tools/autocode.py --status {marker}"))
        self.assertTrue(s.duplicate_runner_command(f"codex exec -C /ws -o {marker}/out.json"))
        self.assertTrue(s.duplicate_runner_command("codex exec resume 01abc"))

    def test_transport_drift_ignores_default_flips_when_roles_override_everything(self):
        full={"astra":{"model":"gpt-6-astra","reasoning_effort":"high"},
              "terra":{"model":"gpt-5.6-terra","reasoning_effort":"high"},
              "sol":{"model":"gpt-5.6-sol","reasoning_effort":"high"}}
        checkpoint={"auth_mode":"ChatGPT","model":"glm-5.3","model_reasoning_effort":"low",
                    "model_provider":None,"openai_base_url":None,
                    "environment_auth_present":False,"environment_base_url_present":False}
        flipped=dict(checkpoint, model="gpt-5.6-terra", model_reasoning_effort="medium")
        # A desktop app flipping saved defaults cannot affect fully-overridden launches.
        self.assertFalse(s.transport_drift(flipped, checkpoint, full))
        # Provider/auth/base-url drift always matters.
        self.assertTrue(s.transport_drift(dict(flipped, model_provider="ZAI"), checkpoint, full))
        self.assertTrue(s.transport_drift(dict(flipped, auth_mode="api"), checkpoint, full))
        self.assertTrue(s.transport_drift(dict(flipped, openai_base_url="https://x"), checkpoint, full))
        # Without per-role overrides, top-level defaults matter again.
        partial={k:{**v, "model":None} for k,v in full.items()}
        self.assertTrue(s.transport_drift(flipped, checkpoint, partial))

    def test_same_command_normalizes_wrapper_on_either_side(self):
        event="/bin/zsh -lc " + shlex.quote("/usr/local/bin/python3 -c 'print(1)'")
        bare="/usr/local/bin/python3 -c 'print(1)'"
        self.assertTrue(s.same_command(event, bare))
        # A report quoting the recorded event line verbatim, wrapper included, also matches.
        self.assertTrue(s.same_command(event, event))
        self.assertTrue(s.same_command(event, event.replace("-lc", "-l -c")))
        self.assertFalse(s.same_command(event, "/usr/local/bin/python3 -c 'print(2)'"))

    def test_same_command_preserves_shell_quoting(self):
        pairs = (
            ("printf '%s\\n' '&&' false", "printf '%s\\n' && false"),
            ("printf '%s\\n' ';' false", "printf '%s\\n' ; false"),
            ("printf '%s\\n' '|' false", "printf '%s\\n' | false"),
            ("printf '%s\\n' '>' /no/such/autocode-evidence", "printf '%s\\n' > /no/such/autocode-evidence"),
        )
        for printed, executed in pairs:
            with self.subTest(printed=printed):
                ran = subprocess.run(printed, shell=True, executable="/bin/zsh", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                claimed = subprocess.run(executed, shell=True, executable="/bin/zsh", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.assertEqual(0, ran.returncode)
                self.assertNotEqual(0, claimed.returncode)
                self.assertFalse(s.same_command(printed, executed))
                wrapped = "/bin/zsh -lc " + shlex.quote(printed)
                self.assertTrue(s.same_command(wrapped, printed))
                self.assertFalse(s.same_command(wrapped, executed))

    def test_explicit_models_high_and_rotation_rollback_persist(self):
        args=SimpleNamespace(astra_model="gpt-6-astra", terra_model="gpt-5.6-terra",
            sol_model="gpt-5.6-sol", reasoning_effort="high", headroom="off",
            context_soft_tokens=7000, rotate_after_input_tokens=0)
        result=runner.configure(args,self.state)
        self.assertEqual("gpt-5.6-terra",result["roles"]["terra"]["model"])
        self.assertEqual({"high"},{r["reasoning_effort"] for r in result["roles"].values()})
        self.assertEqual(0,result["rotation_after_input_tokens"])
        self.assertEqual(7000,result["context_soft_tokens"])
        self.assertFalse(result["headroom"]["enabled"])

    def test_role_reasoning_effort_overrides_shared_value(self):
        args=SimpleNamespace(astra_model=None,terra_model=None,sol_model=None,
            reasoning_effort="medium",astra_reasoning_effort="xhigh",
            terra_reasoning_effort=None,sol_reasoning_effort="high",headroom=None,
            context_soft_tokens=None,rotate_after_input_tokens=None)
        result=runner.configure(args,self.state)
        self.assertEqual("xhigh",result["roles"]["astra"]["reasoning_effort"])
        self.assertEqual("medium",result["roles"]["terra"]["reasoning_effort"])
        self.assertEqual("high",result["roles"]["sol"]["reasoning_effort"])

    def test_provider_saved_per_role_and_kept_on_resume(self):
        args=SimpleNamespace(astra_model=None,terra_model=None,sol_model=None,terra_provider="ZAI",
            reasoning_effort=None,headroom=None,context_soft_tokens=None,rotate_after_input_tokens=None)
        result=runner.configure(args,self.state)
        self.assertEqual("ZAI",result["roles"]["terra"]["provider"])
        self.assertNotIn("provider",result["roles"]["astra"])
        resumed={**self.state,"settings":result}
        kept=runner.configure(SimpleNamespace(astra_model=None,terra_model=None,sol_model=None,
            reasoning_effort=None,headroom=None,context_soft_tokens=None,rotate_after_input_tokens=None),resumed)
        self.assertEqual("ZAI",kept["roles"]["terra"]["provider"])
        self.assertEqual({**{r:f"model-{r}" for r in ("astra","terra","sol")},
                          "completion": "gpt-5.6-sol"},
                         {r:settings["model"] for r,settings in kept["roles"].items()})

    def test_model_and_provider_precedence_on_new_run(self):
        local={"model":"local-model","model_reasoning_effort":"high","model_provider":None}
        history=[{"role":"sol","command":["codex","exec","-c",'model_reasoning_effort="high"',
                  "-c",'model_provider="ZAI"',"--model","glm-5.3"]}]
        state={k:v for k,v in self.state.items() if k!="settings"}
        state["history"]=history
        args=SimpleNamespace(astra_model=None,terra_model="custom-terra",sol_model=None,terra_provider="ZAI",
            reasoning_effort=None,headroom=None,context_soft_tokens=None,rotate_after_input_tokens=None,
            legacy_iteration_ceiling=None,max_iterations=15,max_seconds=None,max_reported_tokens=None,no_progress_limit=3)
        with patch.object(s,"local_settings",return_value=local):
            result=runner.configure(args,state)
        self.assertEqual("gpt-5.6-sol",result["roles"]["astra"]["model"])  # role default, not local-model
        self.assertEqual("custom-terra",result["roles"]["terra"]["model"])  # explicit flag
        self.assertEqual("glm-5.3",result["roles"]["sol"]["model"])         # saved legacy launch
        self.assertEqual("ZAI",result["roles"]["terra"]["provider"])   # explicit flag
        self.assertEqual("ZAI",result["roles"]["sol"]["provider"])     # recovered from saved command

    def test_new_run_has_unlimited_completion_caps_by_default(self):
        state = {k: v for k, v in self.state.items() if k != "settings"}
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None,
            reasoning_effort=None, headroom=None, context_soft_tokens=None,
            rotate_after_input_tokens=None, legacy_iteration_ceiling=None,
            max_iterations=None, max_seconds=None, max_reported_tokens=None,
            no_progress_limit=None)
        with patch.object(s, "local_settings", return_value={}):
            result = runner.configure(args, state)
        self.assertIsNone(result["limits"]["iteration_ceiling"])
        self.assertEqual(0, result["limits"]["no_progress_batches"])
        self.assertEqual(0, result["milestone_checkpoints"]["max_seconds"])
        self.assertIsNone(result["milestone_checkpoints"]["max_replans"])
        self.assertEqual(0, result["milestone_checkpoints"]["stalled_reviews"])
        self.assertIsNone(result["roles"]["astra"]["provider"])        # local Codex default

    def test_legacy_completed_sol_is_not_replayed_after_alphabetic_sort(self):
        decision=self.decision(); decision.pop("plan"); decision.pop("affected_paths")
        self.legacy_final("astra",decision)
        self.legacy_final("terra",{"summary":"done","changed_files":[],"commands_run":[],"results":[],"remaining_risks":[]})
        sol={"verdict":"PASS","findings":[],"checks_run":[],"unverified_criteria":[]}
        self.legacy_final("sol",sol)
        old={"version":1,"workspace":str(self.root),"task":"same","status":"RUNNING","iteration":5,"sessions":{},"history":[]}
        migrated=s.migrate_v1(old,self.run,self.root,self.settings,runner.SCHEMA_DIR)
        self.assertEqual("astra_review",migrated["next_stage"])
        self.assertEqual(["astra_plan","terra","sol"],[x["stage"] for x in migrated["stages"]])

    def test_workspace_lock_prevents_second_writer(self):
        with s.workspace_lock(self.root):
            with self.assertRaises(s.Paused):
                with s.workspace_lock(self.root): pass

    def test_other_workspace_has_separate_lock(self):
        with s.workspace_lock(self.root):
            with s.workspace_lock(self.root/"other"): pass

    def test_run_locks_do_not_block_independent_runs_in_one_workspace(self):
        first=self.root/".autocode"/"runs"/"first"
        second=self.root/".autocode"/"runs"/"second"
        with s.run_lock(first):
            with s.run_lock(second): pass

    def test_run_lock_blocks_duplicate_writer_for_same_run(self):
        with s.run_lock(self.run):
            with self.assertRaises(s.Paused):
                with s.run_lock(self.run): pass

    def test_process_marker_for_another_run_does_not_block_this_run(self):
        other=self.root/".autocode"/"runs"/"other"
        other.mkdir(parents=True)
        s.atomic_json(self.root/".autocode"/"active-processes.json", {"run_dir":str(other),"processes":[]})
        with patch.object(s.subprocess,"run",return_value=subprocess.CompletedProcess([],0,stdout="")):
            s.assert_no_legacy_process(self.run,self.root)

    def test_active_legacy_guard_and_process_check_failure(self):
        result=subprocess.CompletedProcess([],0,stdout=f"101 python tools/autocode.py --run-dir {self.run}\n")
        with patch.object(s.subprocess,"run",return_value=result):
            with self.assertRaisesRegex(s.Paused,"101"): s.assert_no_legacy_process(self.run,self.root)
        with patch.object(s.subprocess,"run",return_value=subprocess.CompletedProcess([],1,stdout="")):
            with self.assertRaisesRegex(s.Paused,"Cannot inspect"): s.assert_no_legacy_process(self.run,self.root)

    def test_process_guard_allows_isolated_workspace_when_sandbox_denies_process_listing(self):
        result=subprocess.CompletedProcess([],1,stdout="",stderr="ps: operation not permitted")
        with patch.object(s.subprocess,"run",return_value=result):
            s.assert_no_legacy_process(self.run,self.root)

    def test_process_guard_allows_isolated_workspace_when_sandbox_raises_permission_error(self):
        with patch.object(s.subprocess,"run",side_effect=OSError("operation not permitted")):
            s.assert_no_legacy_process(self.run,self.root)

    def test_legacy_resume_after_terra_does_not_replay_it(self):
        d=self.decision(); d.pop("plan");d.pop("affected_paths")
        self.legacy_final("astra",d)
        terra={"summary":"done","changed_files":["x"],"commands_run":[],"results":["PASS"],"remaining_risks":[]}
        self.legacy_final("terra",terra)
        old={"version":1,"workspace":str(self.root),"task":"same task","status":"RUNNING","iteration":5,"sessions":{},"history":[]}
        migrated=s.migrate_v1(old,self.run,self.root,self.settings,runner.SCHEMA_DIR)
        self.assertEqual("sol",migrated["next_stage"])
        self.assertEqual("same task",migrated["task"])
        self.assertEqual("terra-explicit",migrated["sessions"]["terra"])
        self.assertEqual(2,len(migrated["stages"]))
        self.assertEqual(1,old["version"])

    def test_incomplete_legacy_turn_pauses(self):
        p=self.run/"iterations/005/terra.jsonl";p.parent.mkdir(parents=True)
        p.write_text('{"type":"turn.started"}\n')
        old={"version":1,"workspace":str(self.root),"task":"same","status":"RUNNING","iteration":5,"sessions":{}}
        m=s.migrate_v1(old,self.run,self.root,self.settings,runner.SCHEMA_DIR)
        self.assertEqual("PAUSED_UNCERTAIN_STAGE",m["status"])

    def test_validated_final_rejects_missing_fields_and_invalid_types(self):
        schema=s.read(runner.SCHEMA_DIR/"v2/astra-decision.schema.json")
        s.validate_schema(self.decision(),schema)
        for bad in ({"status":"TASK_COMPLETE"},{**self.decision(),"plan":"wrong"}):
            with self.assertRaises(ValueError): s.validate_schema(bad,schema)

    def test_no_transcripts_in_packets_and_relevant_handoff(self):
        self.state["history"]=[{"transcript":"PRIVATE_BULK_HISTORY"}]
        self.state["unresolved_findings"]=[{"finding":"specific bug"}]
        self.state["plan"]=["preserve contracts"]
        text,metrics=s.context_packet(self.state,"terra",self.run/"state.json")
        self.assertNotIn("PRIVATE_BULK_HISTORY",text)
        self.assertIn("specific bug",text)
        self.assertIn("repair the full cohort",text)
        self.assertIn(str(self.run/'evidence'),text)
        self.assertIn('Do not create a top-level evidence/',text)
        self.assertIn('Source writes must stay within current_task.affected_paths',text)
        self.assertGreater(metrics["estimated_prompt_tokens"],0)

    def test_context_packet_includes_only_runner_provided_private_source_exceptions(self):
        self.state["private_source_exceptions"]=[{
            "sourceId":"fixture-source", "workspacePath":".autocode/private/source.pdf",
            "canonicalPath":"/outside/source.pdf", "sha256":"a" * 64,
            "access":"READ_ONLY_PRIVATE_CACHE"
        }]
        text,_=s.context_packet(self.state,"terra",self.run/"state.json")
        self.assertIn("fixture-source",text)
        self.assertIn("READ_ONLY_PRIVATE_CACHE",text)

    def test_compaction_keeps_all_errors_and_test_totals(self):
        text="ok\nok\n.....\nError first\nframe.rb:9\nError second\n3 tests, 2 failures\n"
        c=s.compact_output(text)
        for fragment in ["Error first","frame.rb:9","Error second","3 tests, 2 failures"]:
            self.assertIn(fragment,c["content"])
        self.assertEqual({"ok":1},c["repeated_lines"])
        self.assertEqual(1,c["omitted_progress_lines"])
        self.assertEqual(text,s.compact_output(text,enabled=False)["content"])
        self.assertEqual({"errors":["A","B"]},s.compact_output('{"errors":["A","B"]}')["content"])

    def test_rate_and_budget_are_not_success(self):
        p=self.run/"events.jsonl"
        for error,expected in [("rate limit 429","PAUSED_RATE_LIMIT"),("usage limit quota","PAUSED_BUDGET"),("timeout","PAUSED_PROVIDER_UNCERTAIN")]:
            p.write_text(json.dumps({"type":"turn.failed","error":{"message":error}}))
            self.assertEqual(expected,s.failure_status(p))

    def test_metrics_unknown_is_not_zero(self):
        m=s.event_metrics(self.run/"missing")
        self.assertIsNone(m["provider_tokens"]["input_tokens"])
        self.assertIsNone(m["provider_requests"])

    def test_rotation_archives_explicit_session_without_task_restart(self):
        self.state["stages"]=[{"role":"terra","metrics":{"provider_tokens":{"input_tokens":1200000}}}]
        runner.rotate_if_needed(self.state,"terra",self.run)
        self.assertNotIn("terra",self.state["sessions"])
        self.assertEqual("t-session",self.state["session_rotations"][0]["old_session"])
        self.assertEqual(5,self.state["iteration"])
        self.assertEqual("a-session",self.state["sessions"]["astra"])

    def valid_completion(self):
        current=s.snapshot(self.root)
        self.state["validation"]={"verdict":"PASS","criteria_revision":self.state["criteria_revision"],
            "source_revision":current["revision"],"checks":[{"exit_code":0}],"findings":[],"unverified_criteria":[],
            "criterion_results":[{"id":"C1","status":"PASS","evidence_refs":[str(self.evidence)]}],
            "evidence_hashes":{str(self.evidence):s.file_hash(self.evidence)}}
        return current

    def test_completion_requires_current_code_criteria_sol_and_evidence(self):
        current=self.valid_completion()
        self.assertTrue(s.completion_ready(self.state,self.decision("TASK_COMPLETE"),current))
        for key,value in [("verdict","FAIL"),("source_revision","stale"),("criteria_revision","different"),("evidence_hashes",{}),("checks",[])]:
            st=copy.deepcopy(self.state);st["validation"][key]=value
            self.assertFalse(s.completion_ready(st,self.decision("TASK_COMPLETE"),current),key)
        self.evidence.write_text("now failing")
        self.assertFalse(s.completion_ready(self.state,self.decision("TASK_COMPLETE"),current))

    def test_dirty_untracked_source_invalidates_validation(self):
        current=self.valid_completion()
        (self.root/"untracked.py").write_text("changed")
        self.assertNotEqual(current["revision"],s.snapshot(self.root)["revision"])
        self.assertFalse(s.completion_ready(self.state,self.decision("TASK_COMPLETE"),s.snapshot(self.root)))

    def test_executable_mode_invalidates_validation(self):
        script = self.root / "start.sh"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o644)
        current = self.valid_completion()
        script.chmod(0o755)
        self.assertNotEqual(current["revision"], s.snapshot(self.root)["revision"])
        self.assertFalse(s.completion_ready(self.state, self.decision("TASK_COMPLETE"), s.snapshot(self.root)))

    def test_submodule_edits_invalidate_validation(self):
        source = self.root / ".autocode/module-source"
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        (source / "code.py").write_text("value = 1\n")
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=f@example.test",
                        "commit", "-qm", "fixture"], check=True)
        subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "module"],
                       cwd=self.root, check=True)
        current = self.valid_completion()
        (self.root / "module/code.py").write_text("value = 2\n")
        self.assertNotEqual(current["revision"], s.snapshot(self.root)["revision"])
        self.assertFalse(s.completion_ready(self.state, self.decision("TASK_COMPLETE"), s.snapshot(self.root)))

    def test_completion_acceptance_is_an_exclusive_existing_run_action(self):
        for args in (["--accept-completion"], ["--accept-completion", "--show-goal", "--run-dir", str(self.run)]):
            with patch.object(sys, "argv", ["autocode", *args]), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    runner.main()
                self.assertEqual(2, caught.exception.code)

    def test_fresh_dry_run_reports_default_engine_without_writes(self):
        out = io.StringIO()
        before = list((self.root / ".autocode/runs").iterdir())
        with patch.object(sys, "argv", ["autocode", "idea", "--workspace", str(self.root), "--dry-run"]), contextlib.redirect_stdout(out):
            self.assertEqual(0, runner.main())
        self.assertEqual("opencode", json.loads(out.getvalue())["engine"])
        self.assertEqual(before, list((self.root / ".autocode/runs").iterdir()))

    def test_abandon_uncertain_attempt_retains_edits_and_clears_session(self):
        before = self.valid_completion()
        base = self.run / "iterations/005/terra-01"
        base.parent.mkdir(parents=True)
        s.atomic_json(base.with_suffix(".before.json"), before)
        base.with_suffix(".jsonl").write_text('{"type":"thread.started","thread_id":"t-session"}\n')
        (self.root / "partial.py").write_text("# preserved partial implementation\n")
        record = {"role":"terra", "stage":"terra", "iteration":5, "duration_seconds":8,
                  "output":str(base.with_suffix(".json")), "events":str(base.with_suffix(".jsonl")),
                  "before_ref":str(base.with_suffix(".before.json")), "exit_code":-15}
        self.state["active_stage"] = record
        unchanged = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            runner.abandon_stage(self.state, self.run, self.root, "005/terra-02")
        self.assertEqual(unchanged, self.state)
        record["processes"] = [{"pid":123, "started":"fixture", "group":123}]
        with patch.object(runner.processes, "live_processes", return_value=[{"pid":123}]):
            with self.assertRaises(s.Paused):
                runner.abandon_stage(self.state, self.run, self.root, "005/terra-01")
        with patch.object(runner.processes, "live_processes", return_value=[]):
            runner.abandon_stage(self.state, self.run, self.root, "005/terra-01")
        self.assertEqual("PAUSED_STAGE_ABANDONED", self.state["status"])
        self.assertEqual("astra_review", self.state["next_stage"])
        self.assertNotIn("active_stage", self.state)
        self.assertNotIn("terra", self.state["sessions"])
        self.assertNotIn("validation", self.state)
        self.assertEqual(8, self.state["active_seconds"])
        self.assertTrue((self.root / "partial.py").exists())
        archived = self.state["stages"][-1]
        self.assertTrue(archived["abandoned"])
        self.assertEqual(["partial.py"], archived["changed_files"])
        self.assertTrue(Path(archived["events"]).is_file())

    def test_abandon_planning_report_repair_routes_to_owner_stage(self):
        before = self.valid_completion()
        base = self.run / "iterations/005/glm_revise_report_repair-01"
        base.parent.mkdir(parents=True)
        s.atomic_json(base.with_suffix(".before.json"), before)
        base.with_suffix(".jsonl").write_text('{"type":"turn.completed"}\n')
        record = {"role":"glm", "stage":"glm_revise_report_repair",
                  "original_stage":"glm_revise", "planning":True, "report_only":True,
                  "iteration":5, "duration_seconds":1, "exit_code":0, "processes":[],
                  "output":str(base.with_suffix(".json")),
                  "events":str(base.with_suffix(".jsonl")),
                  "before_ref":str(base.with_suffix(".before.json"))}
        self.state["active_stage"] = record
        self.state["pending_report_repair"] = {"original":{"stage":"glm_revise"}}
        runner.abandon_stage(self.state, self.run, self.root, "005/glm_revise_report_repair-01")
        self.assertEqual("PAUSED_STAGE_ABANDONED", self.state["status"])
        self.assertEqual("glm_revise", self.state["next_stage"])
        self.assertNotIn("pending_report_repair", self.state)
        self.assertTrue(self.state["stages"][-1]["abandoned"])

    def test_abandon_completion_report_repair_requires_fresh_validation(self):
        before = self.valid_completion()
        base = self.run / 'iterations/005/astra_review_report_repair-01'
        base.parent.mkdir(parents=True)
        s.atomic_json(base.with_suffix('.before.json'), before)
        base.with_suffix('.jsonl').write_text('{"type":"thread.started","thread_id":"incomplete"}\n')
        self.state['sessions']['completion'] = 'completion-session'
        self.state['human_reviews'] = {'C1': {'approved': True}}
        self.state['active_stage'] = {
            'role': 'astra', 'route_role': 'completion', 'stage': 'astra_review_report_repair',
            'original_stage': 'astra_review', 'report_only': True, 'iteration': 5,
            'exit_code': -15, 'processes': [], 'duration_seconds': 1,
            'output': str(base.with_suffix('.json')), 'events': str(base.with_suffix('.jsonl')),
            'before_ref': str(base.with_suffix('.before.json'))}
        pending = {'original': {'stage': 'astra_review'}}
        self.state['pending_report_repair'] = pending
        runner.abandon_stage(self.state, self.run, self.root, '005/astra_review_report_repair-01')
        self.assertEqual('sol', self.state['next_stage'])
        self.assertIn('Validator', self.state['stop_reason'])
        self.assertNotIn('validation', self.state)
        self.assertNotIn('active_stage', self.state)
        self.assertNotIn('pending_report_repair', self.state)
        self.assertEqual(pending, self.state['report_repair_archive'][-1]['repair'])
        self.assertNotIn('completion', self.state['sessions'])
        self.assertEqual({}, self.state['human_reviews'])
        self.assertEqual(before['revision'], s.snapshot(self.root)['revision'])

    def test_explicit_resume_recovers_legacy_archived_repair_stage(self):
        self.state['settings']['joint_planning'] = True
        self.state.update(status='PAUSED_INVALID_OUTPUT', next_stage='glm_revise_report_repair',
                          recovery_context={'attempt_id':'005/glm_revise_report_repair-01'})
        self.state['stages'].append({'stage':'glm_revise_report_repair', 'original_stage':'glm_revise',
                                     'iteration':5, 'output':str(self.run / 'iterations/005/glm_revise_report_repair-01.json'),
                                     'abandoned':True})
        self.assertTrue(runner.prepare_planning_retry(self.state, self.run))
        self.assertEqual('glm_revise', self.state['next_stage'])

    def test_saved_abandoned_completion_recovery_is_narrow(self):
        current = self.valid_completion()
        abandoned = {'stage': 'astra_review_report_repair', 'original_stage': 'astra_review',
                     'role': 'astra', 'iteration': 5,
                     'output': str(self.run / 'astra_review_report_repair-01.json'),
                     'source_revision': current['revision'], 'abandoned': True, 'rejected': True}
        selected = runner.attempt_id(abandoned)
        self.state.update(status='PAUSED_REPEATED_FAILURE', next_stage='astra_review',
                          recovery_context={'attempt_id': selected, 'source_revision': current['revision']},
                          user_events=[{'kind': 'stage_abandoned', 'attempt_id': selected}])
        self.state['validation_archive'] = [{'reason': 'Uncertain stage abandoned',
                                            'validation': self.state.pop('validation')}]
        self.state['stages'].append(abandoned)
        error = s.Paused('PAUSED_COMPLETION_GATE',
            'Completion rejected: missing, stale, failed or unverified independent evidence')
        for attempt in range(3):
            record = {'stage': 'astra_review_report_repair' if attempt < 2 else 'astra_review',
                      'original_stage': 'astra_review', 'role': 'astra', 'iteration': 5,
                      'output': str(self.run / f'rejected-{attempt}.json'),
                      'source_revision': current['revision'], 'rejected': True,
                      'rejection_reason': str(error)}
            runner.failures.record(self.state, record, error, s.now())
            self.state['stages'].append(record)
            if attempt == 0:
                self.state['stages'].append({'stage': 'resolver', 'runner_owned': True,
                                            'decision': {'action': 'retry'}})
        s.atomic_json(self.run / 'state.json', self.state)
        saved = (self.run / 'state.json').read_bytes()
        for override in ({'validation': {'verdict': 'PASS'}}, {'active_stage': {'stage': 'astra_review'}},
                         {'pending_report_repair': {'original': {'stage': 'astra_review'}}},
                         {'uncertain_artifacts': 'unresolved'}, {'recovery_context': {}},
                         {'user_events': []}, {'validation_archive': []},
                         {'status': 'PAUSED_BUDGET'}, {'next_stage': 'sol'},
                         {'current_task': {'id': 'different-task'}}):
            with self.subTest(override=override):
                state = {**copy.deepcopy(self.state), **override}
                before = copy.deepcopy(state)
                self.assertFalse(runner.prepare_abandoned_completion_revalidation(state, self.run, self.root))
                self.assertEqual(before, state)
                self.assertEqual(saved, (self.run / 'state.json').read_bytes())
        for mismatch in ('other_error', 'accepted_progress', 'changed_source', 'validator_failure'):
            with self.subTest(mismatch=mismatch):
                state = copy.deepcopy(self.state)
                if mismatch == 'other_error':
                    state['stages'][1]['rejection_reason'] = 'Completion rejected: blocking findings'
                elif mismatch == 'accepted_progress':
                    state['stages'].append({'stage': 'sol', 'source_revision': current['revision']})
                elif mismatch == 'changed_source':
                    state['recovery_context']['source_revision'] = 'old-source'
                else:
                    for attempt in range(3):
                        runner.failures.record(state, {'stage': 'sol', 'iteration': 5,
                            'source_revision': current['revision'], 'output': str(self.run / f'sol-{attempt}.json')},
                            ValueError('invalid validation'), s.now())
                before = copy.deepcopy(state)
                self.assertFalse(runner.prepare_abandoned_completion_revalidation(state, self.run, self.root))
                self.assertEqual(before, state)
                self.assertEqual(saved, (self.run / 'state.json').read_bytes())
        history = copy.deepcopy(self.state['failure_history'])
        self.assertTrue(runner.prepare_abandoned_completion_revalidation(self.state, self.run, self.root))
        self.assertEqual('sol', self.state['next_stage'])
        self.assertEqual('PAUSED_STAGE_ABANDONED', self.state['status'])
        self.assertEqual(history, self.state['failure_history'])
        self.assertNotIn('validation', self.state)
        self.assertEqual(selected, self.state['reconciliation_notes'][-1]['attempt_id'])
        reloaded = s.read(self.run / 'state.json')
        runner.repeated_failure_resume_guard(reloaded, self.root)
        self.assertFalse(runner.prepare_abandoned_completion_revalidation(reloaded, self.run, self.root))

    def test_missing_evidence_and_outside_project_rejected(self):
        for refs in ([],["nope"],["/etc/hosts"]):
            with self.assertRaises(ValueError): s.evidence_hashes(refs,self.root,self.run)

    def test_check_must_match_independent_executed_event(self):
        p=self.run/"sol.jsonl"
        p.write_text(json.dumps({"type":"item.completed","item":{"type":"command_execution","id":"x","command":"ruby tests.rb","exit_code":1,"aggregated_output":"FAIL"}}))
        check={"command":"ruby tests.rb","exit_code":1,"evidence_ref":"event:x"}
        s.verify_checks([check],self.root,p)
        with self.assertRaises(ValueError):s.verify_checks([{**check,"exit_code":0}],self.root,p)

    def test_headroom_disabled_direct_and_unverified_enabled_pauses(self):
        self.assertEqual([],s.transport_arguments(self.settings))
        self.settings["headroom"]["enabled"]=True
        with self.assertRaisesRegex(s.Paused,"Headroom disabled"):s.transport_arguments(self.settings)

    def test_criteria_cannot_silently_weaken(self):
        d=self.decision(); d["acceptance_criteria"]=[{**self.criteria[0],"criterion":"weaker"}]
        with self.assertRaises(s.Paused):runner.apply_result(self.state,"astra_review",d,{},self.root,self.run)

    def test_continue_advances_automatically_at_review(self):
        runner.apply_result(self.state,"astra_review",self.decision(),{"output":"report"},self.root,self.run)
        self.assertEqual("terra",self.state["next_stage"])
        self.assertEqual(6,self.state["iteration"])

    def test_capture_tool_failure_preserves_full_output(self):
        path=self.run/"evidence/fail.json"
        cmd=[sys.executable,str(Path(runner.__file__).resolve()),"capture","--output",str(path),"--",sys.executable,"-c","print('middle error'); raise SystemExit(2)"]
        result=subprocess.run(cmd,cwd=self.root,capture_output=True,text=True)
        self.assertEqual(2,result.returncode)
        receipt=json.loads(result.stdout)
        self.assertEqual(2,receipt["exit_code"])
        self.assertIn("middle error",Path(receipt["full_output"]).read_text())

    def test_role_launch_keeps_explicit_session_model_effort_and_sandbox(self):
        commands=[]
        value={"summary":"done","changed_files":[],"commands_run":[],"results":[],"remaining_risks":[],"evidence_refs":[str(self.evidence)]}
        class Child:
            pid=987654321
            def __init__(child, command, **kwargs):
                commands.append(command)
                Path(command[command.index("-o")+1]).write_text(json.dumps(value))
                kwargs["stdout"].write('{"type":"thread.started","thread_id":"t-session"}\n')
                kwargs["stdout"].write(json.dumps({"type":"turn.completed","usage":{"input_tokens":9,"cached_input_tokens":3,"output_tokens":2}})+"\n")
            def wait(child): return 0
        with patch.object(runner.subprocess,"Popen",Child):
            # Snapshot/diff need real subprocesses, so mock those independent
            # filesystem observations rather than making Popen handle git too.
            with patch.object(s,"snapshot",return_value={"head":"h","files":{},"revision":"r"}), patch.object(runner.subprocess,"run"), \
                 patch.object(runner.processes,"process_table",return_value={}), \
                 patch.object(runner.processes,"wait_for_stage",return_value=(0,False)):
                self.settings["roles"]["terra"]["provider"]="ZAI"
                value,record=runner.run_role(role="terra",prompt="small handoff",sandbox="workspace-write",workspace=self.root,run_dir=self.run,
                    state=self.state,schema=runner.SCHEMA_DIR/"v2/terra-report.schema.json",model="gpt-5.6-terra",allow_write=True,dry_run=False)
        command=commands[0]
        self.assertEqual("t-session",command[command.index("resume")+1])
        self.assertEqual("gpt-5.6-terra",command[command.index("--model")+1])
        self.assertIn('model_reasoning_effort="high"',command)
        self.assertIn('model_provider="ZAI"',command)
        self.assertNotIn("--approve-for-me",command)
        self.assertNotIn("--last",command)
        self.assertEqual("workspace-write",command[command.index("--sandbox")+1])
        self.assertEqual(9,record["metrics"]["provider_tokens"]["input_tokens"])

    def test_stage_timeout_stops_stalled_provider_and_preserves_checkpoint(self):
        self.settings["limits"] = {"stage_timeout_seconds": 1}
        class Child:
            pid = 12345
            def __init__(child, command, **kwargs): pass
        with patch.object(runner.subprocess, "Popen", Child), \
             patch.object(s, "snapshot", return_value={"head":"h", "files":{}, "revision":"r"}), \
             patch.object(runner.processes, "process_table", return_value={}), \
             patch.object(runner.processes, "wait_for_stage", return_value=(-15,True)):
            with self.assertRaises(s.Paused) as caught:
                runner.run_role(role="terra", prompt="small handoff", sandbox="workspace-write", workspace=self.root,
                    run_dir=self.run, state=self.state, schema=runner.SCHEMA_DIR/"v2/terra-report.schema.json",
                    model="gpt-5.6-terra", allow_write=True, dry_run=False)
        self.assertEqual("PAUSED_PROVIDER_TIMEOUT", caught.exception.status)
        active = s.read(self.run / "state.json")["active_stage"]
        self.assertTrue(active["timed_out"])
        self.assertEqual(1, active["stage_timeout_seconds"])
        self.assertTrue(active["accounted"])

    def test_automatic_timeout_recovery_archives_stopped_terra_and_continues_glm(self):
        before = s.snapshot(self.root)
        base = self.run / "iterations/005/terra-01"
        base.parent.mkdir(parents=True)
        s.atomic_json(base.with_suffix(".before.json"), before)
        base.with_suffix(".jsonl").write_text('{"type":"thread.started","thread_id":"expired-session"}\n')
        (self.root / "partial.py").write_text("# retained timeout work\n")
        self.state.update(status="RUNNING", phase="EXECUTING")
        self.state["settings"]["workflow"]={"mode":runner.workflow.FINAL_MODE}
        record = {"role":"terra", "stage":"terra", "iteration":5, "duration_seconds":3,
                  "output":str(base.with_suffix(".json")), "events":str(base.with_suffix(".jsonl")),
                  "before_ref":str(base.with_suffix(".before.json")), "exit_code":-15, "timed_out":True,
                  "processes":[]}
        self.state["active_stage"] = record
        # Startup reconciliation classifies incomplete provider logs as uncertain;
        # the durable timed_out record, not that later wording, authorizes recovery.
        error = s.Paused("PAUSED_PROVIDER_TIMEOUT", "timed out")
        self.assertTrue(runner.automatically_recover_timed_out_stage(self.state, self.run, self.root, error))
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual("terra", self.state["next_stage"])
        self.assertNotIn("active_stage", self.state)
        self.assertNotIn("terra", self.state["sessions"])
        self.assertTrue((self.root / "partial.py").exists())
        archived = self.state["stages"][-1]
        self.assertTrue(archived["automatic_recovery"])
        self.assertEqual(["partial.py"], archived["changed_files"])
        self.assertEqual(1, len(self.state["automatic_timeout_recoveries"]))
        self.assertEqual(1, self.state["no_progress_batches"])

    def test_automatic_timeout_recovery_refuses_terminal_attempt(self):
        before = s.snapshot(self.root)
        base = self.run / "iterations/005/terra-01"
        base.parent.mkdir(parents=True)
        s.atomic_json(base.with_suffix(".before.json"), before)
        base.with_suffix(".jsonl").write_text('{"type":"turn.completed"}\n')
        record = {"role":"terra", "stage":"terra", "iteration":5,
                  "output":str(base.with_suffix(".json")), "events":str(base.with_suffix(".jsonl")),
                  "before_ref":str(base.with_suffix(".before.json")), "exit_code":-15, "timed_out":True}
        self.state["active_stage"] = record
        original = copy.deepcopy(self.state)
        error = s.Paused("PAUSED_PROVIDER_TIMEOUT", "timed out")
        self.assertFalse(runner.automatically_recover_timed_out_stage(self.state, self.run, self.root, error))
        self.assertEqual(original, self.state)

    def test_automatic_external_directory_denial_retries_workspace_only(self):
        before = s.snapshot(self.root)
        base = self.run / "iterations/005/terra-01"
        base.parent.mkdir(parents=True)
        s.atomic_json(base.with_suffix(".before.json"), before)
        base.with_suffix(".jsonl").write_text(
            '{"type":"thread.started","thread_id":"t-session"}\n'
            'permission requested: external_directory (/tmp/*); auto-rejecting\n')
        record = {"role":"terra", "stage":"terra", "iteration":5, "duration_seconds":1,
                  "output":str(base.with_suffix(".json")), "events":str(base.with_suffix(".jsonl")),
                  "before_ref":str(base.with_suffix(".before.json")), "exit_code":0, "timed_out":False,
                  "processes":[]}
        self.state["active_stage"] = record
        error = s.Paused("PAUSED_UNCERTAIN_STAGE", "missing terminal turn")
        self.assertTrue(runner.automatically_recover_external_directory_denial(self.state, self.run, self.root, error))
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual("terra", self.state["next_stage"])
        self.assertNotIn("active_stage", self.state)
        self.assertEqual(1, len(self.state["automatic_permission_recoveries"]))
        self.assertEqual(1, runner.recovery_count(self.state))
        self.assertIn("workspace-contained", self.state["recovery_context"]["instruction"])

    def test_crash_after_completed_terra_reconciles_without_reexecution(self):
        before=s.snapshot(self.root)
        base=self.run/"iterations/005/terra-01";base.parent.mkdir(parents=True)
        s.atomic_json(base.with_suffix(".before.json"),before)
        value={"summary":"saved","changed_files":[],"commands_run":[],"results":[],"remaining_risks":[],"evidence_refs":[str(self.evidence)]}
        s.atomic_json(base.with_suffix(".json"),value)
        base.with_suffix(".jsonl").write_text('{"type":"turn.completed"}\n')
        self.state["active_stage"]={"role":"terra","stage":"terra","iteration":5,"output":str(base.with_suffix(".json")),
            "events":str(base.with_suffix(".jsonl")),"schema":str(runner.SCHEMA_DIR/"v2/terra-report.schema.json"),
            "before_ref":str(base.with_suffix(".before.json"))}
        with patch.object(runner,"run_role",side_effect=AssertionError("must not replay")):
            runner.reconcile_active(self.state,self.run,self.root)
        self.assertEqual("sol",self.state["next_stage"])
        self.assertNotIn("active_stage",self.state)

    def test_rejected_completed_discovery_archives_instead_of_reapplying(self):
        from goal_fixtures import body
        self.state.update(version=3,next_stage="astra_discovery")
        before=s.snapshot(self.root)
        base=self.run/"iterations/001/astra_discovery-01";base.parent.mkdir(parents=True)
        s.atomic_json(base.with_suffix(".before.json"),before)
        schema=self.run/"schemas/discovery.json";schema.parent.mkdir(parents=True,exist_ok=True)
        s.atomic_json(schema,goals.DISCOVERY_SCHEMA)
        contract=body(questions=True)
        contract["accepted_assumptions"].append({"text":"Plan is the baseline","basis":"original_request","answer_id":"original-request"})
        s.atomic_json(base.with_suffix(".json"),{"contract":contract,"summary":"draft"})
        base.with_suffix(".jsonl").write_text('{"type":"thread.started","thread_id":"a-session"}\n{"type":"turn.completed"}\n')
        self.state["active_stage"]={"role":"astra","stage":"astra_discovery","iteration":1,
            "output":str(base.with_suffix(".json")),"events":str(base.with_suffix(".jsonl")),
            "schema":str(schema),"before_ref":str(base.with_suffix(".before.json"))}
        with self.assertRaises(s.Paused) as caught:
            runner.reconcile_active(self.state,self.run,self.root)
        self.assertEqual("PAUSED_INVALID_OUTPUT",caught.exception.status)
        self.assertIn("archived",str(caught.exception))
        self.assertNotIn("active_stage",self.state)
        self.assertEqual(1,len(self.state["stages"]))
        self.assertEqual(1,len(self.state["reconciliation_notes"]))
        archived=list((self.run/"iterations/001").glob("archived-astra_discovery-01-*"))
        self.assertEqual(1,len(archived))
        self.assertTrue((archived[0]/"astra_discovery-01.json").exists())
        self.assertFalse(base.with_suffix(".json").exists())
        self.assertEqual("a-session",self.state["sessions"]["astra"])

    def test_complete_resume_loop_and_no_replay_of_completed_stages(self):
        # v3 deliberately requires an actual displayed-revision user approval.
        approve_fixture(self.state, goals)
        local={"auth_mode":"fixture"}
        self.settings.update(transport_identity=local,limits={"iteration_ceiling":18,"max_seconds":None,
            "max_reported_tokens":None,"no_progress_batches":3,"automatic_retries":0})
        self.state["workspace"]=str(self.root.resolve())
        s.atomic_json(self.run/"state.json",self.state)
        called=[]
        def fake_role(**kwargs):
            stage=kwargs["state"]["next_stage"];called.append(stage)
            out=self.run/f"{stage}.json"
            ev=self.run/f"{stage}.jsonl"
            if stage=="terra":
                (self.root/"source.rb").write_text("fixed")
                value={"summary":"fixed","changed_files":["source.rb"],"commands_run":["ruby test.rb"],
                    "results":["pass"],"remaining_risks":[],"evidence_refs":[str(self.evidence)]}
                ev.write_text('{"type":"turn.completed"}\n')
            elif stage=="sol":
                ev.write_text(json.dumps({"type":"item.completed","item":{"id":"check","type":"command_execution",
                    "command":"ruby test.rb","exit_code":0,"aggregated_output":"3 tests passed"}})+"\n")
                value={"verdict":"PASS","checks_run":["ruby test.rb"],"findings":[],"unverified_criteria":[],
                    "end_to_end_result":{"status":"PASS","summary":"Full fixture flow checked","evidence_refs":["event:check"]},
                    "checks":[{"command":"ruby test.rb","exit_code":0,"evidence_ref":"event:check"}],
                    "criterion_results":[{"id":"C1","status":"PASS","evidence_refs":["event:check"]}]}
            else:
                value=self.decision("TASK_COMPLETE");ev.write_text('{"type":"turn.completed"}\n')
            value.update(envelope(kwargs["state"]))
            s.atomic_json(out,value)
            record={"role":kwargs["role"],"stage":stage,"iteration":5,"output":str(out),"events":str(ev),
                "changed_files":["source.rb"] if stage=="terra" else [],"after_ref":str(self.evidence),
                "source_revision":s.snapshot(self.root)["revision"],"duration_seconds":0.01}
            return value,record
        argv=["autocode.py","--workspace",str(self.root.resolve()),"--run-dir",str(self.run.resolve())]
        with patch.object(sys,"argv",argv),patch.object(s,"assert_no_legacy_process"),patch.object(s,"local_settings",return_value=local),patch.object(runner,"run_role",side_effect=fake_role):
            self.assertEqual(0,runner.main())
            self.assertEqual(["terra","sol","astra_review"],called)
            self.assertEqual("TASK_COMPLETE",s.read(self.run/"state.json")["status"])
            self.assertEqual(0,runner.main())
            self.assertEqual(3,len(called))

    def test_compression_failure_returns_original_not_false_pass(self):
        path=self.run/"evidence/uncompressed.json"
        with patch.object(Path,"cwd",return_value=self.root.resolve()),patch.object(s,"compact_output",side_effect=ValueError("bad formatter")):
            code=runner.capture_command(["--output",str(path),"--",sys.executable,"-c","print('distinct failure'); raise SystemExit(3)"])
        self.assertEqual(3,code)
        self.assertEqual("complete_original",s.read(path)["summary"]["fallback"])
        self.assertIn("distinct failure",s.read(path)["summary"]["content"])


if __name__ == "__main__":
    unittest.main()
