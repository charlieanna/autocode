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

# The fake Resolver exits immediately without a terminal report. Move only its real activity
# monitor past the idle deadline, as test_job_failure_recovery does, without waiting in real time.
RESOLVER_TIMEOUT = """\
import sys
sys.path.insert(0, TOOLS)
import autocode

wait = autocode.processes.wait_for_stage
def deadline(child, seconds, checkpoint, *, activity=None, **kwargs):
    if activity is None or not activity.path.name.startswith("resolver-"):
        return wait(child, seconds, checkpoint, activity=activity, **kwargs)
    code = child.wait(timeout=10)
    checkpoint([])
    later = activity.clock() + activity.idle_limit + 1
    activity.clock = lambda: later
    activity.timeout = activity.expired()
    assert activity.timeout is not None, "the fixture must reach the Resolver idle deadline"
    return code, True

autocode.processes.wait_for_stage = deadline
raise SystemExit(autocode.main())
"""


class ResolverRecoveryLifecycleCliTests(unittest.TestCase):
    setUp = subprocess_test_support.SubprocessFlow.setUp
    launch = subprocess_test_support.SubprocessFlow.launch
    new_run_engine_args = ("--engine", "codex")

    def test_a_stuck_resolver_is_investigated_once_and_reopening_cannot_launch_another_model(self):
        provider = self.root / "fixture-bin" / "codex"
        source = provider.read_text()
        marker = '\n_quota_model = os.environ.get("AUTOCODE_FIXTURE_QUOTA_MODEL")'
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, '\nif stage == "resolve":\n    raise SystemExit(0)\n' + marker))
        wrapper = self.root / "resolver-timeout.py"
        wrapper.write_text(RESOLVER_TIMEOUT.replace("TOOLS", repr(str(subprocess_test_support.TOOLS))))
        self.entry = [sys.executable, str(wrapper)]
        self.env.update(AUTOCODE_FIXTURE_MODE="rework", AUTOCODE_FIXTURE_INCOMPLETE_REWORK="1")

        self.launch(["Build greeting", "--chat"], 2, answers="CLI\nyes\n")
        run = next((self.project / ".autocode/runs").iterdir())

        def status():
            return json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]

        view = status()
        attempts = view["usage"]["accounting"]["attempts"]
        stages = [row["stage"] for row in attempts if not row["runner_owned"]]
        self.assertEqual(1, stages.count("builder"), "recovery must retain the completed implementation")
        self.assertGreaterEqual(stages.count("resolve"), 2, stages)
        self.assertLessEqual(stages.count("resolve"), 3, stages)
        self.assertTrue(all(row["timed_out"] for row in attempts if row["stage"] == "resolve"))
        self.assertEqual(1, stages.count(stuck_job.STAGE), f"{stages}: {view['stop_reason']}")
        self.assertEqual(["resolve", stuck_job.STAGE], stages[-2:])
        self.assertNotEqual("TASK_COMPLETE", view["status"])
        self.assertIn("Offline fixture: it cannot diagnose", view["stop_reason"])
        self.assertNotIn("Stale handoff", view["stop_reason"])
        retained = (self.project / "greet.py").read_bytes()

        # A new CLI process observes the saved outcome; opening it grants no further model call.
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        reopened = status()
        after = reopened["usage"]["accounting"]["attempts"]
        self.assertEqual(attempts, after[: len(attempts)], "all recorded attempts must survive reopening")
        self.assertEqual(
            [row for row in attempts if not row["runner_owned"]],
            [row for row in after if not row["runner_owned"]],
            "reopening may record a runner decision but cannot launch another model",
        )
        # Reopening publishes the repeated-failure operational exhaustion as WAITING_FOR_USER
        # instead of leaving RESOLVER_PENDING, retaining the diagnosis without another attempt.
        self.assertEqual("WAITING_FOR_USER", reopened["status"])
        self.assertEqual("answer", reopened["needs"]["kind"])
        self.assertEqual("operational_exhaustion", reopened["needs"]["resolver_scope"])
        self.assertEqual("PAUSED_REPEATED_FAILURE", reopened["needs"]["pause_origin"])
        self.assertIn("Offline fixture: it cannot diagnose", reopened["stop_reason"])
        for field in ("spent_usd", "running_usd", "reported_usd", "estimated_usd", "cap_usd"):
            self.assertEqual(view["cost"][field], reopened["cost"][field], field)
        self.assertEqual(view["routes"], reopened["routes"])
        self.assertEqual(retained, (self.project / "greet.py").read_bytes())


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
