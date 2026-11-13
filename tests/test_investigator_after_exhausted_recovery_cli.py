"""Public offline CLI: an exhausted timeout recovery reaches the Investigator (#3356).

A run whose stage kept timing out used up its automatic recoveries; the Investigator was then called
and refused before it launched, as a stale handoff, because its own freshly chosen route had changed the
run's settings. The run asked the person instead.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import autocode_resolver_recovery as resolver_recovery
import autocode_role_transport as role_transport
import autocode_stuck_job as stuck_job
from autocode_taskrun import TaskRun

from tests import test_subprocess as subprocess_test_support

# A configured tool that never answers: every attempt stops on the idle limit.
SILENT = "import sys, time\nsys.stdin.read()\ntime.sleep(600)\n"


class InvestigatorAfterExhaustedRecoveryCliTests(unittest.TestCase):
    setUp = subprocess_test_support.SubprocessFlow.setUp

    def test_the_investigator_launches_when_the_automatic_recoveries_run_out(self):
        tool = self.root / "silent_tool.py"
        tool.write_text(SILENT)
        config = Path(self.env["XDG_CONFIG_HOME"]) / "autocode/providers/silent_fixture.toml"
        config.parent.mkdir(parents=True)
        roles = ("task_planner", "builder", "tester", "completion", "planner", "plan_reviewer")
        config.write_text(
            'name = "silent_fixture"\noutput = "report_file"\nprompt = "stdin"\ncommand = '
            + json.dumps([sys.executable, str(tool)])
            + "\nmodels = "
            + json.dumps(["fixture/" + role for role in roles])
            + "\n[roles]\n"
            + "\n".join(role + ' = {model="fixture/' + role + '", effort="medium"}' for role in roles)
            + "\n"
        )
        run = TaskRun.start(
            self.project,
            "Explain what this empty repository contains without changing files.",
            options=(
                "--provider",
                "silent_fixture",
                "--no-auto-select",
                "--max-idle-seconds",
                "1",
                "--max-stage-seconds",
                "30",
                "--max-seconds",
                "300",
                "--evidence-provenance",
                "fake",
            ),
            env=self.env,
            cwd=self.root,
            timeout=600,
        )
        status = subprocess.run(
            [*run.command, "--status", "--workspace", str(self.project), "--run-dir", str(run.run_dir)],
            env=self.env,
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        view = json.loads(status.stdout)["view"]
        stages = [row["stage"] for row in view["usage"]["accounting"]["attempts"] if not row["runner_owned"]]
        # Three timed-out Requirements attempts, then the Investigator itself runs (and, on this silent
        # tool, times out too); it is never refused before launch.
        self.assertEqual(3, stages.count("requirements_gather"), stages)
        self.assertIn(stuck_job.STAGE, stages)
        self.assertNotIn("Stale handoff", view["stop_reason"])

    def test_the_binding_names_the_investigator_route_and_tool_binding_it_leaves_out(self):
        self.assertEqual(stuck_job.ROUTE, resolver_recovery.INVESTIGATOR_ROUTE)
        self.assertEqual(role_transport.PREFIX + stuck_job.ROUTE, resolver_recovery.INVESTIGATOR_TRANSPORT)


if __name__ == "__main__":
    unittest.main()
