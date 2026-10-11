"""Canonical reports preserve missing evidence and reject modified delivery records."""

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_evidence_aggregate as aggregate
import autocode_evidence_document as document
import autocode_evidence_export as export
import autocode_evidence_provenance as provenance
import autocode_interaction_timing as interaction_timing
import autocode_run_view as run_view
import autocode_taskrun as taskrun
import autocode_util as util

from scenarios.harness import evidence_reports
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


def public_view():
    return {
        "status": "TASK_COMPLETE",
        "workflow": "build",
        "turn": 1,
        "evidence": {
            "outcome": "Deliver x|y",
            "base_commit": "base",
            "validator_source_revision": "a" * 64,
            "acceptance": [
                {
                    "id": "C1",
                    "criterion": "Deliver x|y",
                    "status": "passed",
                    "evidence": "check.txt",
                    "validator_status": "PASS",
                    "human_reviewed": False,
                }
            ],
            "check_replay": {
                "checks": [
                    {
                        "command": "python check.py",
                        "exit_code": 0,
                        "timed_out": False,
                        "duration_seconds": 1.25,
                        "output": "/owned/check.txt",
                        "output_sha256": "b" * 64,
                        "tail": "a secret raw tail must not be exported",
                    }
                ]
            },
        },
        "verification": {
            "contract_token": "r1:plan",
            "criteria_revision": "criteria",
            "task_id": "task",
            "coverage": [
                {
                    "id": "C1",
                    "verification_method": "python check.py",
                    "human_review": False,
                    "evidence_refs": ["/owned/check.txt"],
                }
            ],
        },
        "usage": {
            "accounting": {
                "attempts": [
                    {
                        "identity": "attempt",
                        "stage": "terra",
                        "model": "model",
                        "engine": "custom",
                        "duration_seconds": 1.5,
                        "runner_owned": False,
                        "tokens": {},
                        "requests": [{"authorization": "must not be exported"}],
                        "command": ["--token", "secret"],
                    }
                ],
                "tokens": {},
                "provider_requests": {"value": None, "complete": False},
                "cost": {"reported_usd": {"value": None, "complete": False}},
                "issues": ["unknown"],
            }
        },
    }


def value(view=None):
    return document.build(
        view or public_view(),
        run_identity="run",
        completed_at="2026-10-08T00:00:00+00:00",
        provenance=provenance.configured({}, "fake"),
        binding="binding",
    )


class EvidenceDocumentTests(unittest.TestCase):
    def test_accounting_binding_uses_all_rendered_facts_and_gap_conditions(self):
        view = public_view()
        accounting = view["usage"]["accounting"]
        # An empty stage uses the public fallback role; known stages use roles.py.
        accounting["attempts"][0].update(stage="", role="External verifier")
        original = copy.deepcopy(accounting)
        facts = document.accounting_facts(accounting)
        report = value(view)
        self.assertEqual(report["attempts"], facts["attempts"])
        self.assertEqual(report["usage"], facts["usage"])
        self.assertEqual("External verifier", facts["attempts"][0]["role"])
        bound = export.binding({}, accounting)
        for key in facts["attempts"][0]:
            changed = copy.deepcopy(accounting)
            changed["attempts"][0][key] = {"changed": True} if key == "tokens" else "changed"
            with self.subTest(attempt_fact=key):
                self.assertNotEqual(bound, export.binding({}, changed))
        for key in ("tokens", "provider_requests", "cost"):
            changed = copy.deepcopy(accounting)
            changed[key] = {"changed": True}
            with self.subTest(total_fact=key):
                self.assertNotEqual(bound, export.binding({}, changed))
        for omitted in ("event_file", "requests", "command", "inspection_freshness"):
            changed = copy.deepcopy(accounting)
            changed["attempts"][0][omitted] = {"mtime_ns": 99, "unused": "later"}
            with self.subTest(unrendered_fact=omitted):
                self.assertEqual(bound, export.binding({}, changed))
        self.assertEqual(original, accounting)
        self.assertTrue(document.accounting_facts({})["incomplete"])
        self.assertFalse(document.accounting_facts({"issues": []})["incomplete"])
        self.assertTrue(document.accounting_facts({"issues": ["incomplete"]})["incomplete"])
        self.assertNotEqual(export.binding({}, {}), export.binding({}, {"issues": []}))
        self.assertNotEqual(export.binding({}, {"issues": []}), export.binding({}, {"issues": ["incomplete"]}))

    def test_legacy_binding_matches_the_pre_timing_exporter(self):
        # Captured from e18cf660's exporter before the timing refactor, source SHA256
        # 8a55754b961014ea2ca357a2e43c7317bfb5e9709671e1d00310782d081d8528.
        legacy = {"status": "TASK_COMPLETE", "workflow": {"kind": "discuss"}, "base_commit": "base"}
        self.assertEqual("1ba551e47412d37bb59bf068c011cda5df6981cf8447cc8b457ae725c6af679f", export.binding(legacy, {}))

    def test_canonical_report_is_deterministic_and_excludes_raw_transport_and_log_tails(self):
        report = value()
        self.assertEqual(report, value())
        rendered = document.render(report)
        self.assertEqual(rendered, document.render(copy.deepcopy(report)))
        self.assertIn("Deliver x\\|y", rendered)
        self.assertEqual("Builder", report["attempts"][0]["role"])
        self.assertNotIn("secret", json.dumps(report))
        self.assertNotIn("authorization", json.dumps(report))
        self.assertEqual(None, report["usage"]["provider_requests"]["value"])
        self.assertIn("**fake**", rendered)

    def test_failed_and_unchecked_criteria_remain_distinct(self):
        view = public_view()
        rows = view["evidence"]["acceptance"]
        rows[0]["validator_status"] = "FAIL"
        rows.append({**rows[0], "id": "C2", "validator_status": None, "status": None})
        report = value(view)
        self.assertEqual(["FAIL", None], [row["validator_status"] for row in report["criteria"]])
        self.assertIn("validator: FAIL", document.render(report))
        self.assertIn("validator: unchecked", document.render(report))
        self.assertTrue(any("C2: Validator not recorded" in row for row in report["unverified"]))

    def test_unknown_usage_is_not_zero_and_unvalidated_read_only_jobs_do_not_invent_checks(self):
        report = value({"status": "TASK_COMPLETE", "workflow": "discuss"})
        self.assertIsNone(report["usage"]["cost"])
        self.assertEqual([], report["checks"])
        self.assertIn("No independent check replay recorded", " ".join(report["unverified"]))

    def test_schema_rejects_extra_keys_and_invalid_numeric_duration(self):
        report = value()
        report["extra"] = True
        with self.assertRaises(ValueError):
            document.validate(report)
        for duration in (True, "seconds", float("nan"), float("inf")):
            report = value()
            report["checks"][0]["duration_seconds"] = duration
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                document.validate(report)
        self.assertIsNone(util.validate_schema(1.5, {"type": "number"}))
        with self.assertRaises(ValueError):
            util.validate_schema(True, {"type": "number"})

    def test_declared_provenance_is_immutable_and_unannotated_providers_are_unknown(self):
        self.assertEqual("unknown", provenance.projection({"engine": "codex"})["kind"])
        fake = provenance.configured({}, "fake")
        self.assertEqual(fake, provenance.configured({"evidence_provenance": fake}, "fake"))
        with self.assertRaisesRegex(ValueError, "fixed at launch"):
            provenance.configured({"evidence_provenance": fake}, "live")
        with self.assertRaisesRegex(ValueError, "legacy resume remains unknown"):
            provenance.configured({"engine": "codex"}, "live")
        self.assertEqual("mixed", provenance.combined([fake, provenance.configured({}, "live")])["kind"])
        self.assertEqual("unknown", provenance.combined([fake, provenance.configured({})])["kind"])

    def test_aggregate_unknown_total_preserves_observed_subtotal(self):
        self.assertEqual(
            {"value": None, "observed": 12, "complete": False},
            aggregate.quantity([{"value": 12, "complete": True}, {"value": None, "complete": False}]),
        )
        self.assertEqual(
            {"value": None, "observed": 18, "complete": False},
            aggregate.quantity([{"value": None, "known": 18, "complete": False}]),
        )

    def test_routed_checkpoint_reports_the_testing_job_and_retains_failure_disclosures(self):
        view = public_view()
        view["usage"]["accounting"]["attempts"][0]["stage"] = "astra_checkpoint"
        view["evidence"]["check_replay"]["checks"][0].update(exit_code=1, results={"failed": 2})
        view["evidence"]["regression_proof"] = {
            "verdict": "FAIL",
            "failures": ["old behavior did not fail"],
            "unverified": ["database is simulated"],
        }
        report = document.build(
            view,
            run_identity="run",
            completed_at=None,
            provenance=provenance.configured({}, "fake"),
            binding="bound",
            role_context={"settings": {"workflow": {"mode": "glm_first_v1"}}},
        )
        self.assertEqual("Tester", report["attempts"][0]["role"])
        self.assertEqual("Completion Reviewer", value(view)["attempts"][0]["role"])
        markdown = document.render(report)
        for text in (
            "Tester",
            "failed",
            "Runner verdict: FAIL.",
            "old behavior did not fail",
            "database is simulated",
            "Check failed or unavailable",
        ):
            self.assertIn(text, markdown)

    def test_read_only_job_outcome_and_elapsed_time_are_reported_without_invented_validation(self):
        view = {
            "status": "TASK_COMPLETE",
            "workflow": "discuss",
            "evidence": {
                "created_at": "2026-10-08T00:00:00+00:00",
                "workflow_result": {
                    "outcome": "answered",
                    "artifact": "/owned/answer.json",
                    "artifact_sha256": None,
                    "summary": "Analyst answer recorded",
                },
            },
        }
        report = document.build(
            view,
            run_identity="run",
            completed_at="2026-10-08T00:00:15+00:00",
            provenance=provenance.configured({}),
            binding="bound",
        )
        self.assertEqual(15, report["run"]["elapsed_seconds"])
        self.assertEqual([], report["checks"])
        self.assertIn("Outcome: answered", document.render(report))
        self.assertIn("No independent check replay recorded", document.render(report))


class EvidenceFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="evidence-files-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_masked_export_keeps_raw_hashes_and_exact_real_client_transport(self):
        import autocode_github as github
        import autocode_issue as issue

        canary = "ghp_CANARY0123456789abcdef"
        raw = self.root / "raw-check.txt"
        raw.write_text("raw evidence " + canary + "\n")
        raw_before = raw.read_bytes()
        view = public_view()
        view["evidence"]["outcome"] = "Deliver " + canary
        view["evidence"]["acceptance"][0]["criterion"] = "Check " + canary
        view["evidence"]["check_replay"]["checks"][0].update(
            command="python check.py --token " + canary,
            output=str(raw),
            output_sha256=util.file_hash(raw),
            results={"diagnostic": canary, "passed": 5},
        )
        view["usage"]["accounting"]["attempts"][0]["model"] = canary
        view["evidence"]["regression_proof"] = {"verdict": "PASS", "commands": {canary: "run " + canary}}
        supplied = value(view)
        before = copy.deepcopy(supplied)
        anchor = export.write(self.root, supplied)
        report = export.read(self.root, anchor, expected_binding="binding", current=True, include=True)
        self.assertEqual("current", report["availability"])
        self.assertEqual(before, supplied)
        self.assertEqual(raw_before, raw.read_bytes())
        self.assertEqual(util.file_hash(raw), report["document"]["checks"][0]["output_sha256"])
        for field in ("binding", "plan", "revision"):
            if field == "plan":
                for key in ("contract_token", "criteria_revision"):
                    self.assertEqual(before[field][key], report["document"][field][key])
            elif field == "revision":
                for key in ("source_revision", "base_commit"):
                    self.assertEqual(before[field][key], report["document"][field][key])
            else:
                self.assertEqual(before[field], report["document"][field])
        self.assertIn(document.REDACTION_NOTICE, report["document"]["unverified"])
        self.assertEqual(5, report["document"]["checks"][0]["results"]["passed"])
        for name in ("evidence.json", "evidence.md"):
            self.assertNotIn(canary, (self.root / name).read_text())
        record = {
            "owner": "acme",
            "repo": "widgets",
            "number": 7,
            "run_dir": str(self.root / ".autocode/runs/run"),
            "base_commit": "a" * 40,
        }
        body = issue.pr_body(record, {}, " app.py | 1 +", evidence_markdown=report["markdown"])
        captured = []

        def transport(method, url, headers, payload):
            captured.append(json.loads(payload))
            return 201, {"number": 1}

        github.Client(token="unused", transport=transport).open_pull_request(
            "acme", "widgets", head="fix", base="main", title="Checked change", body=body
        )
        self.assertEqual(body.encode(), captured[0]["body"].encode())
        self.assertEqual(1, captured[0]["body"].count(report["markdown"]))
        self.assertIn((self.root / "evidence.md").read_bytes(), captured[0]["body"].encode())
        self.assertNotIn(canary, json.dumps(captured))

    def test_final_format_masking_is_disclosed_and_publication_is_idempotent(self):
        supplied = value()
        supplied["plan"]["intended_outcome"] = "api_key=<&"
        before = copy.deepcopy(supplied)
        self.assertEqual(supplied["plan"]["intended_outcome"], util.redact(supplied["plan"]["intended_outcome"]))
        anchor = export.write(self.root, supplied)
        report = export.read(self.root, anchor, current=True, include=True)
        self.assertEqual("current", report["availability"])
        self.assertEqual(before, supplied)
        self.assertIn(document.REDACTION_NOTICE, report["document"]["unverified"])
        self.assertIn("[REDACTED]", report["markdown"])
        self.assertNotIn("api_key=&lt;&amp;", report["markdown"])
        restored = json.loads((self.root / "evidence.json").read_text())
        self.assertEqual(report["markdown"], document.render(restored))
        self.assertEqual(restored, document.sanitize(restored))
        saved = {
            name: ((self.root / name).read_bytes(), (self.root / name).stat().st_mtime_ns)
            for name in ("evidence.json", "evidence.md")
        }
        self.assertEqual(anchor, export.write(self.root, supplied))
        self.assertEqual(
            saved, {name: ((self.root / name).read_bytes(), (self.root / name).stat().st_mtime_ns) for name in saved}
        )

    def test_unsafe_decoded_historical_json_with_safe_markdown_refuses_without_rewrite(self):
        canary = "ghp_CANARY0123456789abcdef"
        supplied = value()
        supplied["plan"]["intended_outcome"] = "Use " + canary
        encoded_canary = "".join("\\u" + format(ord(character), "04x") for character in canary)
        body = (json.dumps(supplied, indent=2, sort_keys=True) + "\n").replace(canary, encoded_canary)
        markdown = document.render(supplied)
        self.assertEqual(body, util.redact(body))
        self.assertEqual(supplied, json.loads(body))
        self.assertEqual(markdown, document.render(json.loads(body)))
        self.assertEqual(markdown, util.redact(markdown))
        self.assertNotEqual(supplied, document.sanitize(supplied))
        anchor = export.write(self.root, value())
        (self.root / "evidence.json").write_text(body)
        (self.root / "evidence.md").write_text(markdown)
        anchor.update(
            json_sha256=util.file_hash(self.root / "evidence.json"),
            markdown_sha256=util.file_hash(self.root / "evidence.md"),
        )
        old_anchor = copy.deepcopy(anchor)
        saved = {
            name: ((self.root / name).read_bytes(), (self.root / name).stat().st_mtime_ns)
            for name in ("evidence.json", "evidence.md")
        }
        found = export.read(self.root, anchor, current=True, include=True)
        self.assertEqual("invalid", found["availability"])
        self.assertNotIn("document", found)
        self.assertNotIn("markdown", found)
        self.assertEqual(old_anchor, anchor)
        self.assertEqual(
            saved, {name: ((self.root / name).read_bytes(), (self.root / name).stat().st_mtime_ns) for name in saved}
        )

    def test_encoded_json_policy_refusal_keeps_new_and_existing_pairs_unchanged(self):
        anchor = export.write(self.root, value())
        old_anchor = copy.deepcopy(anchor)
        saved = {
            name: ((self.root / name).read_bytes(), (self.root / name).stat().st_mtime_ns)
            for name in ("evidence.json", "evidence.md")
        }
        for index, short in enumerate(("token=éé", "token=aa\nbb\ncc")):
            with self.subTest(short=short):
                supplied = value()
                supplied["plan"]["intended_outcome"] = short
                before = copy.deepcopy(supplied)
                self.assertEqual(short, util.redact(short))
                encoded = document.sanitize(supplied)
                body = json.dumps(encoded, indent=2, sort_keys=True) + "\n"
                self.assertNotEqual(body, util.redact(body))
                unused = self.root / f"new-{index}"
                for target in (unused, self.root):
                    with self.assertRaisesRegex(ValueError, "Encoded canonical JSON"):
                        export.write(target, supplied)
                self.assertEqual(before, supplied)
                self.assertFalse((unused / "evidence.json").exists())
                self.assertFalse((unused / "evidence.md").exists())
                self.assertEqual(
                    saved,
                    {name: ((self.root / name).read_bytes(), (self.root / name).stat().st_mtime_ns) for name in saved},
                )
                historical = (self.root / f"historical-{index}").resolve()
                historical.mkdir()
                (historical / "evidence.json").write_text(body)
                (historical / "evidence.md").write_text(document.render(encoded))
                self.assertEqual(encoded, document.sanitize(json.loads(body)))
                self.assertEqual(document.render(json.loads(body)), (historical / "evidence.md").read_text())
                historical_anchor = {
                    **anchor,
                    "json_path": str(historical / "evidence.json"),
                    "markdown_path": str(historical / "evidence.md"),
                    "json_sha256": util.file_hash(historical / "evidence.json"),
                    "markdown_sha256": util.file_hash(historical / "evidence.md"),
                }
                prior = {
                    name: ((historical / name).read_bytes(), (historical / name).stat().st_mtime_ns)
                    for name in ("evidence.json", "evidence.md")
                }
                self.assertEqual("invalid", export.read(historical, historical_anchor, include=True)["availability"])
                self.assertEqual(
                    prior,
                    {
                        name: ((historical / name).read_bytes(), (historical / name).stat().st_mtime_ns)
                        for name in prior
                    },
                )
        self.assertEqual(old_anchor, anchor)

    def test_masking_refuses_colliding_keys_and_changed_authentication_without_writes(self):
        canary = "ghp_CANARY0123456789abcdef"
        fields = (
            ("binding",),
            ("plan", "contract_token"),
            ("plan", "criteria_revision"),
            ("revision", "source_revision"),
            ("checks", 0, "output_sha256"),
        )
        for path in fields:
            supplied = value()
            target = supplied
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = canary
            before = copy.deepcopy(supplied)
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "authentication identifier"):
                export.write(self.root, supplied)
            self.assertEqual(before, supplied)
        supplied = value()
        supplied["regression_proof"] = {"case_tests": {canary: ["one"], "ghp_OTHER0123456789abcdef": ["two"]}}
        before = copy.deepcopy(supplied)
        with self.assertRaisesRegex(ValueError, "distinct evidence keys"):
            export.write(self.root, supplied)
        self.assertEqual(before, supplied)
        self.assertFalse((self.root / "evidence.json").exists())
        self.assertFalse((self.root / "evidence.md").exists())

    def test_aggregate_post_build_facts_use_the_common_masking_boundary(self):
        canary = "ghp_CANARY0123456789abcdef"
        anchor = export.write(self.root / "child", value())
        child = export.read(self.root / "child", anchor, include=True)

        class PublicChild:
            def evidence_report(self, *, require_current):
                return child

        checks = [{"command": "python integration.py --token " + canary, "exit_code": 0}]
        before = copy.deepcopy(checks)
        result = aggregate.publish(
            self.root / "parent",
            kind="program",
            identity="program",
            status="COMPLETE",
            child_runs=[("M1", PublicChild())],
            source_revision="a" * 64,
            checks=checks,
            not_verified=["Runtime: " + canary],
        )
        self.assertEqual("recorded", result["availability"])
        self.assertEqual(before, checks)
        self.assertNotIn(canary, json.dumps(result["document"]))
        self.assertNotIn(canary, result["markdown"])
        self.assertIn(document.REDACTION_NOTICE, result["document"]["unverified"])
        self.assertEqual(anchor["json_sha256"], result["document"]["children"][0]["json_sha256"])

    def test_canonical_pair_authentication_and_read_only_inspection(self):
        anchor = export.write(self.root, value())
        before = {path.name: (util.file_hash(path), path.stat().st_mtime_ns) for path in self.root.iterdir()}
        found = export.read(self.root, anchor, expected_binding="binding", current=True, include=True)
        self.assertEqual("current", found["availability"])
        self.assertEqual(document.render(found["document"]), found["markdown"])
        self.assertEqual(anchor, export.write(self.root, value()))
        self.assertEqual(
            before, {path.name: (util.file_hash(path), path.stat().st_mtime_ns) for path in self.root.iterdir()}
        )

    def test_regression_mapping_order_survives_json_publication_and_authenticated_read(self):
        view = public_view()
        view["evidence"]["regression_proof"] = {
            "verdict": "PASS",
            "case_tests": {"T2": ["tests.Bounds.test_second"], "T1": ["tests.Bounds.test_first"]},
            "commands": {
                "suite": "python -m unittest discover",
                "suite_source": "detected:unittest",
                "regression": "python -m unittest tests.Bounds",
                "regression_source": "derived:unittest",
            },
        }
        report = value(view)
        expected = document.render(report)
        restored = json.loads(json.dumps(report, sort_keys=True))
        self.assertNotEqual(
            list(report["regression_proof"]["commands"]), list(restored["regression_proof"]["commands"])
        )
        self.assertNotEqual(
            list(report["regression_proof"]["case_tests"]), list(restored["regression_proof"]["case_tests"])
        )
        self.assertEqual(expected, document.render(restored))
        anchor = export.write(self.root, report)
        found = export.read(self.root, anchor, expected_binding="binding", current=True, include=True)
        self.assertEqual("current", found["availability"])
        self.assertEqual(expected, found["markdown"])
        self.assertEqual(restored, found["document"])
        self.assertEqual(expected.encode(), (self.root / "evidence.md").read_bytes())

    def test_swapped_read_content_cannot_borrow_unchanged_path_hashes(self):
        anchor = export.write(self.root, value())
        changed = value()
        changed["plan"]["intended_outcome"] = "Invented checked outcome"
        swapped = {
            "evidence.json": (json.dumps(changed, indent=2, sort_keys=True) + "\n").encode(),
            "evidence.md": document.render(changed).encode(),
        }
        original_text, original_bytes = Path.read_text, Path.read_bytes

        def read_text(path, *args, **kwargs):
            return swapped[path.name].decode() if path.name in swapped else original_text(path, *args, **kwargs)

        def read_bytes(path, *args, **kwargs):
            return swapped[path.name] if path.name in swapped else original_bytes(path, *args, **kwargs)

        with patch.object(Path, "read_text", read_text), patch.object(Path, "read_bytes", read_bytes):
            found = export.read(self.root, anchor, current=True, include=True)
        self.assertEqual("invalid", found["availability"])
        self.assertNotIn("markdown", found)
        self.assertEqual(anchor["markdown_sha256"], util.file_hash(self.root / "evidence.md"))

    def test_modified_missing_and_redirected_pairs_are_rejected(self):
        anchor = export.write(self.root, value())
        markdown = self.root / "evidence.md"
        for mode in ("modified", "missing", "symlink"):
            export.write(self.root, value())
            if mode == "modified":
                markdown.write_text("invented PASS")
            else:
                markdown.unlink()
                if mode == "symlink":
                    markdown.symlink_to(self.root / "evidence.json")
            with self.subTest(mode=mode):
                self.assertEqual("invalid", export.read(self.root, anchor, current=True)["availability"])
            if markdown.is_symlink():
                markdown.unlink()

    def test_publication_failure_does_not_downgrade_completion_or_overwrite_redirected_files(self):
        target = self.root / "keep.txt"
        target.write_text("preserve these bytes")
        (self.root / "evidence.md").symlink_to(target)
        state = {"status": "TASK_COMPLETE", "completed_at": "2026-10-08T00:00:00+00:00"}
        export.publish(self.root, state, public_view(), provenance.configured({}, "fake"))
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual("export_failed", export.metadata(state)["availability"])
        self.assertEqual("preserve these bytes", target.read_text())
        (self.root / "evidence.md").unlink()
        export.publish(self.root, state, public_view(), provenance.configured({}, "fake"))
        self.assertEqual("recorded", export.read(self.root, state["evidence_export"])["availability"])

    def test_different_current_plan_is_stale_even_when_files_are_intact(self):
        anchor = export.write(self.root, value())
        self.assertEqual(
            "stale", export.read(self.root, anchor, expected_binding="new-plan", current=True)["availability"]
        )

    def test_changed_canonical_state_inputs_stale_intact_checkpoint_reports(self):
        state = {
            "status": "TASK_COMPLETE",
            "workflow": {"kind": "bugfix"},
            "base_commit": "base",
            "findings_ledger": [{"id": "F1", "status": "resolved", "severity": "minor", "finding": "Original detail"}],
            "investigation": {
                "outcome": "reproduced",
                "test_cases": [{"id": "T1", "given": "an input", "when": "it runs", "then": "the checked result"}],
            },
        }
        interaction_timing.begin(state, "2026-10-10T08:00:00+00:00")
        interaction_timing.mark(state, "plan", "2026-10-10T08:01:00+00:00")
        original_view = run_view.view(state)
        export.publish(self.root, state, original_view, provenance.configured({}, "fake"))
        anchor = state["evidence_export"]
        saved = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.iterdir()}
        original = export.read(
            self.root,
            anchor,
            expected_binding=export.binding(state, original_view["usage"]["accounting"]),
            current=True,
            include=True,
        )
        self.assertEqual("current", original["availability"])
        mutations = {
            "interaction_timing": (
                {**state["interaction_timing"], "first_plan_at": "2026-10-10T08:02:00+00:00"},
                "run",
            ),
            "base_commit": ("different-base", "revision"),
            "findings_ledger": (
                [{"id": "F1", "status": "resolved", "severity": "minor", "finding": "Revised detail"}],
                "findings",
            ),
            "investigation": (
                {
                    "outcome": "reproduced",
                    "test_cases": [
                        {"id": "T1", "given": "an input", "when": "it runs", "then": "a different requirement"}
                    ],
                },
                "test_cases",
            ),
            "active_stage": ({"stage": "sol", "engine": "custom", "model": "changed-model"}, "attempts"),
            "orchestration_batch": ({"workers": [{"milestone_id": "M1", "status": "RUNNING"}]}, "usage"),
        }
        for key, (replacement, section) in mutations.items():
            changed = {**copy.deepcopy(state), key: replacement}
            supplied = run_view.view(changed)
            bound = export.binding(changed, supplied["usage"]["accounting"])
            with self.subTest(input=key):
                self.assertEqual(
                    "stale", export.read(self.root, anchor, expected_binding=bound, current=True)["availability"]
                )
                rebuilt = document.build(
                    supplied,
                    run_identity="run",
                    completed_at=None,
                    provenance=provenance.configured({}, "fake"),
                    binding=bound,
                )
                self.assertNotEqual(original["document"][section], rebuilt[section])
                self.assertEqual(
                    saved, {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.iterdir()}
                )
        # Old intact anchors cannot be relabelled current by the stronger reader.
        legacy = {**original["document"], "binding": "legacy-state-only-binding"}
        legacy_anchor = export.write(self.root / "legacy", legacy)
        self.assertEqual(
            "stale",
            export.read(
                self.root / "legacy",
                legacy_anchor,
                expected_binding=export.binding(state, original_view["usage"]["accounting"]),
                current=True,
            )["availability"],
        )

    def test_invalid_accounting_records_export_failure_and_recovers_without_pair_changes(self):
        state = {"status": "TASK_COMPLETE"}
        supplied = public_view()
        self.assertTrue(export.publish(self.root, state, supplied, provenance.configured({}, "fake")))
        anchor = copy.deepcopy(state["evidence_export"])
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()}
        invalid = copy.deepcopy(supplied)
        opaque = object()
        invalid["usage"]["accounting"]["tokens"] = {"not_json": opaque}
        self.assertTrue(export.publish(self.root, state, invalid, provenance.configured({}, "fake")))
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertIsNone(state["evidence_export"]["binding"])
        self.assertEqual("export_failed", export.read(self.root, state["evidence_export"])["availability"])
        self.assertIs(opaque, invalid["usage"]["accounting"]["tokens"]["not_json"])
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()})
        self.assertTrue(export.publish(self.root, state, supplied, provenance.configured({}, "fake")))
        self.assertEqual(anchor, state["evidence_export"])
        self.assertEqual(
            "current",
            export.read(
                self.root, anchor, expected_binding=export.binding(state, supplied["usage"]["accounting"]), current=True
            )["availability"],
        )
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()})

    def test_native_event_usage_changes_stale_reports_without_state_changes(self):
        events = self.root / "native.jsonl"

        def finish(identity, tokens):
            return {
                "type": "step_finish",
                "sessionID": "session",
                "part": {
                    "id": identity,
                    "cost": 0.1,
                    "tokens": {"input": tokens, "output": 2, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                },
            }

        events.write_text(json.dumps(finish("first", 10)) + "\n")
        state = {
            "status": "TASK_COMPLETE",
            "run_dir": str(self.root),
            "stages": [
                {
                    "stage": "terra",
                    "engine": "opencode",
                    "attempt_id": "attempt",
                    "model": "model",
                    "events": str(events),
                }
            ],
        }
        supplied = run_view.view(state)
        export.publish(self.root / "report", state, supplied, provenance.configured({}, "fake"))
        anchor = state["evidence_export"]
        original_state = copy.deepcopy(state)
        bound = export.binding(state, supplied["usage"]["accounting"])
        original = export.read(self.root / "report", anchor, expected_binding=bound, current=True, include=True)
        self.assertEqual("current", original["availability"])
        self.assertEqual(10, original["document"]["usage"]["tokens"]["input_tokens"]["value"])
        self.assertIn("accounting snapshot captured at this completed checkpoint", original["markdown"])
        original_bytes = events.read_bytes()
        pair = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.root / "report").iterdir()}
        # A timestamp refresh is bookkeeping; the rendered facts remain identical.
        stamp = events.stat().st_mtime_ns
        os.utime(events, ns=(stamp + 1_000_000, stamp + 1_000_000))
        refreshed = run_view.view(state)
        self.assertEqual(bound, export.binding(state, refreshed["usage"]["accounting"]))
        events.write_bytes(original_bytes + (json.dumps(finish("second", 20)) + "\n").encode())
        changed = run_view.view(state)
        self.assertEqual(30, changed["usage"]["accounting"]["tokens"]["input_tokens"]["value"])
        self.assertEqual(original_state, state)
        self.assertEqual(
            "stale",
            export.read(
                self.root / "report",
                anchor,
                expected_binding=export.binding(state, changed["usage"]["accounting"]),
                current=True,
            )["availability"],
        )
        self.assertEqual(
            pair, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.root / "report").iterdir()}
        )
        events.write_bytes(original_bytes)
        self.assertEqual(
            "current",
            export.read(
                self.root / "report",
                anchor,
                expected_binding=export.binding(state, run_view.view(state)["usage"]["accounting"]),
                current=True,
            )["availability"],
        )

    def test_inspection_freshness_and_liveness_do_not_rebind_checkpoint_facts(self):
        state = {"status": "TASK_COMPLETE"}
        supplied = public_view()
        export.publish(self.root, state, supplied, provenance.configured({}, "fake"))
        bound = state["evidence_export"]["binding"]
        inspected = copy.deepcopy(supplied)
        inspected.update(liveness={"checked": True, "alive": False}, inspected_source_revision="later-head")
        inspected["verification"].update(
            freshness="stale", inspected_source_revision="later-head", reasons=["after delivery"]
        )
        self.assertEqual(bound, export.binding(state, inspected["usage"]["accounting"]))
        self.assertEqual(
            "recorded",
            export.read(
                self.root,
                state["evidence_export"],
                expected_binding=export.binding(state, inspected["usage"]["accounting"]),
                include=True,
            )["availability"],
        )
        self.assertFalse(export.publish(self.root, state, inspected, provenance.configured({}, "fake")))

    def test_aggregate_reads_only_public_child_report_and_preserves_fake_provenance(self):
        anchor = export.write(self.root / "child", value())
        report = export.read(self.root / "child", anchor, include=True)

        class PublicChild:
            def evidence_report(self, *, require_current):
                return report

        result = aggregate.publish(
            self.root / "parent",
            kind="program",
            identity="program",
            status="COMPLETE",
            child_runs=[("M1", PublicChild())],
            source_revision="a" * 64,
            outcome="Deliver both",
            not_verified=["SQL is simulated"],
        )
        self.assertEqual("recorded", result["availability"])
        self.assertEqual("fake", result["document"]["provenance"]["kind"])
        self.assertEqual("M1/C1", result["document"]["criteria"][0]["id"])
        self.assertIn("SQL is simulated", result["markdown"])
        self.assertEqual(anchor["json_sha256"], result["document"]["children"][0]["json_sha256"])

    def test_aggregate_does_not_relabel_child_time_as_completion_after_integration(self):
        child_time = "2026-10-08T00:00:00+00:00"
        integration_time = "2026-10-08T00:01:00+00:00"
        anchor = export.write(self.root / "child", value())
        child = export.read(self.root / "child", anchor, include=True)
        self.assertEqual(child_time, child["document"]["run"]["completed_at"])

        class PublicChild:
            def evidence_report(self, *, require_current):
                return child

        for kind in ("program", "components"):
            with self.subTest(kind=kind):
                result = aggregate.publish(
                    self.root / kind,
                    kind=kind,
                    identity=kind,
                    status="COMPLETE",
                    child_runs=[("M1", PublicChild())],
                    source_revision="a" * 64,
                    checks=[
                        {
                            "command": "python integration.py",
                            "exit_code": 0,
                            "results": {"finished_at": integration_time},
                        }
                    ],
                )
                self.assertIsNone(result["document"]["run"]["completed_at"])
                self.assertIsNone(result["document"]["run"]["elapsed_seconds"])
                self.assertIn("Completed: not recorded.", result["markdown"])
                self.assertNotIn("Completed: " + child_time, result["markdown"])
                self.assertIn(integration_time, result["markdown"])
                self.assertIn(
                    "Coordinator completion timestamp and elapsed wall time are not recorded", result["markdown"]
                )
                self.assertEqual(result["markdown"].encode(), (self.root / kind / "evidence.md").read_bytes())

    def test_harness_requires_the_pair_and_honest_provenance_for_completed_tasks(self):
        anchor = export.write(self.root, value())
        report = export.read(self.root, anchor, current=True)
        run = {
            "report_expected": True,
            "run_dir": str(self.root),
            "provider_evidence": "fake",
            "view": {"status": "TASK_COMPLETE", "evidence_report": report},
        }
        self.assertTrue(all(row.ok for row in evidence_reports.checks(run)))
        run["provider_evidence"] = "live"
        self.assertFalse(evidence_reports.checks(run)[0].ok)
        run["provider_evidence"] = "fake"
        (self.root / "evidence.md").write_text("invented PASS")
        self.assertFalse(evidence_reports.checks(run)[0].ok)
        run["view"]["status"] = "AWAITING_GOAL_APPROVAL"
        self.assertEqual([], evidence_reports.checks(run))

    def test_harness_checks_aggregate_and_each_completed_public_child(self):
        child_root = self.root / "child"
        anchor = export.write(child_root, value())
        child = export.read(child_root, anchor, include=True)

        class PublicChild:
            def evidence_report(self, *, require_current):
                return child

        parent = aggregate.publish(
            self.root,
            kind="program",
            identity="p",
            status="COMPLETE",
            child_runs=[("M1", PublicChild())],
            source_revision="a" * 64,
        )
        run = {
            "report_expected": True,
            "provider_evidence": "fake",
            "program": {"status": "COMPLETE", "state_file": str(self.root / "state.json"), "evidence_report": parent},
            "children": {"M1": [{"status": "TASK_COMPLETE", "run_dir": str(child_root), "evidence_report": child}]},
        }
        self.assertEqual(2, len(evidence_reports.checks(run)))
        self.assertTrue(all(row.ok for row in evidence_reports.checks(run)))
        (child_root / "evidence.json").unlink()
        results = evidence_reports.checks(run)
        self.assertTrue(results[0].ok)
        self.assertFalse(results[1].ok)


class PublicTaskReportTests(unittest.TestCase):
    def test_completed_cli_exports_exact_pair_and_refuses_stale_or_tampered_delivery(self):
        with tempfile.TemporaryDirectory(prefix="task-evidence-cli-") as directory:
            root = Path(directory)
            workspace = root / "project"
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=workspace, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=T",
                    "-c",
                    "user.email=t@example.test",
                    "commit",
                    "-q",
                    "--allow-empty",
                    "-m",
                    "base",
                ],
                cwd=workspace,
                check=True,
            )
            binary = root / "bin"
            binary.mkdir()
            shutil.copy2(Path(taskrun.__file__).parent / "live_fixture_provider.py", binary / "codex")
            (binary / "codex").chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                "AUTOCODE_HOME": str(root / "registry"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            run = taskrun.TaskRun.start(workspace, BRIEF, options=FIXTURE_OPTIONS, env=env, timeout=300)
            self.assertEqual("missing", run.status()["evidence_report"]["availability"])
            run.approve_plan(run.status()["needs"]["token"])
            self.assertTrue(run.advance_until_input()["done"])
            report = run.evidence_report()
            document.validate(report["document"])
            self.assertEqual("fake", report["document"]["provenance"]["kind"])
            self.assertEqual(report["markdown"], Path(report["markdown_path"]).read_text())
            before = (Path(report["json_path"]).read_bytes(), Path(report["markdown_path"]).read_bytes())
            run.status(inspect_evidence=True)
            self.assertEqual(
                before, (Path(report["json_path"]).read_bytes(), Path(report["markdown_path"]).read_bytes())
            )
            inspected = run.evidence_report()
            self.assertEqual("current", inspected["availability"])
            self.assertEqual(report["binding"], inspected["binding"])
            self.assertEqual(report["markdown"], inspected["markdown"])
            source = workspace / "greet.py"
            saved = source.read_bytes()
            source.write_bytes(saved + b"\n# changed after completion\n")
            with self.assertRaisesRegex(taskrun.TaskRunError, "not current"):
                run.evidence_report()
            source.write_bytes(saved)
            Path(report["markdown_path"]).write_text("invented PASS")
            with self.assertRaisesRegex(taskrun.TaskRunError, "invalid"):
                run.evidence_report()
