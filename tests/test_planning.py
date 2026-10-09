"""Joint planning gates, billing routes, bounded calls, and OpenCode handoffs."""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode as runner
import autocode_configure
import autocode_goals as goals
import autocode_goal_lifecycle as lifecycle
import autocode_milestones as milestones
import autocode_opencode as oc
import autocode_planning as planning
import autopilot
import autocode_support as support
from goal_fixtures import assert_operational_wait, body
from . import test_subprocess


class PlanningTests(unittest.TestCase):
    def test_progressive_prompts_distinguish_the_product_milestone_from_slices(self):
        state = self.state()
        state["workspace"] = "/fixture"
        for stage in ("astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                prompt, _ = planning.context(state, stage, Path("/fixture/state.json"))
                self.assertIn("exactly one whole-product milestone", prompt)
                self.assertIn("not one milestone per slice", prompt)
                self.assertIn("contract.initial_task executes only slices[0]", prompt)
                self.assertIn("each check's criterion_ids must be a subset", prompt)

    def test_revision_prompt_requires_each_citation_and_a_safe_protected_conflict_exit(self):
        state = self.state()
        state["workspace"] = "/fixture"
        prompt, _ = planning.context(state, "glm_revise", Path("/fixture/state.json"))
        self.assertIn("Each responses[].evidence_refs must be nonempty", prompt)
        self.assertIn("code_refs does not satisfy this per-response requirement", prompt)
        self.assertIn("retain the protected text and add a blocking decision", prompt)
        self.assertIn("contract.initial_task.kind=none", prompt)

    def test_finalizer_context_requests_only_legal_obligation_lists(self):
        state = self.state()
        state.update(workspace="/fixture")
        state["settings"]["roles"] = {"astra": {}}

        prompt, _ = planning.context(state, "astra_finalize", Path("/fixture/state.json"))

        self.assertIn("contract.initial_task", prompt)
        self.assertIn("use [] for obligation_decisions", prompt)
        self.assertNotIn("use [] for remediation_records and obligation_decisions", prompt)
        self.assertNotIn("Planner may add a remediation_records entry", prompt)

    def test_obligation_policy_keeps_existing_nonfinalization_instructions(self):
        for stage in ("astra_discovery", "glm_revise", "astra_challenge"):
            with self.subTest(stage=stage):
                self.assertEqual(planning.OBLIGATION_POLICY, planning.obligation_policy(stage))

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
        no_cli = patch("subprocess.run", side_effect=AssertionError("Pure planning tests must not invoke a CLI"))
        no_cli.start()
        self.addCleanup(no_cli.stop)

    def test_planner_dependencies_are_validated_and_rendered(self):
        draft = body()
        draft["milestones"] = [
            {"id": "M1", "objective": "Define interface", "acceptance_criteria": ["C1"], "depends_on": []},
            {"id": "M2", "objective": "Build component", "acceptance_criteria": ["C1"], "depends_on": ["M1"]},
        ]
        state = self.state()
        lifecycle.validate_body(state, draft)
        cyclic = copy.deepcopy(draft)
        cyclic["milestones"][0]["depends_on"] = ["M2"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            lifecycle.validate_body(state, cyclic)
        missing = copy.deepcopy(draft)
        missing["milestones"][1].pop("depends_on")
        with self.assertRaisesRegex(ValueError, "Every milestone"):
            lifecycle.validate_body(state, missing)
        initial = {"objective": "Build component", "affected_paths": ["greet.py"], "kind": "implement",
                   "milestone_id": "M2", "requirements": ["Real flow"], "acceptance_criteria": ["C1"],
                   "validation_plan": ["Run the CLI"]}
        blocked = {**copy.deepcopy(draft), "initial_task": initial}
        with self.assertRaisesRegex(ValueError, "initial_task milestone M2 has unmet prerequisites"):
            lifecycle.validate_body(state, blocked)
        lifecycle.validate_body(state, {**copy.deepcopy(draft), "initial_task": {**initial, "milestone_id": "M1"}})
        state["settings"]["roles"] = {"plan_reviewer": {"engine": "opencode"}}
        bare = body()
        bare["milestones"][0].pop("depends_on")
        with self.assertRaisesRegex(ValueError, "declare depends_on"):
            autopilot.apply_planning(state, "astra_discovery", {"contract": bare, "summary": "draft",
                           "code_refs": [], "alternatives": [], "uncertainties": [],
                           "contract_changes": [], "requirement_trace": []}, {"output": "draft.json"})

    def test_a_draft_first_task_is_held_to_what_approval_will_assign(self):
        # #615: the draft is checked under the run's own settings, so with milestone checkpoints a first task
        # that writes must name its paths while the plan is a draft; a validate task takes its milestone's.
        state = self.state()
        draft = body()
        draft["milestones"][0]["affected_paths"] = []
        task = {"objective": "Build it", "affected_paths": [], "kind": "implement", "milestone_id": "M1",
                "requirements": ["Real flow"], "acceptance_criteria": ["C1"], "validation_plan": ["Run the CLI"]}
        lifecycle.validate_body(state, {**draft, "initial_task": task})  # without checkpoints [] is unbounded
        state["settings"]["milestone_checkpoints"] = copy.deepcopy(milestones.DEFAULTS)
        with self.assertRaisesRegex(ValueError, r"an implement task \(initial_task in a plan\) must list in "
                                                r"affected_paths the repository files or directories it writes"):
            lifecycle.validate_body(state, {**draft, "initial_task": task})
        lifecycle.validate_body(state, {**draft, "initial_task": {**task, "affected_paths": ["greet.py"]}})
        lifecycle.validate_body(state, {**draft, "initial_task": {**task, "kind": "validate"}})

    def test_a_validate_task_naming_no_paths_checks_its_milestones(self):
        draft = body()  # M1 owns greet.py
        task = {"objective": "Check it", "affected_paths": [], "kind": "validate", "milestone_id": "M1",
                "requirements": ["Real flow"], "acceptance_criteria": ["C1"], "validation_plan": ["Run the CLI"]}
        state = {"goal_contract": {"body": {**draft, "initial_task": task}, "revision": 0, "hash": "draft"},
                 "settings": {"milestone_checkpoints": copy.deepcopy(milestones.DEFAULTS)}}
        self.assertEqual("validate", lifecycle.assign_task(state, goals.initial_decision(state["goal_contract"]["body"]),
                                                           {"revision": "draft"}))
        self.assertEqual(["greet.py"], state["current_task"]["affected_paths"])

    def test_new_plan_review_route_uses_opencode_cursor_opus(self):
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None, glm_model=None)
        settings = {"engine": "opencode", "roles": {r: {} for r in ("astra", "terra", "sol")},
                    "transport_identity": {"engine": "opencode"}}
        autocode_configure.configure_joint(settings, args, fresh=True, planning=planning)
        state = {"settings": settings}
        self.assertEqual("openai/gpt-6-sol", settings["roles"]["plan_reviewer"]["model"])
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
        self.assertEqual("openai/gpt-6-sol", command[command.index("--model") + 1])
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
        autocode_configure.configure_joint(settings, args, fresh=True, planning=planning)
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
            lifecycle.validate_body(state,contract)

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
        autopilot.apply_planning(state, "requirements_gather", requirements, {"output": "/run/requirements_gather-01.json"})
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
            autopilot.apply_planning(state, "astra_discovery", {"contract": draft, "summary": "plan",
                "code_refs": [], "alternatives": [], "uncertainties": [],
                "contract_changes": [], "requirement_trace": []}, {"output": "/run/draft.json"})
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertNotIn("planning", state)

    def state(self):
        state = {"version": 2, "task": "Greeting", "settings": {"joint_planning": True}, "acceptance_criteria": []}
        lifecycle.migrate(state)
        lifecycle.install_draft(state, body(), origin="glm_draft")
        return state

    def test_reviewed_draft_proof_repair_retains_report_and_prior_contract(self):
        state = self.state()
        state["planning"]["reports"]["astra_challenge"] = {"report": {"concerns": []}}
        before = copy.deepcopy(state["goal_contract"])
        draft = copy.deepcopy(before["body"])
        draft["acceptance_criteria"][0]["verification_method"] = "test: test_c1_contract_holds"
        draft["technical_approach"] = ["Use the existing named regression test"]
        draft["milestones"][0]["objective"] = "Deliver with the corrected named proof"
        changes = [{"item": item, "change": "reworded", "basis": "agent_proposed",
                    "answer_id": "", "replacement": "Corrected proposal"} for item in (
                    "C1 verification_method", "technical_approach", "M1")]
        report = {"contract": draft, "summary": "Corrected proof and proposal",
                  "contract_changes": changes, "requirement_trace": [], "responses": [], "code_refs": []}
        autopilot.apply_planning(state, "glm_revise", report, {"output": "retained-revision.json"})
        self.assertEqual("astra_finalize", state["next_stage"])
        self.assertEqual(before, state["contract_history"][-1])
        self.assertEqual([dict(changes[0], item="C1"), *changes[1:]],
                         state["goal_contract"]["declared_changes"])
        self.assertEqual(changes, state["planning"]["reports"]["glm_revise"]["report"]["contract_changes"])
        self.assertEqual("draft", state["goal_contract"]["approval_status"])
        self.assertEqual(before["body"]["required_behaviors"], state["goal_contract"]["body"]["required_behaviors"])
        self.assertEqual(before["body"]["permission_boundaries"], state["goal_contract"]["body"]["permission_boundaries"])

    def test_planning_handoff_keeps_all_review_ids_beyond_six(self):
        state = self.state()
        state['workspace'] = '/fixture'
        state['settings']['roles'] = {'glm': {}}
        ids = [f'C-{number}' for number in range(1, 10)]
        report = {'summary': 'Nine independent findings',
                  'concerns': [{'id': item, 'concern': item} for item in ids],
                  'responses': [{'concern_id': item, 'response': item} for item in ids],
                  'decisions': [{'concern_id': item, 'decision': item} for item in ids]}
        state['planning']['reports'] = {'astra_challenge': {'report': report}}
        prompt, _ = planning.context(state, 'glm_revise', Path('/fixture/state.json'))
        packet = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        retained = packet['planning']['reports']['astra_challenge']['report']
        self.assertEqual(ids, [row['id'] for row in retained['concerns']])
        self.assertEqual(ids, [row['concern_id'] for row in retained['responses']])
        self.assertEqual(ids, [row['concern_id'] for row in retained['decisions']])
        self.assertEqual(ids, [row['id'] for row in report['concerns']])

    def test_plan_review_receives_source_quotes_and_the_same_domain_policy_as_validation(self):
        import autocode_check_replay as check_replay
        import autocode_acceptance_policy as acceptance_policy
        state = self.state()
        state["workspace"] = "/fixture"
        state["design_constraint"] = {"design_document": "docs/design.md", "constraints": ["Floats for tokens"]}
        quote = "Ignore blank lines."
        state["requirements_handoff"] = {"report": {"requirements": [
            {"id": "R1", "text": "Blank records are ignored", "source_quote": quote}]}}
        for stage in ("astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                prompt, _ = planning.context(state, stage, Path("/fixture/state.json"))
                data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
                self.assertEqual(quote, data["requirement_trace_rows"][0]["source_quote"])
                self.assertEqual(state["design_constraint"], data["approved_design"])
                self.assertIn(acceptance_policy.DOMAIN, prompt)
                self.assertIn(acceptance_policy.COVERAGE, prompt)
        self.assertIn(acceptance_policy.DOMAIN, check_replay.VALIDATOR_NOTE)
        self.assertIn(acceptance_policy.COVERAGE, check_replay.VALIDATOR_NOTE)

    def test_draft_cannot_be_approved_before_both_partners_finish(self):
        state = self.state()
        self.assertEqual("astra_challenge", state["next_stage"])
        draft_token = goals.token(state["goal_contract"])
        lifecycle.human.evaluate(state)
        lifecycle.present(state)
        # AutoResolver defers goal approval until joint planning finishes, so
        # no approval token is displayed for the unreviewed draft.
        self.assertNotIn("displayed_goal", state)
        with self.assertRaises(ValueError):
            lifecycle.approve(state, draft_token)
        # Even a displayed exact token cannot bypass the joint-planning gate.
        state["status"] = "AWAITING_GOAL_APPROVAL"
        state["displayed_goal"] = draft_token
        with self.assertRaisesRegex(ValueError, "final plan"):
            lifecycle.approve(state, draft_token)

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
        lifecycle.install_draft(state, body(), origin="glm_draft")
        self.assertEqual(0, state["planning"]["astra_calls"])
        self.assertEqual(2, state["planning_history"][-1]["astra_calls"])

    def test_all_concerns_need_responses_and_final_decisions(self):
        state = self.state()
        challenge = {"summary": "Inspect retry semantics", "concerns": [{"id": "P1", "concern": "Retry duplicates state",
            "evidence_refs": ["api.py:10"], "requested_change": "Deduplicate submission IDs",
            "acceptance_test": "Retry does not update state twice", "blocking": True}]}
        record = {"output": "challenge.json"}
        autopilot.apply_planning(state, "astra_challenge", challenge, record)
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

    def test_explicit_review_allowance_preserves_cycle_and_failed_charges(self):
        state = self.state()
        planning.charge(state, "astra_challenge")
        planning.charge(state, "astra_challenge")
        state.update(status="PAUSED_PLANNING_BUDGET", next_stage="astra_finalize")
        before = copy.deepcopy(state)
        planning.set_review_call_limit(state, 3)
        self.assertEqual(before["goal_contract"], state["goal_contract"])
        self.assertEqual(before["planning"]["reports"], state["planning"]["reports"])
        self.assertEqual(2, state["planning"]["astra_calls"])
        self.assertEqual("PAUSED_PLANNING_BUDGET", state["status"])
        event = state["user_events"][-1]
        self.assertEqual((2, 3, 2), (event["previous_limit"], event["limit"], event["calls_used"]))
        saved = copy.deepcopy(state)
        planning.set_review_call_limit(state, 3)
        self.assertEqual(saved, state)
        self.assertIn("2/3 plan-review calls used", lifecycle.render(state))
        state.update(workspace="/fixture")
        state["settings"]["roles"] = {"astra": {}}
        prompt, _ = planning.context(state, "astra_finalize", Path("/fixture/state.json"))
        self.assertIn("3 plan-review calls in this cycle", prompt)
        planning.charge(state, "astra_finalize")
        with self.assertRaises(support.Paused):
            planning.charge(state, "astra_finalize")
        self.assertEqual(3, state["planning"]["astra_calls"])
        goals.feedback(state, "Start a genuinely new cycle")
        lifecycle.install_draft(state, body(), origin="glm_draft")
        self.assertEqual(2, planning.review_call_limit(state))
        self.assertEqual(0, state["planning"]["astra_calls"])
        self.assertEqual(3, state["planning_history"][-1]["review_call_limit"])

    def test_review_allowance_rejects_invalid_limits_and_unreconciled_boundaries(self):
        state = self.state()
        state.update(status="PAUSED_PLANNING_BUDGET", next_stage="astra_finalize")
        state["planning"]["astra_calls"] = 2
        for limit in (None, True, -1, 1, 2.5, "3"):
            before = copy.deepcopy(state)
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                planning.set_review_call_limit(state, limit)
            self.assertEqual(before, state)
        planning.set_review_call_limit(state, 3)
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            planning.set_review_call_limit(state, 2)
        self.assertEqual(before, state)
        for change in ({"status": "RUNNING"}, {"status": "AWAITING_GOAL_APPROVAL"},
                       {"next_stage": "terra"}, {"active_stage": {"pid": 1}},
                       {"pending_report_repair": {"pending": True}}, {"uncertain_artifacts": "pending"}):
            candidate = {**copy.deepcopy(state), **change}
            before = copy.deepcopy(candidate)
            with self.subTest(change=change), self.assertRaises(ValueError):
                planning.set_review_call_limit(candidate, 4)
            self.assertEqual(before, candidate)
        for invalid in (-1, True, "3"):
            state["planning"]["review_call_limit"] = invalid
            with self.subTest(stored_limit=invalid), self.assertRaises(ValueError):
                planning.charge(state, "astra_finalize")

    def test_explicit_unlimited_review_preserves_usage_and_survives_new_cycle(self):
        state = self.state()
        state.update(status='PAUSED_PLANNING_BUDGET', next_stage='astra_finalize')
        state['planning']['astra_calls'] = 7
        contract = copy.deepcopy(state['goal_contract'])
        planning.set_review_call_limit(state, 0)
        self.assertEqual(7, state['planning']['astra_calls'])
        for _ in range(10):
            planning.charge(state, 'astra_finalize')
        self.assertEqual(17, state['planning']['astra_calls'])
        self.assertEqual(contract, state['goal_contract'])
        self.assertIn('17 plan-review calls used; unlimited', lifecycle.render(state))
        goals.feedback(state, 'Start a genuinely new cycle')
        lifecycle.install_draft(state, body(), origin='glm_draft')
        self.assertEqual(0, planning.review_call_limit(state))
        self.assertEqual('user_explicit', state['planning']['review_call_limit_origin'])
        self.assertEqual(17, state['planning_history'][-1]['astra_calls'])

    def test_unlimited_can_be_saved_at_stopped_checkpoint_but_not_during_a_stage(self):
        state = self.state()
        state.update(status='PAUSED_STAGE_ABANDONED', next_stage='sol')
        state['active_stage'] = {'pid': 123}
        with self.assertRaises(ValueError):
            planning.set_review_call_limit(state, 0)
        state.pop('active_stage')
        planning.set_review_call_limit(state, 0)
        self.assertEqual(0, planning.review_call_limit(state))
        self.assertEqual('PAUSED_STAGE_ABANDONED', state['status'])
        self.assertEqual('sol', state['next_stage'])

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
        autocode_configure.check_subscription(good)
        for bad in ({"auth_mode": "unknown"}, {"model_provider": "external"}, {"environment_auth_present": True},
                    {"environment_base_url_present": True}, {"openai_base_url": "https://other.test"}):
            with self.subTest(bad=bad), self.assertRaises(support.Paused) as error:
                autocode_configure.check_subscription({**good, **bad})
            self.assertEqual("PAUSED_BILLING_ROUTE", error.exception.status)

    def test_sol_defaults_to_opencode_and_saved_model_choices_are_preserved(self):
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None, glm_model=None)
        settings = {"engine": "opencode", "roles": {r: {} for r in ("astra", "terra", "sol")},
                    "transport_identity": {"engine": "opencode"}}
        with patch.object(support, "local_settings", side_effect=AssertionError("No Codex login required")):
            autocode_configure.configure_joint(settings, args, fresh=True, planning=planning)
        self.assertEqual({"engine": "opencode", "provider": None, "model": "openai/gpt-6-sol",
                          "reasoning_effort": "high"}, settings["roles"]["sol"])
        settings["roles"]["sol"] = {"engine": "opencode", "provider": None, "model": "zai-coding-plan/glm-5.3"}
        saved = copy.deepcopy(settings)
        autocode_configure.configure_joint(settings, args, fresh=False, planning=planning)
        self.assertEqual(saved, settings)
        settings["roles"]["sol"]["model"] = "gpt-5.6-sol"
        with self.assertRaisesRegex(ValueError, "session engines cannot be switched"):
            autocode_configure.configure_joint(settings, args, fresh=False, planning=planning)

    def configure_args(self, **overrides):
        args = SimpleNamespace(engine=None, joint_planning=False, glm_model=None, astra_model=None,
                               terra_model=None, sol_model=None, astra_provider=None, terra_provider=None,
                               sol_provider=None, reasoning_effort=None, astra_reasoning_effort=None,
                               terra_reasoning_effort=None, sol_reasoning_effort=None, headroom=None,
                               context_soft_tokens=None, rotate_after_input_tokens=None,
                               legacy_iteration_ceiling=None, max_iterations=None, max_seconds=None,
                               max_stage_seconds=None, no_progress_limit=None,
                               unlimited_iterations=False)
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def test_new_run_uses_joint_planning_without_the_flag(self):
        # Removing implicit joint for a flagless OpenCode new run must fail this test.
        # main() rebinds the runner's opencode facade; autocode_configure reads the
        # same module object, so patching it here patches the actual consumer.
        state = {"workspace": "/tmp/fixture", "iteration": 0}
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}), \
             patch.object(runner.opencode, "local_settings", return_value={"engine": "opencode"}):
            settings = autocode_configure.configure(self.configure_args(), state, planning=planning, milestones=milestones, autopilot=autopilot)
        self.assertTrue(settings["joint_planning"])
        self.assertEqual("opencode", settings["engine"])
        self.assertEqual("glm", planning.role_for({"settings": settings}, "astra_discovery"))
        self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"]["glm"]["model"])
        self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"]["terra"]["model"])
        self.assertEqual({"engine": "opencode", "provider": None, "model": "openai/gpt-6-astra"},
                         {key: settings["roles"]["astra"][key] for key in ("engine", "provider", "model")})
        self.assertEqual({"engine": "opencode", "provider": None, "model": "openai/gpt-6-sol"},
                         {key: settings["roles"]["sol"][key] for key in ("engine", "provider", "model")})
        self.assertEqual({'astra': 'high', 'terra': 'medium', 'sol': 'high', 'completion': 'medium'},
                         {role: settings['roles'][role]['reasoning_effort']
                          for role in ('astra', 'terra', 'sol', 'completion')})
        routed = {'settings': settings}
        self.assertEqual('plan_reviewer', planning.route_for(routed, 'astra_finalize'))
        self.assertEqual('completion', planning.route_for(routed, 'astra_review'))
        self.assertEqual('completion', planning.route_for(routed, 'astra_checkpoint'))

    def test_single_model_mode_routes_every_role_and_keeps_reasoning_overrides(self):
        state = {"workspace": "/tmp/fixture", "iteration": 0}
        args = self.configure_args(single_model="openai/gpt-6-sol", reasoning_effort="medium",
                                   terra_reasoning_effort="low", sol_reasoning_effort="high",
                                   plan_reviewer_reasoning_effort="xhigh")
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}), \
             patch.object(runner.opencode, "local_settings", return_value={"engine": "opencode"}):
            settings = autocode_configure.configure(args, state, planning=planning, milestones=milestones,
                                                    autopilot=autopilot)
        self.assertTrue(settings["single_model_mode"])
        self.assertEqual({"openai/gpt-6-sol"}, {route["model"] for route in settings["roles"].values()})
        self.assertTrue(settings["builder_retry"]["enabled"])
        self.assertEqual("openai/gpt-6-sol", settings["builder_retry"]["strong_model"])
        self.assertEqual("openai/gpt-6-sol", settings["builder_retry"]["checker_model"])
        self.assertEqual("low", settings["roles"]["terra"]["reasoning_effort"])
        self.assertEqual("high", settings["roles"]["sol"]["reasoning_effort"])
        self.assertEqual("xhigh", settings["roles"]["plan_reviewer"]["reasoning_effort"])

    def test_single_model_mode_implies_codex_joint_planning(self):
        state = {"workspace": "/tmp/fixture", "iteration": 0}
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT", "model": "local"}):
            settings = autocode_configure.configure(
                self.configure_args(engine="codex", single_model="gpt-5.6-sol"), state,
                planning=planning, milestones=milestones, autopilot=autopilot)
        self.assertTrue(settings["joint_planning"])
        self.assertTrue(settings["single_model_mode"])
        self.assertEqual({"gpt-5.6-sol"}, {route["model"] for route in settings["roles"].values()})

    def test_codex_engine_stays_single_cli_and_saved_non_joint_runs_do_not_switch(self):
        state = {"workspace": "/tmp/fixture", "iteration": 0}
        codex_args = {"astra_model": "gpt-5.6-sol", "terra_model": "gpt-5.6-terra", "sol_model": "gpt-5.6-sol",
                       "completion_model": "gpt-5.6-sol"}
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT", "model": "local"}):
            settings = autocode_configure.configure(self.configure_args(engine="codex", **codex_args), state, planning=planning, milestones=milestones, autopilot=autopilot)
        self.assertFalse(settings.get("joint_planning"))
        self.assertEqual("codex", settings["engine"])
        self.assertNotIn("glm", settings["roles"])
        self.assertEqual("astra", planning.role_for({"settings": settings}, "astra_discovery"))
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}):
            joint = autocode_configure.configure(self.configure_args(engine="codex", joint_planning=True, **codex_args), state, planning=planning, milestones=milestones, autopilot=autopilot)
        self.assertTrue(joint['joint_planning'])
        self.assertEqual({'codex'}, {planning.engine_for(joint, role) for role in joint['roles']})
        self.assertEqual('requirements', planning.role_for({'settings': joint}, 'requirements_gather'))
        saved = {"settings": {"engine": "opencode", "joint_planning": False, "roles": {
            "astra": {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro"}, "terra": {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro"},
            "sol": {"model": "zai-coding-plan/glm-5.3"}}}, "sessions": {"astra": "saved"}}
        kept = autocode_configure.configure(self.configure_args(), saved, planning=planning, milestones=milestones, autopilot=autopilot)
        self.assertFalse(kept.get("joint_planning"))
        self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", kept["roles"]["terra"]["model"])
        with self.assertRaisesRegex(ValueError, "Start a new run"):
            autocode_configure.configure(self.configure_args(joint_planning=True), saved, planning=planning, milestones=milestones, autopilot=autopilot)

    def test_native_joint_routes_preserve_saved_models_and_check_only_codex_transport(self):
        with patch.object(support, 'local_settings', return_value={'auth_mode': 'ChatGPT'}):
            settings = autocode_configure.configure(self.configure_args(engine='codex', joint_planning=True,
                astra_model='gpt-5.6-sol', terra_model='gpt-5.6-terra', sol_model='gpt-5.6-sol',
                completion_model='gpt-5.6-sol',
                requirements_model='gpt-5.6-sol', glm_model='gpt-5.6-sol',
                plan_reviewer_model='gpt-6-astra', plan_reviewer_reasoning_effort='high'),
                {'workspace': '/tmp/fixture', 'iteration': 0}, planning=planning, milestones=milestones, autopilot=autopilot)
        self.assertEqual('gpt-6-astra', settings['roles']['plan_reviewer']['model'])
        self.assertTrue(settings['roles']['plan_reviewer']['model_pinned'])
        saved = copy.deepcopy(settings)
        autocode_configure.configure_joint(settings, self.configure_args(), fresh=False, planning=planning)
        self.assertEqual(saved, settings)
        with patch.object(support, 'local_settings', return_value={'auth_mode': 'ChatGPT'}), \
             patch.object(runner.opencode, 'local_settings', side_effect=AssertionError('No OpenCode transport')), \
             patch.object(runner.opencode, 'check_subscription_routes', side_effect=AssertionError('No OpenCode auth')):
            runner.check_joint_transports({'settings': settings}, Path('/tmp/fixture'))
        for override in ({'plan_reviewer_model': 'xiaomi-token-plan-sgp/mimo-v2.6-pro'}, {'requirements_model': 'external-model'}):
            with self.assertRaisesRegex(ValueError, 'bare GPT'):
                autocode_configure.configure_joint(copy.deepcopy(settings), self.configure_args(**override), fresh=False, planning=planning)

    def test_enable_native_joint_requires_a_clean_boundary_and_preserves_limits(self):
        with patch.object(support, 'local_settings', return_value={'auth_mode': 'ChatGPT'}):
            settings = autocode_configure.configure(self.configure_args(engine='codex', unlimited_iterations=True,
                max_seconds=0, astra_model='gpt-5.6-sol', terra_model='gpt-5.6-terra', sol_model='gpt-5.6-sol',
                completion_model='gpt-5.6-sol'), {'workspace': '/tmp/fixture', 'iteration': 1}, planning=planning, milestones=milestones, autopilot=autopilot)
        state = {'version': 3, 'workspace': '/tmp/fixture', 'settings': settings,
                 'status': 'PAUSED_INTERVENTION', 'next_stage': 'astra_discovery', 'sessions': {'terra': 'retained'}}
        before = copy.deepcopy(state)
        selected = autocode_configure.configure(self.configure_args(joint_planning=True), state, planning=planning, milestones=milestones, autopilot=autopilot)
        self.assertEqual(before, state)
        self.assertTrue(selected['joint_planning'])
        self.assertEqual(settings['limits'], selected['limits'])
        for field in ('active_stage', 'pending_report_repair', 'uncertain_artifacts'):
            with self.assertRaisesRegex(ValueError, 'Resolve the saved provider attempt'):
                autocode_configure.configure(self.configure_args(joint_planning=True), {**state, field: {'pending': True}}, planning=planning, milestones=milestones, autopilot=autopilot)

    def test_new_openai_terra_keeps_opencode_and_existing_discovery_routes(self):
        for effort in (None, "high"):
            with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}), \
                 patch.object(runner.opencode, "local_settings", return_value={"engine": "opencode"}):
                settings = autocode_configure.configure(self.configure_args(terra_model="xiaomi-token-plan-sgp/mimo-v2.6-pro",
                    terra_reasoning_effort=effort), {"workspace": "/tmp/fixture", "iteration": 0}, planning=planning, milestones=milestones, autopilot=autopilot)
            self.assertEqual({"engine": "opencode", "provider": None, "model": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
                              "reasoning_effort": effort or 'medium'}, settings["roles"]["terra"])
            self.assertEqual("glm", planning.role_for({"settings": settings}, "astra_discovery"))
            self.assertEqual("opencode", planning.engine_for(settings, "glm"))
            self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"]["glm"]["model"])
            state = {"settings": settings, "sessions": {"terra": "opencode-terra-session"}}
            before = copy.deepcopy(state)
            resumed = autocode_configure.configure(self.configure_args(), state, planning=planning, milestones=milestones, autopilot=autopilot)
            self.assertEqual(settings, resumed)
            self.assertEqual(before, state)
            with self.assertRaisesRegex(ValueError, "OpenCode provider/model"):
                autocode_configure.configure(self.configure_args(terra_model="gpt-5.6-terra"), state, planning=planning, milestones=milestones, autopilot=autopilot)
            self.assertEqual(before, state)

    def test_existing_glm_terra_cannot_be_silently_rerouted(self):
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}), \
             patch.object(runner.opencode, "local_settings", return_value={"engine": "opencode"}):
            settings = autocode_configure.configure(self.configure_args(), {"workspace": "/tmp/fixture", "iteration": 28}, planning=planning, milestones=milestones, autopilot=autopilot)
        state = {"settings": settings, "sessions": {"terra": "opencode-existing-session"}}
        before = copy.deepcopy(state)
        self.assertEqual(settings, autocode_configure.configure(self.configure_args(), state, planning=planning, milestones=milestones, autopilot=autopilot))
        with self.assertRaisesRegex(ValueError, "OpenCode provider/model"):
            autocode_configure.configure(self.configure_args(terra_model="gpt-5.6-terra"), state, planning=planning, milestones=milestones, autopilot=autopilot)
        self.assertEqual(before, state)


class JointFlow(unittest.TestCase):
    """Planning and session independence in the simulated, uncontained transport."""
    # Reuse fixture setup, not its full suite of single-engine tests.
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ()

    def prepare(self, mode="no-human"):
        bin_dir = self.root / "fixture-bin"
        shutil.copy2((Path(__file__).resolve().parents[1] / "tools" / ("fake_opencode.py")), bin_dir / "opencode")
        (bin_dir / "opencode").chmod(0o755)
        if "codex" not in self.new_run_engine_args:
            from .opencode_fixture_cli import entrypoint
            self.entry = entrypoint(self.entry)
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
            # Editing saved settings moves the request's binding; publish a
            # fresh request the way the stale-token message tells a user to.
            self.launch(["--run-dir", str(run), "--no-chat"], 2)
            run, state = self.saved()
        # The job recognizer runs first on the requirements route, then the Requirements Gatherer.
        self.assertEqual(["requirements", "requirements", "glm"], [row["role"] for row in state["stages"]])
        self.assertEqual("recognize_workflow", state["stages"][0]["stage"])
        handoff = state["requirements_handoff"]
        self.assertEqual(state["stages"][1]["output"], handoff["output"])
        self.assertNotIn("milestones", json.loads(Path(handoff["output"]).read_text()))
        self.assertNotEqual(state["sessions"]["requirements"], state["sessions"]["glm"])
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        return self.saved()

    def test_stale_answer_token_rebinds_in_one_invocation(self):
        """A saved-state change after display must not strand the shown token."""
        self.prepare()
        self.launch(["Build a greeting tool", "--no-chat"], 2)
        run, state = self.saved()
        published = state.get("resolver_human_request") or {}
        self.assertTrue(published.get("request_token"))
        stages_before = len(state["stages"])
        (self.project / "greet.py").write_text("# operator edit while paused\n")
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI",
                     "--resolver-token", published["request_token"]], 0)
        _, answered = self.saved()
        self.assertIn("CLI", json.dumps(answered.get("answers", {})))
        self.assertIn("Q1", answered.get("answers", {}))
        self.assertEqual(stages_before, len(answered["stages"]), "an answer must not launch a provider")
        reborn = answered["resolver"]["human_escalations"][published["request_id"]]
        self.assertEqual("superseded", reborn["status"],
                         "the answered request must not remain answerable under its old binding")

    def test_bug002_regression_recorded_answers_re_evaluate_readiness(self):
        """docs/bugs/002-answers-not-reevaluated.md: an --answer must re-evaluate
        readiness, not linger while the saved summary still says NOT READY."""
        self.prepare()
        self.launch(["Build a greeting tool", "--no-chat"], 2)
        run, state = self.saved()
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.assertTrue(state["pending_questions"])
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        _, answered = self.saved()
        self.assertEqual([], answered["pending_questions"])
        self.assertEqual([], answered["goal_contract"]["body"]["open_blocking_questions"])
        self.assertEqual("RUNNING", answered["status"])
        self.assertEqual("", answered.get("discovery_summary", ""))
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        _, replanned = self.saved()
        self.assertEqual("AWAITING_GOAL_APPROVAL", replanned["status"])
        self.assertEqual([], replanned["goal_contract"]["body"]["open_blocking_questions"])

    def test_bug003_regression_no_chat_prints_why_it_is_waiting(self):
        """docs/bugs/003-no-chat-exits-silently.md: a non-interactive stop at
        WAITING_FOR_USER prints the questions and the response route."""
        self.prepare()
        result = self.launch(["Build a greeting tool", "--no-chat"], 2)
        run, state = self.saved()
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.assertIn("Answer ID: Q1", result.stdout)
        self.assertIn("AutoResolver token:", result.stdout)

    def test_opencode_planning_approval_then_implementation_and_validation(self):
        run, state = self.draft()
        stages = state["stages"]
        self.assertEqual(["requirements", "requirements", "glm", "glm", "astra", "glm", "astra"],
                         [r["role"] for r in stages])
        self.assertEqual(["opencode"] * 7, [r["engine"] for r in stages])
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
        self.assertEqual(7, len(approved["stages"]))
        self.assertEqual(approved["goal_contract"]["hash"], approved["current_task"]["contract_hash"])
        self.launch([*args, "--no-chat"], 0)
        final = self.saved()[1]
        self.assertEqual("COMPLETE", final["phase"])
        self.assertEqual(["orchestrator", "terra", "sol", "astra_review"], [r["stage"] for r in final["stages"][7:]])
        self.assertEqual(["runner", "opencode", "opencode", "opencode"], [r["engine"] for r in final["stages"][7:]])
        sol = final["stages"][9]
        self.assertEqual("openai/gpt-6-sol", sol["command"][sol["command"].index("--model") + 1])
        self.assertEqual("high", sol["command"][sol["command"].index("--variant") + 1])
        config = json.loads(Path(sol["output"]).with_suffix(".opencode.json").read_text())
        self.assertEqual("deny", config["agent"]["autocode_sol"]["permission"]["edit"])
        completion = final["stages"][10]
        self.assertEqual("astra", completion["role"])
        self.assertEqual("completion", completion["route_role"])
        self.assertEqual("openai/gpt-6-sol", completion["command"][completion["command"].index("--model") + 1])
        self.assertEqual("medium", completion["command"][completion["command"].index("--variant") + 1])
        self.assertIn("autocode_completion", completion["command"])
        self.assertEqual(3, len({final["sessions"][role] for role in ("plan_reviewer", "sol", "completion")}))
        self.assertEqual(2, final["planning"]["astra_calls"])

    def test_gpt_sol_revalidates_terras_rework_in_its_own_opencode_session(self):
        run, state = self.draft("rework")
        # Preserve coverage of Resolver/Validator session separation on the fallback path.
        self.env["AUTOCODE_FIXTURE_INCOMPLETE_REWORK"] = "1"
        args = ["--run-dir", str(run)]
        self.launch([*args, "--approve-goal", state["displayed_goal"]], 0)
        self.launch([*args, "--no-chat"], 0)
        final = self.saved()[1]
        self.assertEqual("COMPLETE", final["phase"])
        self.assertEqual(["orchestrator", "terra", "sol", "astra_review", "astra_resolve",
                          "orchestrator", "terra", "sol", "astra_review"],
                         [r["stage"] for r in final["stages"][7:]])
        resolution = next(r for r in final['stages'] if r['stage'] == 'astra_resolve')
        self.assertEqual('resolver', resolution['route_role'])
        self.assertIsNone(resolution['expected_session'])
        self.assertNotEqual(final['sessions']['resolver'], final['sessions']['completion'])
        validations = [r for r in final["stages"] if r["stage"] == "sol"]
        self.assertEqual(["opencode", "opencode"], [r["engine"] for r in validations])
        # Session reuse is a simulated-transport contract, not native qualification.
        self.assertTrue(all(r.get("tool_containment") is None for r in validations))
        self.assertTrue(all("no OS sandbox" in r["isolation"] for r in validations))
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
        self.assertEqual(["opencode"] * 7,
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

    def test_openai_api_connection_reaches_the_requirements_question(self):
        self.prepare()
        self.env['AUTOCODE_FIXTURE_OPENAI_AUTH'] = 'api'
        result = self.launch(['Build a greeting tool', '--no-chat',
                              '--terra-model', 'openai/gpt-5.6-terra'], 2)
        self.assertIn('Should the greeting be a CLI or web endpoint?', result.stdout)
        self.assertNotIn('PAUSED_BILLING_ROUTE', result.stdout + result.stderr)

    def test_unresolved_final_returns_to_user_without_approval_or_extra_calls(self):
        run, state = self.draft("planning-blocked")
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.assertEqual("P2", state["pending_questions"][0]["id"])
        self.assertEqual(2, state["planning"]["astra_calls"])
        # The draft is displayed with the clarification request, but approving
        # that displayed token is refused while the question is open.
        self.launch(["--run-dir", str(run), "--approve-goal", state["displayed_goal"]], 2)
        self.assertNotEqual("approved", self.saved()[1]["goal_contract"]["approval_status"])
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        self.assertEqual(7, len(self.saved()[1]["stages"]))

    def test_failed_final_cannot_trigger_third_astra_call_on_resume(self):
        run, state = self.draft("planning-invalid", report_repair=0)
        self.assertEqual("PAUSED_INVALID_OUTPUT", state["status"])
        self.assertEqual(2, state["planning"]["astra_calls"])
        # The rejected final was investigated once (autocode_stuck_job); the fixture's Investigator
        # pauses, so the original pause stands and no review allowance was granted.
        self.assertEqual([("astra_finalize:PAUSED_INVALID_OUTPUT", "paused")],
                         [(row["identity"], row["outcome"]) for row in state["stuck_investigations"]])
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        paused = self.saved()[1]
        # An exhausted planning budget is now an operational AutoResolver request.
        assert_operational_wait(self, paused, "PAUSED_PLANNING_BUDGET")
        self.assertEqual(2, paused["planning"]["astra_calls"])
        # Only a runner-owned AutoResolver receipt was added; no third provider call. The one
        # investigate_stuck attempt happened in the draft, before the resume.
        self.assertEqual(state["stages"], [r for r in paused["stages"] if not r.get("runner_owned")])
        self.assertEqual(["resolver"], [r["stage"] for r in paused["stages"] if r.get("runner_owned")])
        self.assertEqual(1, sum(row["stage"] == "investigate_stuck" for row in paused["stages"]))
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
        # The edited draft is not presented for approval until jointly reviewed.
        self.assertNotIn("displayed_goal", revised)
        self.launch(["--run-dir", str(run), "--approve-goal", goals.token(revised["goal_contract"])], 2)
        self.assertFalse((self.project / "greet.py").exists())
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.saved()[1]["status"])

    def test_timeout_retry_can_receive_one_explicit_final_review_without_replanning(self):
        self.prepare()
        fake = self.root / "fixture-bin/opencode"
        from .opencode_fixture_cli import TIMEOUT_ONCE
        fake.write_text(fake.read_text().replace('with tempfile.TemporaryDirectory() as temp:', TIMEOUT_ONCE))
        self.env["AUTOCODE_FIXTURE_TIMEOUT_ONCE"] = str(self.root / "timeout-once")
        self.launch(["Build a greeting tool", "--no-chat", "--max-stage-seconds", "5"], 2)
        run, _ = self.saved()
        args = ["--run-dir", str(run), "--no-chat"]
        self.launch([*args, "--answer", "Q1=CLI"], 0)
        # Since 009c8b8 a proven ordinary planning timeout under the runner-default
        # cap earns one reserved AutoResolver recovery review instead of a
        # PAUSED_PLANNING_BUDGET stop (explicit caps stay protected; see
        # test_failed_extended_review_...). Stop at the pre-final checkpoint with
        # an explicit pause request to inspect the same boundary as before.
        result = self.launch([*args, "--pause-after-stage"], 2)
        for expected_stage in ("astra_challenge", "glm_revise", "astra_finalize"):
            checkpoint = self.saved()[1]
            # Reconcile the one deliberately timed-out challenge if its keeper
            # required a cleanup checkpoint. Other blocked outcomes must fail.
            if (expected_stage == "glm_revise"
                    and checkpoint["status"] == "PAUSED_PROCESS_CLEANUP"
                    and checkpoint["next_stage"] == "astra_challenge"):
                result = self.launch([*args, "--resume-paused", "--pause-after-stage"], 2)
                checkpoint = self.saved()[1]
            self.assertEqual(("PAUSED_REQUESTED", expected_stage),
                             (checkpoint["status"], checkpoint["next_stage"]),
                             f"Planning checkpoint blocked: {checkpoint.get('stop_reason')}\n"
                             + result.stdout + result.stderr)
            if expected_stage != "astra_finalize":
                result = self.launch([*args, "--resume-paused", "--pause-after-stage"], 2)
        paused = self.saved()[1]
        self.assertEqual("PAUSED_REQUESTED", paused["status"])
        # Only the challenge that returned a report counts: the timed-out attempt was given back.
        self.assertEqual(1, paused["planning"]["astra_calls"])
        self.assertEqual("stage", paused["automatic_timeout_recoveries"][-1]["timeout_kind"])
        self.assertIn("glm_revise", paused["planning"]["reports"])
        self.assertFalse(paused["planning"].get("recovery_review_grants"))
        pre_final = goals.token(paused["goal_contract"])
        self.launch([*args, "--approve-goal", pre_final], 2)
        self.launch([*args, "--resume-paused", "--unit", "autoplanner"], 2)
        final = self.saved()[1]
        self.assertEqual("AWAITING_GOAL_APPROVAL", final["status"])
        self.assertEqual(2, final["planning"]["astra_calls"])
        # Exactly one extra provider call: the final review. It is an ordinary second call, inside
        # the allowance, so no recovery grant and no runner-owned resolver stage; no replanning
        # or extra debate round.
        added = final["stages"][len(paused["stages"]):]
        self.assertEqual(["astra_finalize"], [r["stage"] for r in added if not r.get("runner_owned")])
        self.assertEqual([], [r["stage"] for r in added if r.get("runner_owned")])
        self.assertFalse(final["planning"].get("recovery_review_calls_used"))
        self.assertFalse(final["planning"].get("recovery_review_grants"))
        self.assertEqual(1, sum(r["stage"] == "glm_revise" for r in final["stages"]))
        self.assertEqual(paused["goal_contract"]["body"]["open_blocking_questions"],
                         final["goal_contract"]["body"]["open_blocking_questions"])
        self.assertEqual(final["displayed_goal"], final["planning"]["final_token"])
        self.assertFalse(goals.approved(final))
        self.assertFalse((self.project / "greet.py").exists())
        self.launch([*args, "--approve-goal", pre_final], 2)
        self.assertFalse(goals.approved(self.saved()[1]))
        # A lost final transition is reconciled, not charged as another call.
        lost = copy.deepcopy(paused)
        lost["stages"] = final["stages"][:-1]
        lost["active_stage"] = final["stages"][-1]
        for key in ("astra_calls", "review_charges", "recovery_review_grants", "recovery_review_calls_used"):
            if key in final["planning"]:
                lost["planning"][key] = final["planning"][key]
        (run / "state.json").write_text(json.dumps(lost))
        self.launch([*args, "--resume-paused"], 2)
        recovered = self.saved()[1]
        self.assertEqual("AWAITING_GOAL_APPROVAL", recovered["status"])
        self.assertEqual(2, recovered["planning"]["astra_calls"])
        self.assertEqual(len(final["stages"]), len(recovered["stages"]))
        self.assertIn("recovered_at", recovered["stages"][-1])

    def test_failed_extended_review_still_exhausts_budget_and_new_cycle_defaults_to_two(self):
        run, _ = self.draft("planning-invalid", report_repair=0)
        args = ["--run-dir", str(run), "--no-chat"]
        self.launch([*args, "--resume-paused"], 2)
        self.launch([*args, "--planning-review-call-limit", "3"], 0)
        self.launch([*args, "--resume-paused"], 2)
        failed = self.saved()[1]
        self.assertEqual(3, failed["planning"]["astra_calls"])
        self.launch([*args, "--resume-paused"], 2)
        assert_operational_wait(self, self.saved()[1], "PAUSED_PLANNING_BUDGET")
        self.launch([*args, "--planning-review-call-limit", "3"], 0)
        self.launch([*args, "--resume-paused"], 2)
        # No further provider call; AutoResolver may add only its runner-owned receipt.
        provider = lambda rows: [r for r in rows if not r.get("runner_owned")]
        self.assertEqual(provider(failed["stages"]), provider(self.saved()[1]["stages"]))
        self.assertFalse((self.project / "greet.py").exists())
        # "0" is the documented explicit-unlimited setting (335be6c); only
        # malformed or sub-minimum finite limits are rejected here.
        for value in ("1", "-1", "unlimited"):
            checkpoint = (run / "state.json").read_bytes()
            self.launch([*args, "--planning-review-call-limit", value], 2)
            self.assertEqual(checkpoint, (run / "state.json").read_bytes())
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.launch([*args, "--feedback", "Start a new cycle with the simpler plan"], 0)
        self.launch(args, 2)
        fresh = self.saved()[1]
        self.assertEqual("AWAITING_GOAL_APPROVAL", fresh["status"])
        self.assertEqual(2, planning.review_call_limit(fresh))
        self.assertEqual(3, fresh["planning_history"][-1]["review_call_limit"])

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
        self.assertEqual(7, len(recovered["stages"]))
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
        # A provider quota pause is now published as an operational AutoResolver request.
        assert_operational_wait(self, paused, "PAUSED_BUDGET")
        self.assertEqual("opencode", paused["active_stage"]["engine"])
        self.assertEqual(1, paused["planning"]["astra_calls"])
        self.assertEqual(4, len([r for r in paused["stages"] if not r.get("runner_owned")]))
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
        self.assertEqual(['recognize_workflow', 'requirements_gather', 'astra_discovery', 'astra_discovery',
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
                         [record['stage'] for record in final['stages'][7:]])
        self.assertEqual(3, len({final['sessions'][role] for role in ('plan_reviewer', 'sol', 'completion')}))

    def test_saved_codex_work_reenters_requirements_before_independent_review(self):
        self.prepare()
        from tests.figma_inventory_fixtures import install_inventory_hook, native_bundle
        manifest_path = native_bundle(self.root / 'native-source', 'FakeNativePlanning', 'greet.py')
        install_inventory_hook(self.root / 'fixture-bin' / 'codex', result='result', indent='')
        self.env.update(FAKE_NATIVE_MANIFEST=str(manifest_path),
                        FAKE_CAPTURE_REPO=str(Path(__file__).resolve().parents[1]),
                        FAKE_DESIGN_PROMPTS=str(self.root / 'inventory-prompts.jsonl'))
        self.launch(['Build a greeting tool', '--engine', 'codex', '--no-chat', '--unlimited-iterations',
                     '--max-seconds', '0',
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
