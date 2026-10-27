"""Automatic members know where real joint proof belongs, without gaining authority."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_changeset_store as store
import autocode_coordination_context as coordination
import autocode_coordination_store as coordination_store
import autocode_report_repair_context as repair
import autocode_stage_context as runtime
import autocode_trace_coverage as coverage
from units import autoplanner


class CoordinationContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "catalog")})
        environment.start()
        self.addCleanup(environment.stop)
        self.workspace = self.root / "checkout"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--allow-empty",
                "-qm",
                "baseline",
            ],
            cwd=self.workspace,
            check=True,
        )
        self.run = self.workspace / ".autocode/runs/fix"
        self.run.mkdir(parents=True)
        self.state = {
            "task": "Fix the checkout total across repositories",
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "settings": {"joint_planning": True, "spec_first": True, "roles": {}},
            "iteration": 1,
            "stages": [],
            "acceptance_criteria": [],
        }
        self.record = store.reserve(
            source_workspace=self.workspace,
            source_run_dir=self.run,
            target="history",
            workspace=self.root / "history",
            brief="Fix receipt serialization",
        )
        store.bind(self.record["id"], "history", self.root / "history/.autocode/runs/fix")

    def automatic(self):
        store.set_fields(self.record["id"], {"automatic_anchor": str(self.root / "anchor")})

    def test_manual_sets_and_unbound_or_wrong_workspace_runs_get_no_orchestration_claim(self):
        self.assertIsNone(coordination.context(self.state))
        self.automatic()
        for replacement in ({"run_dir": str(self.root / "unrelated")}, {"workspace": str(self.root / "wrong")}):
            with self.subTest(replacement=replacement):
                self.assertIsNone(coordination.context({**self.state, **replacement}))

    def test_leaf_and_source_members_share_joint_proof_and_publication_responsibilities(self):
        self.automatic()
        leaf = {
            **self.state,
            "workspace": str(self.root / "history"),
            "run_dir": str(self.root / "history/.autocode/runs/fix"),
        }
        for state in (self.state, leaf):
            with self.subTest(workspace=state["workspace"]):
                context = coordination.context(state)
                self.assertEqual(["checkout", "history"], context["members"])
                self.assertIn("SEPARATE isolated integration workspace", context["instruction"])
                self.assertIn("fail-before/pass-after", context["instruction"])
                self.assertIn("never discharges the real joint obligation", context["instruction"])
                self.assertIn("not exclusions or proxy acceptance", context["instruction"])
                self.assertIn("only after ALL member gates and independent joint proof pass", context["instruction"])

    def test_discovery_hint_requires_the_same_authenticated_member_identity(self):
        self.automatic()
        leaf = {
            **self.state,
            "workspace": str(self.root / "history"),
            "run_dir": str(self.root / "history/.autocode/runs/fix"),
        }
        hint = "Apply tax here unless Q1 assigns it to checkout."
        document = {
            "version": 1,
            "anchor": str(self.root / "anchor"),
            "members": [
                {"target": "history", "workspace": leaf["workspace"], "run_dir": leaf["run_dir"], "reported_work": hint}
            ],
        }
        coordination_store.save(document)
        context = coordination.context(leaf)
        self.assertEqual(hint, context["reported_work"])
        self.assertIn("not a user requirement or an answered question", context["instruction"])
        self.assertIn("Never copy reported_work into a requirement source_quote", context["instruction"])
        self.assertNotIn("reported_work", coordination.context(self.state))
        for field in ("target", "workspace", "run_dir"):
            with self.subTest(field=field):
                value = document["members"][0][field]
                document["members"][0][field] = str(self.root / "unrelated")
                coordination_store.save(document)
                self.assertNotIn("reported_work", coordination.context(leaf))
                document["members"][0][field] = value

    def test_requirements_planning_runtime_and_report_repair_receive_the_same_bound_context(self):
        self.automatic()
        expected = coordination.context(self.state)
        for stage in ("requirements_gather", "astra_discovery", "astra_challenge"):
            with self.subTest(stage=stage):
                prompt, _ = autoplanner.context(self.state, stage, self.run / "state.json")
                packets = [json.loads(line) for line in prompt.splitlines() if line.startswith('{"task":')]
                self.assertEqual(expected, packets[-1]["coordinated_member"])
        prompt, _ = runtime.context_packet(self.state, "terra", self.run / "state.json")
        packets = [json.loads(line) for line in prompt.splitlines() if line.startswith('{"task":')]
        self.assertEqual(expected, packets[-1]["coordinated_member"])
        repaired = repair.clarification_context(self.state, "requirements_gather")
        self.assertEqual(expected, repaired["coordinated_member"])
        self.assertIsNone(repaired["protected_contract"], "Context does not manufacture an approved goal")

    def test_only_bound_members_distinguish_real_joint_proof_from_a_local_proxy(self):
        for automatic in (False, True):
            if automatic:
                self.automatic()
            with self.subTest(automatic=automatic):
                prompt, _ = autoplanner.context(self.state, "astra_challenge", self.run / "state.json")
                self.assertIn("OUT-OF-CHECKOUT PROXY", prompt)
                self.assertEqual(automatic, "REAL JOINT PROOF IS NOT A LOCAL PROXY" in prompt)
                repaired = repair.clarification_context(self.state, "astra_finalize")
                if automatic:
                    self.assertIn(
                        "REAL JOINT PROOF IS NOT A LOCAL PROXY", repaired["coordinated_member"]["instruction"]
                    )

        requirement = {
            "id": "R1",
            "text": "The real checkout must show `$110.00`.",
            "source_quote": "The real checkout must show `$110.00`.",
        }
        joint = "The coordinator's separate real-code joint proof must establish `$110.00`; local evidence cannot discharge it."
        contract = {
            "constraints": ["The receipt view is outside this checkout."],
            "required_behaviors": [joint],
            "acceptance_criteria": [{"id": "AC1", "criterion": "The receipt amount is numeric 110."}],
        }
        # No validator exemption: a local stand-in still raises the ordinary gap.
        local = [{"requirement_id": "R1", "disposition": "covered", "evidence": "AC1"}]
        self.assertEqual(["R1"], [row["requirement_id"] for row in coverage.proxy_gaps([requirement], local, contract)])
        retained = [{"requirement_id": "R1", "disposition": "covered", "evidence": joint}]
        self.assertEqual([], coverage.proxy_gaps([requirement], retained, contract))


if __name__ == "__main__":
    unittest.main()
