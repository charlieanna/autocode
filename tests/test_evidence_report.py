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
import autocode_plan_file as plan_file
import autocode_run_view as run_view
import autocode_taskrun as taskrun
import autocode_util as util
import goal_fixtures

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


class ChangedCodeReportTests(unittest.TestCase):
    def changed_view(self):
        import autocode_changed_code_evidence as changed_code

        view = public_view()
        source, context, digest = "a" * 64, "c" * 64, "d" * 64
        inventory = [{"path": "src/value.py", "sha256": digest, "lines": [2, 3, 4]}]
        mutation = {
            "source_revision": source,
            "proof_context": context,
            "files": [
                {
                    "path": "src/value.py",
                    "sha256": digest,
                    "mutants": [
                        {
                            "line": 2,
                            "outcome": "killed",
                            "failed_tests": ["tests.Value.test_boundary"],
                            "evidence": ["owned/mutant.log"],
                        }
                    ],
                }
            ],
        }
        initial = changed_code.assess(inventory, None, mutation, source_revision=source, proof_context=context)
        gap = initial["obligations"][1]
        decision = {
            **{key: gap[key] for key in ("gap_id", "path", "sha256", "line")},
            "source_revision": source,
            "proof_context": context,
            "accepted": True,
            "reason_kind": "defensive_unreachable",
            "reason": "Parser excludes this defensive state.",
            "evidence": ["owned/tester-check.log"],
            "reviewer": {"stage": "sol", "role": "sol", "output": "owned/tester.json"},
        }
        assessment = changed_code.assess(
            inventory,
            None,
            mutation,
            source_revision=source,
            proof_context=context,
            exceptions=[decision],
        )
        view["evidence"]["regression_proof"] = {"verdict": "PASS", "changed_code_evidence": assessment}
        view["verification"]["changed_code_evidence"] = copy.deepcopy(assessment)
        base = {
            "kind": "changed_code_rework",
            "task_id": "task",
            "contract_hash": "plan",
            "contract_revision": 1,
            "source_revision": source,
            "review_output": "owned/earlier-tester.json",
            "assessment": initial,
            "at": "2026-10-29T10:00:00+00:00",
        }
        view["verification"]["changed_code_repairs"] = [
            {**copy.deepcopy(base), "action": "builder"},
            {
                **copy.deepcopy(base),
                "action": "investigator",
                "investigation": {
                    "diagnosis": "test_gap",
                    "guidance": "Assert the surviving boundary value.",
                    "evidence_refs": ["owned/earlier-tester.json"],
                    "output": "owned/investigator.json",
                    "sha256": "e" * 64,
                },
            },
        ]
        return view

    def test_gate_reports_current_gaps_accepted_reasons_fallback_and_bound_repair_decisions(self):
        view = self.changed_view()
        before = copy.deepcopy(view)
        report = value(view)
        self.assertEqual(before, view)
        assessment = report["regression_proof"]["changed_code_evidence"]
        self.assertEqual(view["evidence"]["regression_proof"]["changed_code_evidence"], assessment)
        self.assertEqual(view["verification"]["changed_code_repairs"], report["changed_code_repairs"])
        self.assertTrue(any(reason.startswith("Changed-code evidence gate:") for reason in report["unverified"]))
        markdown = document.render(report)
        for phrase in (
            "Runner gate: **UNVERIFIED**",
            "Unresolved obligations block completion",
            "Independently accepted Tester reason",
            "Parser excludes this defensive state.",
            "owned/tester.json",
            "owned/tester-check.log",
            "mutation_only",
            "reviewer_reason_required",
            "Builder",
            "Investigator",
            "Assert the surviving boundary value.",
            "historical decisions retain their own source",
            "c" * 64,
            "d" * 64,
            "e" * 64,
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, markdown)
        for row in assessment["obligations"]:
            self.assertIn(row["gap_id"], markdown)
            self.assertIn(f"src/value.py:{row['line']}", markdown)
        self.assertEqual(markdown, document.render(json.loads(json.dumps(report, sort_keys=True))))

    def test_new_gate_document_pair_authenticates_and_tampered_decision_is_refused(self):
        report = value(self.changed_view())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            anchor = export.write(root, report)
            found = export.read(root, anchor, expected_binding="binding", current=True, include=True)
            self.assertEqual("current", found["availability"])
            self.assertEqual(report, found["document"])
            self.assertEqual(document.render(report), found["markdown"])
            path = root / "evidence.json"
            changed = json.loads(path.read_text())
            changed["changed_code_repairs"][0]["action"] = "investigator"
            path.write_text(json.dumps(changed))
            self.assertNotEqual(
                "current", export.read(root, anchor, expected_binding="binding", current=True)["availability"]
            )

    def test_history_binding_includes_only_displayed_new_decisions_and_keeps_legacy_reports(self):
        state = {"status": "TASK_COMPLETE", "workflow": {"kind": "discuss"}, "base_commit": "base"}
        legacy = export.binding(state, {})
        state["regression_proofs"] = [{"verdict": "PASS", "source_revision": "old"}]
        self.assertEqual(legacy, export.binding(state, {}))
        state["regression_proofs"] += copy.deepcopy(self.changed_view()["verification"]["changed_code_repairs"])
        current = export.binding(state, {})
        self.assertNotEqual(legacy, current)
        for key, replacement in (
            ("action", "investigator"),
            ("source_revision", "changed"),
            ("review_output", "other"),
        ):
            changed = copy.deepcopy(state)
            changed["regression_proofs"][1][key] = replacement
            with self.subTest(key=key):
                self.assertNotEqual(current, export.binding(changed, {}))
        legacy_view = public_view()
        expected = value(legacy_view)
        legacy_view["verification"].update(changed_code_evidence=None, changed_code_repairs=[])
        self.assertEqual(expected, value(legacy_view))
        self.assertNotIn("Changed-code evidence gate", document.render(expected))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            anchor = export.write(root, expected)
            found = export.read(root, anchor, expected_binding="binding", current=True, include=True)
            self.assertEqual("current", found["availability"])
            self.assertEqual(document.render(expected), found["markdown"])

    def test_legacy_proof_projection_preserves_canonical_json_and_markdown(self):
        legacy_proof = {
            "verdict": "PASS",
            "source_revision": "a" * 64,
            "fail_to_pass": ["tests/test_calc.py::test_answer"],
            "case_tests": {"T1": ["tests/test_calc.py::test_answer"]},
        }
        for optional in ({}, {"changed_code_evidence": None}):
            with self.subTest(optional=optional):
                state = {"regression_proof": {**legacy_proof, **optional}}
                original = copy.deepcopy(state)
                view = public_view()
                view["evidence"] = run_view.evidence(state)
                historical = copy.deepcopy(view)
                historical["evidence"]["regression_proof"].pop("changed_code_evidence", None)
                expected = value(historical)

                report = value(view)

                self.assertEqual(expected, report)
                self.assertEqual(document.render(expected), document.render(report))
                self.assertNotIn("changed_code_evidence", view["evidence"]["regression_proof"])
                self.assertNotIn("Changed-code evidence gate", document.render(report))
                self.assertEqual(original, state)

    def test_new_fields_reject_malformed_status_and_do_not_redact_identity_into_new_authority(self):
        report = value(self.changed_view())
        bad = copy.deepcopy(report)
        bad["regression_proof"]["changed_code_evidence"]["status"] = "maybe"
        with self.assertRaises(ValueError):
            document.validate(bad)
        for key in ("proof_context", "gap_id"):
            bad = copy.deepcopy(report)
            assessment = bad["regression_proof"]["changed_code_evidence"]
            target = assessment if key == "proof_context" else assessment["obligations"][0]
            target[key] = "ghp_CANARY0123456789abcdef"
            with self.subTest(key=key), self.assertRaises(ValueError):
                document.sanitize(bad)


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

    def test_surviving_mutants_are_listed_under_the_proof_and_older_reports_render_unchanged(self):
        view = public_view()
        view["evidence"]["regression_proof"] = {"verdict": "PASS", "case_tests": {"C1": ["tests.T.test_c1"]}}
        plain = value(view)
        expected = document.render(plain)
        quiet = copy.deepcopy(view)
        quiet["evidence"]["mutation_check"] = {"collected": True, "survivors": {}}
        self.assertEqual(plain, value(quiet))
        row = {
            "test": "tests.T.test_c1",
            "path": "app.py",
            "line": 5,
            "operator": "swap_bound",
            "before": "<",
            "after": "<=",
            "text": "test_c1 passes with app.py:5 changed (`<` -> `<=`)",
        }
        found = copy.deepcopy(view)
        found["evidence"]["mutation_check"] = {"collected": True, "survivors": {"C1": [row]}}
        report = value(found)
        self.assertEqual({"C1": [row]}, report["regression_proof"]["mutation_survivors"])
        # Report only: a survivor is not a verification gap.
        self.assertEqual(plain["unverified"], report["unverified"])
        line = "- Surviving mutant, C1: `tests.T.test_c1` passes with `app.py:5` changed (`<` -&gt; `<=`)\n"
        rendered = document.render(report)
        self.assertEqual(expected.replace("- C1: tests.T.test_c1\n", "- C1: tests.T.test_c1\n" + line), rendered)
        self.assertEqual(rendered, document.render(json.loads(json.dumps(report, sort_keys=True))))

    def test_a_survivor_renders_its_path_and_code_as_written(self):
        # Markdown would read pager/__init__.py as pager/<strong>init</strong>.py and a*b*c as a<em>b</em>c:
        # each field is its own code span, whose content is literal, fenced past any backtick inside.
        view = public_view()
        view["evidence"]["regression_proof"] = {"verdict": "PASS", "case_tests": {"T1": ["tests.P.test_t1"]}}
        row = {
            "test": "tests.P.test_t1",
            "path": "pkg/__init__.py",
            "line": 9,
            "operator": "constant_return",
            "before": "a*b*c + `x`",
            "after": "None",
            "text": "test_t1 passes with pkg/__init__.py:9 changed (`a*b*c + `x`` -> `None`)",
        }
        view["evidence"]["mutation_check"] = {"collected": True, "survivors": {"T1": [row]}}
        report = value(view)
        self.assertEqual(row, report["regression_proof"]["mutation_survivors"]["T1"][0])  # the JSON is unchanged
        self.assertIn(
            "- Surviving mutant, T1: `tests.P.test_t1` passes with `pkg/__init__.py:9` changed "
            "(`` a*b*c + `x` `` -&gt; `None`)\n",
            document.render(report),
        )
        self.assertEqual("`` `tick` ``", document.code("`tick`"))
        self.assertEqual("`a b`", document.code("a\n b"))

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


APPROVER = {"git_user_name": "Ana Example", "git_user_email": "ana@example.test", "host": "desk-7"}
UNVERIFIED_APPROVER = (
    "Who approved is recorded as given, not verified: the Git identity is what the checkout's Git configuration "
    "held and a name given with --as is the person's own claim"
)


def approved_state():
    """A completed run as saved: a plan approval from before AutoCode recorded approvers, a goal edit, the
    edited plan's approval, a resume out of a time-limit pause and a review acceptance (#722)."""
    return {
        "status": "TASK_COMPLETE",
        "workflow": {"kind": "build"},
        "base_commit": "base",
        "contract_history": [
            {"revision": 1, "hash": "a", "origin": "astra_finalize", "created_at": "2026-10-15T07:00:00+00:00"}
        ],
        "goal_contract": {
            "revision": 2,
            "hash": "b",
            "origin": "user_cli_edit",
            "created_at": "2026-10-15T07:30:00+00:00",
            "provenance": {**APPROVER, "command": "autocode --run-dir --edit-goal --as", "claimed_as": "Bo"},
        },
        "user_events": [
            {"kind": "goal_approval", "actor": "user_cli", "at": "2026-10-15T07:10:00+00:00", "token": "r1:a"},
            {
                "kind": "goal_approval",
                "actor": "user_cli",
                "at": "2026-10-15T08:00:00+00:00",
                "token": "r2:b",
                "provenance": {**APPROVER, "command": "autocode --run-dir --approve-goal", "claimed_as": None},
            },
            {
                "kind": "human_review",
                "actor": "user_cli",
                "at": "2026-10-15T09:00:00+00:00",
                "criterion": "C1",
                "token": "r2:b@src:review-secret",
                "provenance": {
                    **APPROVER,
                    "command": "autocode --run-dir --approve-review --review-token",
                    "claimed_as": None,
                },
            },
        ],
        "pause_acknowledgements": [
            {
                "at": "2026-10-15T08:30:00+00:00",
                "pause_status": "PAUSED_TIME_LIMIT",
                "provenance": {**APPROVER, "command": "autocode resume --run-dir --as", "claimed_as": "Cy"},
            }
        ],
    }


class ApprovalReportTests(unittest.TestCase):
    """Who approved, in the evidence report and the pull request body wrapped around it (#722)."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="evidence-approvals-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_the_report_and_pull_request_body_list_each_approval_with_its_provenance(self):
        import autocode_issue as issue

        state = approved_state()
        view = run_view.view(state)
        export.publish(self.root, state, view, provenance.configured({}, "fake"))
        report = export.read(
            self.root,
            state["evidence_export"],
            expected_binding=export.binding(state, view["usage"]["accounting"]),
            current=True,
            include=True,
        )
        self.assertEqual("current", report["availability"], report["reasons"])
        self.assertEqual(view["approvals"], report["document"]["approvals"])
        self.assertEqual(
            ["plan", "edit", "plan", "resume", "review"], [row["kind"] for row in report["document"]["approvals"]]
        )
        self.assertIn(UNVERIFIED_APPROVER, report["document"]["unverified"])
        self.assertIn(
            "Who approved was not recorded for 1 of the listed approval records, saved before AutoCode recorded it",
            report["document"]["unverified"],
        )
        markdown = report["markdown"]
        rows = [
            "| 2026-10-15T07:10:00+00:00 | Plan r1:a approved | not recorded: saved before AutoCode recorded who "
            "approved | not recorded | not recorded | not recorded |",
            "| 2026-10-15T07:30:00+00:00 | Edited plan r2:b saved | Ana Example &lt;ana@example.test&gt; | desk-7 "
            "| autocode --run-dir --edit-goal --as | Bo (a claim, not verified) |",
            "| 2026-10-15T08:00:00+00:00 | Plan r2:b approved | Ana Example &lt;ana@example.test&gt; | desk-7 "
            "| autocode --run-dir --approve-goal | none given |",
            "| 2026-10-15T08:30:00+00:00 | Resumed from PAUSED_TIME_LIMIT | Ana Example &lt;ana@example.test&gt; "
            "| desk-7 | autocode resume --run-dir --as | Cy (a claim, not verified) |",
            "| 2026-10-15T09:00:00+00:00 | Review of C1 approved | Ana Example &lt;ana@example.test&gt; | desk-7 "
            "| autocode --run-dir --approve-review --review-token | none given |",
        ]
        self.assertIn("\n".join(rows) + "\n", markdown)
        self.assertLess(markdown.index("## Intended outcome"), markdown.index("## Approvals"))
        self.assertLess(markdown.index("## Approvals"), markdown.index("## Acceptance criteria"))
        self.assertIn("a name given with `--as` is the person's own claim", markdown)
        self.assertNotIn("review-secret", markdown + json.dumps(report["document"]))
        record = {"owner": "acme", "repo": "widgets", "number": 7, "run_dir": str(self.root), "base_commit": "a" * 40}
        body = issue.pr_body(record, {}, " app.py | 1 +", evidence_markdown=markdown)
        for row in rows:
            self.assertIn(row, body)

    def test_a_completion_acceptance_is_listed_and_only_then_named_in_the_section(self):
        state = approved_state()
        before = document.render(value({**public_view(), "approvals": run_view.view(state)["approvals"]}))
        # Every report written before AutoCode listed completion acceptances keeps its sentence byte for byte.
        self.assertIn(
            "\nWho approved the plan and each review, edited the plan or resumed the run from a pause, oldest first.\n",
            before,
        )
        state["user_events"].append(
            {
                "kind": "completion_accept",
                "actor": "user_cli",
                "at": "2026-10-15T10:00:00+00:00",
                "basis": "runner-verified gates; 2 completion-report attempts failed",
                "provenance": {
                    **APPROVER,
                    "command": "autocode --run-dir --accept-completion --as",
                    "claimed_as": "Di",
                },
            }
        )
        view = run_view.view(state)
        export.publish(self.root, state, view, provenance.configured({}, "fake"))
        report = export.read(
            self.root,
            state["evidence_export"],
            expected_binding=export.binding(state, view["usage"]["accounting"]),
            current=True,
            include=True,
        )
        self.assertEqual("current", report["availability"], report["reasons"])
        self.assertEqual("completion", report["document"]["approvals"][-1]["kind"])
        markdown = report["markdown"]
        self.assertIn(
            "\nWho approved the plan and each review, edited the plan, resumed the run from a pause or accepted its "
            "completion, oldest first.\n",
            markdown,
        )
        self.assertIn(
            "| 2026-10-15T09:00:00+00:00 | Review of C1 approved | Ana Example &lt;ana@example.test&gt; | desk-7 "
            "| autocode --run-dir --approve-review --review-token | none given |\n"
            "| 2026-10-15T10:00:00+00:00 | Completion accepted | Ana Example &lt;ana@example.test&gt; | desk-7 "
            "| autocode --run-dir --accept-completion --as | Di (a claim, not verified) |\n",
            markdown,
        )

    def test_a_run_without_approvals_says_so_and_an_older_report_renders_unchanged(self):
        # A report built without the status view's approvals (one written before AutoCode listed them, or a
        # verification report) has no field and no section, so its pair still authenticates.
        view = public_view()
        older = value(view)
        self.assertNotIn("approvals", older)
        view["approvals"] = []
        report = value(view)
        self.assertEqual([], report["approvals"])
        self.assertEqual(older["unverified"], report["unverified"])
        rendered = document.render(report)
        section = rendered[rendered.index("## Approvals") : rendered.index("## Acceptance criteria")]
        self.assertIn(
            "\nNone recorded: this run saved no plan approval, review approval, plan edit or resume from a pause.\n",
            section,
        )
        self.assertEqual(rendered.replace(section, ""), document.render(older))
        anchor = export.write(self.root, older)
        self.assertEqual(
            "current", export.read(self.root, anchor, expected_binding="binding", current=True)["availability"]
        )

    def test_a_workflow_policy_approval_names_no_person(self):
        state = {
            "status": "TASK_COMPLETE",
            "user_events": [
                {
                    "kind": "goal_approval",
                    "actor": "workflow_policy",
                    "at": "2026-10-15T08:00:00+00:00",
                    "token": "r1:a",
                }
            ],
        }
        report = value({**public_view(), "approvals": run_view.view(state)["approvals"]})
        self.assertNotIn(UNVERIFIED_APPROVER, report["unverified"])
        self.assertFalse(any("Who approved was not recorded" in gap for gap in report["unverified"]))
        self.assertIn(
            "| 2026-10-15T08:00:00+00:00 | Plan r1:a approved | none: approved under the workflow policy, not by a "
            "person | not applicable | not applicable | not applicable |",
            document.render(report),
        )

    def resumed_report(self, kind, **saved):
        """The published, authenticated report of a completed ``kind`` run that was resumed from a pause once."""
        root = self.root / str(kind)
        root.mkdir()
        state = {
            "status": "TASK_COMPLETE",
            **({"workflow": {"kind": kind}} if kind else {}),
            "base_commit": "base",
            "pause_acknowledgements": [
                {
                    "at": "2026-10-15T08:30:00+00:00",
                    "pause_status": "PAUSED_TIME_LIMIT",
                    "provenance": {**APPROVER, "command": "autocode resume --run-dir --as", "claimed_as": "Cy"},
                }
            ],
            **saved,
        }
        view = run_view.view(state)
        self.assertEqual(["resume"], [row["kind"] for row in view["approvals"]])  # --status still lists it
        export.publish(root, state, view, provenance.configured({}, "fake"))
        report = export.read(
            root,
            state["evidence_export"],
            expected_binding=export.binding(state, view["usage"]["accounting"]),
            current=True,
            include=True,
        )
        self.assertEqual("current", report["availability"], report["reasons"])
        return report

    def test_a_review_or_discussion_report_lists_no_approvals_not_even_who_resumed_it(self):
        # Neither workflow has an approval step, and review-pr --post publishes a review's report verbatim as a
        # GitHub review on someone else's pull request.
        import autocode_pr_review as pr_review

        for kind, saved in (
            ("review", {"review": {"verdict": "approve", "blocking": 0, "advisory": 0, "report_sha256": "c" * 64}}),
            ("discuss", {"answer": {"answer": "Yes.", "note_path": ""}}),
        ):
            with self.subTest(workflow=kind):
                report = self.resumed_report(kind, **saved)
                self.assertEqual(kind, report["document"]["run"]["workflow"])
                self.assertNotIn("approvals", report["document"])
                published = report["markdown"] + json.dumps(report["document"])
                for absent in (
                    "## Approvals",
                    "this run saved no plan approval",
                    "ana@example.test",
                    "desk-7",
                    UNVERIFIED_APPROVER,
                ):
                    self.assertNotIn(absent, published)
                if kind == "review":
                    posted = pr_review.summary(
                        {"verdict": "approve", "summary": "No defects."},
                        report,
                        "https://example.test/pull/7",
                        "a" * 40,
                    )
                    self.assertIn(report["markdown"], posted)
                    self.assertNotIn("## Approvals", posted)
                    self.assertNotIn("ana@example.test", posted)

    def test_a_build_bug_fix_or_design_report_keeps_its_approvals(self):
        # Each can ask for an approval (a reproduced bug and a proposed design are planned and approved); a run
        # saved before workflow recognition (no kind) is a build.
        import autocode_issue as issue

        for kind in ("build", "bugfix", "design", None):
            with self.subTest(workflow=kind):
                report = self.resumed_report(kind)
                self.assertEqual(["resume"], [row["kind"] for row in report["document"]["approvals"]])
                self.assertIn(UNVERIFIED_APPROVER, report["document"]["unverified"])
                row = (
                    "| 2026-10-15T08:30:00+00:00 | Resumed from PAUSED_TIME_LIMIT | Ana Example &lt;ana@example.test&gt; "
                    "| desk-7 | autocode resume --run-dir --as | Cy (a claim, not verified) |"
                )
                self.assertIn(row, report["markdown"])
                record = {"owner": "acme", "repo": "widgets", "number": 7, "run_dir": "run", "base_commit": "a" * 40}
                self.assertIn(row, issue.pr_body(record, {}, " app.py | 1 +", evidence_markdown=report["markdown"]))

    def child(self, name, view):
        """A completed child run's authenticated report built from ``view``, and the public handle a coordinator
        reads it through (TaskRun.evidence_report)."""
        anchor = export.write(self.root / name, value(view))
        report = export.read(self.root / name, anchor, include=True)

        class PublicChild:
            def evidence_report(self, *, require_current):
                return report

        return report, PublicChild()

    def coordinated(self, kind, children, where=None):
        """The ``kind`` report aggregate.publish writes for ``children`` in ``where`` (default ``kind``)."""
        root = self.root / (where or kind)
        result = aggregate.publish(
            root,
            kind=kind,
            identity=kind,
            status="COMPLETE",
            child_runs=children,
            source_revision="a" * 64,
        )
        self.assertEqual("recorded", result["availability"], result["reasons"])
        self.assertEqual(result["markdown"].encode(), (root / "evidence.md").read_bytes())
        return result

    def test_a_program_or_components_report_lists_each_child_run_s_approvals(self):
        approvals = run_view.view(approved_state())["approvals"]
        first, m1 = self.child("m1", {**public_view(), "approvals": approvals})
        second, m2 = self.child("m2", {**public_view(), "approvals": approvals[2:3]})
        for kind, left_out in (
            (
                "program",
                "The program agreement (`autocode program approve`) records no approver, so it is not listed, and "
                "neither are the approvals of a plan run it was derived from (`autocode program derive`).",
            ),
            ("components", "Approvals of the architecture this run was built from are not listed."),
        ):
            with self.subTest(kind=kind):
                result = self.coordinated(kind, [("M1", m1), ("M2", m2)])
                self.assertEqual(
                    [
                        *[{**row, "child": "M1"} for row in first["document"]["approvals"]],
                        {**second["document"]["approvals"][0], "child": "M2"},
                    ],
                    result["document"]["approvals"],
                )
                self.assertEqual(
                    ["plan", "edit", "plan", "resume", "review", "plan"],
                    [row["kind"] for row in result["document"]["approvals"]],
                )
                self.assertIn("M1: " + UNVERIFIED_APPROVER, result["document"]["unverified"])
                markdown = result["markdown"]
                section = markdown[markdown.index("## Approvals") : markdown.index("## Acceptance criteria")]
                self.assertIn(
                    "\nWho approved in each child run, as that run's own evidence report lists it: child by child, "
                    "oldest first.\nNone of it is verified: the Git identity is what the checkout's Git configuration "
                    "held, and a name given with `--as` is the person's own claim.\n" + left_out + "\n",
                    section,
                )
                self.assertIn(
                    "| Child | When | Decision | Git identity | Host | Command | Name given with --as |\n"
                    "| --- | --- | --- | --- | --- | --- | --- |\n"
                    "| M1 | 2026-10-15T07:10:00+00:00 | Plan r1:a approved | not recorded: saved before AutoCode "
                    "recorded who approved | not recorded | not recorded | not recorded |\n",
                    section,
                )
                self.assertIn(
                    "| M1 | 2026-10-15T09:00:00+00:00 | Review of C1 approved | Ana Example &lt;ana@example.test&gt; "
                    "| desk-7 | autocode --run-dir --approve-review --review-token | none given |\n"
                    "| M2 | 2026-10-15T08:00:00+00:00 | Plan r2:b approved | Ana Example &lt;ana@example.test&gt; "
                    "| desk-7 | autocode --run-dir --approve-goal | none given |\n",
                    section,
                )
                self.assertNotIn("review-secret", markdown + json.dumps(result["document"]))

    def test_a_child_report_without_approvals_is_named_not_taken_for_none(self):
        approvals = run_view.view(approved_state())["approvals"]
        _, listed = self.child("listed", {**public_view(), "approvals": approvals[2:3]})
        _, older = self.child("older", public_view())  # written before AutoCode listed approvals
        _, empty = self.child("empty", {**public_view(), "approvals": []})
        mixed = self.coordinated("program", [("M1", listed), ("M2", older)])
        self.assertEqual(["M1"], [row["child"] for row in mixed["document"]["approvals"]])
        self.assertIn("M2: its evidence report lists no approvals", mixed["document"]["unverified"])
        self.assertIn("M2: its evidence report lists no approvals", mixed["markdown"])
        # A report built only from children written before AutoCode listed approvals is unchanged: no field and no
        # section, and nothing said about them.
        unchanged = self.coordinated("program", [("M1", older)], where="older-only")
        self.assertNotIn("approvals", unchanged["document"])
        self.assertNotIn("## Approvals", unchanged["markdown"])
        self.assertNotIn("its evidence report lists no approvals", unchanged["markdown"])
        none = self.coordinated("components", [("api", empty)])
        self.assertEqual([], none["document"]["approvals"])
        self.assertIn(
            "\nNone recorded: no child run's report lists an approval, plan edit or resume.\n", none["markdown"]
        )

    def test_only_a_program_or_components_approval_names_a_child_it_lists(self):
        _, child = self.child("m1", {**public_view(), "approvals": run_view.view(approved_state())["approvals"]})
        program = self.coordinated("program", [("M1", child)])["document"]
        task = value({**public_view(), "approvals": run_view.view(approved_state())["approvals"]})
        for name, report, row in (
            ("a program row without its child", program, lambda row: row.pop("child")),
            ("a program row naming another child", program, lambda row: row.update(child="M9")),
            ("a task row naming a child", task, lambda row: row.update(child="M1")),
        ):
            with self.subTest(name):
                changed = copy.deepcopy(report)
                row(changed["approvals"][0])
                with self.assertRaisesRegex(ValueError, "names an approval's child run"):
                    document.validate(changed)
        document.validate(program)
        document.validate(task)


PLAN_PATH = "/repo/plans/refunds.md"
PLAN_REVIEW = {"stage": "astra_challenge", "events": "/run/a/plan-challenge-01.jsonl", "events_sha256": "1" * 64}
GOAL_HASH_LINE = "Plan goal hash: {} (what `autocode plan --check FILE` prints for a file holding exactly this plan)."


def plan_file_state(approved_body=None, *, cited=True):
    """A completed run whose plan revision 2 was loaded from a plan file with --goal-file and whose approved
    revision 3 is ``approved_body``: by default revision 2's plan as the Plan Reviewer sealed it anew, which
    changes only its record of the review (#720). Without ``cited`` revision 2 was drafted, not loaded."""
    loaded = goal_fixtures.body()
    loaded["brief_acceptance"] = {"manifest": {}, "review": PLAN_REVIEW, "amendments": []}
    if approved_body is None:
        approved_body = copy.deepcopy(loaded)
        approved_body["brief_acceptance"]["review"] = dict(PLAN_REVIEW, events="/run/a/plan-challenge-02.jsonl")
    contract = {"task_id": "task", "revision": 3, "body": approved_body}
    contract["hash"] = util.digest(contract)
    approval = {
        "kind": "goal_approval",
        "actor": "user_cli",
        "at": "2026-10-16T08:00:00+00:00",
        "token": f"r3:{contract['hash']}",
    }
    contract.update(approval_status="approved", approval_event=approval, origin="astra_finalize")
    draft = {"task_id": "task", "revision": 2, "hash": "2" * 64, "body": loaded, "approval_status": "draft"}
    draft.update(approval_event=None, origin="user_cli_edit")
    if cited:
        draft["plan_file"] = {"path": PLAN_PATH, "goal_hash": plan_file.goal_hash(loaded)}
    first = {
        "task_id": "task",
        "revision": 1,
        "hash": "1" * 64,
        "body": goal_fixtures.body(),
        "approval_status": "draft",
    }
    return {
        "status": "TASK_COMPLETE",
        "workflow": {"kind": "build"},
        "task_id": "task",
        "base_commit": "base",
        "contract_history": [first, draft],
        "goal_contract": contract,
        "user_events": [approval],
    }


class PlanFileCitationTests(unittest.TestCase):
    """The approved plan's goal hash and the plan file it was loaded from, in the report and the PR body (#720)."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="evidence-plan-file-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def published(self, state):
        """The status view and the authenticated report of the completed run ``state``."""
        view = run_view.view(state)
        root = Path(tempfile.mkdtemp(dir=self.root))
        export.publish(root, state, view, provenance.configured({}, "fake"))
        report = export.read(
            root,
            state["evidence_export"],
            expected_binding=export.binding(state, view["usage"]["accounting"]),
            current=True,
            include=True,
        )
        self.assertEqual("current", report["availability"], report["reasons"])
        return view, report

    def checked(self, body, revision):
        """What `autocode plan --check FILE` prints for an export of ``body`` at ``revision``."""
        import autocode_plan_command as plan_command

        path = self.root / f"check-{revision}.md"
        path.write_text(plan_file.export(body, revision=revision, approved=False))
        code, line = plan_command.check(path)
        self.assertEqual(0, code, line)
        return line

    def test_the_report_and_pull_request_body_cite_the_plan_file_by_goal_hash(self):
        import autocode_issue as issue

        state = plan_file_state()
        loaded, approved = state["contract_history"][1]["body"], state["goal_contract"]["body"]
        goal, file_goal = plan_file.goal_hash(approved), plan_file.goal_hash(loaded)
        self.assertNotEqual(goal, file_goal)
        view, report = self.published(state)
        self.assertEqual(goal, view["approved_contract"]["goal_hash"])
        cited = {"path": PLAN_PATH, "goal_hash": file_goal, "revision": 2, "changed": []}
        self.assertEqual(cited, view["approved_contract"]["plan_file"])
        self.assertEqual(goal, report["document"]["plan"]["goal_hash"])
        self.assertEqual(cited, report["document"]["plan"]["plan_file"])
        lines = [
            GOAL_HASH_LINE.format(goal),
            f"Plan file: `{PLAN_PATH}` (goal hash {file_goal}), loaded as plan revision 2; the approved plan differs "
            "from that file's plan only in the runner's records of the plan's review.",
        ]
        markdown = report["markdown"]
        self.assertIn(f"Agreed plan: {view['approved_contract']['token']}. Completed: ", markdown)
        self.assertIn("\n" + "\n".join(lines) + "\nElapsed wall time", markdown)
        record = {"owner": "acme", "repo": "widgets", "number": 7, "run_dir": str(self.root), "base_commit": "a" * 40}
        body = issue.pr_body(record, {}, " app.py | 1 +", evidence_markdown=markdown)
        for line in lines:
            self.assertIn(line, body)
        # Each hash is the one `autocode plan --check` prints for a file holding that plan.
        self.assertIn(f"goal hash {goal}; ", self.checked(approved, 3))
        self.assertIn(f"goal hash {file_goal}; ", self.checked(loaded, 2))

    def test_the_wording_follows_the_goal_hashes(self):
        loaded = plan_file_state()["contract_history"][1]["body"]
        edited = copy.deepcopy(loaded)
        edited["constraints"].append("Keep refunds idempotent")
        edited["milestones"][0]["objective"] = "Deliver the refund flow"
        for approved, relation in (
            (loaded, "is that file's plan, unchanged."),
            (edited, "differs from that file's plan in constraints, milestones."),
        ):
            with self.subTest(relation=relation):
                state = plan_file_state(copy.deepcopy(approved))
                _, report = self.published(state)
                self.assertIn(
                    f"Plan file: `{PLAN_PATH}` (goal hash {plan_file.goal_hash(loaded)}), loaded as plan revision 2; "
                    f"the approved plan {relation}\n",
                    report["markdown"],
                )

    def test_a_plan_not_loaded_from_a_file_is_cited_by_goal_hash_alone(self):
        state = plan_file_state(cited=False)
        view, report = self.published(state)
        goal = plan_file.goal_hash(state["goal_contract"]["body"])
        self.assertIsNone(view["approved_contract"]["plan_file"])
        self.assertEqual(
            {"goal_hash": goal, "plan_file": None},
            {key: report["document"]["plan"][key] for key in ("goal_hash", "plan_file")},
        )
        self.assertIn("\n" + GOAL_HASH_LINE.format(goal) + "\nElapsed wall time", report["markdown"])
        self.assertNotIn("Plan file:", report["markdown"])

    def test_an_approval_after_the_load_ends_the_citation(self):
        # A plan drafted after an approval is that approval's plan, not the file's.
        state = plan_file_state()
        state["contract_history"][1].update(approval_status="approved", approval_event={"token": "r2:" + "2" * 64})
        view, report = self.published(state)
        self.assertIsNone(view["approved_contract"]["plan_file"])
        self.assertNotIn("Plan file:", report["markdown"])

    def test_a_malformed_plan_file_record_keeps_the_approved_plan_and_its_report(self):
        cases = {
            "path that is not text": lambda draft: draft["plan_file"].update(path=7),
            "goal hash that is not a digest": lambda draft: draft["plan_file"].update(goal_hash="not-a-digest"),
            "no goal hash": lambda draft: draft["plan_file"].pop("goal_hash"),
            "revision that is not a number": lambda draft: draft.update(revision="2"),
            "loaded revision without its plan": lambda draft: draft.pop("body"),
        }
        for name, damage in cases.items():
            with self.subTest(name):
                state = plan_file_state()
                damage(state["contract_history"][1])
                view, report = self.published(state)
                self.assertEqual(state["goal_contract"]["body"], view["approved_contract"]["body"])
                self.assertIsNone(view["approved_contract"]["plan_file"])
                self.assertIsNone(report["document"]["plan"]["plan_file"])
                self.assertIn(
                    GOAL_HASH_LINE.format(plan_file.goal_hash(state["goal_contract"]["body"])), report["markdown"]
                )
                self.assertNotIn("Plan file:", report["markdown"])
        # A view built elsewhere with a malformed citation gets none either, and nothing unbound to its plan.
        goal = "c" * 64
        for approved, expected in (
            (
                {"token": "r1:plan", "goal_hash": goal, "plan_file": {"path": PLAN_PATH}},
                {"goal_hash": goal, "plan_file": None},
            ),
            ({"token": "r9:other", "goal_hash": goal, "plan_file": None}, {}),
            ({"token": "r1:plan", "goal_hash": "short", "plan_file": None}, {}),
        ):
            with self.subTest(approved=approved):
                plan = value({**public_view(), "approved_contract": approved})["plan"]
                self.assertEqual(expected, {key: plan[key] for key in ("goal_hash", "plan_file") if key in plan})

    def test_an_older_report_without_a_goal_hash_renders_unchanged_and_authenticates(self):
        view = public_view()
        older = value(view)
        self.assertFalse({"goal_hash", "plan_file"} & set(older["plan"]))
        view["approved_contract"] = {"token": "r1:plan", "goal_hash": "c" * 64, "plan_file": None}
        newer = value(view)
        rendered = document.render(newer)
        self.assertEqual(rendered.replace(GOAL_HASH_LINE.format("c" * 64) + "\n", ""), document.render(older))
        self.assertNotIn("Plan goal hash", document.render(older))
        anchor = export.write(self.root, older)
        self.assertEqual(
            "current", export.read(self.root, anchor, expected_binding="binding", current=True)["availability"]
        )

    def test_a_malformed_goal_hash_is_refused(self):
        cited = {"path": PLAN_PATH, "goal_hash": "c" * 64, "revision": 2, "changed": []}
        for name, plan in {
            "goal hash": {"goal_hash": "C" * 64, "plan_file": None},
            "plan file's goal hash": {"goal_hash": "c" * 64, "plan_file": {**cited, "goal_hash": "c" * 63}},
            "plan file without the plan's goal hash": {"plan_file": cited},
            "plan file field the report does not define": {"goal_hash": "c" * 64, "plan_file": {**cited, "body": {}}},
        }.items():
            with self.subTest(name):
                report = value()
                report["plan"].update(plan)
                with self.assertRaises(ValueError):
                    document.validate(report)
        report = value()
        report["plan"].update(goal_hash="c" * 64, plan_file=cited)
        document.validate(report)
        # Masking never rewrites a goal hash: it authenticates the plan like the contract token does.
        report["plan"]["goal_hash"] = "sk-" + "a" * 61
        with self.assertRaisesRegex(ValueError, "authentication identifier"):
            document.sanitize(report)


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
            kind="components",
            identity="c",
            status="COMPLETE",
            child_runs=[("api", PublicChild())],
            source_revision="a" * 64,
        )
        run = {
            "report_expected": True,
            "provider_evidence": "fake",
            "components": {
                "exit_code": 0,
                "evidence_report": parent,
                "components": {
                    "api": {
                        "workspace": str(self.root / "api"),
                        "run_dir": str(child_root),
                        "view": {"status": "TASK_COMPLETE", "evidence_report": child},
                    }
                },
            },
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
