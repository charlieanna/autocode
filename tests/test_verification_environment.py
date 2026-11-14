"""Launcher metadata cannot make unchanged verification repeat after resume."""

import json
import os
import shlex
import unittest
from pathlib import Path
from unittest import mock

import autocode_regression as regression
import autocode_util as util
import autocode_verify as verify

from .test_verify import Project, isolated_python


class VerificationEnvironmentTests(unittest.TestCase):
    def test_commands_do_not_receive_the_controllers_tool_call_id(self):
        project = Project({"app.py": "VALUE = 1\n"})
        self.addCleanup(project.close)
        python = isolated_python(self)
        script = (
            "import json,os; print(json.dumps({k: os.environ[k] "
            "for k in ('CODEX_TOOL_CALL_ID', 'PROOF_MODE') if k in os.environ}))"
        )
        command = shlex.join([python, "-c", script])
        with mock.patch.dict(os.environ, {"CODEX_TOOL_CALL_ID": "call-one", "PROOF_MODE": "strict"}):
            result = verify.run_command(command, project.root, project.evidence / "environment.log")
            self.assertEqual(0, result["exit_code"], result)
            observed = json.loads(Path(result["output"]).read_text())
            self.assertNotIn("CODEX_TOOL_CALL_ID", observed)
            self.assertEqual("strict", observed["PROOF_MODE"])
            self.assertEqual("call-one", os.environ["CODEX_TOOL_CALL_ID"], "Do not mutate the controller")
        supplied = {"CODEX_TOOL_CALL_ID": "explicit-call", "PROOF_MODE": "strict"}
        self.assertNotIn("CODEX_TOOL_CALL_ID", verify.test_environment(project.root, supplied))
        self.assertEqual("explicit-call", supplied["CODEX_TOOL_CALL_ID"])

    def test_resumed_proof_reuses_checks_across_tool_calls_but_not_environment_changes(self):
        header = "import unittest\nfrom app import value\nclass Case(unittest.TestCase):\n"
        existing = "    def test_existing(self):\n        self.assertEqual(2, value(1))\n"
        project = Project({"app.py": "def value(n):\n    return n + 1\n", "test_app.py": header + existing})
        self.addCleanup(project.close)
        project.write(
            {
                "app.py": "def value(n):\n    return 4 if n == 2 else n + 1\n",
                "test_app.py": header + existing + "    def test_fix(self):\n        self.assertEqual(4, value(2))\n",
            }
        )
        command = f"{shlex.quote(isolated_python(self))} -m unittest -v test_app"
        state = {
            "base_commit": project.base,
            "goal_contract": {"body": {"task_kind": "bugfix"}},
            "settings": {"regression": {"test_command": command, "test_timeout": 30}},
        }
        with (
            mock.patch.dict(os.environ, {"CODEX_TOOL_CALL_ID": "call-one", "PROOF_MODE": "strict"}),
            mock.patch.object(verify, "baseline", wraps=verify.baseline) as baseline,
            mock.patch.object(verify, "verify", wraps=verify.verify) as execute,
        ):
            first = regression.prove(state, project.root, project.evidence)
            self.assertEqual(verify.PASS, first["verdict"], first)
            self.assertTrue(first["execution_context"]["identity"]["reuse_supported"])
            self.assertEqual(1, baseline.call_count)
            self.assertEqual(1, execute.call_count)
            # Serialize the saved run just as a later invocation would load it.
            resumed = json.loads(json.dumps(state))
            os.environ["CODEX_TOOL_CALL_ID"] = "call-two"
            second = regression.prove(resumed, project.root, project.evidence)
            self.assertEqual(first["path"], second["path"])
            self.assertEqual(first["receipt_sha256"], second["receipt_sha256"])
            self.assertEqual(1, baseline.call_count, "A new tool invocation must not repeat the baseline")
            self.assertEqual(1, execute.call_count, "A new tool invocation must not repeat the proof")
            identity = second["execution_context"]["identity"]
            self.assertEqual(util.digest(verify.test_environment(project.root)), identity["environment_hash"])

            os.environ["PROOF_MODE"] = "different"
            changed = regression.prove(resumed, project.root, project.evidence)
            self.assertEqual(verify.PASS, changed["verdict"], changed)
            self.assertNotEqual(second["path"], changed["path"])
            self.assertEqual(2, baseline.call_count, "Test inputs must still invalidate the baseline")
            self.assertEqual(2, execute.call_count, "Test inputs must still invalidate the proof")


if __name__ == "__main__":
    unittest.main()
