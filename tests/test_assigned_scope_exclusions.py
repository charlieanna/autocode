"""Core-owned edit exclusions survive model aliases without dropping any work."""

import copy
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_planning_metadata as metadata
import autocode_scope_assignment as assignment
import autocode_scope_question as scope
import autocode_workspace_scope_state as workspace_scope
from goal_fixtures import body
from units import autoplanner as planner


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


class AuthorizedBoundaryRewordTests(unittest.TestCase):
    """A person-approved exact reword moves one authenticated boundary (#2467).

    The original assignment said one peer owns calculation and serialization; the
    person's saved answer corrects the same peer's boundary to numeric serialization
    while the separately discovered peer keeps its own boundary. Superseded text must
    not resurrect during expansion, before the revision guard validates the report,
    and the correction must survive replans and reopen while its scope and assignment
    stay the same.
    """

    def member(self):
        parts = [
            {"target": "peer-original", "work": "calculation and serialization"},
            {"target": "peer-third", "work": "calculation"},
        ]
        run = str(Path("/fixture/member/run").resolve())
        state = {
            "version": 3,
            "task_id": "task-1",
            "task": "Member side of a coordinated fix",
            "workspace": "/absent-workspace",
            "run_dir": run,
            "settings": {"joint_planning": True, "roles": {"plan_reviewer": {}}},
            "answers": {
                "a1": {
                    "id": "a1",
                    "actor": "user_cli",
                    "question": {"id": "ownership"},
                    "text": "calculation belongs to peer-third; peer-original keeps numeric serialization",
                }
            },
            "user_events": [],
            "brief_feedback": [],
            "acceptance_criteria": [],
            "workspace_scope": {"turn": 0, "spans_repositories": True, "external_parts": parts, "dropped": []},
            "contract_history": [],
        }
        peers = {"peer-original": "/fixture/peer-original/run", "peer-third": "/fixture/peer-third/run"}
        record = {
            "automatic_anchor": "/fixture/anchor",
            "source": {"workspace": "/fixture/member", "run_dir": run},
            "members": [
                {"target": target, "workspace": "/fixture/" + target, "run_dir": path} for target, path in peers.items()
            ],
            "scope_assignments": {run: {"identity": workspace_scope.identity(state), "members": peers, "aliases": {}}},
        }
        old = scope.exclusion(parts[0])
        third = scope.exclusion(parts[1])
        corrected = f"{scope.EXCLUSION_PREFIX}peer-original: numeric serialization"
        return state, record, old, third, corrected

    def install_first_draft(self, state, record, old, third):
        first = body()
        first["scope_exclusions"] = [old, third]
        with patch.object(assignment.store, "find", return_value=record):
            lifecycle.install_draft(state, first, origin="glm_draft")
        self.assertEqual([old, third], state["goal_contract"]["body"]["scope_exclusions"])

    def report(self, listed, rows):
        contract = body()
        contract["scope_exclusions"] = listed
        return {"contract": contract, "contract_changes": rows}

    def row(self, item, *, change="reworded", basis="user_answer", answer_id="a1", replacement):
        return {"item": item, "change": change, "basis": basis, "answer_id": answer_id, "replacement": replacement}

    def packet(self, state, stage="glm_revise"):
        prompt, _ = planner.context(state, stage, Path("/tmp/state.json"))
        decoder = json.JSONDecoder()
        for found in re.finditer(r'\{"', prompt):
            try:
                value, _ = decoder.raw_decode(prompt, found.start())
            except ValueError:
                continue
            if isinstance(value, dict) and "scope_exclusions_required" in value:
                return value
        return None

    def planning_pass(self, state, stage, origin, report):
        """The production order for one planner report: expand, gate, install."""
        expanded = metadata.expand_report(state, stage, report)
        goals.check_requirement_trace(state, expanded, expanded["contract"])
        lifecycle.install_draft(state, expanded["contract"], origin=origin, changes=expanded["contract_changes"])
        return expanded

    def test_exact_user_backed_reword_moves_the_boundary_through_the_planning_path(self):
        state, record, old, third, corrected = self.member()
        self.install_first_draft(state, record, old, third)
        report = self.report([corrected, third], [self.row(old, replacement=corrected)])
        original = copy.deepcopy(report)
        with patch.object(assignment.store, "find", return_value=record):
            expanded = self.planning_pass(state, "glm_revise", "glm_revise", report)
            self.assertEqual([corrected, third], expanded["contract"]["scope_exclusions"])
            self.assertEqual(expanded, metadata.expand_report(state, "glm_revise", expanded))
            self.assertEqual(1, len(state[scope.CORRECTIONS_KEY]))
            self.assertEqual([corrected, third], scope.effective_exclusions(state))
            later = metadata.expand_report(state, "glm_revise", self.report([third], []))
            self.assertEqual([third, corrected], later["contract"]["scope_exclusions"])
            scope.check(state, later["contract"])
            packet = self.packet(state)
        self.assertEqual(original, report)  # the provider's report is never rewritten
        self.assertEqual([corrected, third], state["goal_contract"]["body"]["scope_exclusions"])
        self.assertEqual([old], [row["item"] for row in state["goal_contract"]["declared_changes"]])
        self.assertEqual([corrected, third], packet["scope_exclusions_required"])

    def test_discovery_correction_after_a_saved_answer_installs_and_persists(self):
        # The live failing path (#2467): the person's answer restarts discovery, whose
        # report the revision guard never judges for an unapproved previous contract.
        state, record, old, third, corrected = self.member()
        self.install_first_draft(state, record, old, third)
        report = self.report([corrected, third], [self.row(old, replacement=corrected)])
        with patch.object(assignment.store, "find", return_value=record):
            expanded = self.planning_pass(state, "astra_discovery", "glm_draft", report)
            self.assertEqual([corrected, third], expanded["contract"]["scope_exclusions"])
            receipt = state[scope.CORRECTIONS_KEY][0]
            self.assertEqual((old, corrected), (receipt["item"], receipt["replacement"]))
            # the next planner packet has no candidate rows: the receipt alone holds
            self.assertEqual([corrected, third], scope.effective_exclusions(state))
            self.assertEqual([corrected, third], self.packet(state)["scope_exclusions_required"])
            final = self.report([third], [])
            expanded_final = metadata.expand_report(state, "astra_finalize", final)
            self.assertEqual([third, corrected], expanded_final["contract"]["scope_exclusions"])
            lifecycle.install_draft(state, expanded_final["contract"], origin="astra_finalize")
            self.assertEqual([third, corrected], state["goal_contract"]["body"]["scope_exclusions"])
            self.assertEqual([corrected, third], scope.effective_exclusions(state))
            # a JSON serialization round-trip, the persistence format of state.json,
            # keeps the receipt and its correction (same-process reload, not a new run)
            reloaded = json.loads(json.dumps(state))
            self.assertEqual([corrected, third], scope.effective_exclusions(reloaded))
            with patch.object(assignment.store, "find", return_value=record):
                self.assertEqual([corrected, third], scope.effective_exclusions(reloaded))

    def test_unbacked_discovery_declarations_retain_the_original_and_record_nothing(self):
        # The corrected text IS carried by the report, so only the missing authority
        # can refuse it; the discovery installation path itself proves no receipt.
        for basis, answer_id in (("agent_proposed", ""), ("user_answer", "ghost")):
            with self.subTest(basis=basis, answer_id=answer_id):
                state, record, old, third, corrected = self.member()
                self.install_first_draft(state, record, old, third)
                rows = [self.row(old, basis=basis, answer_id=answer_id, replacement=corrected)]
                report = self.report([corrected, third], rows)
                with patch.object(assignment.store, "find", return_value=record):
                    expanded = self.planning_pass(state, "astra_discovery", "glm_draft", report)
                    self.assertIn(old, expanded["contract"]["scope_exclusions"])
                    self.assertEqual([old, third], scope.effective_exclusions(state))
                    self.assertEqual([old, third], self.packet(state)["scope_exclusions_required"])
                self.assertFalse(state.get(scope.CORRECTIONS_KEY))

    def test_declared_removal_with_a_replacement_never_becomes_a_correction(self):
        # A removed or other-typed row that carries valid authority and the corrected
        # text still records nothing: only a declared reword can move a boundary.
        for change in ("removed", "permission_changed"):
            with self.subTest(change=change):
                state, record, old, third, corrected = self.member()
                self.install_first_draft(state, record, old, third)
                rows = [
                    {"item": old, "change": change, "basis": "user_answer", "answer_id": "a1", "replacement": corrected}
                ]
                report = self.report([corrected, third], rows)
                with patch.object(assignment.store, "find", return_value=record):
                    expanded = self.planning_pass(state, "astra_discovery", "glm_draft", report)
                    self.assertIn(old, expanded["contract"]["scope_exclusions"])
                    self.assertEqual([old, third], scope.effective_exclusions(state, report["contract"], rows))
                    self.assertEqual([old, third], self.packet(state)["scope_exclusions_required"])
                self.assertFalse(state.get(scope.CORRECTIONS_KEY))

    def test_malformed_declaration_rows_change_nothing(self):
        state, record, old, third, corrected = self.member()
        self.install_first_draft(state, record, old, third)
        report = self.report([third], ["a bare string", 42, None])
        with patch.object(assignment.store, "find", return_value=record):
            expanded = metadata.expand_report(state, "glm_revise", report)
            self.assertIn(old, expanded["contract"]["scope_exclusions"])

    def test_an_accumulated_scope_corrects_every_instance_of_one_boundary(self):
        # record() accumulates: the old wording and the freshly grounded corrected
        # wording of one peer sit beside the other peer's own boundary.
        state, record, old, third, corrected = self.member()
        parts = state["workspace_scope"]["external_parts"]
        state["workspace_scope"] = {}
        workspace_scope.record(state, {"external_parts": [parts[0]]}, {}, stage="requirements_gather")
        workspace_scope.record(
            state,
            {"external_parts": [{"target": "peer-original", "work": "numeric serialization"}, parts[1]]},
            {},
            stage="requirements_gather",
        )
        receipt = record["scope_assignments"][state["run_dir"]]
        receipt["identity"] = workspace_scope.identity(state)
        with patch.object(assignment.store, "find", return_value=record):
            first = body()
            first["scope_exclusions"] = [old, corrected, third]
            lifecycle.install_draft(state, first, origin="glm_draft")
            # one boundary, two accumulated instances of its text: both move together
            self.planning_pass(
                state,
                "glm_revise",
                "glm_revise",
                self.report([corrected, third], [self.row(old, replacement=corrected)]),
            )
            self.assertEqual([corrected, corrected, third], scope.effective_exclusions(state))
            state["answers"]["a2"] = {
                "id": "a2",
                "actor": "user_cli",
                "question": {"id": "ownership"},
                "text": "totals too",
            }
            newer = f"{scope.EXCLUSION_PREFIX}peer-original: numeric serialization of totals"
            self.planning_pass(
                state,
                "glm_revise",
                "glm_revise",
                self.report([newer, third], [self.row(corrected, answer_id="a2", replacement=newer)]),
            )
            self.assertEqual([newer, newer, third], scope.effective_exclusions(state))
            self.assertEqual([newer, third], self.packet(state)["scope_exclusions_required"])
            retained = metadata.expand_report(state, "glm_revise", self.report([third], []))
            self.assertEqual([third, newer], retained["contract"]["scope_exclusions"])
        self.assertEqual(2, len(state[scope.CORRECTIONS_KEY]))

    def test_a_second_authorized_reword_of_the_corrected_text_follows_the_chain(self):
        state, record, old, third, corrected = self.member()
        self.install_first_draft(state, record, old, third)
        with patch.object(assignment.store, "find", return_value=record):
            self.planning_pass(
                state,
                "glm_revise",
                "glm_revise",
                self.report([corrected, third], [self.row(old, replacement=corrected)]),
            )
            state["answers"]["a2"] = {
                "id": "a2",
                "actor": "user_cli",
                "question": {"id": "ownership"},
                "text": "also totals",
            }
            recorrected = f"{scope.EXCLUSION_PREFIX}peer-original: numeric serialization of totals"
            report = self.report([recorrected, third], [self.row(corrected, answer_id="a2", replacement=recorrected)])
            expanded = self.planning_pass(state, "glm_revise", "glm_revise", report)
            self.assertEqual([recorrected, third], expanded["contract"]["scope_exclusions"])
            # repeated replans and a reopen in the same scope keep the chained correction
            self.assertEqual([recorrected, third], scope.effective_exclusions(state))
            replanned = metadata.expand_report(state, "glm_revise", self.report([third], []))
            self.assertEqual([third, recorrected], replanned["contract"]["scope_exclusions"])

    def test_replacement_must_name_the_same_peer_target(self):
        state, record, old, third, corrected = self.member()
        self.install_first_draft(state, record, old, third)
        # moving the boundary onto the other peer is a scope decision, not this reword
        report = self.report([corrected, third], [self.row(old, replacement=third)])
        with patch.object(assignment.store, "find", return_value=record):
            expanded = metadata.expand_report(state, "glm_revise", report)
            self.assertIn(old, expanded["contract"]["scope_exclusions"])
            self.assertEqual(
                [old, third], scope.effective_exclusions(state, expanded["contract"], report["contract_changes"])
            )
            with self.assertRaisesRegex(ValueError, "contract_changes names"):
                lifecycle.install_draft(
                    state, expanded["contract"], origin="glm_revise", changes=expanded["contract_changes"]
                )
        self.assertFalse(state.get(scope.CORRECTIONS_KEY))

    def test_unbacked_or_stale_revision_bases_keep_the_original_enforced(self):
        for basis, answer_id in (
            ("agent_proposed", ""),
            ("user_answer", "ghost"),
            ("user_feedback", "f1"),
            ("delegated", "a1"),
        ):
            with self.subTest(basis=basis, answer_id=answer_id):
                state, record, old, third, corrected = self.member()
                self.install_first_draft(state, record, old, third)
                rows = [self.row(old, basis=basis, answer_id=answer_id, replacement=corrected)]
                report = self.report([corrected, third], rows)
                with patch.object(assignment.store, "find", return_value=record):
                    expanded = metadata.expand_report(state, "glm_revise", report)
                    self.assertIn(old, expanded["contract"]["scope_exclusions"])
                    with self.assertRaisesRegex(ValueError, "does not cover peer-original"):
                        scope.check(state, report["contract"], changes=rows)

    def test_removal_missing_or_absent_replacement_never_suppresses(self):
        def missing_replacement(old, corrected, third):
            return {
                "item": old,
                "change": "reworded",
                "basis": "user_answer",
                "answer_id": "a1",
                "replacement": "",
            }

        cases = (
            ("removed", lambda old, corrected, third: self.row(old, change="removed", replacement="")),
            ("missing replacement", missing_replacement),
        )
        for label, row in cases:
            with self.subTest(case=label):
                state, record, old, third, corrected = self.member()
                self.install_first_draft(state, record, old, third)
                rows = [row(old, corrected, third)]
                report = self.report([third, corrected], rows)
                with patch.object(assignment.store, "find", return_value=record):
                    expanded = metadata.expand_report(state, "glm_revise", report)
                    self.assertIn(old, expanded["contract"]["scope_exclusions"])
                    self.assertEqual([old, third], scope.effective_exclusions(state, report["contract"], rows))
        # a replacement the report's own contract does not carry is not adopted
        state, record, old, third, corrected = self.member()
        self.install_first_draft(state, record, old, third)
        rows = [self.row(old, replacement=corrected)]
        report = self.report([third], rows)
        with patch.object(assignment.store, "find", return_value=record):
            expanded = metadata.expand_report(state, "glm_revise", report)
            self.assertIn(old, expanded["contract"]["scope_exclusions"])
            self.assertEqual([old, third], scope.effective_exclusions(state, report["contract"], rows))
        self.assertFalse(state.get(scope.CORRECTIONS_KEY))

    def test_installed_receipts_bind_to_the_scope_and_assignment_they_corrected(self):
        state, record, old, third, corrected = self.member()
        self.install_first_draft(state, record, old, third)
        with patch.object(assignment.store, "find", return_value=record):
            self.planning_pass(
                state,
                "glm_revise",
                "glm_revise",
                self.report([corrected, third], [self.row(old, replacement=corrected)]),
            )
        self.assertEqual(1, len(state[scope.CORRECTIONS_KEY]))

        # a replaced assignment: same scope identity, the peer's run moved elsewhere
        replaced = copy.deepcopy(record)
        replaced["scope_assignments"][state["run_dir"]]["members"]["peer-original"] = "/fixture/peer-original/other-run"
        replaced["members"] = [
            {**row, "run_dir": "/fixture/peer-original/other-run"} if row["target"] == "peer-original" else row
            for row in replaced["members"]
        ]
        with patch.object(assignment.store, "find", return_value=replaced):
            self.assertEqual([old, third], scope.effective_exclusions(state))
            retained = metadata.expand_report(state, "glm_revise", self.report([third], []))
            self.assertIn(old, retained["contract"]["scope_exclusions"])

        # a malformed, wrong-digest, wrong-identity or wrong-turn receipt never waives
        with patch.object(assignment.store, "find", return_value=record):
            good = state[scope.CORRECTIONS_KEY][0]
            for forged in (
                {**good, "assignment": "wrong"},
                {**good, "scope_identity": "wrong"},
                {**good, "turn": 7},
                {"item": corrected, "replacement": "Not covered: changes in peer-original: fabricated"},
            ):
                with self.subTest(forged=sorted(forged)):
                    state[scope.CORRECTIONS_KEY] = [forged]
                    self.assertEqual([old, third], scope.effective_exclusions(state))
            state[scope.CORRECTIONS_KEY] = [good]
            # the same scope, reopened exactly, keeps the accepted correction
            self.assertEqual([corrected, third], scope.effective_exclusions(state))

        # a renewed turn or a changed scope renews the authenticated scope: even with a
        # freshly issued, valid assignment for the renewed identity, the old receipt is
        # inert and the original boundary is what the next plan must carry
        for renewed, turns, extra_member in (
            (
                {
                    "turn": 1,
                    "spans_repositories": True,
                    "external_parts": state["workspace_scope"]["external_parts"],
                    "dropped": [],
                },
                ["a follow-up turn"],
                None,
            ),
            (
                {
                    "turn": 0,
                    "spans_repositories": True,
                    "external_parts": [
                        *state["workspace_scope"]["external_parts"],
                        {"target": "peer-fourth", "work": "packaging"},
                    ],
                    "dropped": [],
                },
                [],
                {"target": "peer-fourth", "workspace": "/fixture/peer-fourth", "run_dir": "/fixture/peer-fourth/run"},
            ),
        ):
            with self.subTest(renewed=renewed["turn"] or "changed parts"):
                fresh_state = copy.deepcopy(state)
                fresh_state["workspace_scope"] = renewed
                fresh_state["turns"] = turns
                reissued = copy.deepcopy(record)
                reissued["scope_assignments"][fresh_state["run_dir"]]["identity"] = workspace_scope.identity(
                    fresh_state
                )
                if extra_member:
                    reissued["members"].append(extra_member)
                    reissued["scope_assignments"][fresh_state["run_dir"]]["members"]["peer-fourth"] = extra_member[
                        "run_dir"
                    ]
                with patch.object(assignment.store, "find", return_value=reissued):
                    originals = [scope.exclusion(part) for part in renewed["external_parts"]]
                    self.assertEqual(originals, scope.effective_exclusions(fresh_state))
                    self.assertEqual([old, third], originals[:2])
                    with self.assertRaisesRegex(ValueError, "does not cover peer-original"):
                        scope.check(fresh_state, self.report([third], [])["contract"])

    def test_manual_personal_scope_choice_is_untouched_by_correction_rows(self):
        state, record, old, third, corrected = self.member()
        question = scope.question(state)
        event = {"actor": "user_cli", "question": question, "text": scope.THIS_REPOSITORY}
        state.update(answers={question["id"]: event, **state.pop("answers")}, user_events=[event])
        rows = [self.row(old, replacement=corrected)]
        report = self.report(["Do not change deployment"], rows)
        with patch.object(assignment.store, "find", return_value=record):
            self.assertEqual(report, metadata.expand_report(state, "astra_discovery", report))
            with self.assertRaisesRegex(ValueError, "does not cover peer-original"):
                scope.check(state, report["contract"])
