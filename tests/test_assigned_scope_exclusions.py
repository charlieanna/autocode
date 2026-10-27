"""Core-owned edit exclusions survive model aliases without dropping any work."""

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_planning_metadata as metadata
import autocode_scope_assignment as assignment
import autocode_scope_question as scope


class AssignedScopeExclusionsTests(unittest.TestCase):
    def inputs(self):
        parts = [
            {"target": "tax", "work": "Apply 10% and preserve zero"},
            {"target": "checkout", "work": "Format the USD receipt"},
            {"target": "tax repository", "work": "Verify the actual tax service in the joint run"},
            {"target": "checkout repository", "work": "Verify the actual composed checkout output"},
        ]
        run = str(Path("/fixture/history/run").resolve())
        state = {"run_dir": run, "workspace_scope": {"turn": 0, "spans_repositories": True, "external_parts": parts}}
        peers = {"tax": "/fixture/tax/run", "checkout": "/fixture/checkout/run"}
        receipt = {
            "identity": assignment.scope.identity(state),
            "members": {part["target"]: peers[part["target"].removesuffix(" repository")] for part in parts},
            "aliases": {"tax repository": "tax", "checkout repository": "checkout"},
        }
        record = {
            "automatic_anchor": "/fixture/anchor",
            "source": {"workspace": "/fixture/history", "run_dir": run},
            "members": [
                {"target": target, "workspace": "/fixture/" + target, "run_dir": path} for target, path in peers.items()
            ],
            "scope_assignments": {run: receipt},
        }
        report = {
            "contract": {
                "scope_exclusions": ["Do not change deployment"],
                "required_behaviors": ["Real joint proof remains required"],
            }
        }
        return state, report, record, parts

    def test_alternating_live_alias_reports_keep_every_exact_assigned_work_item(self):
        for selected in ((0, 1), (2, 3)):
            with self.subTest(selected=selected):
                state, report, record, parts = self.inputs()
                report["contract"]["scope_exclusions"] += [scope.exclusion(parts[i]) for i in selected]
                original = copy.deepcopy((state, report, record))
                with patch.object(assignment.store, "find", return_value=record):
                    result = metadata.expand_report(state, "astra_discovery", report)
                    scope.check(state, result["contract"])
                    self.assertEqual(result, metadata.expand_report(state, "astra_discovery", result))
                self.assertEqual(original, (state, report, record))
                self.assertEqual(5, len(result["contract"]["scope_exclusions"]))
                self.assertEqual(
                    set(scope.exclusion(part) for part in parts), set(result["contract"]["scope_exclusions"][1:])
                )
                self.assertEqual(report["contract"]["required_behaviors"], result["contract"]["required_behaviors"])

    def test_unassigned_stale_wrong_member_and_forged_aliases_cannot_supply_exclusions(self):
        for invalid in ("unassigned", "manual", "stale", "wrong-run", "forged-alias", "missing-peer"):
            with self.subTest(invalid=invalid):
                state, report, record, _ = self.inputs()
                receipt = record["scope_assignments"][state["run_dir"]]
                if invalid == "unassigned":
                    record["scope_assignments"] = {}
                elif invalid == "manual":
                    record.pop("automatic_anchor")
                elif invalid == "stale":
                    receipt["identity"] = "older-scope"
                elif invalid == "wrong-run":
                    receipt["members"]["tax"] = "/fixture/unknown/run"
                elif invalid == "forged-alias":
                    receipt["aliases"]["tax repository"] = "unregistered"
                elif invalid == "missing-peer":
                    record["members"].pop()
                with patch.object(assignment.store, "find", return_value=record):
                    self.assertEqual(report, metadata.expand_report(state, "astra_discovery", report))
                    with self.assertRaisesRegex(ValueError, "scope decision"):
                        scope.check(state, report["contract"])

    def test_explicit_personal_scope_choice_still_requires_authored_exclusions(self):
        state, report, record, _ = self.inputs()
        question = scope.question(state)
        event = {"actor": "user_cli", "question": question, "text": scope.THIS_REPOSITORY}
        state.update(answers={question["id"]: event}, user_events=[event])
        with patch.object(assignment.store, "find", return_value=record):
            self.assertEqual(report, metadata.expand_report(state, "astra_discovery", report))
            with self.assertRaisesRegex(ValueError, "does not cover tax"):
                scope.check(state, report["contract"])

    def test_malformed_or_absent_exclusion_lists_are_not_repaired(self):
        for malformed in (None, "tax", [None], ["existing", {}]):
            state, report, record, _ = self.inputs()
            report["contract"]["scope_exclusions"] = malformed
            with patch.object(assignment.store, "find", return_value=record):
                self.assertEqual(report, metadata.expand_report(state, "astra_discovery", report))
        state, report, record, _ = self.inputs()
        report["contract"].pop("scope_exclusions")
        with patch.object(assignment.store, "find", return_value=record):
            self.assertEqual(report, metadata.expand_report(state, "astra_discovery", report))
