"""Planner gates: protected revisions, requirement trace, source refs, milestone order."""

import copy
import tempfile
import unittest
from pathlib import Path

import autocode_goal_lifecycle as lifecycle
import autocode_planning as planning
import autocode_trace_coverage as trace_coverage
import autopilot
from goal_fixtures import body

from . import test_autocode as base

goals = base.goals


def state(task="Build a greeting CLI"):
    return {
        "version": 3,
        "task_id": "task-1",
        "task": task,
        "workspace": "/absent-workspace",
        "settings": {"joint_planning": True, "roles": {"plan_reviewer": {}}},
        "answers": {},
        "user_events": [],
        "acceptance_criteria": [],
    }


class RevisionGuardTests(unittest.TestCase):
    def test_revision_cannot_drop_a_behavior_or_exclusion_or_widen_permissions(self):
        current = state("Print Hello, NAME. Don't touch auth.")
        lifecycle.install_draft(current, body(), origin="glm_draft")
        dropped = body()
        dropped["required_behaviors"] = ["Print a greeting"]
        with self.assertRaisesRegex(ValueError, "without a user-backed"):
            lifecycle.install_draft(current, dropped, origin="glm_revise")
        excluded = body()
        excluded["scope_exclusions"] = ["Web service"]
        with self.assertRaisesRegex(ValueError, "Do not touch auth|without a user-backed"):
            lifecycle.install_draft(current, excluded, origin="glm_revise")
        wider = body()
        wider["permission_boundaries"] = ["May write outside the workspace and call external services"]
        with self.assertRaisesRegex(ValueError, "without a user-backed"):
            lifecycle.install_draft(current, wider, origin="glm_revise")
        self.assertEqual(
            ["Print Hello, NAME for a nonempty name"], current["goal_contract"]["body"]["required_behaviors"]
        )

    def test_a_declared_change_may_wrap_the_item_it_names(self):
        # Live feedback revision (tiny-greeting, 2026-10-02): the item was `required_behaviors: "<text>"`.
        current = state("Print Hello, NAME.")
        lifecycle.install_draft(current, body(), origin="glm_draft")
        feedback = {"kind": "brief_feedback", "id": "feedback-1", "actor": "user_cli", "text": "Add --shout."}
        current["user_events"].append(feedback)
        current["brief_feedback"] = [feedback]
        revised = body()
        revised["required_behaviors"] = ["Print Hello, NAME, or HELLO, NAME with --shout"]
        revised["acceptance_criteria"][0]["criterion"] = "Contract holds, --shout included"
        old = "Print Hello, NAME for a nonempty name"
        changes = [
            {
                "item": f'required_behaviors: "{old}"',
                "change": "reworded",
                "basis": "user_feedback",
                "answer_id": "feedback-1",
                "replacement": revised["required_behaviors"][0],
            },
            {
                "item": "C1.criterion",
                "change": "reworded",
                "basis": "user_feedback",
                "answer_id": "feedback-1",
                "replacement": revised["acceptance_criteria"][0]["criterion"],
            },
        ]
        lifecycle.install_draft(current, revised, origin="glm_revise", changes=changes)
        self.assertEqual([old, "C1"], [row["item"] for row in current["goal_contract"]["declared_changes"]])

    def test_a_reference_to_two_items_is_not_guessed(self):
        current = state("Print Hello, NAME.")
        lifecycle.install_draft(current, body(), origin="glm_draft")
        import autocode_contract_revision as revision

        previous = {**current["goal_contract"]["body"], "acceptance_criteria": [{"id": "C1"}, {"id": "C2"}]}
        rows = revision.canonical_items(previous, [{"item": "C1 and C2"}, {"item": "C2: reworded"}, {"item": "Q1"}])
        self.assertEqual(["C1 and C2", "C2", "Q1"], [row["item"] for row in rows])

    def install_criterion_quoting_a_behavior(self):
        # Review of #259: C1's wording repeats a required behavior, so "C1: '<text>'" quotes both.
        current = state()
        original = body()
        original["acceptance_criteria"][0]["criterion"] = "Print Hello, NAME for a nonempty name"
        lifecycle.install_draft(current, original, origin="glm_draft")
        current["answers"]["Q1"] = {"id": "Q1", "text": "Add --shout."}
        change = {
            "item": "C1: 'Print Hello, NAME for a nonempty name'",
            "change": "reworded",
            "basis": "user_answer",
            "answer_id": "Q1",
            "replacement": "Print Hello, NAME, or HELLO, NAME with --shout",
        }
        return current, original, change

    def test_an_explicit_criterion_id_keeps_its_identity(self):
        current, original, change = self.install_criterion_quoting_a_behavior()
        revised = copy.deepcopy(original)
        revised["acceptance_criteria"][0]["criterion"] = change["replacement"]
        lifecycle.install_draft(current, revised, origin="glm_revise", changes=[change])
        self.assertEqual("C1", current["goal_contract"]["declared_changes"][0]["item"])
        self.assertEqual(
            ["Print Hello, NAME for a nonempty name"], current["goal_contract"]["body"]["required_behaviors"]
        )

    def test_a_criterion_declaration_cannot_authorize_a_behavior_edit(self):
        current, original, change = self.install_criterion_quoting_a_behavior()
        revised = copy.deepcopy(original)
        revised["required_behaviors"] = [change["replacement"]]
        with self.assertRaises(ValueError):
            lifecycle.install_draft(current, revised, origin="glm_revise", changes=[change])

    def test_an_id_beside_another_items_text_is_not_guessed(self):
        import autocode_contract_revision as revision

        previous = {
            "required_behaviors": ["Print Hello, NAME for a nonempty name"],
            "acceptance_criteria": [{"id": "C1", "criterion": "Contract holds"}],
        }
        self.assertEqual(
            ["C1: 'Print Hello, NAME for a nonempty name'"],
            [
                row["item"]
                for row in revision.canonical_items(previous, [{"item": "C1: 'Print Hello, NAME for a nonempty name'"}])
            ],
        )

    def install_with_tab_text(self):
        first = body()
        first["required_behaviors"] = ["Print the fields separated by a literal \\t"]
        first["acceptance_criteria"][0].update(
            criterion="Output is NAME\\tCOUNT", verification_method="Run it and compare with a real\\ttab"
        )
        current = state()
        lifecycle.install_draft(current, first, origin="glm_draft")
        return current

    def test_writing_an_escape_as_its_character_is_not_a_change_of_protected_text(self):
        # A live revision was refused for turning a literal backslash-t into a tab (VALIDATION.md, 2026-09-26).
        current = self.install_with_tab_text()
        revised = body()
        revised["required_behaviors"] = ["Print the fields separated by a literal \t"]
        revised["acceptance_criteria"][0].update(
            criterion="Output is NAME\tCOUNT", verification_method="Run it and compare with a real\ttab"
        )
        lifecycle.install_draft(current, revised, origin="glm_revise")
        saved = current["goal_contract"]["body"]
        # The contract keeps the text the user approved, not the respelling.
        self.assertEqual(["Print the fields separated by a literal \\t"], saved["required_behaviors"])
        self.assertEqual("Output is NAME\\tCOUNT", saved["acceptance_criteria"][0]["criterion"])
        self.assertEqual("Run it and compare with a real\\ttab", saved["acceptance_criteria"][0]["verification_method"])

    def test_a_respelling_that_also_changes_the_meaning_is_still_refused(self):
        current = self.install_with_tab_text()
        for field, value in (
            ("required_behaviors", ["Print the fields separated by a comma"]),
            ("required_behaviors", ["Print the fields separated by a literal \t and a space"]),
        ):
            revised = body()
            revised[field] = list(value)
            revised["acceptance_criteria"][0].update(
                criterion="Output is NAME\\tCOUNT", verification_method="Run it and compare with a real\\ttab"
            )
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "without a user-backed"):
                lifecycle.install_draft(current, revised, origin="glm_revise")
        changed = body()
        changed["required_behaviors"] = ["Print the fields separated by a literal \\t"]
        changed["acceptance_criteria"][0].update(
            criterion="Output is NAME\tCOUNT and a header", verification_method="Run it and compare with a real\\ttab"
        )
        with self.assertRaisesRegex(ValueError, "without a user-backed"):
            lifecycle.install_draft(current, changed, origin="glm_revise")

    def test_user_edit_and_backed_reword_are_allowed(self):
        current = state()
        lifecycle.install_draft(current, body(), origin="glm_draft")
        edited = body()
        edited["required_behaviors"] = ["Print a greeting"]
        lifecycle.install_draft(current, edited, origin="user_cli_edit")
        self.assertEqual(["Print a greeting"], current["goal_contract"]["body"]["required_behaviors"])
        current = state()
        lifecycle.install_draft(current, body(), origin="glm_draft")
        reworded = body()
        reworded["required_behaviors"] = ["Print Hello, NAME"]
        with self.assertRaisesRegex(ValueError, "saved user answer"):
            lifecycle.install_draft(
                current,
                reworded,
                origin="glm_revise",
                changes=[
                    {
                        "item": "Print Hello, NAME for a nonempty name",
                        "change": "reworded",
                        "basis": "agent_proposed",
                        "answer_id": "",
                        "replacement": "Print Hello, NAME",
                    }
                ],
            )
        current["answers"]["Q1"] = {"id": "Q1", "text": "Use the shorter wording"}
        lifecycle.install_draft(
            current,
            reworded,
            origin="glm_revise",
            changes=[
                {
                    "item": "Print Hello, NAME for a nonempty name",
                    "change": "reworded",
                    "basis": "user_answer",
                    "answer_id": "Q1",
                    "replacement": "Print Hello, NAME",
                }
            ],
        )
        self.assertIn("reworded", current["goal_contract"]["declared_changes"][0]["change"])
        self.assertIn("Declared contract changes", lifecycle.render(current))

    def test_plan_review_changes_plan_without_rewriting_protected_requirements(self):
        current = state()
        original = body()
        lifecycle.install_draft(current, original, origin="glm_draft")
        revised = copy.deepcopy(original)
        revised["technical_approach"] = ["Use a smaller parser and run its tests"]
        lifecycle.install_draft(current, revised, origin="glm_revise", changes=[])
        self.assertEqual(original["required_behaviors"], current["goal_contract"]["body"]["required_behaviors"])
        self.assertEqual(original["acceptance_criteria"], current["goal_contract"]["body"]["acceptance_criteria"])
        current["answers"]["Q1"] = {"id": "Q1", "text": "Use the shorter wording"}
        with self.assertRaisesRegex(ValueError, "not changed"):
            lifecycle.install_draft(
                current,
                revised,
                origin="glm_revise",
                changes=[
                    {
                        "item": original["required_behaviors"][0],
                        "change": "reworded",
                        "basis": "user_answer",
                        "answer_id": "Q1",
                        "replacement": "Print Hello, NAME",
                    }
                ],
            )


class TraceTests(unittest.TestCase):
    def test_covered_trace_accepts_whole_id_citations_and_rejects_unknown_ids(self):
        current = state()
        current["requirements_handoff"] = {
            "report": {"requirements": [{"id": "R1", "text": "Greet", "source_quote": "Greet"}]}
        }
        draft = body()
        for evidence in ("C1", draft["required_behaviors"][0], "C1 verifies the requested behavior"):
            goals.check_requirement_trace(
                current,
                {"requirement_trace": [{"requirement_id": "R1", "disposition": "covered", "evidence": evidence}]},
                draft,
            )
        for evidence in (
            "C99 verifies it",
            "C10 verifies it",
            "XC1 verifies it",
            "c1 verifies it",
            "C1 and C99 verify it",
            "It is covered",
        ):
            with self.subTest(evidence=evidence), self.assertRaisesRegex(ValueError, "not covered"):
                goals.check_requirement_trace(
                    current,
                    {"requirement_trace": [{"requirement_id": "R1", "disposition": "covered", "evidence": evidence}]},
                    draft,
                )
        prompt, _ = planning.context(current, "glm_revise", Path("/run/state.json"))
        self.assertIn("AC1 verifies this", prompt)
        self.assertIn("copy required_behaviors", prompt)
        self.assertIn("Reviewer\nconcerns and agent proposals are not saved user authorization", prompt)

    def test_buried_sentence_must_be_quoted_or_ignored(self):
        task = ("Intro. " * 5) + "Reject a name that is only whitespace, exit 2. " + ("Closing. " * 3)
        current = state(task)
        report = {
            "summary": "s",
            "intended_outcome": "o",
            "required_behaviors": ["Greet"],
            "constraints": [],
            "acceptance_tests": ["run"],
            "source_refs": ["task"],
            "proposed_assumptions": [],
            "open_questions": [],
            "requirements": [],
            "ignored_statements": [],
            "conflicts": [],
        }
        with self.assertRaisesRegex(ValueError, "only whitespace"):
            autopilot.apply_planning(current, "requirements_gather", report, {"output": "req.json"})
        report["requirements"] = [
            {
                "id": "R1",
                "text": "Reject whitespace names",
                "source_quote": "Reject a name that is only whitespace, exit 2.",
            }
        ]
        autopilot.apply_planning(current, "requirements_gather", report, {"output": "req.json"})
        draft = body()
        with self.assertRaisesRegex(ValueError, "no trace"):
            autopilot.apply_planning(
                current,
                "astra_discovery",
                {
                    "contract": draft,
                    "summary": "plan",
                    "code_refs": [],
                    "alternatives": [],
                    "uncertainties": [],
                    "contract_changes": [],
                    "requirement_trace": [],
                },
                {"output": "draft.json"},
            )
        autopilot.apply_planning(
            current,
            "astra_discovery",
            {
                "contract": draft,
                "summary": "plan",
                "code_refs": [],
                "alternatives": [],
                "uncertainties": [],
                "contract_changes": [],
                "requirement_trace": [{"requirement_id": "R1", "disposition": "covered", "evidence": "C1"}],
            },
            {"output": "draft.json"},
        )
        self.assertEqual("R1", current["requirements_handoff"]["report"]["requirements"][0]["id"])

    def test_conflict_blocks_until_a_question_or_user_event(self):
        current = state("Use Redis. Actually no additional infrastructure.")
        report = {
            "summary": "s",
            "intended_outcome": "o",
            "required_behaviors": ["Store sessions"],
            "constraints": ["Use Redis.", "No additional infrastructure."],
            "acceptance_tests": ["run"],
            "source_refs": ["task"],
            "proposed_assumptions": [],
            "open_questions": [],
            "ignored_statements": [],
            "conflicts": [
                {"requirement_ids": ["R1", "R2"], "description": "Redis contradicts no extra infrastructure"}
            ],
            "requirements": [
                {"id": "R1", "text": "Use Redis", "source_quote": "Use Redis."},
                {
                    "id": "R2",
                    "text": "No extra infrastructure",
                    "source_quote": "Actually no additional infrastructure.",
                },
            ],
        }
        autopilot.apply_planning(current, "requirements_gather", report, {"output": "req.json"})
        draft = body()
        draft["constraints"] = ["Use Redis.", "No additional infrastructure."]
        payload = {
            "contract": draft,
            "summary": "plan",
            "code_refs": [],
            "alternatives": [],
            "uncertainties": [],
            "contract_changes": [],
            "requirement_trace": [
                {"requirement_id": "R1", "disposition": "covered", "evidence": draft["required_behaviors"][0]},
                {"requirement_id": "R2", "disposition": "covered", "evidence": "C1"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "blocking question"):
            autopilot.apply_planning(current, "astra_discovery", payload, {"output": "draft.json"})
        asked = copy.deepcopy(payload)
        asked["contract"] = body(questions=True)
        asked["contract"]["milestones"] = []
        asked["contract"]["technical_approach"] = []
        asked["contract"]["constraints"] = draft["constraints"]
        autopilot.apply_planning(current, "astra_discovery", asked, {"output": "draft.json"})
        self.assertTrue(current["goal_contract"]["body"]["open_blocking_questions"])

    def test_unrecorded_conflict_resolution_names_what_the_repair_may_resolve(self):
        # #1823: the repair turn sees only the error, so it must say which
        # conflicts are recorded, or that conflict_resolutions must be [].
        requirements = [
            {"id": "R1", "text": "Use Redis", "source_quote": "Use Redis."},
            {"id": "R2", "text": "No extra infrastructure", "source_quote": "Actually no additional infrastructure."},
        ]
        report = {
            "summary": "s",
            "intended_outcome": "o",
            "required_behaviors": ["Store sessions"],
            "constraints": ["Use Redis.", "No additional infrastructure."],
            "acceptance_tests": ["run"],
            "source_refs": ["task"],
            "proposed_assumptions": [],
            "open_questions": [],
            "ignored_statements": [],
            "requirements": requirements,
        }
        draft = body()
        draft["constraints"] = ["Use Redis.", "No additional infrastructure."]
        payload = {
            "contract": draft,
            "summary": "plan",
            "code_refs": [],
            "alternatives": [],
            "uncertainties": [],
            "contract_changes": [],
            "requirement_trace": [
                {"requirement_id": "R1", "disposition": "covered", "evidence": draft["required_behaviors"][0]},
                {"requirement_id": "R2", "disposition": "covered", "evidence": "C1"},
            ],
            "conflict_resolutions": [
                {
                    "requirement_ids": ["R1"],
                    "basis": "user_answer",
                    "answer_id": "Q1",
                    "source_quote": "Use Redis",
                    "resolution": "The answer keeps Redis",
                }
            ],
        }
        for conflicts, expected in (
            ([], r"R1 is not one, and no requirement conflict is recorded .*conflict_resolutions must be \[\]"),
            (
                [{"requirement_ids": ["R2", "R1"], "description": "Redis contradicts"}],
                r"Recorded conflicts: \[R1, R2\]",
            ),
        ):
            with self.subTest(conflicts=conflicts):
                current = state("Use Redis. Actually no additional infrastructure.")
                autopilot.apply_planning(
                    current, "requirements_gather", dict(report, conflicts=conflicts), {"output": "req.json"}
                )
                with self.assertRaisesRegex(ValueError, expected):
                    autopilot.apply_planning(current, "astra_discovery", payload, {"output": "draft.json"})


class ProxyGapTests(unittest.TestCase):
    """A covered criterion proven on an internal value while a note places the user-visible result
    outside this checkout is a stand-in that discharges nothing the user sees (#2068)."""

    def gaps(
        self, requirement, criterion, *, note="The board is rendered by BoardHost, which is outside this checkout"
    ):
        contract = {
            "required_behaviors": ["Show the empty domain list as `--`"],
            "constraints": [note] if note else [],
            "scope_exclusions": ["Web service"],
            "accepted_assumptions": [],
            "acceptance_criteria": [
                {
                    "id": "C1",
                    "criterion": criterion,
                    "verification_method": "Run the board check",
                    "human_review": False,
                }
            ],
        }
        trace = [{"requirement_id": requirement["id"], "disposition": "covered", "evidence": "C1"}]
        return trace_coverage.proxy_gaps([requirement], trace, contract)

    def test_a_proxy_criterion_under_an_external_note_is_one_gap(self):
        requirement = {
            "id": "R1",
            "text": "The board shows the empty domain list as `--`",
            "source_quote": "The board shows the empty domain list as `--`.",
        }
        self.assertEqual(
            [
                {
                    "requirement_id": "R1",
                    "literal": "--",
                    "criteria": ["list_domains returns a non-empty response"],
                }
            ],
            self.gaps(requirement, "list_domains returns a non-empty response"),
        )

    def test_the_same_note_with_a_criterion_that_names_the_visible_literal_is_no_gap(self):
        requirement = {
            "id": "R1",
            "text": "The board shows the empty domain list as `--`",
            "source_quote": "The board shows the empty domain list as `--`.",
        }
        self.assertEqual([], self.gaps(requirement, "The empty domain list renders as `--`"))

    def test_a_proxy_criterion_without_an_external_note_is_no_gap(self):
        requirement = {
            "id": "R1",
            "text": "The board shows the empty domain list as `--`",
            "source_quote": "The board shows the empty domain list as `--`.",
        }
        self.assertEqual([], self.gaps(requirement, "list_domains returns a non-empty response", note=None))

    def test_scope_wording_and_write_discipline_are_no_external_note(self):
        requirement = {
            "id": "R1",
            "text": "The board shows the empty domain list as `--`",
            "source_quote": "The board shows the empty domain list as `--`.",
        }
        for note in (
            "Deployment is outside the scope of this project",
            "Secrets are managed outside this repository",
            "Do not write files outside this workspace",
            "The renderer is outside the scope of this repository",
        ):
            with self.subTest(note=note):
                self.assertEqual([], self.gaps(requirement, "list_domains returns a non-empty response", note=note))

    def test_the_original_incident_phrasing_is_still_a_gap(self):
        requirement = {
            "id": "R1",
            "text": "The board shows the empty domain list as `--`",
            "source_quote": "The board shows the empty domain list as `--`.",
        }
        self.assertEqual(
            [
                {
                    "requirement_id": "R1",
                    "literal": "--",
                    "criteria": ["list_domains returns a non-empty response"],
                }
            ],
            self.gaps(
                requirement, "list_domains returns a non-empty response", note="RegManager is outside this checkout"
            ),
        )

    def test_a_change_refusal_here_is_a_gap_including_the_contracted_forms(self):
        requirement = {
            "id": "R1",
            "text": "The board shows the empty domain list as `--`",
            "source_quote": "The board shows the empty domain list as `--`.",
        }
        gap = [{"requirement_id": "R1", "literal": "--", "criteria": ["list_domains returns a non-empty response"]}]
        for note in (
            "The renderer can't be changed here",
            "The renderer can\u2019t be changed here",
            "The renderer cannot be changed here",
            "The renderer won't be changed here",
        ):
            with self.subTest(note=note):
                self.assertEqual(gap, self.gaps(requirement, "list_domains returns a non-empty response", note=note))

    def test_a_component_owned_by_another_repository_is_a_gap(self):
        requirement = {
            "id": "R1",
            "text": "The board shows the empty domain list as `--`",
            "source_quote": "The board shows the empty domain list as `--`.",
        }
        gap = [{"requirement_id": "R1", "literal": "--", "criteria": ["list_domains returns a non-empty response"]}]
        for note in (
            "The renderer lives in another repository",
            "The renderer is owned by a different repo",
            "The renderer is part of a separate service",
        ):
            with self.subTest(note=note):
                self.assertEqual(gap, self.gaps(requirement, "list_domains returns a non-empty response", note=note))

    def test_the_note_matcher_takes_explicit_statements_only(self):
        from autocode_trace_coverage import places_outside_checkout as places

        for note in (
            "RegManager, which renders the list, is outside this checkout",
            "The renderer can't be changed here",
            "The renderer cannot be modified in this repository",
            "AssetPanel lives in another repository",
            "The renderer is not in this repository",
        ):
            with self.subTest(note=note):
                self.assertTrue(places(note))
        for note in (
            "This repository uses external dependencies",
            "This repository calls an external API",
            "Dependencies of this repository are external and pinned",
            "Do not modify anything outside this checkout",
            "Do not write files outside this workspace",
            "This workspace is beyond the scope of this project",
            "Secrets are managed outside this repository",
            "Python standard library only",
        ):
            with self.subTest(note=note):
                self.assertFalse(places(note))

    def test_a_non_string_requirement_text_is_ignored(self):
        requirement = {"id": "R1", "text": 7}
        self.assertEqual([], self.gaps(requirement, "list_domains returns a non-empty response"))

    def test_a_second_differently_named_proxy_is_a_gap(self):
        requirement = {
            "id": "R2",
            "text": "The asset panel prints `[]` for no assets",
            "source_quote": "The asset panel prints `[]` for no assets.",
        }
        contract = {
            "required_behaviors": ["Print the empty asset list as `[]`"],
            "constraints": [],
            "scope_exclusions": [],
            "accepted_assumptions": [
                {"text": "AssetPanel renders the list and is outside this repository", "basis": "agent_proposed"}
            ],
            "acceptance_criteria": [
                {"id": "C7", "criterion": "assetctl list returns rows", "verification_method": "run assetctl"}
            ],
        }
        trace = [{"requirement_id": "R2", "disposition": "covered", "evidence": "C7"}]
        self.assertEqual(
            [{"requirement_id": "R2", "literal": "[]", "criteria": ["assetctl list returns rows"]}],
            trace_coverage.proxy_gaps([requirement], trace, contract),
        )

    def test_a_requirement_without_a_literal_is_no_gap(self):
        requirement = {
            "id": "R1",
            "text": "The board lists the registered domains",
            "source_quote": "The board lists the registered domains.",
        }
        self.assertEqual([], self.gaps(requirement, "list_domains returns a non-empty response"))


class PlanEvidenceTests(unittest.TestCase):
    def test_existing_source_must_be_cited_and_overlap_needs_an_edge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "api.py").write_text("def route():\n    return 1\n")
            current = state("Keep the current modular layout")
            current["workspace"] = str(root)
            draft = body()
            payload = {
                "contract": draft,
                "summary": "replatform",
                "code_refs": [],
                "alternatives": [],
                "uncertainties": [],
                "contract_changes": [],
                "requirement_trace": [],
            }
            with self.assertRaisesRegex(ValueError, "code_refs"):
                autopilot.apply_planning(current, "astra_discovery", payload, {"output": "draft.json"})
            payload["code_refs"] = ["api.py:1"]
            autopilot.apply_planning(current, "astra_discovery", payload, {"output": "draft.json"})
        hidden = body()
        hidden["milestones"] = [
            {
                "id": "M1",
                "objective": "Parser",
                "acceptance_criteria": ["C1"],
                "depends_on": [],
                "affected_paths": ["shared.py"],
            },
            {
                "id": "M2",
                "objective": "Caller",
                "acceptance_criteria": ["C1"],
                "depends_on": [],
                "affected_paths": ["shared.py"],
            },
        ]
        # Overlap stays a valid plan. Dispatch runs those milestones one at a time
        # instead of treating missing order as a planning error.
        lifecycle.validate_body(state(), hidden)


if __name__ == "__main__":
    unittest.main()
