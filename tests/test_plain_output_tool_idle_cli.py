"""Public offline CLI: a configured tool with no event protocol is not idle while it prints progress (#3309's run).

The GoCode tool runs ``codex exec`` without ``--json``: it writes its commands and results as plain lines and
its report to a file. Every Builder and Resolver attempt in that run stopped at the 300-second idle limit while
the tool was working, because none of those lines counted as activity.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from autocode_taskrun import TaskRun

from tests import test_subprocess as subprocess_test_support

TOOL = """
import sys, time
sys.stdin.read()
for step in range(30):
    print(f"exec /bin/zsh -lc 'step {step}' succeeded", file=sys.stderr, flush=True)
    time.sleep(0.3)
sys.exit(3)
"""


class PlainOutputToolIdleCliTests(unittest.TestCase):
    setUp = subprocess_test_support.SubprocessFlow.setUp

    def test_plain_progress_lines_keep_a_report_file_tool_out_of_the_idle_stop(self):
        tool = self.root / "plain_tool.py"
        tool.write_text(TOOL)
        config = Path(self.env["XDG_CONFIG_HOME"]) / "autocode/providers/plain_fixture.toml"
        config.parent.mkdir(parents=True)
        roles = ("task_planner", "builder", "tester", "completion", "planner", "plan_reviewer")
        config.write_text(
            'name = "plain_fixture"\noutput = "report_file"\nprompt = "stdin"\ncommand = '
            + json.dumps([sys.executable, str(tool), "{report}"])
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
                "plain_fixture",
                "--no-auto-select",
                "--workflow",
                "discuss",
                "--max-idle-seconds",
                "1",
                "--max-stage-seconds",
                "20",
                "--max-seconds",
                "60",
                "--evidence-provenance",
                "fake",
            ),
            env=self.env,
            cwd=self.root,
            timeout=90,
        )
        status = subprocess.run(
            [*run.command, "--status", "--workspace", str(self.project), "--run-dir", str(run.run_dir)],
            env=self.env,
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        view = json.loads(status.stdout)["view"]
        attempts = [row for row in view["usage"]["accounting"]["attempts"] if not row["runner_owned"]]
        self.assertTrue(attempts, view.get("stop_reason"))
        first = attempts[0]
        # Nine seconds of progress lines, past the five-second startup grace, under a one-second idle limit:
        # the attempt ends on the tool's own exit, not on the idle watchdog.
        self.assertFalse(first["timed_out"], first)
        self.assertNotIn("No new provider activity", view.get("stop_reason") or "")
        rows = Path(first["events"]).read_text().splitlines()
        self.assertIn("exec /bin/zsh -lc 'step 29' succeeded", rows)


if __name__ == "__main__":
    unittest.main()
