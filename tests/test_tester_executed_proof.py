"""Executed runner evidence is distinct from the independent line review it feeds."""

import json
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_provider_launch as provider_launch
import autocode_regression as regression
import autocode_replay_transport as transport
import autocode_role_launch as role_launch
import autocode_source_scope as source_scope
import autocode_util as util

from . import test_context_proof_receipts as receipts
from . import test_provider_launch_containment as requests
from . import test_replay_transport as replay


class TesterExecutedProofRequestTests(unittest.TestCase):
    setUp = requests.TesterProofRequest.setUp
    request = requests.TesterProofRequest.request

    def test_pending_independent_line_review_does_not_deny_executed_regression_evidence(self):
        self.project.write(
            {
                "components/gateway/server.py": "def health():\n    marker = None\n    return 'ok' if marker is None else 'bad'\n"
            }
        )
        regression.before_review(self.state, "tester", self.project.root, self.run)
        revision = source_scope.snapshot(self.project.root, self.state)["revision"]
        proof = self.state["regression_proof"]
        self.assertEqual("PASS", proof["verdict"])
        self.assertEqual("UNVERIFIED", proof["changed_code_evidence"]["status"])
        self.assertTrue(regression.complete(self.state, revision, require_changed_code=False))
        self.assertFalse(regression.complete(self.state, revision))
        before = deepcopy(proof)
        instruction, handoff = self.request()
        self.assertIs(True, handoff["runner_regression_proof_current"])
        self.assertIn("The runner authenticated the current regression PASS", instruction)
        self.assertNotIn("No current runner-authenticated regression PASS", instruction)
        self.assertEqual(before, self.state["regression_proof"])
        self.assertFalse(regression.complete(self.state, revision), "A prompt hint grants no independent line decision")
        gaps = [row for row in proof["changed_code_evidence"]["obligations"] if row["status"] == "reason_required"]
        self.assertEqual(["marker = None"], [row["source_line"] for row in gaps])
        review = self.run / "independent-tester-reason.json"
        reason = (
            "The None initializer has no supported value mutation. The candidate health test observes ok; "
            "removing the binding raises NameError in that same tested path."
        )
        util.atomic_json(review, {"reason": reason})
        evidence = [str(self.project.root / gaps[0]["path"]), str(review)]
        self.state["validation"] = {
            "source_revision": revision,
            "reviewer_role": "tester",
            "changed_code_decisions": [
                {
                    "source_revision": revision,
                    "proof_context": proof["changed_code_evidence"]["proof_context"],
                    **{key: row[key] for key in ("gap_id", "path", "sha256", "line")},
                    "accepted": True,
                    "reason": reason,
                    "evidence": evidence,
                    "reviewer": {"stage": "tester", "role": "tester", "output": str(review)},
                }
                for row in gaps
            ],
            "evidence_hashes": {path: util.file_hash(path) for path in evidence},
        }
        self.assertTrue(
            regression.complete(self.state, revision), "Only the pinned independent decision settles the gap"
        )
        self.state["validation"]["reviewer_role"] = "builder"
        self.assertFalse(
            regression.complete(self.state, revision), "The Builder cannot supply that independent decision"
        )

    def test_actual_request_authenticates_the_core_compacted_receipt_projection(self):
        # This isolates serialization from proof execution: the companion test uses real captures
        # and the real full gate; here the caller's authenticated handoff is controlled explicitly.
        canonical = receipts.packet()["regression_proof"]
        canonical["verdict"] = "PASS"
        before = deepcopy(canonical)
        with (
            patch.object(regression, "handoff", return_value=canonical),
            patch.object(regression, "complete", return_value=True),
        ):
            instruction, handoff = self.request()
        self.assertNotEqual(canonical["changed_code_receipts"], handoff["regression_proof"]["changed_code_receipts"])
        self.assertEqual(4, len(handoff["regression_proof"]["changed_code_receipts"]))
        self.assertIn("regression_proof_receipts", handoff["context_artifact"]["fields"])
        self.assertIs(True, handoff["runner_regression_proof_current"])
        self.assertIn("The runner authenticated the current regression PASS", instruction)
        self.assertEqual(before, canonical)


class ReplayHintBoundaryTests(unittest.TestCase):
    setUp = replay.OwnedTransportTests.setUp
    tape = replay.OwnedTransportTests.tape

    def test_actual_replay_worker_never_consults_or_advertises_current_executed_proof(self):
        # Retain a read-only Tester dispatch; prepare its emitter but never launch it.
        record = self.projection["records"][0]
        record["after_ref"] = record["before_ref"]
        (Path(self.projection["workspace"]) / "greet.py").write_text("print('before')\n")
        self.projection["settings"]["roles"] = {"tester": {"model": "fixture-model"}}
        tape = self.tape(
            updates={"stage": "tester", "role": "tester", "route_role": "tester", "original_stage": "tester"}
        )
        row = tape.document["dispatches"][0]
        settings = {
            "replay_recording": {
                "path": str(tape.manifest_path),
                "sha256": tape.sha256,
                "checks": "recorded",
                "dispatch_ids": [row["id"]],
            }
        }
        replay_context = {
            key: row[key] for key in (*transport.CONTEXT_KEYS, "task_id", "contract_hash", "criteria_revision")
        }
        replay_context["consumed"] = 0
        adapter = SimpleNamespace(prompt_for_schema=lambda prompt, *_: prompt)
        *_, worker = provider_launch.prepare(
            engine=row["engine"],
            adapter=adapter,
            role="tester",
            route_role="tester",
            workspace=self.workspace,
            run_dir=self.run_dir,
            session=None,
            model=row["model"],
            effort=row["reasoning_effort"],
            allow_write=False,
            planning=False,
            report=self.report,
            schema=self.schema,
            prompt_file=self.prompt,
            sandbox="read-only",
            transport_args=[],
            chatgpt=False,
            provider=row["provider"],
            settings=settings,
            replay_context=replay_context,
        )
        self.assertIn("replay", worker)
        self.assertNotIn("tool_containment", worker)
        prompt = "CURRENT HANDOFF DATA\n" + json.dumps({"regression_proof": {"verdict": "PASS", "simulated": True}})
        with patch.object(regression, "complete", side_effect=AssertionError("Replay cannot request current proof")):
            rendered = role_launch.decorate_prompt(
                {"settings": settings},
                "tester",
                self.workspace,
                adapter,
                prompt,
                {},
                self.events,
                worker,
                run_dir=self.run_dir,
            )
        self.assertEqual(prompt, rendered)
        self.assertFalse(self.events.exists())
        self.assertFalse(self.report.exists())


if __name__ == "__main__":
    unittest.main()
