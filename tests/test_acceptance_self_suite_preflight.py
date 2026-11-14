"""Bind negative acceptance preflight to actual commands and closed preparation custody."""

import copy
import json
import os
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_scratch_suites as suites
import autocode_util as util
import autocode_verification_preparation as preparation
import autocode_verification_recovery as recovery
import autocode_verification_schedule as schedule
import autocode_verify as verify
import autocode_wrapped_runner as policy
from autocode_test_framework import Framework

from tests.test_python_self_suite import HEADER, SOURCE
from tests.test_verify import Project

COMMAND = "python -m unittest test_todo -v"


class ActualSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "test_todo.py").write_text(SOURCE)

    def refusal(self, command):
        return policy.execution_refusal(
            self.root, ["test_todo.py"], [(Framework("unittest", command), command, "actual")]
        )

    def test_actual_outer_selector_preserves_unrelated_test_in_the_same_file(self):
        self.assertIsNone(self.refusal("python -m unittest test_todo.TodoCliTests.test_ac1_add_then_list_format -v"))
        self.assertIsNotNone(self.refusal(COMMAND))
        (self.root / "test_todo.py").write_text(
            HEADER + "def test_pytest_only():\n subprocess.run(['pytest','test_todo.py'])\n"
        )
        self.assertIsNone(self.refusal(COMMAND))

    def test_all_selected_suites_are_checked_before_the_first_product_execution(self):
        executed = []
        chosen = [
            (Framework("unittest", COMMAND), "python -m unittest test_ok", "first"),
            (Framework("unittest", COMMAND), COMMAND, "second"),
        ]
        result = suites.run_selected(
            self.root, ["test_todo.py"], chosen, lambda *args: executed.append(args), acceptance_preflight=True
        )
        self.assertEqual([], executed)
        self.assertIsNone(result["results"])
        self.assertIn("test_todo.py", result["error"])

    def test_negative_payload_is_exactly_bound_to_marker_source_command_and_json_types(self):
        out = self.root / "evidence"
        tree = out / "scratch/tree"
        tree.mkdir(parents=True)
        (tree / "test_todo.py").write_text(SOURCE)
        result = policy.execution_refusal(tree, ["test_todo.py"], [(Framework("unittest", COMMAND), COMMAND, "actual")])
        parameters = {"acceptance_preflight": True, "tests": ["test_todo.py"], "command": COMMAND}
        self.assertTrue(policy.valid_preflight_refusal(result, self.root, out, parameters))
        variants = [
            ("marker absent", lambda r, p: p.pop("acceptance_preflight")),
            ("marker integer", lambda r, p: p.update(acceptance_preflight=1)),
            ("tests changed", lambda r, p: p.update(tests=["other.py"])),
            ("command changed", lambda r, p: p.update(command="python -m unittest other")),
            ("tree escaped", lambda r, p: r["acceptance_refusal"].update(tree=str(self.root))),
            ("schema boolean", lambda r, p: r["acceptance_refusal"].update(schema=True)),
            ("source forged", lambda r, p: r["acceptance_refusal"]["refused"][0].update(source_sha256="0" * 64)),
            (
                "context forged",
                lambda r, p: r["acceptance_refusal"]["refused"][0].update(context=["TodoCliTests", "test_other"]),
            ),
            ("positive result", lambda r, p: r.update(results={"passed": ["test_other"]})),
            ("extra authority", lambda r, p: r.update(approved=True)),
        ]
        for name, mutate in variants:
            with self.subTest(name=name):
                changed, inputs = copy.deepcopy(result), copy.deepcopy(parameters)
                mutate(changed, inputs)
                self.assertFalse(policy.valid_preflight_refusal(changed, self.root, out, inputs))
        (self.root / "test_todo.py").write_text(SOURCE + "\n# newer source\n")
        self.assertFalse(policy.valid_preflight_refusal(result, self.root, out, parameters))

    def test_preparation_rejects_unclosed_tampered_or_unrequested_negative_results(self):
        out = self.root / "prepared"
        tree = out / "scratch/tree"
        tree.mkdir(parents=True)
        (tree / "test_todo.py").write_text(SOURCE)
        worker = self.root / "immutable_worker.py"
        worker.write_text("# fixture worker identity\n")
        admission = {
            "out": str(out),
            "nonce": "a" * 32,
            "owner": {"pid": 123, "birth_identity": 7},
            "worker": str(worker),
            "worker_sha256": util.file_hash(worker),
        }
        admission_path = out / "admission.json"
        util.atomic_json(admission_path, admission)
        reference = {"path": str(admission_path), "sha256": util.file_hash(admission_path)}
        original = policy.execution_refusal(
            tree, ["test_todo.py"], [(Framework("unittest", COMMAND), COMMAND, "actual")]
        )
        parameters = {"acceptance_preflight": True, "tests": ["test_todo.py"], "command": COMMAND}
        cases = (
            "valid",
            "unclosed",
            "request",
            "worker",
            "admission",
            "source",
            "marker",
            "owner",
            "answer",
            "missing-publication",
            "duplicate-publication",
            "changed-publication",
            "capture",
            "capture-hash",
            "symlink-capture",
            "foreign-capture",
            "result-symlink",
            "terminal-pin",
            "derived-selector",
            "root-string",
        )
        for case in cases:
            with self.subTest(case=case):
                worker.write_text("# fixture worker identity\n")
                (self.root / "test_todo.py").write_text(SOURCE)
                util.atomic_json(admission_path, admission)
                inputs = copy.deepcopy(parameters)
                if case == "marker":
                    inputs.pop("acceptance_preflight")
                if case in ("derived-selector", "root-string"):
                    inputs["command"] = None

                def execute(command, *args, **kwargs):
                    words = shlex.split(command)
                    request = Path(words[words.index("--request") + 1])
                    value = util.read(request)
                    answer = {
                        "request_sha256": util.file_hash(request),
                        "operation": "execute",
                        "nonce": admission["nonce"],
                        "receipt": copy.deepcopy(original),
                    }
                    if case == "request":
                        util.atomic_json(request, {**value, "parameters": {}})
                    if case == "worker":
                        worker.write_text("# changed worker\n")
                    if case == "admission":
                        util.atomic_json(admission_path, {**admission, "nonce": "b" * 32})
                    if case == "source":
                        (self.root / "test_todo.py").write_text(SOURCE + "\n# changed\n")
                    if case == "answer":
                        answer["receipt"]["acceptance_refusal"]["refused"] = []
                    util.atomic_json(value["result"], answer)
                    published = util.file_hash(value["result"])
                    capture = Path(args[1])
                    line = preparation.PREFLIGHT_PUBLICATION + published + "\n"
                    if case == "missing-publication":
                        line = ""
                    if case == "duplicate-publication":
                        line += line
                    if case == "changed-publication":
                        line = preparation.PREFLIGHT_PUBLICATION + "0" * 64 + "\n"
                    capture.write_text(line)
                    capture_hash = util.file_hash(capture)
                    if case == "capture":
                        capture.write_text(line + "changed after capture\n")
                    if case == "capture-hash":
                        capture_hash = "0" * 64
                    if case == "symlink-capture":
                        target = capture.with_suffix(".actual")
                        capture.rename(target)
                        capture.symlink_to(target)
                    if case == "foreign-capture":
                        capture = self.root / "foreign-capture.log"
                        capture.write_text(line)
                    if case == "result-symlink":
                        path = Path(value["result"])
                        target = path.with_suffix(".actual")
                        path.rename(target)
                        path.symlink_to(target)
                    if case in ("derived-selector", "root-string"):
                        answer["receipt"]["acceptance_refusal"]["suites"][0]["command"] = (
                            "python -m unittest test_todo.TodoCliTests.test_ac8_regression_suite_runs -v"
                            if case == "derived-selector"
                            else COMMAND + " " + str(tree) + "-foreign"
                        )
                        util.atomic_json(value["result"], answer)
                        # Self-consistent reported selectors alone are insufficient:
                        # only the independently captured publication hash binds them.
                        self.assertTrue(policy.valid_preflight_refusal(answer["receipt"], self.root, out, inputs))
                    return {
                        "command": command,
                        "exit_code": 0,
                        "timed_out": False,
                        "error": "",
                        "output": str(capture),
                        "output_sha256": capture_hash,
                        "supervision": {
                            "owner": {} if case == "owner" else admission["owner"],
                            "receipt": str(out / "fixture-native.json"),
                        },
                        "supervision_sha256": "1" * 64,
                    }

                with (
                    preparation.admitted(reference),
                    patch.object(preparation.commands, "run", side_effect=execute),
                    patch.object(recovery, "collected_command", return_value=case != "unclosed"),
                    patch.object(
                        recovery,
                        "bind_command",
                        side_effect=lambda out, receipt, **kwargs: {
                            "output": recovery.owned_path(receipt["output"], out),
                            "pins": {
                                receipt["supervision"]["receipt"]: "0" * 64
                                if case == "terminal-pin"
                                else receipt["supervision_sha256"]
                            },
                        },
                    ),
                ):
                    if case == "valid":
                        result = preparation.launch("execute", self.root, out, inputs, timeout=1, env={})
                        self.assertIsNone(result["results"])
                        self.assertIn("preparation", result)
                    else:
                        with self.assertRaises(util.Paused):
                            preparation.launch("execute", self.root, out, inputs, timeout=1, env={})

    def test_root_like_user_argument_is_not_rewritten_as_a_source_path(self):
        before = self.root / "scratch/tree"
        command = shlex.join(["python", "-m", "unittest", "test_todo", str(before) + "-literal"])
        self.assertEqual(command, policy._rebase_command(command, before, self.root))


@unittest.skipUnless(sys.platform == "linux", "Owned preparation requires supported Linux")
class NativePreflightTests(unittest.TestCase):
    def test_actual_scheduled_preflight_returns_only_closed_negative_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "product-executed"
            source = "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n" + SOURCE
            project = Project({"test_todo.py": source})
            self.addCleanup(project.close)
            for command in (None, shlex.join([sys.executable, "-m", "unittest", "test_todo", "-v"])):
                self.check_scheduled(project, marker, command)

    def check_scheduled(self, project, marker, command):
        identity = {
            "fixture": "3447 negative preflight",
            "command": command,
            "source": util.file_hash(project.root / "test_todo.py"),
        }
        try:
            result = schedule.run(
                project.evidence / "obligations",
                identity,
                lambda out: verify.scratch_run(
                    project.root, out, tests=["test_todo.py"], command=command, acceptance_preflight=True, timeout=30
                ),
                reuse_allowed=False,
                reason="fresh",
                current_identity=lambda: identity,
                owned_preparation=True,
            )
        finally:
            if destination := os.environ.get("BUILD_AUDIT_ARTIFACTS"):
                target = Path(destination) / "native-preflight" / ("explicit" if command else "derived")
                target.mkdir(parents=True, exist_ok=True)
                if project.evidence.exists():
                    shutil.copytree(project.evidence, target / "evidence", dirs_exist_ok=True)
                (target / "observation.json").write_text(
                    json.dumps(
                        {"command": command, "marker_executed": marker.exists(), "source_sha256": identity["source"]},
                        indent=2,
                    )
                    + "\n"
                )
        self.assertFalse(marker.exists())
        self.assertIsNone(result["results"])
        self.assertTrue(result["error"].startswith(policy.PREFLIGHT_ERROR))
        outer = result["preparation"]
        self.assertTrue(recovery.collected_command(outer["receipt"], Path(outer["request"]).parent))
        self.assertTrue(util.read(outer["request"])["parameters"]["acceptance_preflight"])
        self.assertFalse((Path(outer["request"]).parent / "scratch/tree").exists())
        self.assertNotIn("exit_code", result)
        self.assertNotIn("passed", result)

    def test_default_scratch_behavior_executes_the_selected_distinct_fixture(self):
        project = Project(
            {
                "test_todo.py": "import unittest\nclass T(unittest.TestCase):\n def test_sum(self): self.assertEqual(2,1+1)\n"
            }
        )
        self.addCleanup(project.close)
        result = verify.scratch_run(project.root, project.evidence / "default", tests=["test_todo.py"], timeout=30)
        self.assertEqual(0, result["exit_code"], result)
        self.assertEqual(1, result["results"]["total"])
        self.assertNotIn("acceptance_refusal", result)
