"""Compile readiness is not named regression or suite evidence."""

import shlex
import shutil
import sys
import unittest
from unittest.mock import patch

import autocode_check_replay as replay
import autocode_scratch_suites as scratch_suites
import autocode_verification_plan as plan
import autocode_verification_preparation as preparation
import autocode_verify as verify

from . import test_check_replay


def state_for(command, kind):
    return {
        "goal_contract": {
            "body": {
                "acceptance_criteria": [
                    {"id": "C1", "criterion": "Check the source", "verification_method": f"{kind}: run `{command}`"}
                ]
            }
        },
        "current_task": {"acceptance_criteria": ["C1"]},
    }


class CheckKindTests(unittest.TestCase):
    def test_plan_distinguishes_compile_regression_and_suite_obligations(self):
        for method, kind, minimum in (
            ("compile: run `go test -run '^$' ./...`", "compile", 0),
            ("suite: run `go test ./...`", "full_suite", 1),
            ("test: TestC1Fix", "named_regression", None),
            ("guard: TestExisting", "named_guard", None),
        ):
            with self.subTest(method=method):
                state = state_for("go test ./...", "suite")
                state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"] = method
                [check] = plan.obligations(state)["checks"]
                self.assertEqual(kind, check["check_kind"])
                if minimum is not None:
                    self.assertEqual(minimum, check["collection_recipe"][0]["minimum_tests"])
                if kind == "compile":
                    self.assertFalse(check["collection_recipe"][0]["require_complete_ids_for_reuse"])

    def test_only_current_explicit_compile_intent_changes_command_acceptance(self):
        command = "go test -run '^$' ./..."
        state = state_for(command, "compile")
        self.assertEqual("compile", plan.command_kind(state, command))
        self.assertEqual("command", plan.command_kind({}, command))
        state["current_task"]["validation_plan"] = [f"suite: run `{command}`"]
        self.assertEqual("full_suite", plan.command_kind(state, command))
        state["current_task"] = {"acceptance_criteria": ["another"]}
        self.assertEqual("command", plan.command_kind(state, command))
        self.assertEqual(
            "full_suite",
            plan.command_kind(
                state, command, progressive_context={"required_checks": [{"method": f"suite: run `{command}`"}]}
            ),
        )

    def test_owned_preparation_preserves_compile_intent(self):
        with patch.object(preparation, "launch", return_value={}) as launch:
            preparation.execute(
                "/workspace", "/out", {}, tests=(), command="go test -run '^$'", timeout=30, check_kind="compile"
            )
        self.assertEqual("compile", launch.call_args.args[3]["check_kind"])

    def test_plan_probe_uses_the_same_acceptance_as_replay(self):
        command = "go test -run '^$' ./..."
        with patch.object(verify, "scratch_run", return_value={"exit_code": 0}) as run:
            self.assertIsNone(replay.probe(command, "/ws", "/out", run, state=state_for(command, "compile")))
        self.assertEqual("compile", run.call_args.kwargs["check_kind"])

    def test_scratch_intent_rejects_unknown_or_proof_replacement(self):
        for kind, options in (
            ("unknown", {}),
            ("compile", {"tests": ["test_a.py"]}),
            ("compile", {"acceptance_preflight": True}),
            ("compile", {"review_mutation": True}),
            ("compile", {"command": None}),
        ):
            with self.subTest(kind=kind, options=options), self.assertRaises(ValueError):
                scratch_suites.validate_intent(kind, **{"command": "go test", **options})

    def test_compile_command_is_executed_exactly_without_a_test_collector(self):
        calls = []

        def execute(framework, command, label):
            calls.append(("suite", command))
            return {"exit_code": 0}

        def exact(command):
            calls.append(("command", command))
            return {"exit_code": 0, "timed_out": False}

        result = scratch_suites.command_check(
            "/tree",
            (),
            None,
            "go test -run '^$'",
            execute,
            exact,
            lambda *_: False,
            lambda _: False,
            check_kind="compile",
        )
        self.assertEqual([("command", "go test -run '^$'")], calls)
        self.assertIsNone(result["results"])
        self.assertFalse(result["results_expected"])


class CompileReplayTests(unittest.TestCase):
    setUp = test_check_replay.ScratchReplayTests.setUp

    def replay(self, command, kind):
        return replay.replay(
            [{"command": command, "exit_code": 0, "evidence_ref": "event:check"}],
            self.workspace,
            self.run_dir,
            {"output": "tester.json", "source_revision": "r"},
            verify.scratch_run,
            approved_state=state_for(command, kind),
            timeout=60,
        )

    @unittest.skipUnless(shutil.which("go"), "Go is required for the compile/vet regression")
    def test_zero_test_go_compile_passes_without_becoming_test_evidence(self):
        (self.workspace / "go.mod").write_text("module example.test/compile\n\ngo 1.20\n")
        (self.workspace / "main.go").write_text("package compile\nfunc Value() int { return 1 }\n")
        (self.workspace / "main_test.go").write_text(
            'package compile\nimport "testing"\nfunc TestValue(t *testing.T) { t.Fatal("must not run") }\n'
        )
        command = "go test -run '^$' ./..."
        with self.assertRaisesRegex(ValueError, "zero tests"):
            self.replay(command, "suite")
        result = self.replay(command, "compile")
        self.assertEqual("PASS", result["verdict"])
        [check] = result["checks"]
        self.assertEqual("compile", check["check_kind"])
        self.assertIsNone(check["results"])
        with self.assertRaisesRegex(ValueError, "exited 1"):
            self.replay("go test ./...", "suite")

    def test_compile_failure_and_other_runners_keep_exact_exit_requirement(self):
        command = f"{shlex.quote(sys.executable)} -m compileall -q app.py"
        (self.workspace / "app.py").write_text("value = 1\n")
        self.assertEqual("PASS", self.replay(command, "compile")["verdict"])
        (self.workspace / "app.py").write_text("value = (\n")
        with self.assertRaisesRegex(ValueError, "exited 1"):
            self.replay(command, "compile")

    def test_compile_kind_cannot_replace_selected_test_or_mutation_execution(self):
        for options in ({"tests": ["test_a.py"]}, {"acceptance_preflight": True}, {"review_mutation": True}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "Compile-only"):
                verify.scratch_run(
                    self.workspace, self.run_dir, command="go test -run '^$'", check_kind="compile", **options
                )
