"""A scripted Tester accepts four fresh receipts from one public capture batch."""

import json
import shlex
import sys
import unittest
from pathlib import Path

from . import test_codex_capture_copy as fixture


class ReadyCaptureBatchCLI(unittest.TestCase):
    run_to_validation = fixture.CodexCaptureWorkflowTests.run_to_validation

    def setUp(self):
        fixture.CodexCaptureWorkflowTests.setUp(self)
        self.options = (*self.options, "--max-cost-usd", "0", "--max-parallel-builders", "1")
        self.commands = [
            shlex.join([sys.executable, "-B", "-m", "unittest", "test_greet.py"]),
            shlex.join([sys.executable, "-B", "greet.py", "Ada"]),
            shlex.join([sys.executable, "-B", "greet.py", "Grace"]),
            shlex.join([sys.executable, "-B", "-m", "unittest", "test_greet.py", "-v"]),
        ]
        provider = self.root / "bin/codex"
        self.offered_plan = self.root / "offered-batch-plan.json"
        script = provider.read_text().replace("import json\n", "import json\nimport base64\n", 1)
        script = script.replace(
            "PLANNED_CHECK = " + repr(self.command),
            "PLANNED_CHECK = " + repr(" and ".join("`" + command + "`" for command in self.commands)),
            1,
        )
        begin = script.index('    elif stage in ("sol", "astra_checkpoint"):')
        end = script.index('    elif stage in ("astra_review", "astra_plan", "astra_resolve"):', begin)
        script = (
            script[:begin]
            + """    elif stage in ("sol", "astra_checkpoint"):
        batch = data['ready_capture_batch']['command']
        checked = subprocess.run(batch, shell=True, executable='/bin/bash', capture_output=True, text=True)
        print(json.dumps({'type':'item.completed','item':{'id':'ready-batch',
            'type':'command_execution','command':batch,'exit_code':checked.returncode,
            'aggregated_output':checked.stdout + checked.stderr}}),flush=True)
        checks=[]
        for line in checked.stdout.splitlines():
            row=json.loads(line)
            if 'evidence_ref' in row:
                checks.append({**row,'command':shlex.join(row['command'])})
        refs=[row['evidence_ref'] for row in checks]
        report={**common,'verdict':'PASS','checks_run':[row['command'] for row in checks],
            'findings':[],'finding_dispositions':[],'unverified_criteria':[], 'checks':checks,
            'criterion_results':[{'id':'C1','status':'PASS','evidence_refs':refs}],
            'end_to_end_result':{'status':'PASS','summary':'All approved checks captured in one batch',
                'evidence_refs':refs,'technical_result':None,'pending_human_criteria':[]}}
"""
            + script[end:]
        )
        script = script.replace(
            "        batch = data['ready_capture_batch']['command']\n",
            "        batch = data['ready_capture_batch']['command']\n"
            + "        Path("
            + repr(str(self.offered_plan))
            + ").write_text(json.dumps("
            + "json.loads(base64.urlsafe_b64decode(shlex.split(batch)[-1]))))\n",
            1,
        )
        provider.write_text(script)

    def test_one_batch_keeps_four_independently_bound_checks_and_completes(self):
        run, view = self.run_to_validation()
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        offered = json.loads(self.offered_plan.read_text())
        self.assertEqual({"mode", "commands", "directory"}, set(offered))
        self.assertIn(offered["mode"], ("raw", "conservative"))
        self.assertEqual([shlex.split(command) for command in self.commands], offered["commands"])
        receipts = sorted((run.run_dir / "evidence").glob("ready-*.json"))
        self.assertEqual(4, len(receipts))
        records = [json.loads(path.read_text()) for path in receipts]
        self.assertEqual([shlex.split(command) for command in self.commands], [r["command"] for r in records])
        self.assertTrue(all(row["exit_code"] == 0 for row in records))
        launches = [json.loads(line) for line in self.probe.read_text().splitlines()]
        tester = next(row for row in launches if row["stage"] == "sol")
        manifest = json.loads(Path(tester["copy"]).read_text())
        expected = {"source_revision": manifest["source_revision"], "manifest_sha256": tester["copy_sha256"]}
        self.assertEqual([expected] * 4, [row["verification_copy"] for row in records])
        self.assertIsNotNone(view["evidence"]["check_replay"])
        batch_events = [
            json.loads(line)
            for path in (run.run_dir / "iterations").glob("*/*.jsonl")
            for line in path.read_text().splitlines()
            if json.loads(line).get("item", {}).get("id") == "ready-batch"
        ]
        self.assertEqual(1, len(batch_events))
