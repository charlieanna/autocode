"""The report repair handoff retains human clarification provenance."""

import copy
import json
import unittest
from pathlib import Path

import autocode_goal_lifecycle as lifecycle
import autocode_plan_retention as retention
import autocode_report_repair_context as context
import autocode_roles as roles
from goal_fixtures import approve_fixture

from . import test_report_repair as repair_fixtures


class ClarificationContextTests(unittest.TestCase):
    def test_protected_list_inventory_is_literal_read_only_and_not_an_authority_decision(self):
        protected = {
            "important_failure_cases": ["Keep zero", "Keep negatives"],
            "constraints": ["Local only"],
            "permission_boundaries": ["a.py"],
        }
        content = {
            "contract": {
                "important_failure_cases": ["Keep zero", "Keep negative values"],
                "constraints": ["Local only"],
                "permission_boundaries": ["b.py"],
            },
            "contract_changes": [
                {
                    "item": "Keep negatives",
                    "change": "reworded",
                    "basis": "user_answer",
                    "answer_id": "Q1",
                    "replacement": "Keep negative values",
                }
            ],
        }
        source = {"format": "json", "truncated": False, "content": content}
        before = copy.deepcopy((protected, source))
        self.assertEqual(
            [{"field": "important_failure_cases", "missing": ["Keep negatives"]}],
            context.protected_list_differences(protected, source),
        )
        self.assertEqual(before, (protected, source), "Inventories neither edit receipts nor grant/refuse changes")
        retained = copy.deepcopy(source)
        retained["content"]["contract"]["important_failure_cases"].append("Keep negatives")
        self.assertEqual([], context.protected_list_differences(protected, retained))
        for unknown in (
            {**source, "truncated": True},
            {**source, "format": "text"},
            {**source, "content": []},
            {**source, "content": {"contract": {"retain_contract": "r1:hash"}}},
            {**source, "content": {"contract": {"important_failure_cases": "invalid"}}},
        ):
            with self.subTest(source=unknown):
                self.assertEqual([], context.protected_list_differences(protected, unknown))

    def test_historical_overlay_keeps_all_old_values_available_without_read_tools(self):
        original = {
            "shared": "retained text " * 500,
            "flags": [False, 1.0, None],
            "old_only": {"value": 0},
            "nested": {"list": [1, 2], "a/b~key": "héllo"},
        }
        latest = {
            "shared": original["shared"],
            "flags": [0, 1, {}],
            "new_only": True,
            "nested": {"list": [1], "a/b~key": "changed"},
        }
        source = {"format": "json", "truncated": False, "content": original}
        before = copy.deepcopy((source, latest))
        instruction, compact = context.historical_author_report("glm_revise", source, {**source, "content": latest})
        self.assertIn("No file access is needed", instruction)
        self.assertEqual("rejected_report.content", compact["content_base"])
        restored = copy.deepcopy(latest)
        for path in compact["content_delta"]["remove"]:
            parent = restored
            for part in path[:-1]:
                parent = parent[part]
            del parent[path[-1]]
        for change in compact["content_delta"]["replace"]:
            path = change["path"]
            if not path:
                restored = copy.deepcopy(change["value"])
                continue
            parent = restored
            for part in path[:-1]:
                parent = parent[part]
            parent[path[-1]] = copy.deepcopy(change["value"])
        self.assertEqual(json.dumps(original, sort_keys=True), json.dumps(restored, sort_keys=True))
        self.assertEqual(before, (source, latest))

    def test_historical_compaction_is_skipped_when_distinct_content_would_grow(self):
        original = {"format": "json", "truncated": False, "content": {"summary": "a" * 1000}}
        latest = {**original, "content": {"summary": "b" * 1000}}
        self.assertEqual(("", original), context.historical_author_report("glm_revise", original, latest))

    def test_historical_reference_compaction_preserves_other_repair_baselines(self):
        original = {"format": "json", "truncated": False, "content": {"summary": "original"}}
        latest = {"format": "json", "truncated": False, "content": {"summary": "latest"}}
        for stage in ("terra", "sol", "astra_finalize", "requirements_gather"):
            with self.subTest(stage=stage):
                self.assertEqual(("", original), context.historical_author_report(stage, original, latest))
        for source in (
            {**original, "format": "text", "content": "incomplete JSON"},
            {**original, "truncated": True},
            {**original, "content": []},
        ):
            with self.subTest(source=source):
                self.assertEqual(("", source), context.historical_author_report("glm_revise", source, latest))
                self.assertEqual(("", original), context.historical_author_report("glm_revise", original, source))

    def test_revision_and_finalization_repairs_get_only_the_current_planning_exchange(self):
        reports = {
            "astra_discovery": {"output": "current-discovery.json", "report": {"contract": {"scope": ["greet.py"]}}},
            "astra_challenge": {
                "output": "current-review.json",
                "report": {
                    "concerns": [
                        {"id": "C1", "concern": "Guard test is missing", "requested_change": "Plan the guard test"}
                    ]
                },
            },
            "glm_revise": {"output": "current-revision.json", "report": {"responses": [{"concern_id": "C1"}]}},
            "astra_finalize": {"report": {"summary": "unrelated final"}},
        }
        state = {
            "planning": {"reports": reports},
            "planning_history": [{"reports": {"astra_challenge": {"report": {"concerns": [{"id": "OLD"}]}}}}],
        }
        before = copy.deepcopy(state)
        for stage, names in (
            ("glm_revise", ("astra_discovery", "astra_challenge")),
            ("astra_finalize", ("astra_discovery", "astra_challenge", "glm_revise")),
        ):
            with self.subTest(stage=stage):
                result = context.clarification_context(state, stage)
                self.assertEqual({name: reports[name] for name in names}, result["planning_exchange"])
                result["planning_exchange"]["astra_challenge"]["report"]["concerns"][0]["id"] = "changed"
                self.assertEqual(before, state)
        self.assertEqual({}, context.clarification_context(state, "requirements_gather")["planning_exchange"])
        self.assertEqual({}, context.clarification_context({}, "glm_revise")["planning_exchange"])

    def test_the_planning_exchange_carries_only_the_draft_under_review(self):
        # #1337: the superseded discovery draft rode beside the revised one in every plan-finalize repair.
        reports = {
            "astra_discovery": {"output": "discovery.json", "report": {"summary": "First", "contract": {"v": 1}}},
            "astra_challenge": {"output": "review.json", "report": {"concerns": [{"id": "C1"}]}},
            "glm_revise": {"output": "revision.json", "report": {"responses": [], "contract": {"v": 2}}},
        }
        state = {"planning": {"reports": reports}}
        before = copy.deepcopy(state)

        final = context.clarification_context(state, "astra_finalize")["planning_exchange"]

        self.assertEqual({"output": "discovery.json", "report": {"summary": "First"}}, final["astra_discovery"])
        self.assertEqual(reports["astra_challenge"], final["astra_challenge"])
        self.assertEqual(reports["glm_revise"], final["glm_revise"])
        self.assertEqual(before, state)

    def test_repair_instruction_requires_actual_saved_concern_ids_and_substantive_responses(self):
        text = context.instruction("glm_revise")
        self.assertIn("planning_exchange.astra_challenge.report.concerns", text)
        self.assertIn("every saved concern ID exactly once", text)
        self.assertIn("Do not invent a replacement concern ID", text)
        self.assertIn("existing evidence_refs", text)

    def test_finalizer_repair_instructions_use_only_final_report_fields(self):
        text = context.instruction("astra_finalize")

        self.assertIn("planning_exchange.astra_challenge.report.concerns", text)
        self.assertIn("every saved concern ID exactly once in decisions", text)
        self.assertIn("substantive rationale", text)
        self.assertIn("contract.initial_task", text)
        self.assertIn("contract.open_blocking_questions", text)
        self.assertIn("saved stage schema", text)
        for forbidden_instruction in (
            "in responses or decisions",
            "decisions, with substantive changes and existing evidence_refs",
            "Record human decisions in requirements",
            "ignored_statements using its exact checklist text",
            "machine_resolutions and access_blockers must be []",
        ):
            with self.subTest(instruction=forbidden_instruction):
                self.assertNotIn(forbidden_instruction, text)

    def test_finalizer_decision_shape_matches_the_strict_saved_schema(self):
        from units import autoplanner

        text = context.instruction("astra_finalize")
        fields = autoplanner.SCHEMAS["astra_finalize"]["properties"]["decisions"]["items"]["properties"]
        self.assertIn("Each decision contains only " + ", ".join(fields), text)
        self.assertNotIn("evidence_refs", fields)
        self.assertNotIn("Add evidence_refs", text)

    def test_finalizer_repair_explains_the_observed_root_initial_task_error(self):
        from autocode_util import validate_schema
        from goal_fixtures import body
        from units import autoplanner

        initial_task = {
            "objective": "Fix blank names",
            "affected_paths": ["greet.py"],
            "kind": "implement",
            "milestone_id": "M1",
            "requirements": ["Reject blanks"],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["Run the regression"],
        }
        # Retained revision 31: repairs added the sole extra root field while the
        # contract still omitted it. Both locations must be corrected together.
        report = {
            "contract": body(),
            "summary": "Final plan",
            "decisions": [],
            "contract_changes": [],
            "conflict_resolutions": [],
            "requirement_trace": [],
            "initial_task": initial_task,
        }
        schema = autoplanner.SCHEMAS["astra_finalize"]
        with self.assertRaisesRegex(ValueError, r"\$: unexpected fields"):
            validate_schema(report, schema)
        without_extra = {key: value for key, value in report.items() if key != "initial_task"}
        with self.assertRaisesRegex(ValueError, r"\$\.contract: missing initial_task"):
            validate_schema(without_extra, schema)

        text = context.instruction("astra_finalize")
        self.assertIn("Remove a root-level initial_task", text)
        self.assertIn("preserving that task inside contract.initial_task", text)

    def test_finalizer_repair_defers_to_alternate_progressive_review_schemas(self):
        text = context.instruction("astra_finalize")

        self.assertIn("For alternate progressive review schemas, follow only their saved fields", text)
        self.assertIn("do not add contract, initial_task or decisions", text)

    def test_planning_repair_gets_only_matching_answers_and_current_investigation(self):
        question = {"id": "Q1", "question": "Which behavior?"}
        handoff = {
            "report": {
                "open_questions": [question],
                "requirements": [
                    {"id": "preserve", "source_quote": "Preserve current output", "text": "Keep it stable."}
                ],
            }
        }
        request = {
            "stage": "requirements_gather",
            "questions": [question],
            "question_ids": ["Q1"],
            "handoff_hash": "h1",
        }
        state = {
            "requirements_handoff": handoff,
            "investigation_request": request,
            "clarification_episode": {"id": "episode-1", "investigation_used": True},
            "answers": {"Q1": {"text": "Use option A"}, "Q9": {"text": "Unrelated"}},
            "brief_feedback": [
                {
                    "id": "feedback-1",
                    "actor": "user_cli",
                    "text": "Keep output stable",
                    "contract_token": "private-token",
                }
            ],
        }

        result = context.clarification_context(state, "requirements_gather")

        self.assertEqual(handoff, result["requirements_handoff"])
        self.assertEqual(request, result["investigation_request"])
        self.assertEqual(state["clarification_episode"], result["clarification_episode"])
        self.assertEqual({"preserve": "Preserve current output"}, result["required_source_quotes"])
        self.assertEqual({"Q1": state["answers"]["Q1"]}, result["saved_answers"])
        self.assertEqual(
            [{"id": "feedback-1", "actor": "user_cli", "text": "Keep output stable"}], result["saved_feedback"]
        )
        self.assertNotIn("Q9", result["saved_answers"])

    def test_human_clarification_is_not_a_machine_resolution(self):
        text = context.instruction("astra_discovery")
        self.assertIn("Human answers and feedback are never machine_resolutions", text)
        self.assertIn("only when clarification_context has an investigation_request", text)
        self.assertIn("Without that matching request, machine_resolutions and access_blockers must be []", text)
        self.assertIn("Historical resolutions in a prior handoff are not new resolutions", text)
        self.assertIn("ignored_statements using its exact checklist text", text)
        self.assertIn("Do not expand a prior source_quote to cover a checklist sentence", text)

    def test_nonplanning_report_repair_has_no_clarification_payload_or_rule(self):
        state = {"requirements_handoff": {"sensitive": "unrelated"}}
        self.assertEqual({}, context.clarification_context(state, "terra"))
        self.assertEqual("", context.instruction("terra"))
        self.assertEqual("", context.instruction("sol"))

    def test_original_planning_draft_is_not_an_immutable_answer_baseline(self):
        text = context.baseline_instruction("requirements_gather")
        self.assertIn("original_report is historical planning context", text)
        self.assertIn("Do not restore invalid machine_resolutions", text)
        self.assertNotIn("immutable execution-history baseline", text)
        self.assertIn("immutable execution-history baseline", context.baseline_instruction("terra"))


class DecisionRepairContextTests(unittest.TestCase):
    setUp = repair_fixtures.base.RetrofitTest.setUp

    def test_first_id_repair_also_names_independently_missing_protected_text(self):
        runner = repair_fixtures.runner
        approve_fixture(self.state, runner.goals)
        self.state["settings"]["roles"]["glm"] = copy.deepcopy(self.state["settings"]["roles"]["astra"])
        self.state["next_stage"] = "glm_revise"
        exact = "Nonpositive size must still raise ValueError after the change"
        self.state["goal_contract"]["body"]["important_failure_cases"] = [exact]
        value = {
            "contract": copy.deepcopy(self.state["goal_contract"]["body"]),
            "requirement_trace": [{"requirement": "Keep invalid-size behavior"}],
            "contract_changes": [],
        }
        value["contract"]["important_failure_cases"] = [
            "Nonpositive size (0 and negative) must still raise ValueError after the change"
        ]
        before = copy.deepcopy(value)
        repair_fixtures.RepairTests.queue(
            self,
            stage="glm_revise",
            role="glm",
            report=json.dumps(value),
            error=ValueError("Planner report is missing requirement_trace[0].requirement_id"),
        )
        request = repair_fixtures.RepairTests.repair_request(self)
        data = json.loads(request["prompt"].split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertIn("requirement_id", data["error"])
        self.assertEqual([{"field": "important_failure_cases", "missing": [exact]}], data["protected_list_differences"])
        self.assertEqual(before, data["rejected_report"]["content"])
        self.assertIn("retain the saved text verbatim", request["prompt"])
        self.assertIn("grants no change authority", request["prompt"])
        with self.assertRaisesRegex(ValueError, "drops or changes"):
            runner.goals.revision_guard(self.state, value["contract"], [], "glm_revise")

    def test_revision_repair_receives_current_review_even_when_execution_findings_are_empty(self):
        concerns = [
            {"id": "C4", "concern": "Cite the saved answer", "requested_change": "Retain the exact answer quote"},
            {
                "id": "C5",
                "concern": "The copy requirement contradicts its exception",
                "requested_change": "Clarify the exception",
            },
        ]
        review = {"output": "current-review.json", "report": {"summary": "Second review", "concerns": concerns}}
        self.state.update(
            next_stage="glm_revise",
            planning={
                "reports": {
                    "astra_discovery": {"report": {"contract": {"intended_outcome": "superseded-draft"}}},
                    "astra_challenge": review,
                }
            },
            planning_history=[{"reports": {"astra_challenge": {"report": {"concerns": [{"id": "OLD"}]}}}}],
        )
        self.state["settings"]["roles"]["glm"] = copy.deepcopy(self.state["settings"]["roles"]["astra"])
        repair_fixtures.RepairTests.queue(
            self,
            stage="glm_revise",
            role="glm",
            report=json.dumps({"responses": []}),
            error=ValueError("Every plan-review concern needs exactly one response/decision using its ID"),
        )
        saved_planning = copy.deepcopy(self.state["planning"])
        request = repair_fixtures.RepairTests.repair_request(self)
        data = json.loads(request["prompt"].split("CURRENT HANDOFF DATA\n", 1)[1])

        self.assertEqual([], data["open_findings"])
        self.assertEqual({"astra_challenge": review}, data["planning_exchange"])
        self.assertNotIn("superseded-draft", request["prompt"])
        self.assertNotIn('"OLD"', request["prompt"])
        self.assertIn("open_findings is the execution findings ledger", request["prompt"])
        self.assertIn("exactly one substantive response", request["prompt"])
        self.assertEqual(saved_planning, self.state["planning"])

    def test_author_repair_offers_delta_only_when_its_pinned_schema_allows_it(self):
        runner = repair_fixtures.runner
        approve_fixture(self.state, runner.goals)
        self.state["settings"]["roles"]["glm"] = copy.deepcopy(self.state["settings"]["roles"]["astra"])
        before = copy.deepcopy(self.state)
        for stage in ("astra_discovery", "glm_revise"):
            for legacy in (False, True):
                with self.subTest(stage=stage, legacy=legacy):
                    self.state = copy.deepcopy(before)
                    self.state["next_stage"] = stage
                    schema = copy.deepcopy(runner.planning.SCHEMAS[stage])
                    if not legacy:
                        schema = retention.model_schema(schema, self.state, stage)
                    path = self.run / f"{stage}-{legacy}.schema.json"
                    path.write_text(json.dumps(schema))
                    original_bytes = path.read_bytes()
                    repair_fixtures.RepairTests.queue(self, stage=stage, role="glm", schema=str(path))
                    request = repair_fixtures.RepairTests.repair_request(self)
                    data = json.loads(request["prompt"].split("CURRENT HANDOFF DATA\n", 1)[1])
                    self.assertEqual(retention.token(self.state), data["retained_contract"])
                    self.assertEqual(not legacy, retention.DELTA_RULE in request["prompt"])
                    self.assertEqual(original_bytes, path.read_bytes())

    def test_criterion_author_repairs_receive_exact_current_retention_references(self):
        runner = repair_fixtures.runner
        approve_fixture(self.state, runner.goals)
        self.state["settings"]["roles"]["glm"] = copy.deepcopy(self.state["settings"]["roles"]["astra"])
        before = copy.deepcopy(self.state)
        for stage in sorted(retention.AUTHORS - retention.FINAL):
            with self.subTest(stage=stage):
                self.state = copy.deepcopy(before)
                self.state["next_stage"] = stage
                expected = retention.inventory(self.state)
                repair_fixtures.RepairTests.queue(
                    self, stage=stage, role="glm", error=ValueError("Retained criterion reference is stale")
                )
                request = repair_fixtures.RepairTests.repair_request(self)
                data = json.loads(request["prompt"].split("CURRENT HANDOFF DATA\n", 1)[1])
                self.assertIn("retained_criteria", data)
                self.assertEqual(expected, data["retained_criteria"])
                self.assertIn(retention.RULE, request["prompt"])
                expanded = retention.expand(
                    self.state, stage, {"contract": {"acceptance_criteria": data["retained_criteria"]}}
                )
                self.assertEqual(
                    before["goal_contract"]["body"]["acceptance_criteria"],
                    expanded["contract"]["acceptance_criteria"],
                )

    def test_unrelated_repair_does_not_receive_criterion_retention_references(self):
        runner = repair_fixtures.runner
        approve_fixture(self.state, runner.goals)
        repair_fixtures.RepairTests.queue(self, stage="terra", role="terra")
        request = repair_fixtures.RepairTests.repair_request(self)
        data = json.loads(request["prompt"].split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertNotIn("retained_criteria", data)
        self.assertNotIn(retention.RULE, request["prompt"])

    def test_author_without_a_current_contract_gets_no_retention_rule_or_inventory(self):
        self.state.pop("goal_contract", None)
        self.state["settings"]["roles"]["glm"] = copy.deepcopy(self.state["settings"]["roles"]["astra"])
        self.state["next_stage"] = "glm_revise"
        repair_fixtures.RepairTests.queue(self, stage="glm_revise", role="glm")
        request = repair_fixtures.RepairTests.repair_request(self)
        data = json.loads(request["prompt"].split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertNotIn("retained_criteria", data)
        self.assertNotIn(retention.RULE, request["prompt"])

    def test_final_review_keeps_its_existing_contract_reference_context(self):
        runner = repair_fixtures.runner
        approve_fixture(self.state, runner.goals)
        self.state["next_stage"] = "astra_finalize"
        repair_fixtures.RepairTests.queue(self, stage="astra_finalize", role="astra")
        request = repair_fixtures.RepairTests.repair_request(self)
        data = json.loads(request["prompt"].split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertNotIn("retained_criteria", data)
        self.assertEqual(retention.token(self.state), data["clarification_context"]["retained_contract"])
        self.assertEqual(retention.inventory(self.state), data["clarification_context"]["retained_criteria"])

    def test_superseded_author_draft_is_referenced_without_doubling_the_complete_latest_draft(self):
        runner = repair_fixtures.runner
        approve_fixture(self.state, runner.goals)
        self.state["settings"]["roles"]["glm"] = copy.deepcopy(self.state["settings"]["roles"]["astra"])
        self.state["next_stage"] = "glm_revise"
        original = {"summary": "retained " + "x" * 120000}
        latest = {**original, "repair_fault": True}
        pending = repair_fixtures.RepairTests.queue(self, stage="glm_revise", role="glm", report=json.dumps(original))
        original_source = runner.repair_report_source(pending["original"])
        with self.assertRaises(runner.ReportRepairQueued):
            repair_fixtures.RepairTests.reject_repair(
                self, 6, ValueError("Missing criterion citation"), report=json.dumps(latest)
            )
        request = repair_fixtures.RepairTests.repair_request(self)
        data = json.loads(request["prompt"].split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(latest, data["rejected_report"]["content"])
        self.assertNotIn("content", list(data["original_report"]))
        self.assertEqual({"remove": [["repair_fault"]], "replace": []}, data["original_report"]["content_delta"])
        for key in ("path", "sha256", "bytes", "format", "truncated"):
            self.assertEqual(original_source[key], data["original_report"][key])
        self.assertEqual(original, json.loads(Path(data["original_report"]["path"]).read_text()))
        self.assertLess(len(request["prompt"].encode()), 262144)

    def test_discovery_repair_controller_supplies_the_approved_protected_contract(self):
        runner = repair_fixtures.runner
        approve_fixture(self.state, runner.goals)
        self.state["settings"]["roles"]["glm"] = copy.deepcopy(self.state["settings"]["roles"]["astra"])
        self.state["next_stage"] = "astra_discovery"
        expected = runner.goals.protected_contract_snapshot(self.state)
        repair_fixtures.RepairTests.queue(self, stage="astra_discovery", role="glm")
        prompt = repair_fixtures.RepairTests.repair_request(self)["prompt"]
        data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertIsInstance(data["protected_contract"], dict)
        self.assertEqual(expected, data["protected_contract"])

    def test_decision_repair_distinguishes_proposed_tasks_from_executed_history(self):
        runner = repair_fixtures.runner
        approve_fixture(self.state, runner.goals)
        self.state["settings"]["roles"]["resolver"] = copy.deepcopy(self.state["settings"]["roles"]["astra"])
        before = copy.deepcopy(self.state)
        for stage in ("astra_plan", "astra_review", "astra_checkpoint", "astra_resolve"):
            with self.subTest(stage=stage):
                self.state = copy.deepcopy(before)
                self.state["next_stage"] = stage
                # l9folr37's last rejected handoff used this slice ID as a milestone.
                decision = {
                    "status": "CONTINUE",
                    "next_objective": "Revalidate the approved behavior",
                    "affected_paths": ["greet.py"],
                    "next_task": {
                        "kind": "validate",
                        "milestone_id": "s2-recommendations",
                        "requirements": ["Preserve the approved behavior"],
                        "acceptance_criteria": ["C1"],
                        "validation_plan": ["python3 -m unittest"],
                    },
                }
                if stage == "astra_resolve":
                    decision.update(status="REWORK", evidence=[str(self.evidence)])
                    decision["next_task"]["kind"] = "implement"
                with self.assertRaisesRegex(ValueError, "Task must belong to an approved milestone") as rejected:
                    lifecycle.assign_task(self.state, decision, runner.support.snapshot(self.root))
                report = (
                    {"decision": decision, "validation": {"verdict": "FAIL"}}
                    if stage == "astra_checkpoint"
                    else decision
                )
                repair_fixtures.RepairTests.queue(
                    self, error=rejected.exception, stage=stage, role="astra", report=json.dumps(report)
                )
                request = repair_fixtures.RepairTests.repair_request(self)
                prompt = request["prompt"]
                data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
                self.assertEqual(report, data["rejected_report"]["content"])
                self.assertIn("Task must belong to an approved milestone", data["error"])
                self.assertEqual(
                    ["M1"],
                    [
                        row["id"]
                        for row in runner.support.read(data["state_file"])["goal_contract"]["body"]["milestones"]
                    ],
                )
                self.assertEqual("read-only", request["sandbox"])
                self.assertFalse(request["allow_write"])
                self.assertTrue(request["report_only"])
                self.assertNotIn(
                    "original_report is also supplied, it is the immutable execution-history baseline", prompt
                )
                for instruction in (
                    "proposed next_task is not executed history",
                    "goal_contract.body.milestones",
                    "existing approved milestone ID",
                    "Progressive slice IDs are never milestone IDs",
                    "requirements, acceptance_criteria and validation_plan must be nonempty",
                    "Do not invent scope, milestones, unreviewed checks, PASS or citations",
                    "Preserve executed commands, outcomes, Validator facts, findings, failures and uncertainty",
                    "Do not change current_task or report_identity",
                ):
                    self.assertIn(instruction, prompt)
                self.assertIn("decision.next_task", prompt)

    def test_missing_requirements_and_unknown_ids_are_not_repaired_by_guessing(self):
        runner = repair_fixtures.runner
        approve_fixture(self.state, runner.goals)
        self.state["next_stage"] = "astra_review"
        report = {
            "next_task": {"milestone_id": "unknown-milestone", "requirements": []},
            "acceptance_criteria": [{"id": "C1", "status": "unverified", "evidence": ""}],
        }
        repair_fixtures.RepairTests.queue(
            self,
            stage="astra_review",
            role="astra",
            error=ValueError("The bounded task needs requirements"),
            report=json.dumps(report),
        )
        prompt = repair_fixtures.RepairTests.repair_request(self)["prompt"]
        data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(report, data["rejected_report"]["content"])
        self.assertIn("requirements, acceptance_criteria and validation_plan must be nonempty", prompt)
        self.assertIn(
            "If the saved approved context does not establish a valid correction, preserve the uncertainty", prompt
        )
        self.assertIn("do not guess an ID or default to M1", prompt)
        self.assertIn("Missing evidence must remain NOT_VERIFIED, never invented PASS", prompt)
        latest = copy.deepcopy(report)
        latest["next_task"].update(milestone_id="s2-recommendations", requirements=["Preserve approved behavior"])
        with self.assertRaises(runner.ReportRepairQueued):
            repair_fixtures.RepairTests.reject_repair(
                self,
                6,
                ValueError("Task must belong to an approved milestone and its acceptance criteria"),
                report=json.dumps(latest),
            )
        prompt = repair_fixtures.RepairTests.repair_request(self)["prompt"]
        data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(report, data["original_report"]["content"])
        self.assertEqual(latest, data["rejected_report"]["content"])
        self.assertIn("proposed next_task is not executed history", prompt)
        self.assertIn("existing approved milestone ID", prompt)

    def test_planning_stages_take_the_planning_baseline(self):
        expected = (
            "If supplied, original_report is historical planning context; rejected_report is the latest failed "
            "repair and error applies to that draft. Repair the latest draft against the current clarification "
            "context and protected contract. Do not restore invalid machine_resolutions or other rejected "
            "fields from original_report. "
        )
        for stage in roles.ALL_PLANNING_STAGES:
            with self.subTest(stage=stage):
                instruction = context.baseline_instruction(stage)
                if stage == "requirements_gather":
                    self.assertTrue(instruction.startswith(expected))
                    self.assertIn("requirements_correction_sources", instruction)
                    self.assertIn("ignored_requirements", instruction)
                else:
                    self.assertTrue(instruction.startswith(expected))
                    self.assertIn("regression_reference names the saved repository comparison commit", instruction)
                    self.assertIn("does not reset this reference", instruction)
                    self.assertNotIn("immutable execution-history baseline", instruction)

    def test_other_roles_keep_the_execution_history_baseline(self):
        expected = (
            "If original_report is also supplied, it is the immutable execution-history baseline; "
            "rejected_report is the latest failed repair and error applies to that draft. "
        )
        for stage in ("terra", "sol", "astra_diagnose", "investigate_stuck", "unknown-stage"):
            with self.subTest(stage=stage):
                self.assertEqual(expected, context.baseline_instruction(stage))


if __name__ == "__main__":
    unittest.main()


class ProtectedContractRepairContextTests(unittest.TestCase):
    def setUp(self):
        from goal_fixtures import body

        self.body = body()
        self.clauses = (
            "Do not weaken, delete, rename, or skip protected tests.",
            "Preserve existing public APIs unless the issue explicitly requires a change.",
        )
        self.body["constraints"].extend(self.clauses)
        self.state = {"goal_contract": {"body": self.body, "approval_status": "approved"}}

    def test_contract_writer_repairs_receive_every_protected_field_verbatim(self):
        fields = (
            "required_behaviors",
            "scope_exclusions",
            "constraints",
            "important_failure_cases",
            "acceptance_criteria",
            "permission_boundaries",
        )
        expected = {field: self.body[field] for field in fields}
        for stage in ("astra_discovery", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                payload = context.clarification_context(self.state, stage)
                self.assertEqual(expected, payload.get("protected_contract"))

    def test_discovery_repair_snapshot_cannot_mutate_the_approved_contract(self):
        before = copy.deepcopy(self.state)
        payload = context.clarification_context(self.state, "astra_discovery")
        self.assertIsInstance(payload.get("protected_contract"), dict)
        payload["protected_contract"]["constraints"].clear()
        payload["protected_contract"]["acceptance_criteria"][0]["criterion"] = "weakened"
        self.assertEqual(before, self.state)

    def test_requirement_only_and_nonplanning_repairs_do_not_receive_a_contract(self):
        for stage in ("requirements_gather", "terra", "astra_review", "unknown-stage"):
            with self.subTest(stage=stage):
                self.assertIsNone(context.clarification_context(self.state, stage).get("protected_contract"))

    def test_discovery_revision_still_rejects_either_omitted_protected_clause(self):
        from autocode_contract_revision import revision_guard

        for clause in self.clauses:
            with self.subTest(clause=clause):
                changed = copy.deepcopy(self.body)
                changed["constraints"].remove(clause)
                with self.assertRaisesRegex(ValueError, "without a user-backed contract change"):
                    revision_guard(self.state, changed, [], "astra_discovery")

    def test_repair_payloads_keep_the_contract_separate_and_copies_isolated(self):
        fields = (
            "required_behaviors",
            "scope_exclusions",
            "constraints",
            "important_failure_cases",
            "acceptance_criteria",
            "permission_boundaries",
        )
        expected = {field: self.body[field] for field in fields}
        before = copy.deepcopy(self.state)
        for stage in ("astra_discovery", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                clarification, protected = context.repair_payloads(self.state, stage)
                self.assertNotIn("protected_contract", clarification)
                self.assertEqual(expected, protected)
                self.assertIn("requirements_handoff", clarification)
                protected["constraints"].clear()
                self.assertEqual(before, self.state)
        for stage in ("requirements_gather", "terra", "unknown-stage"):
            with self.subTest(stage=stage):
                clarification, protected = context.repair_payloads(self.state, stage)
                self.assertIsNone(protected)
                self.assertNotIn("protected_contract", clarification)
                if stage != "requirements_gather":
                    self.assertEqual({}, clarification)
