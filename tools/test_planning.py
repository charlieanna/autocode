"""Joint planning gates, billing routes, bounded calls, and mixed-CLI handoffs."""
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_goals as goals
import autocode_opencode as oc
import autocode_planning as planning
import autocode_support as support
from goal_fixtures import body
import test_subprocess


class PlanningTests(unittest.TestCase):
    def test_joint_context_distinguishes_conversation_context_from_saved_feedback(self):
        state=self.state();state['workspace']='/fixture'
        state['task']='Conversation reference: demo. You: Kubernetes with re-teaching.'
        prompt,_=planning.context(state,'astra_discovery',Path('/fixture/state.json'))
        self.assertIn('basis=original_request with answer_id=""',prompt)
        self.assertIn('Conversation IDs and message labels are not saved feedback IDs',prompt)
        self.assertIn('Your suggestions and model-written drafts are basis=agent_proposed',prompt)
        self.assertIn('milestones[].acceptance_criteria must contain ONLY those existing ID strings',prompt)
        contract=copy.deepcopy(state['goal_contract']['body'])
        contract['accepted_assumptions'].append({'text':'Kubernetes','basis':'user_feedback','answer_id':'conversation-demo'})
        with self.assertRaisesRegex(ValueError,'actual saved feedback event'):
            goals.validate_body(state,contract)

    def state(self):
        state = {"version": 2, "task": "Greeting", "settings": {"joint_planning": True}, "acceptance_criteria": []}
        goals.migrate(state)
        goals.install_draft(state, body(), origin="glm_draft")
        return state

    def test_draft_cannot_be_approved_before_both_partners_finish(self):
        state = self.state()
        self.assertEqual("astra_challenge", state["next_stage"])
        goals.present(state)
        with self.assertRaises(ValueError):
            goals.approve(state, state["displayed_goal"])
        state["status"] = "AWAITING_GOAL_APPROVAL"
        with self.assertRaisesRegex(ValueError, "final plan"):
            goals.approve(state, state["displayed_goal"])

    def test_astra_budget_includes_failed_attempts_and_requires_explicit_new_cycle(self):
        state = self.state()
        planning.charge(state, "astra_challenge")
        planning.charge(state, "astra_finalize")
        with self.assertRaises(support.Paused) as error:
            planning.charge(state, "astra_finalize")
        self.assertEqual("PAUSED_PLANNING_BUDGET", error.exception.status)
        self.assertEqual(2, state["planning"]["astra_calls"])
        state["status"] = "PAUSED_PLANNING_BUDGET"
        goals.feedback(state, "Resolve this with a simpler approach")
        self.assertEqual(2, state["planning"]["astra_calls"])
        goals.install_draft(state, body(), origin="glm_draft")
        self.assertEqual(0, state["planning"]["astra_calls"])
        self.assertEqual(2, state["planning_history"][-1]["astra_calls"])

    def test_all_concerns_need_responses_and_final_decisions(self):
        state = self.state()
        challenge = {"summary": "Inspect retry semantics", "concerns": [{"id": "P1", "concern": "Retry duplicates state",
            "evidence_refs": ["api.py:10"], "requested_change": "Deduplicate submission IDs",
            "acceptance_test": "Retry does not update state twice", "blocking": True}]}
        record = {"output": "challenge.json"}
        planning.apply(state, "astra_challenge", challenge, record)
        original = copy.deepcopy(state)
        revision = {"summary": "Revised", "contract": body(), "code_refs": [], "responses": []}
        with self.assertRaisesRegex(ValueError, "Every Astra concern"):
            runner.apply_result(state, "glm_revise", revision, record, None, None)
        self.assertEqual(original, state)
        final_body = body()
        final_body["initial_task"] = {"objective": "Build CLI", "affected_paths": ["greet.py"], "kind": "implement",
            "milestone_id": "M1", "requirements": ["Greet"], "acceptance_criteria": ["C1"], "validation_plan": ["Run tests"]}
        final = {"contract": final_body, "summary": "Still blocked", "decisions": [{"concern_id": "P1",
            "decision": "Ask user", "rationale": "Unknown requirement", "acceptance_test": "Retry test", "resolved": False}]}
        with self.assertRaisesRegex(ValueError, "blocking questions"):
            runner.apply_result(state, "astra_finalize", final, record, None, None)
        self.assertEqual(original, state)

    def test_planner_permissions_deny_shell_custom_tools_and_delegation(self):
        with patch.dict(os.environ, {"OPENCODE_CONFIG_CONTENT": json.dumps({
                "agent": {"autocode_glm": {"permission": {"custom_writer": "allow", "bash": "allow"}}}})}):
            command, env, config = oc.launch("glm", Path("/workspace"), Path("/run"), None,
                                            "zai-coding-plan/glm-5.3", None, False, planning=True)
        agent = command[command.index("--agent") + 1]
        self.assertNotEqual("autocode_glm", agent)
        policy = json.loads(env["OPENCODE_CONFIG_CONTENT"])["agent"][agent]["permission"]
        for key in ("*", "edit", "bash", "task", "external_directory"):
            self.assertEqual("deny", policy[key])
        self.assertEqual("allow", policy["read"])
        self.assertNotIn("custom_writer", policy)

    def test_subscription_guard_refuses_api_routes(self):
        good = {"auth_mode": "ChatGPT", "model_provider": None}
        runner.check_subscription(good)
        for bad in ({"auth_mode": "unknown"}, {"model_provider": "external"}, {"environment_auth_present": True},
                    {"environment_base_url_present": True}, {"openai_base_url": "https://other.test"}):
            with self.subTest(bad=bad), self.assertRaises(support.Paused) as error:
                runner.check_subscription({**good, **bad})
            self.assertEqual("PAUSED_BILLING_ROUTE", error.exception.status)

    def test_sol_defaults_to_codex_and_saved_opencode_routes_are_preserved(self):
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None, glm_model=None)
        settings = {"engine": "opencode", "roles": {r: {} for r in ("astra", "terra", "sol")},
                    "transport_identity": {"engine": "opencode"}}
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}):
            runner.configure_joint(settings, args, fresh=True)
        self.assertEqual({"engine": "codex", "provider": "openai", "model": "gpt-5.6-sol"}, settings["roles"]["sol"])
        settings["roles"]["sol"] = {"engine": "opencode", "provider": None, "model": "zai-coding-plan/glm-5.3"}
        saved = copy.deepcopy(settings)
        runner.configure_joint(settings, args, fresh=False)
        self.assertEqual(saved, settings)
        settings["roles"]["sol"]["model"] = "gpt-5.6-sol"
        with self.assertRaisesRegex(ValueError, "session engines cannot be switched"):
            runner.configure_joint(settings, args, fresh=False)

    def configure_args(self, **overrides):
        args = SimpleNamespace(engine=None, joint_planning=False, glm_model=None, astra_model=None,
                               terra_model=None, sol_model=None, astra_provider=None, terra_provider=None,
                               sol_provider=None, reasoning_effort=None, astra_reasoning_effort=None,
                               terra_reasoning_effort=None, sol_reasoning_effort=None, headroom=None,
                               context_soft_tokens=None, rotate_after_input_tokens=None,
                               legacy_iteration_ceiling=None, max_iterations=None, max_seconds=None,
                               max_stage_seconds=None, max_reported_tokens=None, no_progress_limit=None,
                               unlimited_iterations=False)
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def test_new_run_uses_joint_planning_without_the_flag(self):
        # Removing implicit joint for a flagless OpenCode new run must fail this test.
        state = {"workspace": "/tmp/fixture", "iteration": 0}
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}), \
             patch.object(oc, "local_settings", return_value={"engine": "opencode"}):
            settings = runner.configure(self.configure_args(), state)
        self.assertTrue(settings["joint_planning"])
        self.assertEqual("opencode", settings["engine"])
        self.assertEqual("glm", planning.role_for({"settings": settings}, "astra_discovery"))
        self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"]["glm"]["model"])
        self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"]["terra"]["model"])
        self.assertEqual({"engine": "codex", "provider": "openai", "model": "gpt-6-astra"},
                         {key: settings["roles"]["astra"][key] for key in ("engine", "provider", "model")})
        self.assertEqual({"engine": "codex", "provider": "openai", "model": "gpt-5.6-sol"},
                         {key: settings["roles"]["sol"][key] for key in ("engine", "provider", "model")})

    def test_codex_engine_stays_single_cli_and_saved_non_joint_runs_do_not_switch(self):
        state = {"workspace": "/tmp/fixture", "iteration": 0}
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT", "model": "local"}):
            settings = runner.configure(self.configure_args(engine="codex"), state)
        self.assertFalse(settings.get("joint_planning"))
        self.assertEqual("codex", settings["engine"])
        self.assertNotIn("glm", settings["roles"])
        self.assertEqual("astra", planning.role_for({"settings": settings}, "astra_discovery"))
        with self.assertRaisesRegex(ValueError, "omit --engine codex"):
            runner.configure(self.configure_args(engine="codex", joint_planning=True), state)
        saved = {"settings": {"engine": "opencode", "joint_planning": False, "roles": {
            "astra": {"model": "openai/gpt-6-astra"}, "terra": {"model": "openai/gpt-5.6-terra"},
            "sol": {"model": "zai-coding-plan/glm-5.3"}}}, "sessions": {"astra": "saved"}}
        kept = runner.configure(self.configure_args(), saved)
        self.assertFalse(kept.get("joint_planning"))
        self.assertEqual("openai/gpt-5.6-terra", kept["roles"]["terra"]["model"])
        with self.assertRaisesRegex(ValueError, "Start a new run"):
            runner.configure(self.configure_args(joint_planning=True), saved)


class JointFlow(unittest.TestCase):
    # Reuse fixture setup, not its full suite of single-engine tests.
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ()

    def prepare(self, mode="no-human"):
        bin_dir = self.root / "fixture-bin"
        shutil.copy2(Path(__file__).with_name("fake_opencode.py"), bin_dir / "opencode")
        (bin_dir / "opencode").chmod(0o755)
        self.env.update(AUTOCODE_FIXTURE_MODE=mode, CODEX_HOME=str(self.root / "codex-config"),
                        XDG_CONFIG_HOME=str(self.root / "config"))
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "OPENCODE_CONFIG_CONTENT"):
            self.env.pop(key, None)

    def draft(self, mode="no-human", report_repair=None):
        self.prepare(mode)
        self.launch(["Build a greeting tool", "--no-chat"], 2)
        run, state = self.saved()
        if report_repair is not None:
            # Explicitly exercise compatibility with saved no-recovery runs.
            state['settings']['report_repair'] = {'max_attempts': report_repair}
            (run/'state.json').write_text(json.dumps(state))
        self.assertEqual("glm", state["stages"][0]["role"])
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        return self.saved()

    def test_mixed_cli_planning_approval_then_implementation_and_validation(self):
        run, state = self.draft()
        stages = state["stages"]
        self.assertEqual(["glm", "glm", "astra", "glm", "astra"], [r["role"] for r in stages])
        self.assertEqual(["opencode", "opencode", "codex", "opencode", "codex"], [r["engine"] for r in stages])
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])
        self.assertEqual(2, state["planning"]["astra_calls"])
        self.assertFalse((self.project / "greet.py").exists())
        self.assertIn("Reject whitespace-only input", state["goal_contract"]["body"]["important_failure_cases"])
        for stage in stages:
            if stage["engine"] == "codex":
                self.assertIn("read-only", stage["command"])
                self.assertIn('forced_login_method="chatgpt"', stage["command"])
            self.assertEqual([], stage["changed_files"])
        args = ["--run-dir", str(run)]
        self.launch([*args, "--approve-goal", state["displayed_goal"]], 0)
        approved = self.saved()[1]
        self.assertEqual("terra", approved["next_stage"])
        self.assertEqual(5, len(approved["stages"]))
        self.assertEqual(approved["goal_contract"]["hash"], approved["current_task"]["contract_hash"])
        self.launch([*args, "--no-chat"], 0)
        final = self.saved()[1]
        self.assertEqual("COMPLETE", final["phase"])
        self.assertEqual(["terra", "sol", "astra_review"], [r["stage"] for r in final["stages"][5:]])
        self.assertEqual(["opencode", "codex", "codex"], [r["engine"] for r in final["stages"][5:]])
        sol = final["stages"][6]
        self.assertEqual("gpt-5.6-sol", sol["command"][sol["command"].index("--model") + 1])
        self.assertIn("read-only", sol["command"])
        self.assertIn('forced_login_method="chatgpt"', sol["command"])
        self.assertNotEqual(final["sessions"]["astra"], final["sessions"]["sol"])
        self.assertEqual(2, final["planning"]["astra_calls"])

    def test_gpt_sol_revalidates_terras_rework_in_its_own_codex_session(self):
        run, state = self.draft("rework")
        args = ["--run-dir", str(run)]
        self.launch([*args, "--approve-goal", state["displayed_goal"]], 0)
        self.launch([*args, "--no-chat"], 0)
        final = self.saved()[1]
        self.assertEqual("COMPLETE", final["phase"])
        self.assertEqual(["terra", "sol", "astra_review"] * 2, [r["stage"] for r in final["stages"][5:]])
        validations = [r for r in final["stages"] if r["stage"] == "sol"]
        self.assertEqual(["codex", "codex"], [r["engine"] for r in validations])
        self.assertEqual(final["sessions"]["sol"], validations[1]["expected_session"])
        self.assertNotEqual(final["sessions"]["astra"], validations[1]["expected_session"])
        self.assertEqual("FAIL", final["validation_archive"][-1]["validation"]["verdict"])
        self.assertEqual("PASS", final["validation"]["verdict"])

    def test_unresolved_final_returns_to_user_without_approval_or_extra_calls(self):
        run, state = self.draft("planning-blocked")
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.assertEqual("P2", state["pending_questions"][0]["id"])
        self.assertEqual(2, state["planning"]["astra_calls"])
        self.launch(["--run-dir", str(run), "--approve-goal", state["displayed_goal"]], 2)
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        self.assertEqual(5, len(self.saved()[1]["stages"]))

    def test_failed_final_cannot_trigger_third_astra_call_on_resume(self):
        run, state = self.draft("planning-invalid", report_repair=0)
        self.assertEqual("PAUSED_INVALID_OUTPUT", state["status"])
        self.assertEqual(2, state["planning"]["astra_calls"])
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        paused = self.saved()[1]
        self.assertEqual("PAUSED_PLANNING_BUDGET", paused["status"])
        self.assertEqual(5, len(paused["stages"]))
        self.assertNotIn("active_stage", paused)
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.launch(["--run-dir", str(run), "--feedback", "Try the simpler version"], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        fresh = self.saved()[1]
        self.assertEqual("AWAITING_GOAL_APPROVAL", fresh["status"])
        self.assertEqual(2, fresh["planning_history"][-1]["astra_calls"])
        self.assertEqual(2, fresh["planning"]["astra_calls"])

    def test_editing_final_plan_requires_a_new_joint_review(self):
        run, state = self.draft()
        old_token = state["displayed_goal"]
        edited = state["goal_contract"]["body"]
        edited["constraints"].append("Keep names Unicode")
        path = self.root / "edited.json"
        path.write_text(json.dumps(edited))
        self.launch(["--run-dir", str(run), "--edit-goal", str(path)], 0)
        revised = self.saved()[1]
        self.assertEqual("astra_challenge", revised["next_stage"])
        self.launch(["--run-dir", str(run), "--approve-goal", old_token], 2)
        self.launch(["--run-dir", str(run), "--approve-goal", revised["displayed_goal"]], 2)
        self.assertFalse((self.project / "greet.py").exists())
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.saved()[1]["status"])

    def test_checkpoint_and_recovery_preserve_budget_without_replaying_final(self):
        self.prepare()
        self.launch(["Build a greeting tool", "--no-chat"], 2)
        run, _ = self.saved()
        args = ["--run-dir", str(run), "--no-chat"]
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        for next_stage, count in (("astra_challenge", 0), ("glm_revise", 1), ("astra_finalize", 1)):
            self.launch([*args, "--resume-paused", "--pause-after-stage"], 2)
            state = self.saved()[1]
            self.assertEqual(next_stage, state["next_stage"])
            self.assertEqual(count, state["planning"]["astra_calls"])
        before_final = copy.deepcopy(state)
        self.launch([*args, "--resume-paused"], 2)
        final = self.saved()[1]
        # Simulate loss of the last state transition, with terminal artifacts intact.
        before_final["active_stage"] = final["stages"][-1]
        before_final["planning"]["astra_calls"] = 2
        (run / "state.json").write_text(json.dumps(before_final))
        self.launch(args, 2)
        recovered = self.saved()[1]
        self.assertEqual("AWAITING_GOAL_APPROVAL", recovered["status"])
        self.assertEqual(2, recovered["planning"]["astra_calls"])
        self.assertEqual(5, len(recovered["stages"]))
        self.assertIn("recovered_at", recovered["stages"][-1])
        self.assertEqual(final["planning"]["final_token"], recovered["planning"]["final_token"])

    def test_quota_pauses_without_fallback_or_automatic_replay(self):
        self.prepare()
        self.env["AUTOCODE_FIXTURE_QUOTA_STAGE"] = "astra_challenge"
        self.launch(["Build a greeting tool", "--no-chat"], 2)
        run, _ = self.saved()
        args = ["--run-dir", str(run), "--no-chat"]
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        self.launch(args, 2)
        paused = self.saved()[1]
        self.assertEqual("PAUSED_BUDGET", paused["status"])
        self.assertEqual("codex", paused["active_stage"]["engine"])
        self.assertEqual(1, paused["planning"]["astra_calls"])
        self.assertEqual(2, len(paused["stages"]))
        del self.env["AUTOCODE_FIXTURE_QUOTA_STAGE"]
        self.launch([*args, "--resume-paused"], 2)
        still = self.saved()[1]
        self.assertEqual(paused["active_stage"], still["active_stage"])
        self.assertEqual(1, still["planning"]["astra_calls"])
        self.assertFalse((self.project / "greet.py").exists())


if __name__ == "__main__":
    unittest.main()
