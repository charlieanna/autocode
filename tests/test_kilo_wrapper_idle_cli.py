"""Public offline CLI: completed tools cannot turn Kilo's wrapper into tool work."""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import autocode_process as processes
from autocode_taskrun import TaskRun

from tests import test_subprocess as subprocess_test_support


@unittest.skipUnless(sys.platform == "linux", "Requires the supported Linux provider boundary and ELF fixture")
class KiloWrapperIdleCliTests(unittest.TestCase):
    setUp = subprocess_test_support.SubprocessFlow.setUp

    def test_completed_tools_then_silent_model_wait_uses_idle_and_cleans_the_wrapper(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "The supported Linux CLI fixture requires Node")
        native = self.root / ".kilo"
        shutil.copy2("/bin/sleep", native)
        probe = self.root / "calls.jsonl"
        provider = self.root / "provider.cjs"
        provider.write_text(
            "const fs = require('node:fs');\n"
            "const {spawn} = require('node:child_process');\n"
            "const native = process.argv[2], probe = process.argv[3];\n"
            "const emit = row => process.stdout.write(JSON.stringify(row) + '\\n');\n"
            "const child = spawn(native, ['60'], {stdio: 'ignore'});\n"
            "fs.appendFileSync(probe, JSON.stringify({root:process.pid, child:child.pid}) + '\\n');\n"
            "emit({type:'step_start',sessionID:'fixture',part:{id:'first-step'}});\n"
            "for(let i=0;i<3;i++) emit({type:'tool_use',sessionID:'fixture',part:{\n"
            " id:'tool-'+i,tool:'read',state:{status:'completed',input:{},output:'read'}}});\n"
            "emit({type:'step_finish',sessionID:'fixture',part:{id:'finished-step',reason:'tool-calls'}});\n"
            "emit({type:'step_start',sessionID:'fixture',part:{id:'pending-model'}});\n"
            "setInterval(()=>{},1000); // Keep the provider wrapper alive without stream progress.\n"
        )
        config = Path(self.env["XDG_CONFIG_HOME"]) / "autocode/providers/wrapper_fixture.toml"
        config.parent.mkdir(parents=True)
        command = [node, str(provider), str(native), str(probe)]
        roles = ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")
        config.write_text(
            'name = "wrapper_fixture"\noutput = "opencode_events"\nprompt = "stdin"\nresume = ["--session", "{session}"]\ncommand = '
            + json.dumps(command)
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
                "wrapper_fixture",
                "--no-auto-select",
                "--workflow",
                "discuss",
                "--max-idle-seconds",
                "1",
                "--max-tool-seconds",
                "8",
                "--max-stage-seconds",
                "20",
                "--max-seconds",
                "60",
                "--evidence-provenance",
                "fake",
            ),
            env=self.env,
            cwd=self.root,
            timeout=60,
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
        envelope = json.loads(status.stdout)
        view = envelope["view"]
        self.assertEqual("PAUSED_JOB_FAILURE", view["status"], view.get("stop_reason"))
        self.assertIn("No new provider activity", view["stop_reason"])
        self.assertEqual("retry_job", view["needs"]["kind"])
        self.assertFalse(view["done"])
        self.assertIsNone(view.get("approved_contract"))
        limits = envelope["settings"]["limits"]
        self.assertEqual(
            (1, 8, 20, 60),
            tuple(
                limits[key]
                for key in ("idle_timeout_seconds", "tool_timeout_seconds", "stage_timeout_seconds", "max_seconds")
            ),
        )
        self.assertEqual("fixture/astra", envelope["settings"]["roles"]["astra"]["model"])
        attempts = [row for row in view["usage"]["accounting"]["attempts"] if not row["runner_owned"]]
        self.assertEqual(1, len(attempts), "No automatic provider replay")
        attempt = attempts[0]
        self.assertEqual("answer_question", attempt["stage"])
        self.assertTrue(attempt["timed_out"])
        self.assertFalse(attempt["active"])
        events = Path(attempt["events"])
        rows = [json.loads(line) for line in events.read_text().splitlines()]
        self.assertEqual(3, sum(row["type"] == "tool_use" for row in rows))
        self.assertEqual("pending-model", rows[-1]["part"]["id"])
        self.assertEqual(1, len(probe.read_text().splitlines()))
        # Job failure archives its event prefix; the keeper receipt remains at
        # the original iteration-level artifact path named by that event stem.
        receipt = json.loads((events.parent.parent / events.with_suffix(".supervision.json").name).read_text())
        launched = json.loads(probe.read_text().splitlines()[0])
        self.assertLessEqual(set(launched.values()), {row["pid"] for row in receipt["processes"]})
        self.assertEqual("stopped", receipt["phase"])
        self.assertIsNone(receipt["cleanup_error"])
        self.assertEqual([], processes.live_processes(receipt["processes"]))
        self.assertEqual([], list(self.project.glob("*.py")), "No implementation was written")


if __name__ == "__main__":
    unittest.main()
