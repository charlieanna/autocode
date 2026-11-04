"""Saved plan references bind to the replay's exact current contract."""

import json
import tempfile
import unittest
from pathlib import Path

import autocode_replay_bindings as bindings
import autocode_replay_recording as recording
import autocode_replay_transport as transport
import autocode_replay_view as view

from tests.test_replay_recording import make_export_projection


class RetainedPlanReplayTests(unittest.TestCase):
    def test_public_replay_projection_preserves_the_original_contract_revision(self):
        projected = view.project(
            {"stages": [{"events": "/run/review.jsonl", "contract_revision": 2, "contract_hash": "a" * 64}]}
        )
        self.assertEqual(2, projected["records"][0].get("contract_revision"))

    def test_only_exact_plan_reference_fields_are_bound(self):
        token = "r2:" + "a" * 64
        payload = {
            "report": {
                "contract": {"retain_contract": token, "acceptance_criteria": {"retain_from": token}},
                "summary": token,
                "other": {"retain_contract": token},
            }
        }
        found = bindings.bindings(payload, {"contract_revision": 2}, {"contract_hash": "a" * 64}, "/work", "/run", [])
        self.assertEqual(
            [
                {"pointer": "/report/contract/retain_contract", "symbol": "retained_contract", "original_valid": True},
                {
                    "pointer": "/report/contract/acceptance_criteria/retain_from",
                    "symbol": "retained_contract",
                    "original_valid": True,
                },
            ],
            found,
        )
        for field in ("retain_contract", "revise_contract"):
            bad = {"report": {"contract": {field: "r1:" + "a" * 64}}}
            self.assertEqual(
                [], bindings.bindings(bad, {"contract_revision": 2}, {"contract_hash": "a" * 64}, "/work", "/run", [])
            )
        revised = {"report": {"contract": {"revise_contract": token}}}
        self.assertEqual(
            "/report/contract/revise_contract",
            bindings.bindings(revised, {"contract_revision": 2}, {"contract_hash": "a" * 64}, "/work", "/run", [])[0][
                "pointer"
            ],
        )

    def test_schema_provides_current_reference_before_prompt_is_written(self):
        token = "r2:" + "b" * 64
        delta = {"type": "object", "properties": {"retain_from": {"type": "string", "enum": [token]}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schema.json"
            for body in (
                {"properties": {"acceptance_criteria": delta}},
                {"anyOf": [{"properties": {"revise_contract": {"enum": [token]}}}]},
            ):
                path.write_text(json.dumps({"properties": {"contract": body}}))
                self.assertEqual(token, transport.retained_contract_symbol(path, "b" * 64))
                with self.assertRaises(recording.RecordingError):
                    transport.retained_contract_symbol(path, "c" * 64)
            path.write_text("{}")
            with self.assertRaises(recording.RecordingError):
                transport.retained_contract_symbol(path, "b" * 64)

    def test_export_and_rebind_preserve_the_plan_reference_without_authorizing_a_stale_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            projection = make_export_projection(root)
            record = projection["records"][0]
            record.update(contract_revision=2, contract_hash="a" * 64)
            Path(record["output"]).write_text(json.dumps({"contract": {"retain_contract": "r2:" + "a" * 64}}))
            tape = recording.load(recording.export(projection, root / "export"))
            row = tape.document["dispatches"][0]
            payload = {"report": json.loads(tape.artifact(row["report"]))}
            self.assertEqual(
                "r3:" + "b" * 64,
                recording.rebind(payload, row["bindings"], {"retained_contract": "r3:" + "b" * 64})["report"][
                    "contract"
                ]["retain_contract"],
            )


if __name__ == "__main__":
    unittest.main()
