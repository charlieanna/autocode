"""Golden pre-extraction renderings: every stage and conditional prompt family.

The fixed packets avoid filesystem/provider variability. Only the installation
path of autocode.py is normalized; all instruction and handoff bytes are hashed.
The hashes were captured before moving the strings, rather than from the loader.
"""

import hashlib
import json
import sys
import unittest
from contextlib import ExitStack, suppress
from pathlib import Path
from unittest.mock import patch

import autocode as cli
import autocode_artifacts as artifacts
import autocode_bug_job as bug
import autocode_cmd_only_report as command_only
import autocode_component_runtime as component_runtime
import autocode_design_check_job as design_check
import autocode_design_intake as design_intake
import autocode_design_job as design
import autocode_discuss_job as discuss
import autocode_figma as figma
import autocode_format_correction as correction
import autocode_goals as goals
import autocode_milestone_replan as replan
import autocode_milestones as milestones
import autocode_output_policy as output_policy
import autocode_progressive_state as progressive
import autocode_protected_oracles as protected_oracles
import autocode_provider_launch as provider_launch
import autocode_report_repair_context as repair
import autocode_report_source as report_source
import autocode_stage_access as access
import autocode_stage_context as stage_context
import autocode_stuck_job as stuck
import autocode_stuck_repair_context as stuck_repair
import autocode_support as support
import autocode_workflow as workflow
import autocode_workflows as workflows
from providers import codex_sandbox, command, opencode
from units import autoplanner as planner
from units import autoresolver, autoreview, common

FIXTURES = Path(__file__).with_name("prompt_rendering_fixtures.json")
WORKSPACE = "/prompt-fixture/workspace"
STATE_PATH = Path("/prompt-fixture/run/state.json")
SNAPSHOT = {"head": "fixed-head", "revision": "fixed-revision", "files": {}}


def fixed_state():
    return {
        "version": 3,
        "task_id": "task-fixture",
        "task": "Build a greeting CLI. It must preserve TestGreeting and print Hello.",
        "workspace": WORKSPACE,
        "status": "RUNNING",
        "stages": [],
        "answers": {"Q1": "Hello"},
        "user_events": [],
        "acceptance_criteria": [],
        "current_task": {"id": "task-1", "kind": "implement", "affected_paths": ["greeting.py"]},
        "settings": {
            "joint_planning": True,
            "context_soft_tokens": 10000,
            "roles": {
                name: {"model": "fixed-model", "engine": "codex"}
                for name in ("astra", "glm", "terra", "sol", "requirements", "plan_reviewer", "resolver")
            },
        },
        "stuck_investigation": {
            "stage": "terra",
            "status": "PAUSED_TEST",
            "reason": "a fixed failure",
            "identity": "incident-1",
        },
        "design_intake": {"references": ["https://figma.com/design/fixed-key/fixture"]},
        "resolution_request": {"source_revision": "fixed-revision", "reason": "a fixed failure"},
        "diagnosis_request": {"repeated_count": 2, "reason": "a fixed failure"},
    }


def rendered_cases():
    cases = {}
    schema_dir = Path(support.__file__).parent / "autocode-schemas"

    def add(name, result):
        text = result[0] if isinstance(result, tuple) else result
        # This path is installation-dependent data, not instruction text.
        text = text.replace(str(Path(stage_context.__file__).with_name("autocode.py")), "/prompt-fixture/autocode.py")
        text = text.replace(
            str(Path(design_intake.__file__).with_name("figma_inventory_page.js")),
            "/prompt-fixture/figma_inventory_page.js",
        )
        cases[name] = text

    with ExitStack() as stack:
        stack.enter_context(patch.object(sys, "executable", "/prompt-fixture/python"))
        stack.enter_context(patch.object(stage_context.source_scope, "snapshot", return_value=SNAPSHOT))
        stack.enter_context(patch.object(planner, "workspace_inventory", return_value={"files": ["greeting.py"]}))
        stack.enter_context(patch.object(planner.artifacts, "verify_predecessor", return_value=None))
        stack.enter_context(patch.object(goals, "execution_guard"))
        stack.enter_context(patch.object(autoresolver, "guard"))
        stack.enter_context(patch.object(autoresolver, "diagnosis_guard"))
        for stage in support.STABLE:
            for version in (2, 3):
                state = fixed_state()
                state["version"] = version
                add(f"execution/{stage}/v{version}", stage_context.context_packet(state, stage, STATE_PATH))
        add("execution/astra_discovery/v3", stage_context.context_packet(fixed_state(), "astra_discovery", STATE_PATH))
        for stage in planner.PROMPTS:
            for variant in ("ordinary", "figma", "investigation", "design", "adaptive"):
                state = fixed_state()
                if variant == "figma":
                    state["settings"]["figma_file"] = "https://figma.com/design/fixed-key/fixture"
                elif variant == "investigation":
                    state["investigation_request"] = {"stage": stage, "question": "Q1"}
                elif variant == "design":
                    state["design_constraint"] = {"constraints": ["Preserve the greeting interface"]}
                elif variant == "adaptive":
                    state["settings"]["adaptive_planning"] = True
                add(f"planning/{stage}/{variant}", planner.context(state, stage, STATE_PATH))
        for job in (workflows, bug, design, design_check, discuss, design_intake):
            for engine in ("codex", "opencode"):
                add(
                    f"job/{job.__name__}/{engine}", job.prompt(fixed_state(), {"files": ["greeting.py"]}, engine=engine)
                )
        for stage in ("terra", "sol", "astra_review", "astra_checkpoint"):
            add(f"unit/{stage}", common.execution_request(fixed_state(), stage, STATE_PATH, schema_dir).prompt)
        for stage in ("sol", "astra_review", "astra_checkpoint"):
            add(f"review/{stage}", autoreview.prepare(fixed_state(), stage, STATE_PATH, schema_dir).prompt)
        for stage in ("astra_resolve", "astra_diagnose"):
            add(f"resolver/{stage}", autoresolver.prepare(fixed_state(), stage, STATE_PATH, schema_dir).prompt)
        schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
        prompt = "Instruction\nCURRENT HANDOFF DATA\n" + json.dumps({"workspace": WORKSPACE})
        add(
            "provider/opencode-output-contract",
            opencode.prompt_for_schema(prompt, schema, Path("/prompt-fixture/run/events.jsonl")),
        )
        for adapter in (None, codex_sandbox.ADAPTER):
            config = {"name": "fixture", "roles": {}, "output": "report_file", "sandbox_adapter": adapter}
            provider = command.CommandProvider(config, Path("/prompt-fixture/provider.toml"))
            add(
                f"provider/command-output-contract/{adapter}",
                provider.prompt_for_schema(prompt, schema, Path("/prompt-fixture/run/events.jsonl")),
            )
        add(
            "context/component-runtime",
            json.dumps(
                component_runtime.architecture_contract("Deliver components.json with runtime and smoke.json"),
                sort_keys=True,
            ),
        )
        add("context/output-policy", json.dumps(output_policy.context({}), sort_keys=True))
        record = {
            "files": {"tests/test_greeting.py": "fixed-hash"},
            "binding_hash": "fixed-binding",
            "root": WORKSPACE,
            "inventory_path": "/prompt-fixture/inventory.json",
            "command": "python3 -m unittest",
        }
        with patch.object(protected_oracles, "verify_binding"):
            add(
                "context/protected-tests",
                json.dumps(protected_oracles.context({"protected_tests": record}, ["tests/"]), sort_keys=True),
            )
        previous = {
            "revision": 1,
            "design_under_review": "docs/design.md",
            "verdict": "request_changes",
            "summary": "fixed review",
            "satisfied": [],
            "concerns": [],
            "questions": [],
            "said": "Keep the interface",
        }
        with patch.object(design, "revising", return_value=previous):
            add("design/revise", design.prompt(fixed_state()))
        add("repair/command-only-correction", correction.prompt(command_only.ERROR))
        add("repair/command-only-source", report_source.repair_report_instruction({"error": command_only.ERROR}))
        add(
            "bug/prepared-scratch",
            bug.prompt(
                fixed_state(),
                engine="opencode",
                scratch_workspace=WORKSPACE + "/.autocode/investigation/fixed",
                python_executable="/prompt-fixture/python",
            ),
        )
        add("stuck/ordinary", stuck.prompt(fixed_state(), STATE_PATH))
        state = fixed_state()
        state["stuck_investigation"].update(
            mode="builder_failure", failure_evidence={"failure_id": "F1", "evidence_refs": ["run/failure.json"]}
        )
        add("stuck/builder-failure", stuck.prompt(state, STATE_PATH))
        for stage in (
            "requirements_gather",
            "astra_discovery",
            "glm_revise",
            "astra_challenge",
            "astra_finalize",
            "terra",
            "sol",
            "investigate_stuck",
        ):
            add(f"repair/clarification/{stage}", repair.instruction(stage))
            add(f"repair/baseline/{stage}", repair.baseline_instruction(stage))
            add(f"repair/evidence/{stage}", stuck_repair.evidence_instruction(stage))
        for variant, pending in (
            ("ordinary", {}),
            ("truncated", {"original": {"truncated_output": True}}),
            ("command-only", {"error": "$: missing summary; final response is only a shell command"}),
        ):
            add(f"repair/source/{variant}", report_source.repair_report_instruction(pending))
        add("repair/correction", correction.prompt("$: missing summary"))
        proof = {"verdict": "PASS", "source_revision": "fixed-revision"}
        for stage in ("terra", "sol", "astra_review", "astra_checkpoint"):
            for current in (False, True):
                prompt = "Instruction\nCURRENT HANDOFF DATA\n" + json.dumps({"regression_proof": proof})
                add(
                    f"containment/{stage}/{current}",
                    provider_launch.containment_prompt(
                        prompt,
                        {"tool_containment": {"scratch": "/prompt-fixture/scratch"}},
                        stage=stage,
                        regression_proof_current=current,
                        regression_handoff=proof,
                    ),
                )
        for stage in ("terra", "sol", "astra_review", "astra_checkpoint"):
            state = fixed_state()
            with patch.object(
                progressive,
                "context",
                return_value={
                    "active_slice": {"id": "S1"},
                    "required_checks": [{"id": "K1", "method": "python3 -m unittest"}],
                    "checkpoint_checks": [{"id": "K1", "method": "python3 -m unittest"}],
                },
            ):
                add(f"progressive/{stage}", stage_context.context_packet(state, stage, STATE_PATH))
            for batch in (False, True):
                state = fixed_state()
                if batch:
                    state["current_task"]["milestone_ids"] = ["M1", "M2"]
                checkpoint = {
                    "current": {"id": "M1", "status": "PENDING"},
                    "limits": {"max_replans": 1, "stalled_reviews": 2},
                }
                with (
                    patch.object(milestones, "enabled", return_value=True),
                    patch.object(milestones, "summary", return_value=checkpoint),
                    patch.object(milestones, "scope", return_value={"members": []}),
                    patch.object(milestones, "evidence_ready", return_value=False),
                ):
                    add(f"milestones/{stage}/{batch}", stage_context.context_packet(state, stage, STATE_PATH))
        for stage in ("sol", "astra_checkpoint"):
            for final in (False, True):
                with (
                    patch.object(workflow, "enabled", return_value=True),
                    patch.object(workflow, "guard"),
                    patch.object(workflow, "final_only", return_value=final),
                ):
                    state = fixed_state()
                    state["settings"]["workflow"] = {"mode": "glm_first_v1"}
                    add(f"workflow/{stage}/{final}", stage_context.context_packet(state, stage, STATE_PATH))
        for stage in (
            "requirements_gather",
            "astra_discovery",
            "astra_challenge",
            "glm_revise",
            "astra_finalize",
            "terra",
            "sol",
            "astra_review",
            "investigate_stuck",
        ):

            class Captured(Exception):
                pass

            def capture(**kwargs):
                add(f"full-repair/{stage}", kwargs["prompt"])
                raise Captured

            state = fixed_state()
            state["next_stage"] = stage
            schema = planner.schema_for(state, stage) if stage in planner.STAGES else {}
            original = {
                "role": "astra",
                "stage": stage,
                "schema": "/prompt-fixture/schema.json",
                "source_revision": "fixed-revision",
                "events": "/prompt-fixture/events.jsonl",
            }
            state["pending_report_repair"] = {
                "original": original,
                "attempts": 0,
                "pins": {},
                "contract_hash": None,
                "error": "$: missing summary",
            }
            source = {
                "path": "/prompt-fixture/report.json",
                "sha256": "fixed-report",
                "content": {"summary": "fixed draft"},
            }
            with (
                patch.object(cli, "repair_limit", return_value=5),
                patch.object(cli, "stale_report_repair", return_value=False),
                patch.object(cli.resolver_runtime, "boundary"),
                patch.object(cli.format_correction, "execute", return_value=False),
                patch.object(cli, "repair_report_source", return_value=source),
                patch.object(cli.support, "read", return_value=schema),
                patch.object(cli.support, "events", return_value=[]),
                patch.object(cli, "write_json"),
                patch.object(cli, "run_role", side_effect=capture),
            ):
                with suppress(Captured):
                    cli.execute_report_repair(state, STATE_PATH.parent, Path(WORKSPACE))
        for stage in artifacts.FILE_SLUGS:
            add(f"scratch/{stage}", access.scratch_rule(stage))
        for review in ("automatic", "human"):
            for stage in (None, "terra", "sol"):
                add(
                    f"figma/{review}/{stage}",
                    figma.instructions(
                        {"figma_file": "https://figma.com/design/fixed-key/fixture", "figma_review": review},
                        stage=stage,
                        current_task={"id": "task-1"},
                    ),
                )
        for count in (0, 2):
            for spent in (0, 1):
                for batch in (False, True):
                    row = {"id": "M1", "reviews_without_progress": count, "replans": spent}
                    if batch:
                        row["milestone_ids"] = ["M1", "M2"]
                    add(
                        f"milestone/constraint/{count}/{spent}/{batch}",
                        replan.constraint(row, {"max_replans": 1, "stalled_reviews": 2}),
                    )
    return cases


class RenderedPromptTests(unittest.TestCase):
    def test_planning_reports_follow_the_provider_output_contract(self):
        cases = {name: text for name, text in rendered_cases().items() if name.startswith("planning/")}
        self.assertTrue(cases)
        for name, text in cases.items():
            with self.subTest(case=name):
                self.assertIn("Follow the provider output contract for reporting.", text)
                self.assertNotIn("return the report, the runner saves it", text)

    def test_every_fixed_stage_rendering_preserves_its_pre_extraction_bytes(self):
        expected = json.loads(FIXTURES.read_text())
        actual = rendered_cases()
        self.assertEqual(set(expected), set(actual))
        for name, text in actual.items():
            with self.subTest(case=name):
                self.assertEqual(
                    expected[name],
                    {"bytes": len(text.encode("utf-8")), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()},
                )


if __name__ == "__main__":
    unittest.main()
