"""Independent attribution, immutable attempts and discriminating regression pins."""

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_arena as arena
import autocode_arena_policy as policy
import autocode_failure_attribution as attribution
from autocode_arena_store import ArenaError, Store, write_json


class AttributionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name) / "arena")
        self.store.initialize()
        for ident, split in (("development", "development"), ("holdout", "holdout")):
            self.store.add(
                {"id": ident, "split": split, "repository": ident, "base_commit": "a" * 40, "issue_sha256": "a" * 64}
            )
            row = {
                "id": ident,
                "case_id": ident,
                "cohort": "baseline",
                "verdict": "FALSE_COMPLETE",
                "checks": [{"name": "greeting", "ok": False}],
                "error": None,
            }
            self.store.insert(row)
            root = self.store.root / "attempts" / ident
            write_json(root / "result.json", row)
            (root / "handoff.txt").write_text("The task requires a comma; the delivered greeting omits it.\n")
        self.root = self.store.root / "attempts/development"
        self.before = self.regression("before", "Hello World\n")
        self.after = self.regression("after", "Hello, World\n")
        self.report = {
            "evidence_refs": [{"handoff": "task-spec-to-evidence", **self.pin("handoff.txt")}],
            "suspected_cause": "The implementation may have omitted the required comma.",
            "confirmed_diagnosis": "The delivered greeting omitted the required comma.",
            "regression": {"check": "greeting", "before": self.before, "after": self.after},
        }

    def pin(self, name):
        return {"path": name, "sha256": hashlib.sha256((self.root / name).read_bytes()).hexdigest()}

    def regression(self, name, greeting):
        directory = self.root / name
        directory.mkdir()
        source = "GREETING = " + repr(greeting) + "\n"
        (directory / "app.py").write_text(source)
        (directory / "test_driver.py").write_text(
            "import json\nfrom app import GREETING\n"
            "print(json.dumps({'checks': [{'name': 'greeting', 'ok': GREETING == 'Hello, World\\n'}]}))\n"
        )
        executed = subprocess.run(
            [sys.executable, "-B", "test_driver.py"], cwd=directory, capture_output=True, text=True, check=True
        )
        value = json.loads(executed.stdout)
        value.update(command="python -B test_driver.py", source_sha256=hashlib.sha256(source.encode()).hexdigest())
        write_json(self.root / (name + ".json"), value)
        return self.pin(name + ".json")

    def test_legacy_and_producer_claims_have_no_confirmation_authority(self):
        self.assertEqual(attribution.unknown(), self.store.rows()[0]["failure_attribution"])
        with self.assertRaisesRegex(ValueError, "explicit reviewer"):
            attribution.validate({**self.report, "confirmed_by": "AutoCode"}, self.root)
        suggestion = {"suspected_cause": "Requirements or implementation may have drifted."}
        record = self.store.annotate("development", suggestion)
        self.assertIsNone(record["failure_attribution"]["confirmed_diagnosis"])
        self.assertEqual(
            "UNDETERMINED", arena.proposal(self.store, "baseline")["development_failures"][0]["root_cause"]
        )

    def test_confirmed_investigation_is_appended_without_changing_outcomes_or_result(self):
        original = (self.root / "result.json").read_bytes()
        summary = policy.summarize(self.store.rows())
        first = self.store.annotate("development", {"suspected_cause": "A format mismatch."})
        second = self.store.annotate("development", self.report, reviewer="independent reviewer")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["attempt_sha256"], second["attempt_sha256"])
        self.assertEqual(original, (self.root / "result.json").read_bytes())
        self.assertEqual(summary, policy.summarize(self.store.rows()))
        proposal = arena.proposal(self.store, "baseline")
        self.assertEqual(["development"], [row["case"] for row in proposal["development_failures"]])
        result = proposal["development_failures"][0]
        self.assertEqual("FALSE_COMPLETE", result["outcome"])
        self.assertEqual(self.report["confirmed_diagnosis"], result["root_cause"])
        self.assertEqual("independent reviewer", result["failure_attribution"]["confirmed_by"])
        self.assertFalse(proposal["promoted"])

    def test_tampered_handoff_or_regression_invalidates_confirmation_but_not_delivery(self):
        for name in ("handoff.txt", "before.json", "after.json"):
            with self.subTest(name=name):
                self.store.annotate("development", self.report, reviewer="independent reviewer")
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b"tampered")
                row = self.store.rows()[0]
                self.assertEqual("FALSE_COMPLETE", row["verdict"])
                self.assertEqual(attribution.unknown(), row["failure_attribution"])
                self.assertIn("evidence changed", row["attribution_error"])
                path.write_bytes(original)

    def test_confirmation_needs_actual_named_fail_then_pass_against_distinct_sources(self):
        path = self.root / "after.json"
        original = json.loads(path.read_text())
        mutations = (
            {**original, "checks": [{"name": "greeting", "ok": False}]},
            {**original, "checks": [{"name": "different", "ok": True}]},
            {**original, "checks": [{"name": "greeting", "ok": 1}]},
            {**original, "checks": [*original["checks"], *original["checks"]]},
            {**original, "error": "collection failed"},
            {**original, "command": "a different command"},
            {**original, "source_sha256": json.loads((self.root / "before.json").read_text())["source_sha256"]},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                write_json(path, mutation)
                document = {**self.report, "regression": {**self.report["regression"], "after": self.pin("after.json")}}
                with self.assertRaises(ValueError):
                    self.store.annotate("development", document, reviewer="independent reviewer")

    def test_missing_escaping_and_reused_report_references_are_refused(self):
        for path in ("../outside.json", str(self.root / "handoff.txt"), "missing.txt"):
            with self.subTest(path=path):
                document = {**self.report, "evidence_refs": [{**self.report["evidence_refs"][0], "path": path}]}
                with self.assertRaises(ValueError):
                    self.store.annotate("development", document, reviewer="independent reviewer")
        document = {**self.report, "regression": {**self.report["regression"], "after": self.before}}
        with self.assertRaisesRegex(ValueError, "distinct"):
            self.store.annotate("development", document, reviewer="independent reviewer")
        self.store.insert({"id": "running", "verdict": "RUNNING", "cohort": "baseline"})
        with self.assertRaisesRegex(ArenaError, "finished attempt"):
            self.store.annotate("running", {})

    def test_public_cli_requires_review_and_keeps_holdouts_out_of_proposals(self):
        report = self.root / "investigation.json"
        write_json(report, self.report)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = arena.main(
                [
                    "--arena",
                    str(self.store.root),
                    "annotate",
                    "development",
                    "--report",
                    str(report),
                    "--reviewer",
                    "reviewer",
                ]
            )
        self.assertEqual(0, result)
        self.assertEqual("reviewer", json.loads(out.getvalue())["failure_attribution"]["confirmed_by"])
        # Holdout metadata never appears in a development proposal, even after investigation.
        holdout = self.store.root / "attempts/holdout"
        for name in ("before.json", "after.json"):
            (holdout / name).write_bytes((self.root / name).read_bytes())
        self.store.annotate("holdout", self.report, reviewer="reviewer")
        serialized = json.dumps(arena.proposal(self.store, "baseline"))
        self.assertNotIn('"case": "holdout"', serialized)


if __name__ == "__main__":
    unittest.main()
