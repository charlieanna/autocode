"""Original-attempt investigation evidence reaches discovery through the public CLI."""

import json
import os
import subprocess
import sys
import unittest

from autocode_taskrun import TaskRun

from .test_bug_job import diagnosis
from .test_job_failure_recovery import PROVIDER, WRAPPER, JobHarness


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised fake-provider regression requires supported Linux")
class InvestigationHandoffCliTests(JobHarness):
    def test_accepted_reproduction_reaches_discovery_without_an_extra_investigation(self):
        # A minimal isolated runtime makes complete dependency identity possible;
        # no pip/network or credentials are needed.
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(self.workspace / ".venv")], check=True)
        ignore = self.workspace / ".gitignore"
        ignore.write_text(ignore.read_text() + ".venv/\n")
        self.env["PATH"] = (
            str(self.workspace / ".autocode/bin")
            + os.pathsep
            + str(self.workspace / ".venv/bin")
            + os.pathsep
            + self.env["PATH"]
        )
        capture = self.workspace / ".autocode/discovery-handoff.json"
        report = self.workspace / ".autocode/investigation-fixture.json"
        value = diagnosis(
            fix_size="large",
            note_path="docs/bugs/double.json",
            affected_paths=["calc.py"],
            test_paths=["tests/test_calc.py"],
            observed="double(5) returns 10",
            root_cause="multiplier is two",
            reproduction="double(5) == 10",
            invariant="double(5) returns 15",
            probe="PYTHONDONTWRITEBYTECODE=1 python3 -B -c 'from calc import double; assert double(5) == 10'",
            untestable="",
            counterexample={"file": "calc.py", "find": "return n * 2", "replace": "return n * 3"},
            test_cases=[{"id": "T1", "given": "n=5", "when": "double(5)", "then": "returns 15", "kind": "restore"}],
        )
        report.write_text(json.dumps(value))
        provider = PROVIDER.replace("Logged in (offline)", "Logged in using ChatGPT (offline fixture)").replace(
            "sys.stdin.read()",
            """prompt=sys.stdin.read()
packet=json.loads(prompt.split('CURRENT HANDOFF DATA\\n',1)[1])
with Path(os.environ['JOB_CALLS']).open('a') as calls:calls.write(packet.get('stage','unknown')+'\\n')
if packet.get('stage')=='investigate_bug':
 print(json.dumps({'type':'thread.started','thread_id':str(uuid.uuid4())}),flush=True)
 Path(sys.argv[sys.argv.index('-o')+1]).write_text(Path(os.environ['JOB_REPORT']).read_text())
 print(json.dumps({'type':'turn.completed','usage':{'input_tokens':20,'output_tokens':10}}),flush=True)
 raise SystemExit(0)
if packet.get('stage')=='astra_discovery':
 print(json.dumps({'type':'thread.started','thread_id':str(uuid.uuid4())}),flush=True)
 Path(os.environ['JOB_CAPTURE']).write_text(json.dumps(packet))
 print(json.dumps({'type':'turn.failed','error':{'message':'fixture stops after inspecting discovery'}}),flush=True)
 raise SystemExit(77)
""",
        )
        (self.workspace / ".autocode/bin/codex").write_text(provider)
        self.wrapper.write_text(
            WRAPPER.replace(
                "import autocode\n",
                'for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):os.environ.pop(key,None)\nimport autocode\n',
            )
        )
        codex_home = self.workspace / ".autocode/codex-home"
        codex_home.mkdir()
        self.env.update(CODEX_HOME=str(codex_home), OPENAI_API_KEY="", CODEX_API_KEY="", OPENAI_BASE_URL="")
        self.env.update(JOB_MODE="filtered", JOB_REPORT=str(report), JOB_CAPTURE=str(capture))
        run = TaskRun.start(
            self.workspace,
            "Fix double(5) to return 15, preserving integer inputs.",
            command=(sys.executable, "-B", str(self.wrapper)),
            options=(
                *self.options,
                "--joint-planning",
                "--no-adaptive-planning",
                "--evidence-provenance",
                "fake",
                "--astra-model",
                "gpt-6-astra",
                "--terra-model",
                "gpt-5.6-terra",
                "--sol-model",
                "gpt-5.6-sol",
                "--glm-model",
                "gpt-5.6-sol",
                "--plan-reviewer-model",
                "gpt-6-astra",
                "--completion-model",
                "gpt-6-astra",
            ),
            start_options=("--workflow", "bugfix"),
            env=self.env,
            timeout=240,
        )
        view = run.advance_until_input()
        self.assertTrue(
            capture.is_file(), {key: view.get(key) for key in ("status", "next_stage", "stop_reason", "needs")}
        )
        packet = json.loads(capture.read_text())
        handoff = packet["bug_diagnosis_evidence"]
        self.assertEqual("reusable_planning_context", handoff["eligibility"], handoff)
        self.assertEqual(str(run.run_dir.resolve()), handoff["origin"]["run_dir"])
        self.assertEqual("investigate_bug", handoff["origin"]["stage"])
        self.assertEqual(value["root_cause"], packet["bug_diagnosis"]["root_cause"])
        self.assertEqual(value["probe"], handoff["reproduction_receipt"]["probe"])
        self.assertEqual(0, handoff["reproduction_receipt"]["exit_code"])
        self.assertNotEqual(0, handoff["reproduction_receipt"]["counterexample"]["exit_code"])
        self.assertIn("calc.py", handoff["cited_sources"])
        self.assertEqual(["investigate_bug", "astra_discovery"], self.calls.read_text().splitlines())
        self.assertFalse(view["done"])
        self.assertNotEqual("TASK_COMPLETE", view["status"])
        self.assertEqual("def double(n):\n    return n * 2\n", (self.workspace / "calc.py").read_text())
