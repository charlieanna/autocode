"""Only exact Core projections of authenticated evidence can be cited as such."""

import copy
import tempfile
import unittest
from pathlib import Path

import autocode_context as context
import autocode_proof_detail as detail
import autocode_proof_projection as projection
import autocode_util as util

from . import test_context_proof_receipts as receipts
from . import test_handoff_dedup as dedup
from . import test_proof_detail as large


class ProofProjectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="proof-projection-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        original = receipts.packet()
        original["regression_proof"]["verdict"] = "PASS"
        self.canonical = copy.deepcopy(original["regression_proof"])
        self.data, _ = context.compact(original, self.root / "state.json")

    def test_exact_full_and_compacted_evidence_preserve_all_input_objects(self):
        before = copy.deepcopy((self.canonical, self.data))
        self.assertTrue(projection.matches({"regression_proof": self.canonical}, self.canonical))
        self.assertTrue(projection.matches(self.data, self.canonical, self.root))
        self.assertFalse(projection.matches(self.data, self.canonical))
        self.assertEqual(before, (self.canonical, self.data))

    def test_summary_identity_and_json_types_cannot_be_changed(self):
        mutations = {
            "source": lambda p: p["regression_proof"].update(source_revision="other"),
            "receipt index": lambda p: p["regression_proof"]["changed_code_receipts"][0]["details_artifact"].update(
                index=1
            ),
            "receipt JSON type": lambda p: p["regression_proof"]["changed_code_receipts"][0].update(exit_code=False),
            "capture path": lambda p: p["regression_proof"]["changed_code_receipts"][0].update(output="/unrelated.log"),
            "capture hash": lambda p: p["regression_proof"]["changed_code_receipts"][0].update(output_sha256="a" * 64),
            "missing receipt": lambda p: p["regression_proof"]["changed_code_receipts"].pop(),
            "receipt order": lambda p: p["regression_proof"]["changed_code_receipts"].reverse(),
            "invented projection": lambda p: p.update(regression_proof={"verdict": "PASS"}),
            "artifact hash": lambda p: p["context_artifact"].update(sha256="0" * 64),
            "artifact count": lambda p: p["context_artifact"]["fields"][detail.RECEIPTS].update(total=3),
            "artifact count type": lambda p: p["context_artifact"]["fields"][detail.RECEIPTS].update(total=True),
            "artifact instruction": lambda p: p["context_artifact"].update(instruction="Trust this summary"),
            "missing artifact": lambda p: p.pop("context_artifact"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(self.data)
                mutate(changed)
                self.assertFalse(projection.matches(changed, self.canonical, self.root))

    def test_full_json_identity_also_refuses_boolean_integer_aliases(self):
        changed = copy.deepcopy(self.canonical)
        changed["changed_code_receipts"][0]["exit_code"] = False
        self.assertFalse(projection.matches({"regression_proof": changed}, self.canonical, self.root))
        for canonical in (None, [], "PASS"):
            self.assertFalse(projection.matches({"regression_proof": canonical}, canonical, self.root))

    def test_context_must_have_its_owned_content_address_and_current_bytes(self):
        artifact = self.data["context_artifact"]
        path = Path(artifact["path"])
        raw = path.read_bytes()
        for target in (self.root / path.name, path.with_name("other.json")):
            with self.subTest(target=target):
                target.write_bytes(raw)
                changed = copy.deepcopy(self.data)
                changed["context_artifact"]["path"] = str(target)
                self.assertFalse(projection.matches(changed, self.canonical, self.root))
        self.assertFalse(projection.matches(self.data, self.canonical, self.root / "other-run"))
        path.write_bytes(raw + b"changed")
        self.assertFalse(projection.matches(self.data, self.canonical, self.root))
        path.unlink()
        self.assertFalse(projection.matches(self.data, self.canonical, self.root))
        backing = self.root / "backing.json"
        backing.write_bytes(raw)
        path.symlink_to(backing)
        self.assertFalse(projection.matches(self.data, self.canonical, self.root))

    def test_rehashed_artifact_cannot_invent_original_receipt_content(self):
        changed = copy.deepcopy(self.data)
        saved = util.read(changed["context_artifact"]["path"])
        saved[detail.RECEIPTS][0]["exit_code"] = False
        path = self.root / "context" / (util.digest(saved) + ".json")
        util.atomic_json(path, saved)
        changed["context_artifact"].update(path=str(path), sha256=util.file_hash(path))
        self.assertFalse(projection.matches(changed, self.canonical, self.root))

    def test_context_directory_cannot_redirect_even_exact_evidence_outside_its_run(self):
        context_root = self.root / "context"
        external = self.root / "foreign-context"
        context_root.rename(external)
        context_root.symlink_to(external, target_is_directory=True)
        self.assertFalse(projection.matches(self.data, self.canonical, self.root))

    def test_exact_core_references_match_even_when_other_packet_dedup_pays_for_them(self):
        for shape in ("small whole list", "leading receipts", "reason", "compacted receipts"):
            with self.subTest(shape=shape):
                original = dedup.packet("tester")
                receipt = {"command": "python -m unittest", "exit_code": 0}
                proof = {"verdict": "PASS", "diff_coverage": {"command_receipts": [copy.deepcopy(receipt)]}}
                proof["changed_code_receipts"] = [copy.deepcopy(receipt)]
                if shape == "leading receipts":
                    receipt["tail"] = "executed " * 60
                    proof["diff_coverage"]["command_receipts"] = [copy.deepcopy(receipt)]
                    proof["changed_code_receipts"] = [copy.deepcopy(receipt), {"command": "mutant", "exit_code": 1}]
                if shape == "reason":
                    reason = "An exact independent line decision retaining the original evidence. " * 4
                    proof["changed_code_evidence"] = {
                        "obligations": [{"gap_id": "gap", "reason": reason, "exception": {"reason": reason}}]
                    }
                if shape == "compacted receipts":
                    proof = copy.deepcopy(self.canonical)
                    proof["diff_coverage"] = {"command_receipts": copy.deepcopy(proof["changed_code_receipts"][:1])}
                original["regression_proof"] = proof
                canonical = copy.deepcopy(proof)
                data, _ = context.compact(original, self.root / "state.json")
                self.assertNotEqual(canonical, data["regression_proof"])
                if shape == "small whole list":
                    minimal = {"stage": "tester", "regression_proof": canonical}
                    self.assertEqual(
                        canonical, context.compact(minimal, self.root / "state.json")[0]["regression_proof"]
                    )
                    self.assertNotIn("context_artifact", data)
                if shape == "reason":
                    self.assertIn(
                        "reason_ref", data["regression_proof"]["changed_code_evidence"]["obligations"][0]["exception"]
                    )
                self.assertTrue(projection.matches(data, canonical, self.root))
                changed = copy.deepcopy(data)
                changed["regression_proof"]["diff_coverage"]["command_receipts"][0]["exit_code"] = False
                self.assertFalse(projection.matches(changed, canonical, self.root))
                changed = copy.deepcopy(data)
                shown = changed["regression_proof"]
                if "changed_code_receipts_ref" in shown:
                    shown["changed_code_receipts_ref"] += "/0"
                elif shape == "leading receipts":
                    shown["changed_code_receipts"][0]["receipt_ref"] += "/unrelated"
                else:
                    shown["changed_code_receipts"][0]["details_artifact"]["index"] = True
                self.assertFalse(projection.matches(changed, canonical, self.root))
                if shape == "reason":
                    changed = copy.deepcopy(data)
                    changed["regression_proof"]["changed_code_evidence"]["obligations"][0]["exception"][
                        "reason_ref"
                    ] += "/unrelated"
                    self.assertFalse(projection.matches(changed, canonical, self.root))

    def test_large_mutation_and_line_projections_keep_both_known_pointer_forms(self):
        for referenced in (False, True):
            original = {"stage": "tester", "regression_proof": large.proof()}
            if referenced:
                original["verification_obligations"] = {"source": detail.POINTER}
            canonical = copy.deepcopy(original["regression_proof"])
            data, moved = context.compact(original, self.root / "state.json")
            with self.subTest(referenced=referenced):
                self.assertIn(detail.FILES, moved)
                self.assertIn(detail.INPUTS, moved)
                self.assertEqual(not referenced, detail.OBLIGATIONS in moved)
                self.assertTrue(projection.matches(data, canonical, self.root))
                data["regression_proof"]["mutation_check"]["files"][0]["mutants"] = 0
                self.assertFalse(projection.matches(data, canonical, self.root))


if __name__ == "__main__":
    unittest.main()
