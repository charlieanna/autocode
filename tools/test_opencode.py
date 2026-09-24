"""OpenCode transport tests with native JSON events and no network/model calls."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_opencode as oc
import autocode_support as support
import test_subprocess as subprocess_tests


def event(kind, **part):
    return {"type": kind, "sessionID": "ses_fixture", "part": {
        "id": "prt_" + kind, "sessionID": "ses_fixture", "messageID": "msg_fixture", **part}}


def terminal(**overrides):
    return event("step_finish", reason="stop", tokens={"input": 10, "output": 5, "reasoning": 2,
                 "cache": {"read": 7, "write": 3}}, **overrides)


class OpenCodeTests(unittest.TestCase):
    def test_completed_commands_normalize_with_exact_ids_outputs_and_usage(self):
        command = event("tool_use", tool="bash", state={"status": "completed", "input": {"command": "python test.py"},
                        "metadata": {"exit": 3}, "output": "Real failure"})
        events = oc.normalized_events([command, copy.deepcopy(command), terminal()])
        checks = [row["item"] for row in events if row["type"] == "item.completed"]
        self.assertEqual(1, len(checks))
        self.assertEqual("prt_tool_use", checks[0]["id"])
        self.assertEqual(3, checks[0]["exit_code"])
        self.assertEqual("Real failure", checks[0]["aggregated_output"])
        self.assertEqual(20, events[-1]["usage"]["input_tokens"])
        self.assertEqual(7, events[-1]["usage"]["output_tokens"])

    def test_absent_exit_code_or_model_claims_cannot_be_command_evidence(self):
        # Completed output without an integer exit can attest a capture receipt.
        # It is not a command_execution, so it cannot be cited as event: evidence.
        output_only = oc.normalized_events([event("tool_use", tool="bash", state={
            "status": "completed", "input": {"command": "tests"}, "output": "PASS"}), terminal()])
        completed = [row["item"] for row in output_only if row["type"] == "item.completed"]
        self.assertEqual(["tool_output"], [item["type"] for item in completed])
        self.assertNotIn("exit_code", completed[0])
        for state in ({"status": "error", "input": {"command": "tests"}, "metadata": {"exit": 0}},
                      {"status": "completed", "input": {"command": "tests"}, "metadata": {"exit": False}}):
            events = oc.normalized_events([event("tool_use", tool="bash", state=state), terminal()])
            self.assertFalse(any(row["type"] == "item.completed" for row in events))

    def test_terminal_error_truncation_and_mixed_sessions_do_not_complete(self):
        for rows in ([event("step_finish", reason="length")], [event("step_start")],
                     [terminal(), event("step_start")],
                     [terminal(), {"type": "error", "sessionID": "ses_fixture", "error": {"message": "429"}}],
                     [terminal(), {**event("text"), "sessionID": "ses_other"}]):
            self.assertFalse(any(row["type"] == "turn.completed" for row in oc.normalized_events(rows)))

    def test_unknown_usage_remains_unknown(self):
        end = terminal(); del end["part"]["tokens"]["cache"]
        usage = oc.normalized_events([end])[-1]["usage"]
        self.assertNotIn("input_tokens", usage)
        self.assertEqual(7, usage["output_tokens"])

    def test_final_report_uses_only_the_terminal_message(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            earlier = event("text", id="earlier", messageID="old", text='{"status":"fake"}')
            rows = [earlier, event("text", text='```json\n{"ok":true}\n```'), terminal()]
            path.write_text("\n".join(json.dumps(row) for row in rows))
            self.assertEqual({"ok": True}, oc.final_report(path))
            rows[1]["part"]["text"] = 'Commentary {"ok":true}'
            path.write_text("\n".join(json.dumps(row) for row in rows))
            with self.assertRaisesRegex(RuntimeError, "not a JSON report"):
                oc.final_report(path)

    def test_schema_prompt_keeps_agent_within_the_target_workspace(self):
        prompt = oc.prompt_for_schema("Task\nCURRENT HANDOFF DATA\n{}", {"type": "object"}, Path("/tmp/events.jsonl"))
        self.assertIn("strict filesystem boundary", prompt)
        self.assertIn("ancestor AGENTS.md", prompt)
        self.assertIn("private_source_exceptions", prompt)

    def test_launch_restricts_reviews_preserves_config_and_resumes_exact_session(self):
        original = {"provider": {"custom": {"models": {"m": {}}}}, "permission": {"bash": "ask"}}
        with patch.dict(os.environ, {"OPENCODE_CONFIG_CONTENT": json.dumps(original)}):
            command, env, config = oc.launch("sol", Path("/workspace"), Path("/run"), "ses_saved",
                                             "zai-coding-plan/glm-5.3", "high", False)
        self.assertEqual("ses_saved", command[command.index("--session") + 1])
        self.assertNotIn("--auto", command)
        self.assertNotIn("--continue", command)
        self.assertEqual("high", command[command.index("--variant") + 1])
        self.assertEqual("deny", config["agent"]["autocode_sol"]["permission"]["edit"])
        combined = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(original["provider"], combined["provider"])
        self.assertEqual(original["permission"], combined["permission"])
        self.assertEqual("disabled", combined["share"])

    def test_engine_cannot_reuse_other_engines_sessions(self):
        with self.assertRaisesRegex(ValueError, "not interchangeable"):
            runner.configure(SimpleNamespace(engine="opencode"), {"settings": {"roles": {}}, "sessions": {"astra": "old"}})

    def test_inline_role_denies_and_patterns_survive_launch(self):
        for policy in ("deny", {"bash":"deny", "webfetch":"deny"},
                       {"bash":{"*":"ask", "git push*":"deny"}, "edit":{"*":"ask"}}):
            with self.subTest(policy=policy), patch.dict(os.environ, {"OPENCODE_CONFIG_CONTENT":json.dumps({
                "agent":{"autocode_sol":{"permission":policy,"temperature":0.2}}})}):
                _, env, _ = oc.launch("sol",Path("/workspace"),Path("/run"),None,"test/model",None,False)
                role = json.loads(env["OPENCODE_CONFIG_CONTENT"])["agent"]["autocode_sol"]
                expected = {"*":"deny"} if isinstance(policy,str) else policy
                for key, value in expected.items():
                    if key != "edit":
                        self.assertEqual(value,role["permission"][key])
                self.assertEqual("deny",role["permission"]["edit"])
                self.assertEqual(0.2,role["temperature"])

    def test_model_preflight_uses_target_workspace(self):
        with patch.object(oc.subprocess,"run",return_value=subprocess.CompletedProcess([],0,stdout="fixture/model\n")) as call:
            oc.check_models({"sol":{"model":"fixture/model"}},Path("/target/repo"))
        self.assertEqual(Path("/target/repo"),call.call_args.kwargs["cwd"])

    def test_custom_config_and_agent_changes_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/".git").mkdir()
            custom=root/"custom"; (custom/"agents").mkdir(parents=True)
            config=custom/"opencode.json"; config.write_text('{"permission":{"bash":"deny"}}')
            with patch.dict(os.environ,{"OPENCODE_CONFIG_DIR":str(custom)}), \
                 patch.object(oc.subprocess,"run",return_value=subprocess.CompletedProcess([],0,stdout="1.18.31\n")), \
                 patch.object(oc.shutil,"which",return_value="/bin/opencode"):
                first=oc.local_settings(root)
                config.write_text('{"permission":{"bash":"allow"}}')
                second=oc.local_settings(root)
                self.assertTrue(oc.transport_drift(second,first))
                (custom/"agents/autocode_sol.md").write_text('---\npermission:\n  bash: deny\n---\nReview')
                self.assertTrue(oc.transport_drift(oc.local_settings(root),second))
                # A coverage upgrade retains all checks present in old checkpoints.
                old={k:v for k,v in first.items() if k!='identity_version'}
                self.assertTrue(oc.transport_drift(second,old))
                self.assertFalse(oc.transport_drift(first,old))

    def test_invalid_completed_report_archives_and_charges_attempt_once(self):
        for report in ('Not JSON', '{"summary":"Missing fields"}'):
            with self.subTest(report=report), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); run=root/".autocode/runs/fixture"; run.mkdir(parents=True)
                base=run/"terra-01"
                log=base.with_suffix(".jsonl")
                log.write_text('\n'.join(json.dumps(row) for row in [event("text",text=report),terminal()]))
                state={"sessions":{},"stages":[],"active_seconds":2,
                    "active_stage":{"engine":"opencode","role":"terra","stage":"terra","iteration":1,
                        "events":str(log),"output":str(base.with_suffix(".json")),"exit_code":0,
                        "schema":str(runner.SCHEMA_DIR/"v2/terra-report.schema.json"),"duration_seconds":3}}
                with self.assertRaises(support.Paused) as error:
                    runner.reconcile_active(state,run,root)
                self.assertEqual("PAUSED_INVALID_OUTPUT",error.exception.status)
                self.assertNotIn("active_stage",state)
                self.assertEqual(5,state["active_seconds"])
                archived=state["stages"][0]
                self.assertTrue(Path(archived["events"]).is_file())
                self.assertEqual(20,archived["metrics"]["provider_tokens"]["input_tokens"])
                runner.reconcile_active(state,run,root)
                runner.account_stage(state,archived)
                self.assertEqual(5,state["active_seconds"])

    def test_metadata_timeouts_are_reported_without_launching_an_agent(self):
        with patch.object(oc.shutil, "which", return_value="/bin/opencode"), \
             patch.object(oc.subprocess, "run", side_effect=subprocess.TimeoutExpired("opencode", 15)):
            with self.assertRaisesRegex(RuntimeError, "no agent was launched"):
                oc.local_settings(Path("/tmp"))
            with self.assertRaisesRegex(RuntimeError, "no agent was launched"):
                oc.check_models({"astra": {"model": "openai/gpt-6-astra"}})

    def test_opencode_orphan_is_detected_without_matching_wrapper_shells(self):
        self.assertTrue(support.duplicate_runner_command("/bin/opencode run --title /repo/.autocode/runs/x"))
        self.assertTrue(support.duplicate_runner_command("/bin/opencode.exe run --title /repo/.autocode/runs/x"))
        self.assertFalse(support.duplicate_runner_command("/bin/zsh -lc 'opencode run ...'"))

    def test_open_code_events_feed_existing_evidence_verifier(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            rows = [event("tool_use", tool="bash", state={"status": "completed", "input": {"command": "python test.py"},
                    "metadata": {"exit": 0}, "output": "pass"}), terminal()]
            path.write_text("\n".join(json.dumps(row) for row in rows))
            support.verify_checks([{"command": "python test.py", "exit_code": 0, "evidence_ref": "event:prt_tool_use"}], Path(temp), path)
            with self.assertRaises(ValueError):
                support.verify_checks([{"command": "different test", "exit_code": 0, "evidence_ref": "event:prt_tool_use"}], Path(temp), path)
            self.assertEqual("ses_fixture", runner.event_thread_id(path))

    def test_completed_raw_stage_recovers_without_relaunch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=f@example.test",
                            "commit", "--allow-empty", "-qm", "fixture"], check=True)
            run = root / ".autocode/runs/recovery"
            run.mkdir(parents=True)
            base = run / "terra-01"
            evidence = run / "check.log"; evidence.write_text("preserved evidence")
            before = base.with_suffix(".before.json")
            support.atomic_json(before, support.snapshot(root))
            report = {"summary": "Saved work", "changed_files": [], "commands_run": [], "results": [],
                      "remaining_risks": [], "evidence_refs": [str(evidence)]}
            log = base.with_suffix(".jsonl")
            log.write_text("\n".join(json.dumps(row) for row in [event("text", text=json.dumps(report)), terminal()]))
            state = {"version": 2, "workspace": str(root), "status": "RUNNING", "next_stage": "terra",
                     "sessions": {}, "stages": [], "history": [], "iteration": 1, "settings": {"engine": "opencode"},
                     "active_stage": {"engine": "opencode", "role": "terra", "stage": "terra", "iteration": 1,
                       "output": str(base.with_suffix(".json")), "events": str(log), "before_ref": str(before),
                       "schema": str(runner.SCHEMA_DIR / "v2/terra-report.schema.json")}}
            with patch.object(runner, "run_role", side_effect=AssertionError("No relaunch")):
                runner.reconcile_active(state, run, root)
            self.assertEqual("sol", state["next_stage"])
            self.assertNotIn("active_stage", state)
            self.assertEqual("ses_fixture", state["sessions"]["terra"])
            self.assertEqual(report, json.loads(base.with_suffix(".json").read_text()))

    def test_archive_checkpoint_failure_leaves_original_evidence_recoverable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / ".autocode/runs/fixture"
            run.mkdir(parents=True)
            log = run / "terra-01.jsonl"
            log.write_text('\n'.join(json.dumps(row) for row in [event("text", text="Not JSON"), terminal()]))
            state = {"sessions":{}, "stages":[], "active_seconds":2,
                "active_stage":{"engine":"opencode", "role":"terra", "stage":"terra", "iteration":1,
                    "events":str(log), "output":str(log.with_suffix(".json")), "exit_code":0,
                    "schema":str(runner.SCHEMA_DIR / "v2/terra-report.schema.json"), "duration_seconds":3}}
            support.atomic_json(run / "state.json", state)
            with patch.object(runner, "write_json", side_effect=OSError("fixture disk full")):
                with self.assertRaises(OSError):
                    runner.reconcile_active(state, run, root)
            self.assertTrue(log.exists())
            recovered = support.read(run / "state.json")
            with self.assertRaises(support.Paused):
                runner.reconcile_active(recovered, run, root)
            saved = support.read(run / "state.json")
            self.assertEqual("PAUSED_INVALID_OUTPUT", saved["status"])
            self.assertEqual(5, saved["active_seconds"])
            self.assertEqual(1, len(saved["stages"]))
            self.assertNotIn("active_stage", saved)
            self.assertTrue(Path(saved["stages"][0]["events"]).exists())

    def test_config_identity_detects_changes_without_opening_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".git").mkdir()
            config = root / "opencode.json"
            config.write_text('{"permission":{"edit":"ask"}}')
            calls = []
            original = Path.read_bytes
            def observed(path):
                calls.append(path.name)
                return original(path)
            with patch.object(oc.shutil, "which", return_value="/bin/opencode"), \
                 patch.object(oc.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="1.18.31\n")), \
                 patch.object(Path, "read_bytes", observed), patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root / "config")}):
                first = oc.local_settings(root)
                config.write_text('{"permission":{"edit":"deny"}}')
                second = oc.local_settings(root)
            self.assertNotEqual(first, second)
            self.assertNotIn("auth.json", calls)


class OpenCodeFlow(unittest.TestCase):
    # Product default is joint planning. Codex-only coverage stays in SubprocessFlow.
    new_run_engine_args = ()
    launch = subprocess_tests.SubprocessFlow.launch
    saved = subprocess_tests.SubprocessFlow.saved

    def setUp(self):
        subprocess_tests.SubprocessFlow.setUp(self)
        source = Path(__file__).resolve().parent
        target = self.root / "fixture-bin/opencode"
        shutil.copy2(source / "fake_opencode.py", target)
        target.chmod(0o755)
        self.env.update(CODEX_HOME=str(self.root / "codex-config"),
                        XDG_CONFIG_HOME=str(self.root / "config"))
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "OPENCODE_CONFIG_CONTENT"):
            self.env.pop(key, None)

    def test_standalone_cli_full_interview_approval_review_and_completion(self):
        self.launch(["Greeting tool", "--chat"], 0, answers="CLI\nyes\nyes\n")
        _, state = self.saved()
        self.assertEqual("opencode", state["settings"]["engine"])
        self.assertTrue(state["settings"]["joint_planning"])
        self.assertEqual("requirements", state["stages"][0]["role"])
        expected = {"requirements": "zai-coding-plan/glm-5.3", "glm": "zai-coding-plan/glm-5.3", "astra": "openai/gpt-5.6-sol",
                    "terra": "openai/gpt-5.6-terra", "sol": "openai/gpt-5.6-sol",
                    "completion": "openai/gpt-5.6-sol",
                    "plan_reviewer": "cursor-acp/claude-opus-5-5-high"}
        self.assertEqual(expected, {role: settings["model"] for role, settings in state["settings"]["roles"].items()})
        self.assertEqual("COMPLETE", state["phase"])
        self.assertNotEqual(state["sessions"]["terra"], state["sessions"]["sol"])
        self.assertNotEqual(state["sessions"]["plan_reviewer"], state["sessions"]["sol"])
        engines = {role: "opencode" for role in ("requirements", "glm", "terra", "astra", "sol", "completion", "plan_reviewer")}
        for record in state["stages"]:
            if record.get("runner_owned"):
                self.assertIn(record['stage'], ('orchestrator', 'resolver'))
                self.assertEqual("runner", record["engine"])
                self.assertNotIn("command", record)
                if record['stage'] == 'resolver':
                    self.assertEqual(0, record['runner_calls'])
                    self.assertIn(record['decision']['action'], ('continue', 'retry', 'escalate'))
                continue
            command = record["command"]
            role = record.get("route_role", record["role"])
            self.assertEqual(engines[role], record["engine"])
            self.assertEqual(engines[role], command[0])
            self.assertNotIn("--auto", command)
            self.assertEqual(expected[role], command[command.index("--model") + 1])


if __name__ == "__main__":
    unittest.main()
