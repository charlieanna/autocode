"""A saved qualified case reaches its independent writer through the named resume.

Planning uses the existing supervised fake fixture. The resume wrapper stops at
the next provider boundary: a Builder launch is the regression, and no paid model
or repeated test execution is needed to expose it.
"""

import copy
import json
import shlex
import sys
import unittest
from pathlib import Path

import autocode_acceptance_tests as acceptance
import autocode_contract_identity as identity
import autocode_util as util

from . import test_independent_evidence_repair as fixtures
from . import test_no_progress_recovery, test_subprocess


class IndependentEvidenceRepairCLI(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    stop_before_builder = test_no_progress_recovery.NoProgressBoundTests.stop_before_builder
    new_run_engine_args = ("--engine", "codex")

    def test_named_resume_assigns_the_independent_writer_without_another_builder_or_plan(self):
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
        record = {**fixture["stages"][0], **binding}
        state["stages"].append(record)
        state["current_task"]["kind"] = "implement"
        state["current_task"]["acceptance_criteria"] = [case["id"] for case in cases]
        state["current_task"]["affected_paths"] = []
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
        before = copy.deepcopy(state)
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
        shown = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["view"]
        self.assertIn("--resume-paused", shown["next_command"])
        self.entry = [sys.executable, str(wrapper)]
        self.launch(shlex.split(shown["next_command"])[1:], 2)
        self.assertEqual({"stage": acceptance.STAGE}, json.loads(marker.read_text()))
        shown = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["view"]
        self.assertEqual(acceptance.STAGE, shown["next_stage"])
        self.assertTrue(
            any(row["headline"].startswith("Independent test strengthening") for row in shown["recoveries"]),
            shown["recoveries"],
        )
        _, after = self.saved()
        for key in ("goal_contract", "current_task", "settings", "iteration"):
            self.assertEqual(before[key], after[key], key)
