"""Joint planning gates, billing routes, bounded calls, and OpenCode handoffs."""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / 'tools', _ROOT / 'tests', _ROOT / 'tests' / 'fakes'):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import autocode as runner
import autocode_goals as goals
import autocode_opencode as oc
import autocode_planning as planning
import autocode_support as support
from goal_fixtures import body
import test_subprocess


class PlanningTests(unittest.TestCase):
    def setUp(self):
        # Hermetic default-provider resolution: a contributor's own
        # ~/.config/autocode/config.toml or AUTOCODE_PROVIDER must never
        # change what these in-process configure() calls resolve to.
        config_home = tempfile.TemporaryDirectory()
        self.addCleanup(config_home.cleanup)
        self._env_patch = patch.dict(os.environ, {"XDG_CONFIG_HOME": config_home.name})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        previous_provider = os.environ.pop("AUTOCODE_PROVIDER", None)
        if previous_provider is not None:
            self.addCleanup(os.environ.__setitem__, "AUTOCODE_PROVIDER", previous_provider)

    def test_planner_dependencies_are_validated_and_rendered(self):
        draft = body()
        draft["milestones"] = [
            {"id": "M1", "objective": "Define interface", "acceptance_criteria": ["C1"], "depends_on": []},
            {"id": "M2", "objective": "Build component", "acceptance_criteria": ["C1"], "depends_on": ["M1"]},
        ]
        state = self.state()
        goals.validate_body(state, draft)
        cyclic = copy.deepcopy(draft)
        cyclic["milestones"][0]["depends_on"] = ["M2"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            goals.validate_body(state, cyclic)
        missing = copy.deepcopy(draft)
        missing["milestones"][1].pop("depends_on")
        with self.assertRaisesRegex(ValueError, "Every milestone"):
            goals.validate_body(state, missing)
        initial = {"objective": "Build component", "affected_paths": ["greet.py"], "kind": "implement",
                   "milestone_id": "M2", "requirements": ["Real flow"], "acceptance_criteria": ["C1"],
                   "validation_plan": ["Run the CLI"]}
        blocked = {**copy.deepcopy(draft), "initial_task": initial}
        with self.assertRaisesRegex(ValueError, "initial_task milestone M2 has unmet prerequisites"):
            goals.validate_body(state, blocked)
        goals.validate_body(state, {**copy.deepcopy(draft), "initial_task": {**initial, "milestone_id": "M1"}})
        state["settings"]["roles"] = {"plan_reviewer": {"engine": "opencode"}}
        bare = body()
        bare["milestones"][0].pop("depends_on")
        with self.assertRaisesRegex(ValueError, "declare depends_on"):
            planning.apply(state, "astra_discovery", {"contract": bare, "summary": "draft",
                           "code_refs": [], "alternatives": [], "uncertainties": [],
                           "contract_changes": [], "requirement_trace": []}, {"output": "draft.json"})

    def test_new_plan_review_route_uses_opencode_cursor_opus(self):
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None, glm_model=None)
        settings = {"engine": "opencode", "roles": {r: {} for r in ("astra", "terra", "sol")},
                    "transport_identity": {"engine": "opencode"}}
        runner.configure_joint(settings, args, fresh=True)
        state = {"settings": settings}
        self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", settings["roles"]["plan_reviewer"]["model"])
        self.assertEqual("opencode", settings["roles"]["plan_reviewer"]["engine"])
        for stage in ("astra_challenge", "astra_finalize"):
            self.assertEqual("plan_reviewer", planning.route_for(state, stage))
        self.assertEqual("astra", planning.route_for({"settings": {"joint_planning": True}}, "astra_challenge"))
        self.assertIn("depends_on", planning.PROMPTS["astra_discovery"])
        self.assertIn("claimed independent milestone", planning.PROMPTS["astra_challenge"])
        self.assertIn("depends_on", planning.PROMPTS["glm_revise"])
        self.assertIn("dependencies are complete and acyclic", planning.PROMPTS["astra_finalize"])
        command, environment, _ = oc.launch("plan_reviewer", Path("/tmp/fixture"), Path("/tmp/run"),
                                            None, settings["roles"]["plan_reviewer"]["model"],
                                            None, False, planning=True)
        self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", command[command.index("--model") + 1])
        agent = command[command.index("--agent") + 1]
        permissions = json.loads(environment["OPENCODE_CONFIG_CONTENT"])["agent"][agent]["permission"]
        self.assertEqual("deny", permissions["edit"])
        self.assertEqual("deny", permissions["bash"])

    def test_three_planner_roles_can_use_distinct_models_and_efforts(self):
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None,
                               requirements_model='openai/gpt-6-luna',
                               requirements_reasoning_effort='high',
                               glm_model='openai/gpt-6-sol', glm_reasoning_effort='medium',
                               plan_reviewer_model='openai/gpt-6-sol',
                               plan_reviewer_reasoning_effort='high')
        settings = {"engine": "opencode", "roles": {r: {} for r in ("astra", "terra", "sol")},
                    "transport_identity": {"engine": "opencode"}}
        runner.configure_joint(settings, args, fresh=True)
        self.assertEqual(('openai/gpt-6-luna', 'high'),
                         (settings['roles']['requirements']['model'],
                          settings['roles']['requirements']['reasoning_effort']))
        self.assertEqual(('openai/gpt-6-sol', 'medium'),
                         (settings['roles']['glm']['model'], settings['roles']['glm']['reasoning_effort']))
        self.assertEqual(('openai/gpt-6-sol', 'high'),
                         (settings['roles']['plan_reviewer']['model'],
                          settings['roles']['plan_reviewer']['reasoning_effort']))

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

    def test_requirements_handoff_is_separate_from_plan_and_preserves_questions(self):
        state = {"version": 3, "task": "Build greeting", "settings": {
            "joint_planning": True, "roles": {"requirements": {}, "glm": {}, "plan_reviewer": {}}},
            "status": "RUNNING", "next_stage": "requirements_gather", "answers": {}}
        requirements = {"summary": "Clarify the greeting", "intended_outcome": "Local greeting",
            "required_behaviors": ["Greet a name"], "constraints": ["No network"],
            "acceptance_tests": ["Run valid and invalid inputs"], "source_refs": ["task"],
            "proposed_assumptions": ["A CLI may suffice"],
            "open_questions": [{"id": "Q1", "question": "CLI or web?", "why": "Interface",
                                "options": ["CLI", "Web"], "proposed_default": "CLI"}],
            "requirements": [], "ignored_statements": [], "conflicts": []}
        planning.apply(state, "requirements_gather", requirements, {"output": "/run/requirements_gather-01.json"})
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertEqual("/run/requirements_gather-01.json", state["requirements_handoff"]["output"])
        self.assertNotIn("milestones", state["requirements_handoff"]["report"])
        self.assertEqual("requirements", planning.role_for(state, "requirements_gather"))
        self.assertEqual("glm", planning.role_for(state, "astra_discovery"))
        self.assertEqual("plan_reviewer", planning.route_for(state, "astra_challenge"))
        requirements_prompt, _ = planning.context({**state, "task": "Build greeting",
            "workspace": "/tmp/test-workspace", "planning": {},
            "goal_contract": {"body": {"milestones": ["unapproved draft"]}}},
            "requirements_gather", Path("/tmp/state.json"))
        self.assertIn('"goal_contract": null', requirements_prompt)
        self.assertNotIn("MILESTONE HANDOFF POLICY", requirements_prompt)
        self.assertNotIn("unapproved draft", requirements_prompt)
        draft = body(questions=False)
        with self.assertRaisesRegex(ValueError, "dropped unresolved requirements questions"):
            planning.apply(state, "astra_discovery", {"contract": draft, "summary": "plan",
                "code_refs": [], "alternatives": [], "uncertainties": [],
                "contract_changes": [], "requirement_trace": []}, {"output": "/run/draft.json"})
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertNotIn("planning", state)

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
        revision = {"summary": "Revised", "contract": body(), "code_refs": [], "responses": [],
                    "contract_changes": [], "requirement_trace": []}
        with self.assertRaisesRegex(ValueError, "Every plan-review concern"):
            runner.apply_result(state, "glm_revise", revision, record, None, None)
        self.assertEqual(original, state)
        final_body = body()
        final_body["initial_task"] = {"objective": "Build CLI", "affected_paths": ["greet.py"], "kind": "implement",
            "milestone_id": "M1", "requirements": ["Greet"], "acceptance_criteria": ["C1"], "validation_plan": ["Run tests"]}
        final = {"contract": final_body, "summary": "Still blocked", "contract_changes": [], "requirement_trace": [],
                 "decisions": [{"concern_id": "P1",
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

    def test_sol_defaults_to_opencode_and_saved_model_choices_are_preserved(self):
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None, glm_model=None)
        settings = {"engine": "opencode", "roles": {r: {} for r in ("astra", "terra", "sol")},
                    "transport_identity": {"engine": "opencode"}}
        with patch.object(support, "local_settings", side_effect=AssertionError("No Codex login required")):
            runner.configure_joint(settings, args, fresh=True)
        self.assertEqual({"engine": "opencode", "provider": None, "model": "zai-coding-plan/glm-5.3",
                          "reasoning_effort": "high"}, settings["roles"]["sol"])
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
        self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", settings["roles"]["terra"]["model"])
        self.assertEqual({"engine": "opencode", "provider": None, "model": "xiaomi-token-plan-sgp/mimo-v2.6-pro"},
                         {key: settings["roles"]["astra"][key] for key in ("engine", "provider", "model")})
        self.assertEqual({"engine": "opencode", "provider": None, "model": "zai-coding-plan/glm-5.3"},
                         {key: settings["roles"]["sol"][key] for key in ("engine", "provider", "model")})
        self.assertEqual({'astra': 'high', 'terra': 'medium', 'sol': 'high', 'completion': 'medium'},
                         {role: settings['roles'][role]['reasoning_effort']
                          for role in ('astra', 'terra', 'sol', 'completion')})
        routed = {'settings': settings}
        self.assertEqual('plan_reviewer', planning.route_for(routed, 'astra_finalize'))
        self.assertEqual('completion', planning.route_for(routed, 'astra_review'))
        self.assertEqual('completion', planning.route_for(routed, 'astra_checkpoint'))

    def test_codex_engine_stays_single_cli_and_saved_non_joint_runs_do_not_switch(self):
        state = {"workspace": "/tmp/fixture", "iteration": 0}
        codex_args = {"astra_model": "gpt-5.6-sol", "terra_model": "gpt-5.6-terra", "sol_model": "gpt-5.6-sol",
                       "completion_model": "gpt-5.6-sol"}
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT", "model": "local"}):
            settings = runner.configure(self.configure_args(engine="codex", **codex_args), state)
        self.assertFalse(settings.get("joint_planning"))
        self.assertEqual("codex", settings["engine"])
        self.assertNotIn("glm", settings["roles"])
        self.assertEqual("astra", planning.role_for({"settings": settings}, "astra_discovery"))
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}):
            joint = runner.configure(self.configure_args(engine="codex", joint_planning=True, **codex_args), state)
        self.assertTrue(joint['joint_planning'])
        self.assertEqual({'codex'}, {planning.engine_for(joint, role) for role in joint['roles']})
        self.assertEqual('requirements', planning.role_for({'settings': joint}, 'requirements_gather'))
        saved = {"settings": {"engine": "opencode", "joint_planning": False, "roles": {
            "astra": {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro"}, "terra": {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro"},
            "sol": {"model": "zai-coding-plan/glm-5.3"}}}, "sessions": {"astra": "saved"}}
        kept = runner.configure(self.configure_args(), saved)
        self.assertFalse(kept.get("joint_planning"))
        self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", kept["roles"]["terra"]["model"])
        with self.assertRaisesRegex(ValueError, "Start a new run"):
            runner.configure(self.configure_args(joint_planning=True), saved)

    def test_native_joint_routes_preserve_saved_models_and_check_only_codex_transport(self):
        with patch.object(support, 'local_settings', return_value={'auth_mode': 'ChatGPT'}):
            settings = runner.configure(self.configure_args(engine='codex', joint_planning=True,
                astra_model='gpt-5.6-sol', terra_model='gpt-5.6-terra', sol_model='gpt-5.6-sol',
                completion_model='gpt-5.6-sol',
                requirements_model='gpt-5.6-sol', glm_model='gpt-5.6-sol',
                plan_reviewer_model='gpt-6-astra', plan_reviewer_reasoning_effort='high'),
                {'workspace': '/tmp/fixture', 'iteration': 0})
        self.assertEqual('gpt-6-astra', settings['roles']['plan_reviewer']['model'])
        self.assertTrue(settings['roles']['plan_reviewer']['model_pinned'])
        saved = copy.deepcopy(settings)
        runner.configure_joint(settings, self.configure_args(), fresh=False)
        self.assertEqual(saved, settings)
        with patch.object(support, 'local_settings', return_value={'auth_mode': 'ChatGPT'}), \
             patch.object(oc, 'local_settings', side_effect=AssertionError('No OpenCode transport')), \
             patch.object(oc, 'check_subscription_routes', side_effect=AssertionError('No OpenCode auth')):
            runner.check_joint_transports({'settings': settings}, Path('/tmp/fixture'))
        for override in ({'plan_reviewer_model': 'xiaomi-token-plan-sgp/mimo-v2.6-pro'}, {'requirements_model': 'external-model'}):
            with self.assertRaisesRegex(ValueError, 'bare GPT'):
                runner.configure_joint(copy.deepcopy(settings), self.configure_args(**override), fresh=False)

    def test_enable_native_joint_requires_a_clean_boundary_and_preserves_limits(self):
        with patch.object(support, 'local_settings', return_value={'auth_mode': 'ChatGPT'}):
            settings = runner.configure(self.configure_args(engine='codex', unlimited_iterations=True,
                max_seconds=0, max_reported_tokens=0,
                astra_model='gpt-5.6-sol', terra_model='gpt-5.6-terra', sol_model='gpt-5.6-sol',
                completion_model='gpt-5.6-sol'), {'workspace': '/tmp/fixture', 'iteration': 1})
        state = {'version': 3, 'workspace': '/tmp/fixture', 'settings': settings,
                 'status': 'PAUSED_INTERVENTION', 'next_stage': 'astra_discovery', 'sessions': {'terra': 'retained'}}
        before = copy.deepcopy(state)
        selected = runner.configure(self.configure_args(joint_planning=True), state)
        self.assertEqual(before, state)
        self.assertTrue(selected['joint_planning'])
        self.assertEqual(settings['limits'], selected['limits'])
        for field in ('active_stage', 'pending_report_repair', 'uncertain_artifacts'):
            with self.assertRaisesRegex(ValueError, 'Resolve the saved provider attempt'):
                runner.configure(self.configure_args(joint_planning=True), {**state, field: {'pending': True}})

    def test_new_openai_terra_keeps_opencode_and_existing_discovery_routes(self):
        for effort in (None, "high"):
            with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}), \
                 patch.object(oc, "local_settings", return_value={"engine": "opencode"}):
                settings = runner.configure(self.configure_args(terra_model="xiaomi-token-plan-sgp/mimo-v2.6-pro",
                    terra_reasoning_effort=effort), {"workspace": "/tmp/fixture", "iteration": 0})
            self.assertEqual({"engine": "opencode", "provider": None, "model": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
                              "reasoning_effort": effort or 'medium'}, settings["roles"]["terra"])
            self.assertEqual("glm", planning.role_for({"settings": settings}, "astra_discovery"))
            self.assertEqual("opencode", planning.engine_for(settings, "glm"))
            self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"]["glm"]["model"])
            state = {"settings": settings, "sessions": {"terra": "opencode-terra-session"}}
            before = copy.deepcopy(state)
            resumed = runner.configure(self.configure_args(), state)
            self.assertEqual(settings, resumed)
            self.assertEqual(before, state)
            with self.assertRaisesRegex(ValueError, "OpenCode provider/model"):
                runner.configure(self.configure_args(terra_model="gpt-5.6-terra"), state)
            self.assertEqual(before, state)

    def test_existing_glm_terra_cannot_be_silently_rerouted(self):
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}), \
             patch.object(oc, "local_settings", return_value={"engine": "opencode"}):
            settings = runner.configure(self.configure_args(), {"workspace": "/tmp/fixture", "iteration": 28})
        state = {"settings": settings, "sessions": {"terra": "opencode-existing-session"}}
        before = copy.deepcopy(state)
        self.assertEqual(settings, runner.configure(self.configure_args(), state))
        with self.assertRaisesRegex(ValueError, "OpenCode provider/model"):
            runner.configure(self.configure_args(terra_model="gpt-5.6-terra"), state)
        self.assertEqual(before, state)


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
        self.assertEqual(["requirements", "glm"], [row["role"] for row in state["stages"]])
        handoff = state["requirements_handoff"]
        self.assertEqual(state["stages"][0]["output"], handoff["output"])
        self.assertNotIn("milestones", json.loads(Path(handoff["output"]).read_text()))
        self.assertNotEqual(state["sessions"]["requirements"], state["sessions"]["glm"])
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        return self.saved()

    def test_opencode_planning_approval_then_implementation_and_validation(self):
        run, state = self.draft()
        stages = state["stages"]
        self.assertEqual(["requirements", "glm", "glm", "astra", "glm", "astra"],
                         [r["role"] for r in stages])
        self.assertEqual(["opencode"] * 6, [r["engine"] for r in stages])
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])
        self.assertEqual(2, state["planning"]["astra_calls"])
        self.assertEqual(3, len({state["sessions"][role]
                                 for role in ("requirements", "glm", "plan_reviewer")}))
        self.assertFalse((self.project / "greet.py").exists())
        self.assertIn("Reject whitespace-only input", state["goal_contract"]["body"]["important_failure_cases"])
        for stage in stages:
            config = json.loads(Path(stage["output"]).with_suffix(".opencode.json").read_text())
            agent = stage["command"][stage["command"].index("--agent") + 1]
            self.assertEqual("deny", config["agent"][agent]["permission"]["edit"])
            self.assertEqual("deny", config["agent"][agent]["permission"]["bash"])
            self.assertEqual([], stage["changed_files"])
        args = ["--run-dir", str(run)]
        self.launch([*args, "--approve-goal", state["displayed_goal"]], 0)
        approved = self.saved()[1]
        self.assertEqual("orchestrator", approved["next_stage"])
        self.assertEqual(6, len(approved["stages"]))
        self.assertEqual(approved["goal_contract"]["hash"], approved["current_task"]["contract_hash"])
        self.launch([*args, "--no-chat"], 0)
        final = self.saved()[1]
        self.assertEqual("COMPLETE", final["phase"])
        self.assertEqual(["orchestrator", "terra", "sol", "astra_review"], [r["stage"] for r in final["stages"][6:]])
        self.assertEqual(["runner", "opencode", "opencode", "opencode"], [r["engine"] for r in final["stages"][6:]])
        sol = final["stages"][8]
        self.assertEqual("zai-coding-plan/glm-5.3", sol["command"][sol["command"].index("--model") + 1])
        self.assertEqual("high", sol["command"][sol["command"].index("--variant") + 1])
        config = json.loads(Path(sol["output"]).with_suffix(".opencode.json").read_text())
        self.assertEqual("deny", config["agent"]["autocode_sol"]["permission"]["edit"])
        completion = final["stages"][9]
        self.assertEqual("astra", completion["role"])
        self.assertEqual("completion", completion["route_role"])
        self.assertEqual("zai-coding-plan/glm-5.3", completion["command"][completion["command"].index("--model") + 1])
        self.assertEqual("medium", completion["command"][completion["command"].index("--variant") + 1])
        self.assertIn("autocode_completion", completion["command"])
        self.assertEqual(3, len({final["sessions"][role] for role in ("plan_reviewer", "sol", "completion")}))
        self.assertEqual(2, final["planning"]["astra_calls"])

    def test_gpt_sol_revalidates_terras_rework_in_its_own_opencode_session(self):
        run, state = self.draft("rework")
        args = ["--run-dir", str(run)]
        self.launch([*args, "--approve-goal", state["displayed_goal"]], 0)
        self.launch([*args, "--no-chat"], 0)
        final = self.saved()[1]
        self.assertEqual("COMPLETE", final["phase"])
        self.assertEqual(["orchestrator", "terra", "sol", "astra_review", "astra_resolve",
                          "orchestrator", "terra", "sol", "astra_review"],
                         [r["stage"] for r in final["stages"][6:]])
        resolution = next(r for r in final['stages'] if r['stage'] == 'astra_resolve')
        self.assertEqual('resolver', resolution['route_role'])
        self.assertIsNone(resolution['expected_session'])
        self.assertNotEqual(final['sessions']['resolver'], final['sessions']['completion'])
        validations = [r for r in final["stages"] if r["stage"] == "sol"]
        self.assertEqual(["opencode", "opencode"], [r["engine"] for r in validations])
        self.assertEqual(final["sessions"]["sol"], validations[1]["expected_session"])
        self.assertNotEqual(final["sessions"]["plan_reviewer"], validations[1]["expected_session"])
        self.assertNotEqual(final["sessions"]["completion"], validations[1]["expected_session"])
        self.assertEqual("FAIL", final["validation_archive"][-1]["validation"]["verdict"])
        self.assertEqual("PASS", final["validation"]["verdict"])

    def test_openai_terra_executes_and_resumes_through_opencode_after_joint_planning(self):
        self.prepare("rework")
        self.launch(["Build a greeting tool", "--no-chat", "--terra-model", "xiaomi-token-plan-sgp/mimo-v2.6-pro",
                     "--terra-reasoning-effort", "high"], 2)
        run, state = self.saved()
        args = ["--run-dir", str(run)]
        self.launch([*args, "--answer", "Q1=CLI"], 0)
        self.launch([*args, "--no-chat"], 2)
        state = self.saved()[1]
        self.assertEqual(["opencode"] * 6,
                         [stage["engine"] for stage in state["stages"]])
        self.launch([*args, "--approve-goal", state["displayed_goal"]], 0)
        self.launch([*args, "--no-chat"], 0)
        final = self.saved()[1]
        self.assertEqual("COMPLETE", final["phase"])
        stages = [r for r in final["stages"] if r["stage"] == "terra"]
        self.assertEqual(2, len(stages))
        for stage in stages:
            self.assertEqual("opencode", stage["engine"])
            self.assertEqual("opencode", stage["command"][0])
            self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", stage["command"][stage["command"].index("--model") + 1])
            self.assertEqual("autocode_terra", stage["command"][stage["command"].index("--agent") + 1])
        # The requested effort applies to every build. Builder retry policy owns
        # the rework route: the ordinary retry keeps the configured effort and
        # rotates the session instead of climbing the reasoning ladder.
        self.assertEqual("high", stages[0]["command"][stages[0]["command"].index("--variant") + 1])
        self.assertEqual("high", stages[1]["command"][stages[1]["command"].index("--variant") + 1])
        self.assertIsNone(stages[0]["expected_session"])
        self.assertIsNone(stages[1]["expected_session"])
        self.assertEqual("retry", final["builder_retry_decisions"][-1]["action"])
        self.assertEqual("high", final["builder_retry_decisions"][-1]["selected_effort"])
        self.assertNotEqual(final["sessions"]["terra"], final["sessions"]["sol"])
        self.assertNotEqual(final["sessions"]["terra"], final["sessions"]["completion"])

    def test_openai_api_connection_pauses_before_any_provider_stage(self):
        self.prepare()
        self.env['AUTOCODE_FIXTURE_OPENAI_AUTH'] = 'api'
        self.launch(["Build a greeting tool", "--no-chat", "--terra-model", "openai/gpt-5.6-terra"], 2)
        state = self.saved()[1]
        self.assertEqual('PAUSED_BILLING_ROUTE', state['status'])
        self.assertEqual([], state['stages'])
        self.assertNotIn('active_stage', state)

    def test_unresolved_final_returns_to_user_without_approval_or_extra_calls(self):
        run, state = self.draft("planning-blocked")
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.assertEqual("P2", state["pending_questions"][0]["id"])
        self.assertEqual(2, state["planning"]["astra_calls"])
        self.launch(["--run-dir", str(run), "--approve-goal", state["displayed_goal"]], 2)
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        self.assertEqual(6, len(self.saved()[1]["stages"]))

    def test_failed_final_cannot_trigger_third_astra_call_on_resume(self):
        run, state = self.draft("planning-invalid", report_repair=0)
        self.assertEqual("PAUSED_INVALID_OUTPUT", state["status"])
        self.assertEqual(2, state["planning"]["astra_calls"])
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        paused = self.saved()[1]
        self.assertEqual("PAUSED_PLANNING_BUDGET", paused["status"])
        self.assertEqual(6, len(paused["stages"]))
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
        self.assertEqual(6, len(recovered["stages"]))
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
        self.assertEqual("opencode", paused["active_stage"]["engine"])
        self.assertEqual(1, paused["planning"]["astra_calls"])
        self.assertEqual(3, len(paused["stages"]))
        del self.env["AUTOCODE_FIXTURE_QUOTA_STAGE"]
        self.launch([*args, "--resume-paused"], 2)
        still = self.saved()[1]
        self.assertEqual(paused["active_stage"], still["active_stage"])
        self.assertEqual(1, still["planning"]["astra_calls"])
        self.assertFalse((self.project / "greet.py").exists())


class NativeJointFlow(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex', '--joint-planning',
                          '--astra-model', 'gpt-5.6-sol', '--terra-model', 'gpt-5.6-terra',
                          '--sol-model', 'gpt-5.6-sol', '--completion-model', 'gpt-5.6-sol',
                          '--plan-reviewer-model', 'gpt-6-astra', '--plan-reviewer-reasoning-effort', 'high')
    draft = JointFlow.draft

    def prepare(self, mode='no-human'):
        JointFlow.prepare(self, mode)
        (self.root / 'fixture-bin/opencode').write_text('#!/bin/sh\necho Unexpected OpenCode launch >&2\nexit 99\n')

    def test_native_codex_review_precedes_approval_and_uses_separate_sessions(self):
        run, state = self.draft()
        self.assertEqual('AWAITING_GOAL_APPROVAL', state['status'])
        self.assertEqual(['requirements_gather', 'astra_discovery', 'astra_discovery',
                          'astra_challenge', 'glm_revise', 'astra_finalize'],
                         [record['stage'] for record in state['stages']])
        self.assertEqual({'codex'}, {record['engine'] for record in state['stages']})
        self.assertEqual(3, len({state['sessions'][role] for role in ('requirements', 'glm', 'plan_reviewer')}))
        for record in state['stages']:
            self.assertIsNone(record['expected_session'])
            self.assertEqual('read-only', record['command'][record['command'].index('--sandbox') + 1])
            self.assertEqual([], record['changed_files'])
            if record['stage'] in ('astra_challenge', 'astra_finalize'):
                self.assertEqual('plan_reviewer', record['route_role'])
                self.assertEqual('gpt-6-astra', record['command'][record['command'].index('--model') + 1])
        self.assertFalse((self.project / 'greet.py').exists())
        self.launch(['--run-dir', str(run), '--approve-goal', state['displayed_goal']], 0)
        self.launch(['--run-dir', str(run), '--no-chat'], 0)
        final = self.saved()[1]
        self.assertEqual('TASK_COMPLETE', final['status'])
        self.assertEqual(['orchestrator', 'terra', 'sol', 'astra_review'],
                         [record['stage'] for record in final['stages'][6:]])
        self.assertEqual(3, len({final['sessions'][role] for role in ('plan_reviewer', 'sol', 'completion')}))

    def test_saved_codex_work_reenters_requirements_before_independent_review(self):
        self.prepare()
        self.launch(['Build a greeting tool', '--engine', 'codex', '--no-chat', '--unlimited-iterations',
                     '--max-seconds', '0', '--max-reported-tokens', '0',
                     '--figma-file', 'https://www.figma.com/design/FakeNativePlanning'], 2)
        run, _ = self.saved()
        base = ['--run-dir', str(run)]
        self.launch([*base, '--answer', 'Q1=CLI'], 0)
        self.launch([*base, '--no-chat'], 2)
        state = self.saved()[1]
        self.launch([*base, '--approve-goal', state['displayed_goal']], 0)
        self.launch([*base, '--no-chat', '--pause-after-stage'], 2)
        self.launch([*base, '--no-chat', '--resume-paused', '--pause-after-stage'], 2)
        before = self.saved()[1]
        built = (self.project / 'greet.py').read_bytes()
        self.launch([*base, '--joint-planning', '--plan-reviewer-model', 'gpt-6-astra',
                     '--resume-paused', '--no-chat', '--pause-after-stage'], 2)
        migrated = self.saved()[1]
        self.assertEqual('requirements_gather', migrated['stages'][-1]['stage'])
        self.assertEqual(before['stages'], migrated['stages'][:-1])
        self.assertEqual(before['settings']['limits'], migrated['settings']['limits'])
        self.assertEqual(before['settings']['figma_file'], migrated['settings']['figma_file'])
        self.assertEqual(before['sessions']['terra'], migrated['sessions']['terra'])
        self.assertEqual(built, (self.project / 'greet.py').read_bytes())
        self.assertEqual('draft', migrated['goal_contract']['approval_status'])
        backup = json.loads(Path(migrated['planning_migrations'][-1]['backup']).read_text())
        self.assertEqual(before['stages'], backup['stages'])
        self.launch([*base, '--resume-paused', '--no-chat'], 2)
        reviewed = self.saved()[1]
        self.assertEqual('AWAITING_GOAL_APPROVAL', reviewed['status'])
        self.assertEqual('astra_finalize', reviewed['stages'][-1]['stage'])
        self.assertEqual('plan_reviewer', reviewed['stages'][-1]['route_role'])
        self.assertEqual(built, (self.project / 'greet.py').read_bytes())


if __name__ == "__main__":
    unittest.main()
