"""A saved qualified case reaches its independent writer through the named resume.

Planning uses the existing supervised fake fixture. The resume wrapper stops at
the next provider boundary: a Builder launch is the regression, and no paid model
or repeated test execution is needed to expose it.
"""

import copy
import json
import os
import select
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

import autocode_acceptance_tests as acceptance
import autocode_contract_identity as identity
import autocode_util as util
import psutil

from . import test_independent_evidence_repair as fixtures
from . import test_no_progress_recovery, test_subprocess


@unittest.skipUnless(sys.platform == "linux", "supervised fake providers require supported Linux")
class IndependentEvidenceRepairCLI(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    stop_before_builder = test_no_progress_recovery.NoProgressBoundTests.stop_before_builder
    new_run_engine_args = ("--engine", "codex")

    def qualified_run(self):
        state = self.stop_before_builder()
        state["settings"]["spec_first"] = True
        # A resumed qualified run already has its writer's saved route. The
        # historical planning fixture has never launched that stage yet.
        state["settings"]["roles"]["acceptance_tester"] = copy.deepcopy(state["settings"]["roles"]["tester"])
        contract = state["goal_contract"]
        contract["body"]["acceptance_criteria"][0]["verification_method"] = "test: test_c1_binding"
        contract["revision"] += 1
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract)}
        contract["approval_event"] = approval
        state["user_events"].append(approval)
        state["displayed_goal"] = identity.token(contract)
        cases = acceptance.due(state)
        self.assertTrue(cases)
        # This is an owned historical fixture, not an operator modification of a live run.
        review_root = self.root / "review-fixture"
        review_root.mkdir()
        fixture, _ = fixtures.IndependentEvidenceRepairTests().fixture(review_root)
        binding = {
            "task_id": state["current_task"]["id"],
            "contract_hash": state["goal_contract"]["hash"],
            "contract_revision": state["goal_contract"]["revision"],
        }
        for key in ("regression_proof", "validation", "regression_proofs"):
            state[key] = copy.deepcopy(fixture[key])
        state["validation"].update(binding)
        state["regression_proofs"][-1].update(binding)
        state["current_task"].update(binding)
        record = {**fixture["stages"][0], **binding}
        state["stages"].append(record)
        state["current_task"]["kind"] = "implement"
        state["current_task"]["acceptance_criteria"] = [case["id"] for case in cases]
        archive = self.root / "qualified-tests"
        archive.write_text("# Existing independently qualified tests\n")
        # Source-byte rejection is still enforced when a workspace is supplied.
        product = self.project / "src/bind.py"
        product.parent.mkdir()
        product.write_text("def bind():\n    return bound_report\n")
        digest = util.file_hash(product)
        gap = state["regression_proof"]["changed_code_evidence"]["obligations"][0]
        gap["sha256"] = digest
        state["validation"]["changed_code_decisions"][0]["sha256"] = digest
        state["regression_proofs"][-1]["assessment"] = copy.deepcopy(state["regression_proof"]["changed_code_evidence"])
        acceptance.record_receipt(
            state,
            cases,
            {
                "contract_hash": binding["contract_hash"],
                "case_ids": [case["id"] for case in cases],
                "output": str(self.root / "old-writer.json"),
                "files": {"tests/test_bind.py": {"archive": str(archive), "sha256": util.file_hash(archive)}},
            },
        )
        util.atomic_json(self.run / "state.json", state)
        return state

    def observe_resume(self, shown):
        wrapper = self.root / "observe_dispatch.py"
        marker = self.root / "dispatch.json"
        runtime = Path(__file__).resolve().parents[1]
        wrapper.write_text(
            f"import sys, json\nfrom pathlib import Path\nsys.path.insert(0, {str(runtime / 'tools')!r})\n"
            "import autocode\nfrom autocode_support import Paused\n"
            "def boundary(**kwargs):\n"
            f"    Path({str(marker)!r}).write_text(json.dumps({{'stage': kwargs['state']['next_stage']}}))\n"
            "    raise Paused('PAUSED_INTERVENTION', 'Fixture stopped at the provider boundary')\n"
            "autocode.run_role = boundary\nraise SystemExit(autocode.main())\n"
        )
        self.assertIn("--resume-paused", shown["next_command"])
        self.entry = [sys.executable, str(wrapper)]
        self.launch(shlex.split(shown["next_command"])[1:], 2)
        return json.loads(marker.read_text())

    def test_named_resume_assigns_the_independent_writer_without_another_builder_or_plan(self):
        before = copy.deepcopy(self.qualified_run())
        shown = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["view"]
        self.assertEqual({"stage": acceptance.STAGE}, self.observe_resume(shown))
        shown = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["view"]
        self.assertEqual(acceptance.STAGE, shown["next_stage"])
        self.assertTrue(
            any(row["headline"].startswith("Independent test strengthening") for row in shown["recoveries"]),
            shown["recoveries"],
        )
        _, after = self.saved()
        for key in ("goal_contract", "current_task", "settings", "iteration"):
            self.assertEqual(before[key], after[key], key)

    def test_stopping_an_older_queued_builder_keeps_its_independent_repair_on_public_resume(self):
        self.qualified_run()
        ready, gate = self.root / "builder-ready", self.root / "builder-gate"
        for path in (ready, gate):
            os.mkfifo(path)
        ready_fd = os.open(ready, os.O_RDWR | os.O_NONBLOCK)
        gate_fd = os.open(gate, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, ready_fd)
        self.addCleanup(os.close, gate_fd)
        provider = self.root / "fixture-bin/codex"
        source = provider.read_text()
        marker = 'stage = data["stage"]\n'
        self.assertEqual(1, source.count(marker))
        # Hold the actual fake provider before it can change source. Only the
        # priming controller omits the new redirect, as an older runtime did.
        provider.write_text(
            source.replace(
                marker,
                marker
                + 'if stage == "builder":\n'
                + f"    with Path({str(ready)!r}).open('w') as stream:\n"
                + "        stream.write(json.dumps({'pid': os.getpid()}) + '\\n')\n"
                + f"    with Path({str(gate)!r}).open('rb') as stream:\n"
                + "        stream.read(1)\n",
            )
        )
        runtime = Path(__file__).resolve().parents[1]
        legacy = self.root / "prime_older_dispatch.py"
        legacy.write_text(
            f"import sys\nsys.path.insert(0, {str(runtime / 'tools')!r})\n"
            "import autocode, autocode_acceptance_evidence_repair\n"
            "from unittest.mock import patch\n"
            "with patch.object(autocode_acceptance_evidence_repair, 'pending', return_value=None):\n"
            "    raise SystemExit(autocode.main())\n"
        )
        before = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)
        original = (self.project / "src/bind.py").read_bytes()
        command = shlex.split(before["view"]["next_command"])
        self.assertEqual("autocode", command[0])
        with (self.root / "legacy.stdout").open("w") as output, (self.root / "legacy.stderr").open("w") as errors:
            worker = subprocess.Popen(
                [sys.executable, str(legacy), *command[1:]],
                cwd=self.root,
                env=self.env,
                stdout=output,
                stderr=errors,
            )
            try:
                exited = os.pidfd_open(worker.pid)
                try:
                    readable, _, _ = select.select([ready_fd, exited], [], [], 150)
                finally:
                    os.close(exited)
                self.assertIn(
                    ready_fd,
                    readable,
                    "The primed older Builder exited or missed its provider barrier:\n"
                    + (self.root / "legacy.stderr").read_text()[-3500:]
                    + (self.root / "legacy.stdout").read_text()[-1000:],
                )
                entered = json.loads(os.read(ready_fd, 4096))
                child = psutil.Process(entered["pid"])
                running = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)
                self.assertEqual("builder", running["active_stage"]["stage"])
                self.assertEqual("source-one", running["view"]["evidence"]["validator_source_revision"])
                offer = running["view"]["pause"]["stop_now"]["next_command"]
                stop = ["stop-retained-review" if word == "REQUEST_ID" else word for word in offer]
                submitted = subprocess.run(
                    [*self.entry, *stop], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30
                )
                self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
                self.assertTrue(json.loads(submitted.stdout)["accepted"])
                self.assertEqual(2, worker.wait(timeout=60))
                self.assertTrue(not child.is_running() or child.status() == psutil.STATUS_ZOMBIE)
            finally:
                if worker.poll() is None:
                    worker.terminate()
                    worker.wait(timeout=20)
        stopped = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)
        self.assertEqual("PAUSED_INTERVENTION", stopped["view"]["status"])
        self.assertEqual(0, stopped["view"]["usage"]["accounting"]["active_attempts"])
        self.assertIsNone(stopped["view"]["evidence"]["validator_source_revision"])
        self.assertEqual(running["settings"], stopped["settings"])
        self.assertEqual(running["view"]["routes"], stopped["view"]["routes"])
        self.assertEqual(original, (self.project / "src/bind.py").read_bytes())
        _, retained = self.saved()
        self.assertNotIn("validation", retained, "Stopping the actual Builder retires its preceding review")
        archive = retained["validation_archive"]
        self.assertEqual(1, sum(row.get("validation", {}).get("verdict") == "FAIL" for row in archive))
        self.assertEqual({"stage": acceptance.STAGE}, self.observe_resume(stopped["view"]))
        after = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)
        self.assertEqual(acceptance.STAGE, after["next_stage"])
        self.assertEqual(stopped["view"]["approved_contract"], after["view"]["approved_contract"])
        self.assertIsNone(after["view"]["evidence"]["validator_source_revision"])
