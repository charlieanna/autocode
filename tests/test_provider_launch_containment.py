"""The public launch seam must fail before models if its tool boundary is absent."""

import json
import shlex
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_provider_launch as launch
import autocode_tool_containment as containment
import autocode_util as util

import tests  # noqa: F401 - runtime import path


class LaunchContainment(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.policy = {"version": 1, "workspace": str(self.root), "scratch": str(self.root / ".autocode/stage/scratch")}
        self.adapter = SimpleNamespace(
            NAME="opencode",
            CONFIGURED=False,
            launch=mock.Mock(
                return_value=(["opencode", "run"], {"AUTOCODE_TOOL_CONTAINMENT": json.dumps(self.policy)}, {})
            ),
        )
        self.options = dict(
            engine="opencode",
            adapter=self.adapter,
            role="validator",
            route_role="sol",
            workspace=self.root,
            run_dir=self.root / ".autocode/runs/one",
            session=None,
            model="openai/gpt-6-sol",
            effort="high",
            allow_write=False,
            planning=False,
            report=self.root / "output.json",
            schema=self.root / "schema.json",
            prompt_file=self.root / "prompt.txt",
            sandbox="read-only",
            transport_args=[],
            chatgpt=True,
            provider=None,
        )

    def test_real_native_launch_requests_boundary_and_preserves_model(self):
        _, _, _, worker = launch.prepare(**self.options)
        args, kwargs = self.adapter.launch.call_args
        self.assertEqual(("openai/gpt-6-sol", "high", False), args[4:7])
        self.assertEqual(self.policy, worker["tool_containment"])
        self.assertIn(str(self.options["run_dir"]), kwargs["containment"]["protected_paths"])
        self.assertNotIn(str(Path.home()), kwargs["containment"]["read_roots"])

    def test_planning_and_command_preview_do_not_create_a_shell_boundary(self):
        for changes in ({"planning": True}, {"enforce_tool_boundary": False}):
            with self.subTest(changes=changes):
                launch.prepare(**{**self.options, **changes})
                self.assertNotIn("containment", self.adapter.launch.call_args.kwargs)

    def test_fresh_contained_session_does_not_change_the_event_protocol(self):
        _, _, _, worker = launch.prepare(**{**self.options, "session": "previous-session"})
        self.assertIsNone(self.adapter.launch.call_args.args[3])
        self.assertIsNone(worker["provider_session"])
        self.assertIn("fresh_session_reason", worker)
        _, _, _, simulated = launch.prepare(
            **{**self.options, "session": "previous-session", "enforce_tool_boundary": False}
        )
        self.assertEqual("previous-session", self.adapter.launch.call_args.args[3])
        self.assertEqual("previous-session", simulated["provider_session"])

    def test_configured_provider_does_not_inherit_an_unproven_kernel_claim(self):
        self.adapter.CONFIGURED = True
        self.adapter.launch.return_value = (["custom-provider"], {}, {})
        _, _, _, worker = launch.prepare(**self.options)
        self.assertNotIn("containment", self.adapter.launch.call_args.kwargs)
        self.assertNotIn("tool_containment", worker)

    def test_noncontained_and_writer_adapters_cannot_restore_ambient_copy_authority(self):
        ambient = {
            "AUTOCODE_VERIFICATION_COPY": "/old/stage/verification-copy.json",
            "AUTOCODE_VERIFICATION_COPY_SHA256": "old",
        }
        for configured, changes, settings in (
            (True, {}, None),
            (True, {"allow_write": True}, None),
            (False, {"planning": True}, None),
            (False, {"enforce_tool_boundary": False}, None),
            (False, {"allow_write": True}, None),
            (False, {}, {"allow_uncontained_tools": True}),
        ):
            with (
                self.subTest(configured=configured, changes=changes, settings=settings),
                mock.patch.dict("os.environ", ambient),
            ):
                self.adapter.CONFIGURED = configured
                self.adapter.launch.return_value = (["fixture-provider"], dict(ambient), {})
                _, environment, _, worker = launch.prepare(**{**self.options, **changes}, settings=settings)
                supplied = self.adapter.launch.call_args.kwargs["env"]
                for name in ambient:
                    self.assertNotIn(name, supplied)
                    self.assertNotIn(name, environment)
                self.assertNotIn("verification_copy", worker)

    def test_native_contained_readonly_adapter_keeps_its_fresh_copy_authority(self):
        fresh = {
            "AUTOCODE_TOOL_CONTAINMENT": json.dumps(self.policy),
            "AUTOCODE_VERIFICATION_COPY": "/native/owned/verification-copy.json",
            "AUTOCODE_VERIFICATION_COPY_SHA256": "fresh",
        }
        self.adapter.launch.return_value = (["opencode", "run"], fresh, {})
        _, environment, _, _ = launch.prepare(**self.options)
        self.assertEqual(fresh["AUTOCODE_VERIFICATION_COPY"], environment["AUTOCODE_VERIFICATION_COPY"])
        self.assertEqual("fresh", environment["AUTOCODE_VERIFICATION_COPY_SHA256"])

    def test_failed_or_timed_out_conformance_pauses_before_launch(self):
        for error in (
            RuntimeError("native boundary unavailable"),
            ValueError("unsafe root"),
            subprocess.TimeoutExpired(["opencode", "debug"], 1),
        ):
            with self.subTest(error=type(error).__name__):
                self.adapter.launch.side_effect = error
                with self.assertRaises(util.Paused) as caught:
                    launch.prepare(**self.options)
                self.assertEqual("PAUSED_TOOL_CONTAINMENT", caught.exception.status)
                # Available at setup, failed at launch: the pause names the explicit way on (#413).
                self.assertIn("--allow-uncontained-tools", str(caught.exception))

    def test_saved_opt_out_launches_uncontained_and_says_so_on_the_stage_record(self):
        # #413: only the saved settings flag opts out; the declared tool commands are then never computed.
        commands = mock.Mock(return_value=["make test"])
        self.adapter.launch.return_value = (["opencode", "run"], {}, {})
        _, _, _, worker = launch.prepare(
            **self.options, tool_commands=commands, settings={"allow_uncontained_tools": True}
        )
        self.assertNotIn("containment", self.adapter.launch.call_args.kwargs)
        commands.assert_not_called()
        record = launch.stage_record(worker)
        self.assertIs(True, record["uncontained_tools"])
        self.assertIsNone(record["tool_containment"])
        self.assertEqual(
            "OpenCode tool permissions and workspace snapshot checks; no OS sandbox; "
            "kernel containment waived by --allow-uncontained-tools",
            record["isolation"],
        )
        _, _, _, planner = launch.prepare(
            **{**self.options, "planning": True}, settings={"allow_uncontained_tools": True}
        )
        self.assertNotIn("uncontained_tools", launch.stage_record(planner))  # planning is never contained

    def test_without_the_saved_opt_out_the_stage_stays_contained(self):
        for settings in (None, {}, {"allow_uncontained_tools": "true"}, {"allow_uncontained_tools": 1}):
            with self.subTest(settings=settings):
                _, _, _, worker = launch.prepare(**self.options, settings=settings, tool_commands=lambda: ["make test"])
                self.assertEqual(["make test"], self.adapter.launch.call_args.kwargs["containment"]["tool_commands"])
                record = launch.stage_record(worker)
                self.assertNotIn("uncontained_tools", record)
                self.assertEqual(self.policy, record["tool_containment"])
                self.assertEqual("Kernel-constrained native shell; other tools disabled", record["isolation"])

    def test_handoff_changes_only_the_capture_example_not_old_evidence(self):
        data = {"workspace": str(self.root), "old_receipt": ".autocode/evidence/accepted.json"}
        text = "Example --output .autocode/evidence/<unique-name>.json\nCURRENT HANDOFF DATA\n" + json.dumps(data)
        updated = launch.containment_prompt(text, {"tool_containment": self.policy})
        parsed = json.loads(updated.split("\nCURRENT HANDOFF DATA\n")[1])
        self.assertEqual(data["old_receipt"], parsed["old_receipt"])
        self.assertEqual(self.policy, parsed["tool_containment"])
        self.assertIn(self.policy["scratch"], updated)
        self.assertNotIn("--output .autocode/evidence/<unique-name>.json", updated)

    def test_every_capture_example_names_the_one_location_a_contained_stage_can_write(self):
        # Live self-build 2026-10-06: a contained Validator's prompt named the run directory (COMMON) and the
        # scratch (provider contract) for the same receipts; the sandbox lets it write only the scratch.
        import autocode_check_replay as check_replay
        import autocode_support as support
        from providers import opencode

        prompt = opencode.prompt_for_schema(
            support.COMMON
            + check_replay.VALIDATOR_NOTE
            + "\nCURRENT HANDOFF DATA\n"
            + json.dumps({"workspace": str(self.root)}),
            {},
            self.root / "events.jsonl",
        )
        updated = launch.containment_prompt(prompt, {"tool_containment": self.policy})
        instructions = updated.split("\nCURRENT HANDOFF DATA\n")[0]
        scratch_example = "--output " + self.policy["scratch"] + "/evidence-<unique-name>.json"
        self.assertEqual(2, instructions.count(scratch_example))
        self.assertEqual(2, instructions.count("<unique-name>.json"))
        self.assertIn("replaces any other evidence or scratch directory named above", instructions)

    def test_changed_policy_is_a_prelaunch_hold_not_report_repair(self):
        with mock.patch.object(containment, "verify", side_effect=RuntimeError("changed policy")):
            with self.assertRaises(util.Paused) as caught:
                launch.verify_containment({"tool_containment": self.policy})
        self.assertEqual("PAUSED_TOOL_CONTAINMENT", caught.exception.status)
        launch.verify_containment({})

    def test_uncontained_prompt_ignores_handoff_and_environment_boundary_claims(self):
        text = "Instructions\nCURRENT HANDOFF DATA\n" + json.dumps(
            {"stage": "sol", "tool_containment": self.policy, "regression_proof": {"verdict": "PASS"}}
        )
        with mock.patch.dict("os.environ", {"AUTOCODE_TOOL_CONTAINMENT": json.dumps(self.policy)}):
            self.assertEqual(text, launch.containment_prompt(text, {}, stage="sol", regression_proof_current=True))

    def test_tester_note_uses_caller_stage_not_route_or_handoff_claims(self):
        text = "Instructions\nCURRENT HANDOFF DATA\n" + json.dumps({"stage": "sol"})
        worker = {"role": "sol", "tool_containment": self.policy}
        self.assertNotIn("CONTAINED TESTER", launch.containment_prompt(text, worker, stage="terra"))
        worker["role"] = "glm"
        updated = launch.containment_prompt(text, worker, stage="sol")
        self.assertIn("CONTAINED TESTER", updated)
        self.assertIn("No current runner-authenticated regression PASS", updated)


class TesterProofRequest(unittest.TestCase):
    """Generate actual stage requests with runner proof; never start a model."""

    def setUp(self):
        import autocode as runner

        from tests.test_test_root import ARCHITECTURE, component_files, state
        from tests.test_verify import Project, isolated_python

        self.runner = runner
        self.project = Project(ARCHITECTURE)
        self.addCleanup(self.project.close)
        self.project.write(component_files())
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.run = Path(directory.name).resolve()
        self.state = state(self.project, python=isolated_python(self))
        self.state.update(
            version=2,
            workspace=str(self.project.root),
            task="Validate component behavior",
            next_stage="sol",
            sessions={},
        )
        self.state["goal_contract"].update(revision=1, hash="fixture")
        self.state["settings"].update(
            engine="codex",
            roles={
                "sol": {"engine": "opencode", "model": "reviewer/model"},
                "glm": {"engine": "opencode", "model": "reviewer/model"},
            },
        )
        self.policy = {
            "version": 1,
            "workspace": str(self.project.root),
            "scratch": str(self.project.root / ".autocode" / ("tool-containment-" + "a" * 32) / "scratch"),
        }

    def request(
        self,
        *,
        contained=True,
        role="sol",
        alter_handoff=None,
        configured=False,
        dry_run=False,
        report_only=False,
        stage="sol",
    ):
        import autocode_stage_context as context

        runner = self.runner
        self.state["next_stage"] = stage
        planning = runner.planning.is_planning(self.state, stage)
        context_packet = runner.planning.context if planning else context.context_packet
        prompt, _ = context_packet(self.state, stage, self.run / "state.json")
        if alter_handoff:
            instruction, marker, raw = prompt.rpartition("\nCURRENT HANDOFF DATA\n")
            data = json.loads(raw)
            alter_handoff(data)
            prompt = instruction + marker + json.dumps(data)
        boundary = contained and not (configured or dry_run or report_only or planning)
        environment = {"AUTOCODE_TOOL_CONTAINMENT": json.dumps(self.policy)} if boundary else {}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(runner.opencode, "CONFIGURED", configured, create=True))
            stack.enter_context(mock.patch.object(runner.opencode, "NAME", "fixture-provider", create=True))
            stack.enter_context(
                mock.patch.object(runner.opencode, "launch", return_value=(["fixture-provider"], environment, {}))
            )
            stack.enter_context(mock.patch.object(runner.processes, "preflight"))
            stack.enter_context(mock.patch.object(runner.task_preflight, "guard"))
            stack.enter_context(mock.patch.object(runner.readonly_events, "prepare_opencode_snapshots"))
            stack.enter_context(mock.patch.object(launch, "verify_containment"))
            transform = stack.enter_context(
                mock.patch.object(launch, "containment_prompt", wraps=launch.containment_prompt)
            )
            admission = stack.enter_context(
                mock.patch.object(
                    runner.supervision, "launch", side_effect=RuntimeError("request captured before provider")
                )
            )
            complete = stack.enter_context(
                mock.patch.object(runner.regression, "complete", wraps=runner.regression.complete)
            )

            def run():
                return runner.run_role(
                    role=role,
                    prompt=prompt,
                    sandbox="read-only",
                    workspace=self.project.root,
                    run_dir=self.run,
                    state=self.state,
                    schema=runner.SCHEMA_DIR / "v2/sol-report.schema.json",
                    model="reviewer/model",
                    allow_write=False,
                    dry_run=dry_run,
                    report_only=report_only,
                )

            if dry_run:
                _, record = run()
                sent = Path(record["prompt"]).read_text()
                admission.assert_not_called()
            else:
                with self.assertRaisesRegex(RuntimeError, "request captured before provider"):
                    run()
                sent = Path(admission.call_args.kwargs["stdin"].name).read_text()
            self.state.pop("active_stage", None)
            self.state["iteration"] += 1
            if boundary and stage == "sol":
                complete.assert_called_once()
            else:
                complete.assert_not_called()
            if not boundary:
                self.assertEqual(transform.call_args.args[0], sent)
        instruction, _, handoff = sent.rpartition("\nCURRENT HANDOFF DATA\n")
        return instruction, json.loads(handoff)

    def test_current_runner_proof_is_cited_not_claimed_as_tester_execution(self):
        import autocode_regression as regression

        regression.before_review(self.state, "sol", self.project.root, self.run)
        self.assertEqual("PASS", self.state["regression_proof"]["verdict"])
        original = deepcopy(regression.handoff(self.state))
        instruction, handoff = self.request(role="glm")
        self.assertIs(True, handoff["runner_regression_proof_current"])
        self.assertEqual(original, handoff["regression_proof"])
        self.assertIn("The runner authenticated the current regression PASS", instruction)
        self.assertIn("never as your own execution", instruction)
        self.assertIn("not just health or static behavior", instruction)
        self.assertIn("Still execute permitted independent non-network checks", instruction)
        self.assertIn("using capture_command and the check schema", instruction)
        self.assertIn("clean replay has no runner artifacts", instruction)
        self.assertEqual(self.policy, handoff["tool_containment"])

    def test_unusable_proof_never_authorizes_http_pass(self):
        import autocode_regression as regression

        regression.before_review(self.state, "sol", self.project.root, self.run)
        original = deepcopy(self.state["regression_proof"])
        path = Path(original["path"])
        receipt = path.read_bytes()
        row = next(iter(json.loads(receipt)["checks"].values()))
        output = Path(row["output"])
        output_bytes = output.read_bytes()
        for reason in ("missing", "stale", "failed", "incomplete", "missing artifact", "changed check output"):
            with self.subTest(reason=reason):
                self.state["regression_proof"] = deepcopy(original)
                path.write_bytes(receipt)
                output.write_bytes(output_bytes)
                if reason == "missing":
                    self.state.pop("regression_proof")
                elif reason == "stale":
                    self.state["regression_proof"]["source_revision"] = "old-source"
                elif reason == "failed":
                    self.state["regression_proof"]["verdict"] = "FAIL"
                elif reason == "incomplete":
                    result = json.loads(receipt)
                    result["checks"] = {}
                    util.atomic_json(path, result)
                    self.state["regression_proof"]["receipt_sha256"] = util.file_hash(path)
                elif reason == "missing artifact":
                    path.unlink()
                else:
                    output.write_text("model-declared PASS, not executed output\n")
                instruction, handoff = self.request()
                self.assertIs(False, handoff["runner_regression_proof_current"])
                self.assertIn("Do not report HTTP PASS", instruction)
                self.assertIn("missing, stale, incomplete, failed, skipped or zero-test evidence cannot", instruction)

    def test_valid_state_does_not_authenticate_a_different_presented_proof(self):
        import autocode_regression as regression

        regression.before_review(self.state, "sol", self.project.root, self.run)
        original = deepcopy(regression.handoff(self.state))
        revision = regression.source_scope.snapshot(self.project.root, self.state)["revision"]
        mutations = {
            "missing": lambda data: data.pop("regression_proof"),
            "stale source": lambda data: data["regression_proof"].update(source_revision="old-source"),
            "fabricated cases": lambda data: data["regression_proof"].update(case_tests={"C1": ["made.up.test"]}),
            "fabricated checks": lambda data: data["regression_proof"].update(checks={"made-up": {"exit_code": 0}}),
            "fabricated path": lambda data: data["regression_proof"].update(path="/made-up/PASS.json"),
            "changed JSON type": lambda data: data["regression_proof"]["checks"]["regression_on_candidate"].update(
                exit_code=False
            ),
        }
        for reason, mutate in mutations.items():
            with self.subTest(reason=reason):
                self.assertTrue(regression.complete(self.state, revision))
                if reason == "changed JSON type":
                    self.assertIs(int, type(original["checks"]["regression_on_candidate"]["exit_code"]))
                    self.assertEqual(0, original["checks"]["regression_on_candidate"]["exit_code"])
                instruction, handoff = self.request(alter_handoff=mutate)
                self.assertIs(False, handoff["runner_regression_proof_current"])
                self.assertNotIn("The runner authenticated the current regression PASS", instruction)
                self.assertIn("Do not report HTTP PASS", instruction)
                self.assertEqual(original, regression.handoff(self.state))
                self.assertNotEqual(
                    json.dumps(original, sort_keys=True), json.dumps(handoff.get("regression_proof"), sort_keys=True)
                )

    def test_uncontained_configured_preview_and_report_only_requests_are_unchanged(self):
        import autocode_regression as regression

        regression.before_review(self.state, "sol", self.project.root, self.run)
        for options in ({"contained": False}, {"configured": True}, {"dry_run": True}, {"report_only": True}):
            with self.subTest(options=options):
                instruction, handoff = self.request(**options)
                self.assertNotIn("CONTAINED TESTER", instruction)
                self.assertNotIn("runner_regression_proof_current", handoff)

    def test_planning_and_checkpoint_requests_do_not_gain_sol_authority(self):
        import autocode_regression as regression

        regression.before_review(self.state, "sol", self.project.root, self.run)
        self.state["settings"]["joint_planning"] = True
        instruction, handoff = self.request(stage="astra_discovery", role="glm")
        self.assertNotIn("CONTAINED TESTER", instruction)
        self.assertNotIn("runner_regression_proof_current", handoff)
        self.state["settings"]["joint_planning"] = False
        instruction, handoff = self.request(stage="astra_checkpoint", role="glm")
        self.assertEqual(self.policy, handoff["tool_containment"])
        self.assertNotIn("CONTAINED TESTER", instruction)
        self.assertNotIn("runner_regression_proof_current", handoff)

    def test_real_skipped_or_zero_test_runs_do_not_supply_current_pass(self):
        import autocode_regression as regression

        from tests.test_test_root import NEW_TEST, component_files

        for reason in ("skipped", "zero tests"):
            with self.subTest(reason=reason):
                self.project.write(component_files())
                self.state["settings"]["regression"].pop("test_command", None)
                if reason == "skipped":
                    self.project.write(
                        {
                            "components/gateway/tests/test_gateway.py": NEW_TEST.replace(
                                "    def test_c1_", '    @unittest.skip("network unavailable")\n    def test_c1_'
                            )
                        }
                    )
                else:
                    self.project.write({"components/gateway/checks/__init__.py": ""})
                    python = self.state["settings"]["regression"]["python"]
                    self.state["settings"]["regression"]["test_command"] = (
                        shlex.quote(python) + " -m unittest discover -v -s components/gateway/checks"
                    )
                regression.before_review(self.state, "sol", self.project.root, self.run)
                self.assertNotEqual("PASS", self.state["regression_proof"]["verdict"])
                checks = util.read(self.state["regression_proof"]["path"])["checks"]
                self.assertTrue(
                    any(
                        row.get("results", {}).get("skipped")
                        if reason == "skipped"
                        else row.get("results", {}).get("total") == 0
                        for row in checks.values()
                    )
                )
                instruction, handoff = self.request()
                self.assertIs(False, handoff["runner_regression_proof_current"])
                self.assertIn("Do not report HTTP PASS", instruction)


if __name__ == "__main__":
    unittest.main()
