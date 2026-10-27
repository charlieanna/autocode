"""Proof corrections in an unapproved plan do not change the agreed behavior."""

import copy
import unittest

import autocode_contract_revision as revision
import autocode_rewording_question as rewording
from autocode_goals import revision_guard


class DraftVerificationRevisionTests(unittest.TestCase):
    def inputs(self, **contract_fields):
        body = {
            "acceptance_criteria": [
                {
                    "id": "AC5",
                    "criterion": "The complete project suite passes.",
                    "verification_method": "Run python3 -m unittest nonexistent_test.py",
                    "human_review": False,
                }
            ],
            "required_behaviors": ["Preserve the current output exactly"],
            "scope_exclusions": [],
            "constraints": [],
            "important_failure_cases": [],
            "permission_boundaries": ["No network"],
        }
        contract = {"body": body, "approval_status": "draft", "approval_event": None, **contract_fields}
        state = {"goal_contract": contract}
        after = copy.deepcopy(body)
        after["acceptance_criteria"][0]["verification_method"] = "Run python3 -m unittest discover -s tests -t ."
        return state, after

    def test_unapproved_proof_can_be_corrected_without_changing_the_behavior(self):
        state, after = self.inputs()
        before = copy.deepcopy(state)
        revision_guard(state, after, [], "glm_revise")
        self.assertEqual(before, state)

    def follow_up_draft(self):
        state, _ = self.inputs()
        contract = state["goal_contract"]
        contract["origin"] = "astra_discovery"
        approved = copy.deepcopy(contract)
        approved.update(approval_status="approved", revision=6, hash="older")
        state["contract_history"] = [approved]
        contract["body"]["acceptance_criteria"].append(
            {
                "id": "AC8",
                "criterion": "Given source [3, -1, 4], sum_positive(x for x in source, start=1) returns 8.",
                "verification_method": "test: test_ac8_generator",
                "human_review": False,
                "sources": [{"source_id": "feedback:new", "quote": "Preserve generator input support."}],
            }
        )
        after = copy.deepcopy(contract["body"])
        after["acceptance_criteria"][-1]["criterion"] = (
            "Given source [3, -1, 4], sum_positive((x for x in source), start=1) returns 8."
        )
        return state, after

    def test_older_approval_does_not_make_a_new_draft_criterion_approved(self):
        state, after = self.follow_up_draft()
        before = copy.deepcopy(state)
        self.assertEqual([], revision_guard(state, after, [], "glm_revise"))
        self.assertEqual(before, state)

    def test_new_criterion_correction_keeps_approved_ids_and_source_quotes_protected(self):
        for kind in (
            "old_id",
            "lost_quote",
            "approved_now",
            "user_edited",
            "current_user_edit",
            "incomplete_history",
            "malformed_history",
            "changed_proof",
        ):
            state, after = self.follow_up_draft()
            if kind == "old_id":
                after["acceptance_criteria"][0]["criterion"] = "Different previously approved behavior."
            elif kind == "lost_quote":
                after["acceptance_criteria"][-1]["sources"] = []
            elif kind == "approved_now":
                state["goal_contract"]["approval_status"] = "approved"
            elif kind == "user_edited":
                state["contract_history"].append({**copy.deepcopy(state["goal_contract"]), "origin": "user_cli_edit"})
            elif kind == "current_user_edit":
                state["goal_contract"]["origin"] = "user_cli_edit"
            elif kind == "incomplete_history":
                state["contract_history"].append({"approval_status": "approved"})
            elif kind == "malformed_history":
                state["contract_history"].append({"approval_status": "approved", "body": ["invalid"]})
            else:
                after["acceptance_criteria"][-1]["verification_method"] = "Run any aggregate command"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                revision_guard(state, after, [], "glm_revise")

    def test_a_declared_agent_proof_correction_is_accepted_before_approval(self):
        state, after = self.inputs()
        changes = [
            {
                "item": "AC5",
                "change": "reworded",
                "basis": "agent_proposed",
                "answer_id": "",
                "replacement": after["acceptance_criteria"][0]["verification_method"],
            }
        ]
        revision_guard(state, after, changes, "astra_finalize")

    def test_malformed_draft_guard_selector_can_be_repaired_without_weakening_coverage(self):
        state, after = self.inputs()
        old = state["goal_contract"]["body"]["acceptance_criteria"][0]
        old["criterion"] = "Retain all three existing tests and their assertions."
        old["verification_method"] = "guard: retain all three existing tests"
        after["acceptance_criteria"][0] = {**old, "verification_method": "guard: test_ac5_all_existing_behaviors"}
        retained = copy.deepcopy((state, after))
        revision_guard(state, after, [], "astra_finalize")
        self.assertEqual(retained, (state, after))
        after["acceptance_criteria"][0]["verification_method"] = "Run python3 -m unittest discover"
        with self.assertRaises(ValueError):
            revision_guard(state, after, [], "astra_finalize")

    def test_guard_selector_correction_keeps_approved_and_user_authored_proofs_protected(self):
        for protection in ({"approval_status": "approved"}, {"origin": "user_cli_edit"}):
            with self.subTest(protection=protection):
                state, after = self.inputs(**protection)
                state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"] = (
                    "guard: test_ac5_original"
                )
                after["acceptance_criteria"][0]["verification_method"] = "guard: test_ac5_corrected"
                with self.assertRaises(ValueError):
                    revision_guard(state, after, [], "astra_finalize")

    def test_mismarked_literal_suite_can_lose_only_its_erroneous_test_marker_before_approval(self):
        command = "python3 -m pytest test_totals.py -q"
        state, after = self.inputs()
        old = state["goal_contract"]["body"]["acceptance_criteria"][0]
        old["verification_method"] = "test: " + command
        after["acceptance_criteria"][0]["verification_method"] = command
        revision_guard(state, after, [], "glm_revise")
        for fields in ({"approval_status": "approved"}, {"origin": "user_cli_edit"}):
            with self.subTest(fields=fields):
                state["goal_contract"].update({"approval_status": "draft", "origin": "glm_draft", **fields})
                with self.assertRaises(ValueError):
                    revision_guard(state, after, [], "glm_revise")

    def test_marker_correction_cannot_weaken_a_real_test_guard_or_change_the_command(self):
        for old, new in (
            ("test: test_ac5_suite", "python3 -m pytest test_totals.py -q"),
            ("guard: python3 -m pytest test_totals.py -q", "python3 -m pytest test_totals.py -q"),
            ("test: python3 -m pytest test_totals.py -q", "python3 -m pytest other.py -q"),
            ("test: python3 -m pytest test_totals.py -q", "Run python3 -m pytest test_totals.py -q"),
            ("test: python3 -m pytest; echo PASS", "python3 -m pytest; echo PASS"),
        ):
            state, after = self.inputs()
            state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"] = old
            after["acceptance_criteria"][0]["verification_method"] = new
            with self.subTest(old=old, new=new), self.assertRaises(ValueError):
                revision_guard(state, after, [], "glm_revise")

    def revision_report(self):
        state, after = self.inputs()
        before = state["goal_contract"]["body"]
        before.update(
            technical_approach=["Old proposal"],
            initial_task={"objective": "Old"},
            milestones=[{"id": "M1", "objective": "Old"}],
        )
        after.update(
            technical_approach=["Corrected proposal"],
            initial_task={"objective": "Corrected"},
            milestones=[{"id": "M1", "objective": "Corrected"}],
        )
        after["acceptance_criteria"].append(
            {
                "id": "AC8",
                "criterion": "Exact helper literals match",
                "verification_method": "test: test_ac8_exact_literals",
                "human_review": False,
            }
        )
        changes = [
            {
                "item": item,
                "change": "reworded",
                "basis": "agent_proposed",
                "answer_id": "",
                "replacement": "Corrected engineering proposal",
            }
            for item in (
                "AC5 verification_method",
                "AC8 (new criterion added)",
                "technical_approach",
                "M1",
                "initial_task",
            )
        ]
        return state, after, changes

    def test_live_report_overdeclared_engineering_deltas_do_not_require_permission(self):
        state, after, changes = self.revision_report()
        retained = copy.deepcopy((state, after, changes))
        revision_guard(state, after, changes, "glm_revise")
        self.assertEqual(retained, (state, after, changes))

    def test_engineering_delta_alias_does_not_authorize_a_protected_change(self):
        for mutation in ("behavior", "permission", "required", "approved", "user_set", "history", "marker"):
            with self.subTest(mutation=mutation):
                state, after, changes = self.revision_report()
                if mutation == "behavior":
                    after["acceptance_criteria"][0]["criterion"] = "Only a selected check passes"
                elif mutation == "permission":
                    after["permission_boundaries"] = ["Allow network"]
                elif mutation == "required":
                    after["required_behaviors"] = []
                elif mutation == "approved":
                    state["goal_contract"]["approval_status"] = "approved"
                elif mutation == "user_set":
                    state["goal_contract"]["origin"] = "user_cli_edit"
                elif mutation == "history":
                    prior = copy.deepcopy(state["goal_contract"])
                    prior["approval_status"] = "approved"
                    state["contract_history"] = [prior]
                else:
                    state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"] = (
                        "test: test_ac5_old"
                    )
                with self.assertRaises(ValueError):
                    revision_guard(state, after, changes, "glm_revise")

    def test_synthetic_deltas_do_not_resolve_real_or_forged_user_changes(self):
        for kind in ("existing_addition", "unknown", "forged_user", "protected_collision", "removed"):
            with self.subTest(kind=kind):
                state, after, changes = self.revision_report()
                if kind == "existing_addition":
                    changes[1]["item"] = "AC5 (new criterion added)"
                    after["acceptance_criteria"][0]["criterion"] = "Only a selected check passes"
                elif kind == "unknown":
                    changes.append(dict(changes[0], item="unknown.proposal"))
                elif kind == "forged_user":
                    changes[0].update(basis="user_feedback", answer_id="not-a-saved-event")
                elif kind == "protected_collision":
                    state["goal_contract"]["body"]["required_behaviors"].append("technical_approach")
                    after["required_behaviors"].append("technical_approach")
                else:
                    changes[0]["change"] = "removed"
                with self.assertRaises(ValueError):
                    revision_guard(state, after, changes, "glm_revise")

    def test_rejection_identifies_the_declared_item_for_bounded_repair(self):
        state, after = self.inputs()
        with self.assertRaisesRegex(ValueError, "'unknown.proposal' needs a saved user"):
            revision_guard(
                state,
                after,
                [{"item": "unknown.proposal", "change": "reworded", "basis": "agent_proposed", "answer_id": ""}],
                "glm_revise",
            )

    def test_current_or_invalidated_user_approval_keeps_verification_protected(self):
        for fields in (
            {"approval_status": "approved"},
            {"approval_status": "draft", "approval_event": {"kind": "goal_approval"}},
        ):
            with self.subTest(fields=fields):
                state, after = self.inputs(**fields)
                with self.assertRaisesRegex(ValueError, "without a user-backed"):
                    revision_guard(state, after, [], "glm_revise")

    def test_clearing_the_current_receipt_does_not_erase_saved_user_approval(self):
        state, after = self.inputs()
        state["user_events"] = [{"kind": "goal_approval", "token": "r1:previously-approved"}]
        state["contract_history"] = [
            dict(copy.deepcopy(state["goal_contract"]), revision=1, hash="previously-approved")
        ]
        with self.assertRaisesRegex(ValueError, "without a user-backed"):
            revision_guard(state, after, [], "glm_revise")

    def test_proof_correction_does_not_authorize_a_changed_behavior_or_permission(self):
        for change in ("behavior", "permission", "required_behavior", "remove", "human_review"):
            with self.subTest(change=change):
                state, after = self.inputs()
                if change == "behavior":
                    after["acceptance_criteria"][0]["criterion"] = "Only one test must pass"
                elif change == "permission":
                    after["permission_boundaries"] = ["Network allowed"]
                elif change == "required_behavior":
                    after["required_behaviors"] = []
                elif change == "human_review":
                    after["acceptance_criteria"][0]["human_review"] = True
                else:
                    after["acceptance_criteria"] = []
                with self.assertRaisesRegex(ValueError, "without a user-backed"):
                    revision_guard(state, after, [], "glm_revise")

    def test_unrelated_prior_turn_approval_does_not_freeze_a_new_draft(self):
        state, after = self.inputs()
        state["user_events"] = [{"kind": "goal_approval", "token": "r1:old"}]
        old = copy.deepcopy(state["goal_contract"])
        old.update(revision=1, hash="old")
        old["body"]["acceptance_criteria"][0]["id"] = "PREVIOUS"
        state["contract_history"] = [old]
        revision_guard(state, after, [], "glm_revise")

    def test_user_set_methods_remain_protected(self):
        for origin in ("user_cli_edit", "user_answer", "user_feedback"):
            state, after = self.inputs()
            prior = copy.deepcopy(state["goal_contract"])
            prior["origin"] = origin
            event = {"id": "F1", "kind": "brief_feedback", "text": "Use this proof"}
            state.update(answers={"Q1": {}}, brief_feedback=[event], user_events=[event])
            if origin != "user_cli_edit":
                prior["declared_changes"] = [
                    {"item": "AC5", "basis": origin, "answer_id": "Q1" if origin == "user_answer" else "F1"}
                ]
            state["contract_history"] = [prior]
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                revision_guard(state, after, [], "glm_revise")

    def test_runner_enforced_proof_cannot_be_replaced_by_prose(self):
        for mark in ("test:", "guard:"):
            state, after = self.inputs()
            state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"] = (
                mark + " test_ac5_behavior"
            )
            with self.subTest(mark=mark), self.assertRaises(ValueError):
                revision_guard(state, after, [], "glm_revise")

    def test_approved_human_review_cannot_be_removed_with_unchanged_proof(self):
        state, _ = self.inputs(approval_status="approved")
        before = state["goal_contract"]["body"]
        before["acceptance_criteria"][0]["human_review"] = True
        after = copy.deepcopy(before)
        after["acceptance_criteria"][0]["human_review"] = False
        with self.assertRaises(ValueError):
            revision_guard(state, after, [], "glm_revise")

    def test_an_explicit_user_proof_change_is_consumed_once(self):
        state, after = self.inputs()
        state["answers"] = {"Q1": {"text": "Use this proof"}}
        revision_guard(
            state,
            after,
            [
                {
                    "item": "AC5",
                    "change": "reworded",
                    "basis": "user_answer",
                    "answer_id": "Q1",
                    "replacement": after["acceptance_criteria"][0]["verification_method"],
                }
            ],
            "glm_revise",
        )

    def test_draft_can_add_human_review_but_approved_or_user_set_review_stays_protected(self):
        for kind in ("draft", "approved", "user_cli_edit", "previously_approved"):
            state, _ = self.inputs(approval_status="approved" if kind == "approved" else "draft")
            if kind == "user_cli_edit":
                state["goal_contract"]["origin"] = "user_cli_edit"
            if kind == "previously_approved":
                prior = dict(copy.deepcopy(state["goal_contract"]), revision=1, hash="old", approval_status="approved")
                state["contract_history"] = [prior]
            after = copy.deepcopy(state["goal_contract"]["body"])
            after["acceptance_criteria"][0]["human_review"] = True
            with self.subTest(kind=kind):
                if kind == "draft":
                    revision_guard(state, after, [], "glm_revise")
                else:
                    with self.assertRaisesRegex(ValueError, "without a user-backed"):
                        revision_guard(state, after, [], "glm_revise")


class GreetingRevisionTests(unittest.TestCase):
    def report(self):
        # Sanitized retained greeting revision: additions were declared as one
        # aggregate delta alongside M1 and technical_approach, with review receipts.
        before = {
            "acceptance_criteria": [
                {
                    "id": "AC1",
                    "criterion": "Print Hello, World with a trailing newline",
                    "verification_method": "test: test_ac1_greeting",
                    "human_review": False,
                }
            ],
            "required_behaviors": ["Print every nonempty name verbatim"],
            "constraints": ["Python standard library only"],
            "scope_exclusions": [],
            "important_failure_cases": [r'Single empty-string argument (python3 greet.py \""): exit 2'],
            "permission_boundaries": ["No network"],
            "technical_approach": ["Test success and argument counts"],
            "milestones": [
                {
                    "id": "M1",
                    "objective": "Deliver greet.py and tests",
                    "acceptance_criteria": ["AC1"],
                    "depends_on": [],
                }
            ],
        }
        after = copy.deepcopy(before)
        for number, name in enumerate((" ", " Ada ", "Jos\u00e9", "--help"), 11):
            after["acceptance_criteria"].append(
                {
                    "id": f"AC{number}",
                    "criterion": f"Given name {name!r}, print {'Hello, ' + name!r} and exit 0",
                    "verification_method": f"test: test_ac{number}_verbatim",
                    "human_review": False,
                }
            )
        after["milestones"][0].update(
            objective="Deliver greeting with verbatim-name regressions",
            acceptance_criteria=[row["id"] for row in after["acceptance_criteria"]],
        )
        after["technical_approach"] = ["Assert usage content and exact verbatim greetings before comparing repeats"]
        changes = [
            {
                "item": item,
                "change": "reworded",
                "basis": "agent_proposed",
                "answer_id": "",
                "replacement": replacement,
                "example_correction": {"concern_id": concern, "before": "Coverage was missing", "after": replacement},
            }
            for item, concern, replacement in (
                ("acceptance_criteria", "PR2", "Add AC11-AC14; retain existing criteria verbatim"),
                ("M1", "PR1", "Include the added criteria and fail-first tests"),
                ("technical_approach", "PR1", "Require content-bearing usage assertions"),
            )
        ]
        state = {
            "goal_contract": {"body": before, "origin": "glm_draft", "approval_status": "draft", "approval_event": None}
        }
        return state, {"contract": after, "contract_changes": changes, "summary": "Close review coverage gaps"}

    def test_greeting_revision_accepts_aggregate_additions_without_dropping_criteria(self):
        state, report = self.report()
        retained = copy.deepcopy((state, report))
        changes = revision_guard(state, report["contract"], report["contract_changes"], "glm_revise")
        self.assertEqual(report["contract_changes"], changes)
        self.assertEqual(retained, (state, report))

    def test_aggregate_additions_cannot_authorize_protected_edits_or_forged_receipts(self):
        for kind in (
            "criterion",
            "proof",
            "review",
            "removed",
            "no_additions",
            "permission",
            "behavior",
            "collision",
            "forged_user",
            "removed_delta",
        ):
            with self.subTest(kind=kind):
                state, report = self.report()
                after = report["contract"]
                if kind == "criterion":
                    after["acceptance_criteria"][0]["criterion"] = "Print anything"
                if kind == "proof":
                    after["acceptance_criteria"][0]["verification_method"] = "Inspect it"
                if kind == "review":
                    after["acceptance_criteria"][0]["human_review"] = True
                if kind == "removed":
                    after["acceptance_criteria"].pop(0)
                if kind == "no_additions":
                    after["acceptance_criteria"] = after["acceptance_criteria"][:1]
                if kind == "permission":
                    after["permission_boundaries"] = ["Network allowed"]
                if kind == "behavior":
                    after["required_behaviors"] = []
                if kind == "collision":
                    state["goal_contract"]["body"]["required_behaviors"].append("acceptance_criteria")
                    after["required_behaviors"].append("acceptance_criteria")
                if kind == "forged_user":
                    report["contract_changes"][0].update(basis="user_answer", answer_id="invented")
                if kind == "removed_delta":
                    report["contract_changes"][0]["change"] = "removed"
                with self.assertRaises(ValueError):
                    revision_guard(state, after, report["contract_changes"], "glm_revise")

    def test_aggregate_additions_do_not_normalize_a_changed_command_backslash(self):
        state, report = self.report()
        report["contract"]["important_failure_cases"] = [
            r"Single empty-string argument (python3 greet.py \"\"): exit 2"
        ]
        retained = copy.deepcopy((state, report))
        with self.assertRaisesRegex(ValueError, "drops or changes .*Single empty-string argument"):
            revision_guard(state, report["contract"], report["contract_changes"], "glm_revise")
        self.assertEqual(retained, (state, report))


class DraftExampleRevisionTests(unittest.TestCase):
    def inputs(self):
        before = '{"rows": 1, "errors": []}\\n'
        after = '{"rows": 2, "errors": []}\\n'
        criterion = (
            "Given CSV `name,age,email\\nA,0,a@b\\nB,130,b@c\\n`, when "
            "`python3 -m csvcheck PATH` runs, then it exits 0, writes exactly "
            f"`{before}` to stdout, and writes empty stderr."
        )
        body = {
            "acceptance_criteria": [
                {
                    "id": "AC1",
                    "criterion": criterion,
                    "verification_method": "test: test_ac1_rows",
                    "human_review": False,
                }
            ],
            "required_behaviors": ["Count all logical data records"],
            "scope_exclusions": [],
            "constraints": [],
            "important_failure_cases": [],
            "permission_boundaries": ["No network"],
        }
        concern = {
            "id": "C_COUNT",
            "blocking": True,
            "concern": f"AC1 counts two data records incorrectly: `{before}` must be `{after}`.",
        }
        state = {
            "task": "Count all logical data records in the CSV.",
            "goal_contract": {"body": body, "origin": "glm_draft", "approval_status": "draft", "approval_event": None},
            "planning": {"reports": {"astra_challenge": {"report": {"concerns": [concern]}}}},
        }
        revised = copy.deepcopy(body)
        revised["acceptance_criteria"][0]["criterion"] = criterion.replace(before, after)
        receipt = [
            {
                "item": "AC1",
                "change": "reworded",
                "basis": "agent_proposed",
                "answer_id": "",
                "replacement": revised["acceptance_criteria"][0]["criterion"],
                "example_correction": {"concern_id": "C_COUNT", "before": before, "after": after},
            }
        ]
        return state, revised, receipt

    def test_reviewer_supported_numeric_draft_example_correction_preserves_original_state(self):
        state, after, changes = self.inputs()
        snapshot = copy.deepcopy(state)
        revision_guard(state, after, changes, "glm_revise")
        self.assertEqual(snapshot, state)

    def test_approval_and_user_authorship_always_protect_the_example(self):
        for kind in (
            "approved",
            "receipt",
            "history",
            "user_edit",
            "user_answer",
            "literal",
            "literal_formatting",
            "feedback",
            "requirement_quote",
        ):
            state, after, changes = self.inputs()
            contract = state["goal_contract"]
            if kind == "approved":
                contract["approval_status"] = "approved"
            elif kind == "receipt":
                contract["approval_event"] = {"kind": "goal_approval"}
            elif kind == "history":
                prior = dict(copy.deepcopy(contract), approval_status="approved")
                prior["body"]["acceptance_criteria"][0]["verification_method"] = "Run an old suite"
                state["contract_history"] = [prior]
            elif kind == "user_edit":
                contract["origin"] = "user_cli_edit"
            elif kind == "user_answer":
                state["answers"] = {"Q1": {"text": "Keep my example"}}
                contract["declared_changes"] = [{"item": "AC1", "basis": "user_answer", "answer_id": "Q1"}]
            elif kind == "literal":
                state["task"] += " Expected stdout: " + changes[0]["example_correction"]["before"]
            elif kind == "literal_formatting":
                state["task"] += ' Expected stdout: {"rows":1,"errors":[]}.'
            elif kind == "requirement_quote":
                # Run state keeps the requirements report under "report" (autopilot.py).
                quote = "Expected stdout: " + changes[0]["example_correction"]["before"]
                state["requirements_handoff"] = {
                    "report": {"requirements": [{"id": "R1", "source_quote": quote}]},
                    "output": "requirements.json",
                }
            else:
                state["brief_feedback"] = [{"text": "Expected stdout: " + changes[0]["example_correction"]["before"]}]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                revision_guard(state, after, changes, "glm_revise")

    def test_a_receipt_cannot_authorize_an_input_behavior_proof_or_permission_change(self):
        for kind in ("input", "command", "exit", "proof", "human", "permission", "behavior", "output_shape"):
            state, after, changes = self.inputs()
            row = after["acceptance_criteria"][0]
            if kind == "input":
                row["criterion"] = row["criterion"].replace("B,130", "B,129")
            elif kind == "command":
                row["criterion"] = row["criterion"].replace("csvcheck PATH", "csvcheck OTHER")
            elif kind == "exit":
                row["criterion"] = row["criterion"].replace("exits 0", "exits 1")
            elif kind == "proof":
                row["verification_method"] = "Run the suite"
            elif kind == "human":
                row["human_review"] = True
            elif kind == "permission":
                after["permission_boundaries"] = ["Network allowed"]
            elif kind == "behavior":
                after["required_behaviors"] = []
            else:
                row["criterion"] = row["criterion"].replace('"errors": []', '"errors": null')
                changes[0]["example_correction"]["after"] = changes[0]["example_correction"]["after"].replace(
                    '"errors": []', '"errors": null'
                )
            changes[0]["replacement"] = row["criterion"]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                revision_guard(state, after, changes, "glm_revise")

    def test_missing_stale_or_invented_review_receipts_do_not_allow_rewording(self):
        for kind in ("missing", "unknown_concern", "no_review", "advisory", "wrong_result", "duplicate", "no_origin"):
            state, after, changes = self.inputs()
            if kind == "missing":
                changes[0].pop("example_correction")
            elif kind == "unknown_concern":
                changes[0]["example_correction"]["concern_id"] = "C_INVENTED"
            elif kind == "no_review":
                state["planning"]["reports"] = {}
            elif kind == "advisory":
                state["planning"]["reports"]["astra_challenge"]["report"]["concerns"][0]["blocking"] = False
            elif kind == "wrong_result":
                changes[0]["example_correction"]["after"] = '{"rows": 3, "errors": []}\\n'
            elif kind == "duplicate":
                changes *= 2
            else:
                state["goal_contract"].pop("origin")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                revision_guard(state, after, changes, "glm_revise")

    def test_tab_separated_word_counts_can_be_recomputed_without_changing_words(self):
        state, after, changes = self.inputs()
        receipt = changes[0]["example_correction"]
        old, new = "red\\t1\\nblue\\t1\\n", "red\\t2\\nblue\\t1\\n"
        for row in state["goal_contract"]["body"]["acceptance_criteria"]:
            row["criterion"] = row["criterion"].replace(receipt["before"], old)
        after["acceptance_criteria"][0]["criterion"] = after["acceptance_criteria"][0]["criterion"].replace(
            receipt["after"], new
        )
        receipt.update(before=old, after=new)
        changes[0]["replacement"] = after["acceptance_criteria"][0]["criterion"]
        state["planning"]["reports"]["astra_challenge"]["report"]["concerns"][0]["concern"] = (
            f"AC1: `{old}` should be `{new}`."
        )
        revision_guard(state, after, changes, "glm_revise")
        after["acceptance_criteria"][0]["criterion"] = after["acceptance_criteria"][0]["criterion"].replace(
            "red", "green"
        )
        changes[0]["replacement"] = after["acceptance_criteria"][0]["criterion"]
        with self.assertRaises(ValueError):
            revision_guard(state, after, changes, "glm_revise")

    def test_a_numeric_receipt_cannot_hide_duplicate_keys_or_change_non_numeric_output(self):
        for old, new in (
            ('{"rows": 1, "errors": []}', '{"rows": 2, "rows": 3, "errors": []}'),
            ('{"rows": 1, "ok": true}', '{"rows": 2, "ok": false}'),
            ('{"rows": 1, "name": "A"}', '{"rows": 2, "name": "B"}'),
            ('{"rows": 1, "errors": []}', '{"rows": 2, "errors": [], "extra": 1}'),
        ):
            state, after, changes = self.inputs()
            receipt = changes[0]["example_correction"]
            state["goal_contract"]["body"]["acceptance_criteria"][0]["criterion"] = state["goal_contract"]["body"][
                "acceptance_criteria"
            ][0]["criterion"].replace(receipt["before"], old)
            after["acceptance_criteria"][0]["criterion"] = after["acceptance_criteria"][0]["criterion"].replace(
                receipt["after"], new
            )
            changes[0]["replacement"] = after["acceptance_criteria"][0]["criterion"]
            receipt.update(before=old, after=new)
            state["planning"]["reports"]["astra_challenge"]["report"]["concerns"][0]["concern"] = (
                f"AC1: `{old}` should be `{new}`."
            )
            with self.subTest(new=new), self.assertRaises(ValueError):
                revision_guard(state, after, changes, "glm_revise")


class LayoutExampleRevisionTests(unittest.TestCase):
    """#676: a model-derived table row whose column padding was miscounted (a live run approved ten
    spaces where the brief's rule gives twelve, and the Resolver had to stop the run for a person)."""

    def inputs(self, before, after):
        criterion = (
            "Given two rows with labels `2025-W10 core` and `2025-W11 extra`, when "
            "`python3 -m timesheet --by-project` runs, then it writes exactly "
            f"`{before}` to stdout and writes empty stderr."
        )
        body = {
            "acceptance_criteria": [
                {
                    "id": "AC1",
                    "criterion": criterion,
                    "verification_method": "test: test_ac1_by_project_total",
                    "human_review": False,
                }
            ],
            "required_behaviors": ["Total the hours per project"],
            "scope_exclusions": [],
            "constraints": [],
            "important_failure_cases": [],
            "permission_boundaries": ["No network"],
        }
        concern = {
            "id": "C_PAD",
            "blocking": True,
            "concern": f"AC1 pads the total row wrongly: `{before}` must be `{after}`.",
        }
        state = {
            "task": "Show the hours per project, right-aligned in the same 0.00h column.",
            "goal_contract": {"body": body, "origin": "glm_draft", "approval_status": "draft", "approval_event": None},
            "planning": {"reports": {"astra_challenge": {"report": {"concerns": [concern]}}}},
        }
        revised = copy.deepcopy(body)
        revised["acceptance_criteria"][0]["criterion"] = criterion.replace(before, after)
        receipt = [
            {
                "item": "AC1",
                "change": "reworded",
                "basis": "agent_proposed",
                "answer_id": "",
                "replacement": revised["acceptance_criteria"][0]["criterion"],
                "example_correction": {"concern_id": "C_PAD", "before": before, "after": after},
            }
        ]
        return state, revised, receipt

    def test_padding_of_a_table_row_is_corrected_without_a_question(self):
        before = "total" + " " * 10 + "15.50h\\n"
        after = "total" + " " * 12 + "15.50h\\n"
        state, revised, changes = self.inputs(before, after)
        self.assertEqual(changes, revision_guard(state, revised, changes, "glm_revise"))

    def test_a_word_or_number_inside_the_row_cannot_change_under_the_padding_receipt(self):
        before = "total" + " " * 10 + "15.50h\\n"
        for after in (
            "total" + " " * 12 + "16.50h\\n",
            "totals" + " " * 11 + "15.50h\\n",
            "total" + " " * 12 + "15.50h\\n15.50h\\n",
        ):
            state, revised, changes = self.inputs(before, after)
            with self.subTest(after=after), self.assertRaises(ValueError):
                revision_guard(state, revised, changes, "glm_revise")

    def test_a_padding_literal_the_user_wrote_is_protected(self):
        before = "total" + " " * 10 + "15.50h\\n"
        after = "total" + " " * 12 + "15.50h\\n"
        state, revised, changes = self.inputs(before, after)
        state["task"] += " Expected: `" + before + "`"
        with self.assertRaises(ValueError):
            revision_guard(state, revised, changes, "glm_revise")

    def test_a_row_without_a_number_is_not_padding(self):
        from autocode_draft_examples import layout_padding

        self.assertFalse(layout_padding("name" + " " * 4 + "city\\n", "name" + " " * 6 + "city\\n"))


class ExampleCorrectionSchemaTests(unittest.TestCase):
    """A contract change that is not a draft example correction says example_correction: null."""

    def change(self, receipt):
        return {
            "item": "AC7",
            "change": "reworded",
            "basis": "user_feedback",
            "answer_id": "feedback-1",
            "replacement": "AC7 documents --shout",
            "example_correction": receipt,
        }

    def test_the_schema_the_planner_is_given_accepts_null_and_a_receipt_only(self):
        # The live tiny-greeting draft (2026-10-02) was refused for example_correction: null.
        from autocode_util import model_output_schema, validate_schema
        from units import autoplanner

        for stage in ("astra_discovery", "glm_revise", "astra_finalize"):
            item = model_output_schema(autoplanner.SCHEMAS[stage])["properties"]["contract_changes"]["items"]
            self.assertIn("example_correction", item["required"], stage)
            validate_schema(self.change(None), item)
            validate_schema(self.change({"concern_id": "C1", "before": "3", "after": "4"}), item)
            with self.assertRaisesRegex(ValueError, "example_correction: expected object or null"):
                validate_schema(self.change("none"), item)
            with self.assertRaisesRegex(ValueError, "missing after"):
                validate_schema(self.change({"concern_id": "C1", "before": "3"}), item)


class PermissionReplacementTests(unittest.TestCase):
    """ "Build it." after a design turn (issue #185): the approved contract allows docs only, and the user's
    follow-up is the basis for allowing code. The Planner declares that as one row, as it is asked to: the
    previous boundary as item, the new one as replacement. Live runs were refused for it and asked the user."""

    DOCS = "Create only docs/design/metadata-cache.md; no code."
    CODE = "Edit only app/ and tests/; no network."

    def inputs(self):
        event = {"kind": "brief_feedback", "id": "feedback-1", "text": "Build it."}
        body = {
            "acceptance_criteria": [],
            "required_behaviors": [],
            "scope_exclusions": [],
            "constraints": [],
            "important_failure_cases": [],
            "permission_boundaries": [self.DOCS],
            "accepted_assumptions": [
                {"text": "The deliverable is a design document only.", "basis": "agent_proposed", "answer_id": ""}
            ],
            "delegated_decisions": [],
        }
        state = {
            "goal_contract": {"body": body, "approval_status": "approved"},
            "brief_feedback": [event],
            "user_events": [event],
            "answers": {"Q1": {"answer": "yes"}},
        }
        after = dict(copy.deepcopy(body), permission_boundaries=[self.CODE])
        return state, after

    def row(self, item, replacement="", basis="user_feedback", answer_id="feedback-1", change="permission_changed"):
        return {"item": item, "change": change, "basis": basis, "answer_id": answer_id, "replacement": replacement}

    def test_one_row_replacing_a_boundary_with_a_user_basis_is_accepted(self):
        for basis, answer_id in (("user_feedback", "feedback-1"), ("user_answer", "Q1")):
            with self.subTest(basis=basis):
                state, after = self.inputs()
                revision_guard(state, after, [self.row(self.DOCS, self.CODE, basis, answer_id)], "astra_discovery")
        # Declaring the new boundary in a row of its own still works.
        state, after = self.inputs()
        revision_guard(state, after, [self.row(self.DOCS), self.row(self.CODE)], "astra_discovery")

    def test_a_permission_change_still_needs_a_user_basis_and_a_matching_replacement(self):
        cases = {
            "undeclared": ([], "drops or changes"),
            "agent proposed": ([self.row(self.DOCS, self.CODE, "agent_proposed", "")], "needs a saved user"),
            "forged event": ([self.row(self.DOCS, self.CODE, answer_id="feedback-forged")], "needs a saved user"),
            "replacement not in the body": (
                [self.row(self.DOCS, "Edit anything")],
                "must appear in permission_boundaries",
            ),
            "no replacement": ([self.row(self.DOCS)], "adds permission boundary 'Edit only app/"),
        }
        for name, (changes, refusal) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, refusal):
                state, after = self.inputs()
                revision_guard(state, after, changes, "astra_discovery")
        # A second boundary added beside the replacement needs its own row.
        state, after = self.inputs()
        after["permission_boundaries"].append("Network allowed")
        with self.assertRaisesRegex(ValueError, "adds permission boundary 'Network allowed' without a user-backed"):
            revision_guard(state, after, [self.row(self.DOCS, self.CODE)], "astra_discovery")

    def test_a_declared_assumption_the_revision_dropped_is_not_an_error_and_a_leftover_row_is_named(self):
        state, after = self.inputs()
        assumption = state["goal_contract"]["body"]["accepted_assumptions"][0]["text"]
        after["accepted_assumptions"] = []
        revision_guard(
            state, after, [self.row(self.DOCS, self.CODE), self.row(assumption, change="removed")], "astra_discovery"
        )
        # Declared but kept, or a protected item declared but unchanged: refused, naming the row.
        state, after = self.inputs()
        with self.assertRaisesRegex(
            ValueError, "names 'The deliverable is a design document only.', which was not changed"
        ):
            revision_guard(
                state,
                after,
                [self.row(self.DOCS, self.CODE), self.row(assumption, change="removed")],
                "astra_discovery",
            )


class UnapprovedRewordingTests(unittest.TestCase):
    """#1691: the Planner may correct its own wording in a plan nobody approved, keeping every request quote."""

    QUOTE = {
        "source_id": "task:0",
        "quote": "Combining `--by-project` with `--project NAME` prints only that project's rows.",
    }
    WRONG = "stdout is exactly '2025-W10 ops     3.50h\\ntotal             3.50h\\n'"
    RIGHT = "stdout is exactly '2025-W10 ops     3.50h\\ntotal            3.50h\\n'"

    def inputs(self, **contract_fields):
        body = {
            "acceptance_criteria": [
                {
                    "id": "AC2",
                    "criterion": self.WRONG,
                    "verification_method": "Run python3 -m timesheet report examples/march.csv --by-project --project ops",
                    "human_review": False,
                    "sources": [self.QUOTE],
                }
            ],
            "required_behaviors": [],
            "scope_exclusions": [],
            "constraints": [],
            "important_failure_cases": [],
            "permission_boundaries": ["No network"],
        }
        contract = {
            "body": body,
            "approval_status": "draft",
            "approval_event": None,
            "origin": "glm_draft",
            **contract_fields,
        }
        after = copy.deepcopy(body)
        after["acceptance_criteria"][0]["criterion"] = self.RIGHT
        changes = [
            {"item": "AC2", "change": "reworded", "basis": "agent_proposed", "answer_id": "", "replacement": self.RIGHT}
        ]
        return {"goal_contract": contract}, after, changes

    def test_a_planner_corrects_its_own_unapproved_criterion(self):
        state, after, changes = self.inputs()
        self.assertEqual(changes, revision_guard(state, after, changes, "glm_revise"))

    def test_a_rewording_that_drops_a_request_quote_is_still_refused(self):
        state, after, changes = self.inputs()
        after["acceptance_criteria"][0]["sources"] = []
        with self.assertRaisesRegex(ValueError, "needs a saved user answer"):
            revision_guard(state, after, changes, "glm_revise")

    def test_a_refused_rewording_names_the_quotes_it_dropped(self):
        # #1630: a live revision dropped one of AC7's request quotes while rewording it, and the refusal only said
        # a user answer was missing, so two report repairs were spent before the run paused.
        state, after, changes = self.inputs()
        after["acceptance_criteria"][0]["sources"] = []
        with self.assertRaisesRegex(
            ValueError, r"needs a saved user answer.*still cites every request quote.*Keep"
        ) as raised:
            revision_guard(state, after, changes, "glm_revise")
        self.assertIn(str(self.QUOTE["quote"])[:40], str(raised.exception))

    def test_an_approved_contract_gets_no_citation_hint_because_the_person_decides(self):
        state, after, changes = self.inputs(approval_status="approved")
        after["acceptance_criteria"][0]["sources"] = []
        with self.assertRaises(ValueError) as raised:
            revision_guard(state, after, changes, "glm_revise")
        self.assertNotIn("nobody approved", str(raised.exception))

    def test_a_refused_proof_downgrade_says_to_keep_the_named_test(self):
        # #1630: a revision turned AC6's `test:` proof into the aggregate suite command.
        state, after, _ = self.inputs()
        state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"] = (
            "test: test_ac2_report_filters"
        )
        after["acceptance_criteria"][0]["criterion"] = self.WRONG
        after["acceptance_criteria"][0]["verification_method"] = "Run python3 -m unittest discover"
        with self.assertRaisesRegex(
            ValueError,
            r"AC2.*without a user-backed contract change\. AC2's verification_method names a proof.*own criterion",
        ):
            revision_guard(state, after, [], "glm_revise")

    def test_a_refusal_with_nothing_to_explain_is_unchanged(self):
        state, after, _ = self.inputs()
        after["acceptance_criteria"][0]["human_review"] = True
        state["goal_contract"]["body"]["acceptance_criteria"][0]["human_review"] = False
        state["goal_contract"]["approval_event"] = {"kind": "goal_approval"}
        with self.assertRaises(ValueError) as raised:
            revision_guard(state, after, [], "glm_revise")
        self.assertNotIn(". AC2", str(raised.exception).split("contract change", 1)[-1])

    def test_a_declared_example_correction_is_not_a_plain_rewording(self):
        # The receipt claims a count correction the example guard does not accept (no blocking concern
        # backs it, and the criterion's text changed beyond the literal): the allowance for a Planner's own
        # unapproved wording must not wave it through. Without the receipt the same text passes (above).
        state, after, changes = self.inputs()
        changes[0]["example_correction"] = {"concern_id": "C_COUNT", "before": self.WRONG, "after": self.RIGHT}
        with self.assertRaisesRegex(ValueError, "needs a saved user answer"):
            revision_guard(state, after, changes, "glm_revise")

    def test_a_user_backed_rewording_keeps_its_declaration_with_a_matching_proof(self):
        # #1764: the person backed this rewording, so it is a declared change and not the Planner's own
        # wording to wave through. Correcting the criterion's proof with it must not lose the declaration.
        state, after, changes = self.inputs()
        changes[0].update(basis="user_answer", answer_id="A1")
        after["acceptance_criteria"][0]["verification_method"] += " | head -20"
        state["answers"] = {"A1": {"kind": "answer", "text": "compare only the first two rows"}}
        self.assertEqual(changes, revision_guard(state, after, changes, "glm_revise"))

    def test_an_allowable_rewording_may_correct_its_unmarked_proof(self):
        # A live revision (2026-10-21) aligned AC9's wording with the person's storage answer, kept the request
        # quote, and moved its unmarked verification with it; the guard refused only because the proof moved.
        state, after, changes = self.inputs()
        after["acceptance_criteria"][0]["verification_method"] = "Run python3 -m unittest discover -s tests -t ."
        self.assertEqual(changes, revision_guard(state, after, changes, "glm_revise"))

    def test_a_proof_the_runner_checks_still_cannot_become_a_plain_command_with_a_rewording(self):
        state, after, changes = self.inputs()
        state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"] = (
            "test: test_ac2_report_filters"
        )
        after["acceptance_criteria"][0]["verification_method"] = "Run python3 -m unittest discover"
        with self.assertRaisesRegex(ValueError, "names a proof.*own criterion"):
            revision_guard(state, after, changes, "glm_revise")

    def test_once_approved_every_rewording_needs_the_person(self):
        for fields, history in (
            ({"approval_status": "approved"}, []),
            ({"approval_status": "draft", "approval_event": {"kind": "goal_approval"}}, []),
            ({}, [{"approval_status": "approved"}]),
            ({}, [{"origin": "user_cli_edit"}]),
        ):
            with self.subTest(fields=fields, history=history):
                state, after, changes = self.inputs(**fields)
                state["contract_history"] = history
                with self.assertRaisesRegex(ValueError, "needs a saved user answer"):
                    revision_guard(state, after, changes, "glm_revise")


class PersonTracedAssumptionTests(unittest.TestCase):
    """A live planning revision (2026-10-21): the Planner may correct its own assumption, never one saved from the person."""

    TEXT = "notes.json is created at the workspace root, next to the CLI."
    PROPOSED = "notes.json is created in the working directory where the CLI is invoked."

    def inputs(self, basis="user_answer"):
        body = {
            "acceptance_criteria": [],
            "required_behaviors": [],
            "scope_exclusions": [],
            "constraints": [],
            "important_failure_cases": [],
            "permission_boundaries": [],
            "accepted_assumptions": [{"text": self.TEXT, "basis": basis, "answer_id": "A1"}],
            "delegated_decisions": [],
        }
        state = {
            "goal_contract": {"body": body, "approval_status": "draft", "approval_event": None},
            "answers": {"A1": {"kind": "answer", "text": "next to the CLI"}},
        }
        return state, copy.deepcopy(body)

    def row(self, change="reworded", basis="agent_proposed", answer_id="", replacement=None):
        return {
            "item": self.TEXT,
            "change": change,
            "basis": basis,
            "answer_id": answer_id,
            "replacement": self.PROPOSED if replacement is None and change == "reworded" else replacement or "",
        }

    def test_the_planners_own_assumption_can_be_reworded_or_removed_without_permission(self):
        for change in ("reworded", "removed"):
            with self.subTest(change=change):
                state, after = self.inputs(basis="agent_proposed")
                after["accepted_assumptions"] = (
                    [{"text": self.PROPOSED, "basis": "agent_proposed", "answer_id": ""}]
                    if change == "reworded"
                    else []
                )
                changes = [self.row(change)]
                self.assertEqual(changes, revision_guard(state, after, changes, "glm_revise"))

    def test_a_wrapped_assumption_reference_is_recognized(self):
        state, after = self.inputs(basis="agent_proposed")
        after["accepted_assumptions"] = [{"text": self.PROPOSED, "basis": "agent_proposed", "answer_id": ""}]
        changes = [dict(self.row(), item=f'accepted_assumptions: "{self.TEXT}"')]
        returned = revision_guard(state, after, changes, "glm_revise")
        self.assertEqual(self.TEXT, returned[0]["item"])

    def test_a_reworded_assumption_saved_from_the_answer_asks_the_person(self):
        state, after = self.inputs()
        after["accepted_assumptions"] = [{"text": self.PROPOSED, "basis": "agent_proposed", "answer_id": ""}]
        with self.assertRaises(rewording.ProtectedRewording) as caught:
            revision_guard(state, after, [self.row()], "glm_revise")
        self.assertEqual(
            [{"item": self.TEXT, "approved": self.TEXT, "proposed": self.PROPOSED, "traced": True}],
            caught.exception.rewordings,
        )

    def test_a_dropped_assumption_saved_from_the_answer_asks_the_person(self):
        for basis in ("user_answer", "user_feedback"):
            with self.subTest(basis=basis):
                state, after = self.inputs(basis=basis)
                if basis == "user_feedback":
                    event = {"kind": "brief_feedback", "id": "A1"}
                    state["brief_feedback"] = [event]
                    state["user_events"] = [event]
                after["accepted_assumptions"] = []
                with self.assertRaises(rewording.ProtectedRewording) as caught:
                    revision_guard(state, after, [], "glm_revise")
                self.assertEqual(
                    [
                        {
                            "item": self.TEXT,
                            "approved": self.TEXT,
                            "proposed": revision.DROP_ASSUMPTION,
                            "traced": True,
                        }
                    ],
                    caught.exception.rewordings,
                )

    def test_a_decision_the_person_delegated_is_the_planners_to_revise(self):
        for change in ("reworded", "removed"):
            with self.subTest(change=change):
                state, after = self.inputs(basis="delegated")
                state["answers"] = {"A1": {"kind": "delegated"}}
                after["accepted_assumptions"] = (
                    [{"text": self.PROPOSED, "basis": "agent_proposed", "answer_id": ""}]
                    if change == "reworded"
                    else []
                )
                changes = [self.row(change)]
                self.assertEqual(changes, revision_guard(state, after, changes, "glm_revise"))

    def test_an_undeclared_rewording_of_the_persons_assumption_names_the_new_text(self):
        state, after = self.inputs()
        after["accepted_assumptions"] = [{"text": self.PROPOSED, "basis": "agent_proposed", "answer_id": ""}]
        with self.assertRaises(rewording.ProtectedRewording) as caught:
            revision_guard(state, after, [], "glm_revise")
        self.assertEqual(self.PROPOSED, caught.exception.rewordings[0]["proposed"])

    def test_wording_the_person_accepted_stays_theirs_in_the_next_revision(self):
        state, after = self.inputs()
        after["accepted_assumptions"] = [{"text": self.PROPOSED, "basis": "agent_proposed", "answer_id": ""}]
        revision_guard(state, after, [self.row(basis="user_answer", answer_id="A1")], "glm_revise")
        self.assertEqual(
            [{"text": self.PROPOSED, "basis": "user_answer", "answer_id": "A1"}], after["accepted_assumptions"]
        )
        state["goal_contract"]["body"] = copy.deepcopy(after)
        again = copy.deepcopy(after)
        again["accepted_assumptions"] = []
        changes = [{"item": self.PROPOSED, "change": "removed", "basis": "agent_proposed", "answer_id": ""}]
        with self.assertRaises(rewording.ProtectedRewording):
            revision_guard(state, again, changes, "glm_revise")

    def test_a_declared_replacement_the_revision_does_not_keep_is_refused(self):
        state, after = self.inputs()
        after["accepted_assumptions"] = [{"text": "Something else.", "basis": "agent_proposed", "answer_id": ""}]
        with self.assertRaisesRegex(ValueError, "which the revision does not keep"):
            revision_guard(state, after, [self.row(basis="user_answer", answer_id="A1")], "glm_revise")

    def test_a_forged_or_agent_basis_does_not_authorize_dropping_the_persons_assumption(self):
        cases = ({"basis": "agent_proposed", "answer_id": ""}, {"basis": "delegated", "answer_id": "A1"})
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(rewording.ProtectedRewording):
                state, after = self.inputs()
                after["accepted_assumptions"] = []
                revision_guard(state, after, [self.row("removed", **fields)], "glm_revise")

    def test_the_persons_answer_lets_the_revision_drop_the_assumption(self):
        for change in ("reworded", "removed"):
            with self.subTest(change=change):
                state, after = self.inputs()
                after["accepted_assumptions"] = (
                    [{"text": self.PROPOSED, "basis": "agent_proposed", "answer_id": ""}]
                    if change == "reworded"
                    else []
                )
                changes = [self.row(change, basis="user_answer", answer_id="A1")]
                self.assertEqual(changes, revision_guard(state, after, changes, "glm_revise"))


class ReviewerRequestedOwnTextTests(unittest.TestCase):
    """A revision may apply the Plan Reviewer's blocking request to the Planner's own constraint or permission line
    while nobody has approved or edited the plan (live vague-brief run, 2026-10-20: the refusal cost a repair, a
    question AutoCode answered itself and a planning restart from scratch)."""

    SPLIT = "Each milestone owns a disjoint set of affected_paths: M2 adds only new files (practice.py)"
    REBALANCED = "M1 delivers the minimal end-to-end student journey; M2 adds practice.py and its tests"
    BOUNDARY = "M2 edits only adaptive_learning/commands/practice.py and tests/test_practice.py"
    NEW_BOUNDARY = "Edit only adaptive_learning/ and tests/; no network"
    BRIEF_LINE = "Python standard library only"
    CONCERN = {
        "id": "C1",
        "concern": "The milestones do not deliver a student journey",
        "requested_change": "Rebalance the milestones around a minimal end-to-end student journey",
        "blocking": True,
    }

    def inputs(self, *, blocking=True, **state_fields):
        body = {
            "acceptance_criteria": [],
            "required_behaviors": ["Students can start a practice session"],
            "scope_exclusions": [],
            "constraints": [self.BRIEF_LINE, self.SPLIT],
            "important_failure_cases": [],
            "permission_boundaries": [self.BOUNDARY],
            "accepted_assumptions": [],
            "delegated_decisions": [],
        }
        state = {
            "task": "Build an adaptive learning CLI for students; I haven't decided the details. "
            "Python standard library only.",
            "goal_contract": {"body": body, "approval_status": "draft", "approval_event": None, "revision": 2},
            "planning": {
                "reports": {"astra_challenge": {"report": {"concerns": [{**self.CONCERN, "blocking": blocking}]}}}
            },
            "answers": {},
            "user_events": [],
            "brief_feedback": [],
        }
        state.update(state_fields)
        after = copy.deepcopy(body)
        after["constraints"] = [self.BRIEF_LINE, self.REBALANCED]
        after["permission_boundaries"] = [self.NEW_BOUNDARY]
        return state, after

    def row(self, item, change, replacement="", basis="agent_proposed", answer_id=""):
        return {"item": item, "change": change, "basis": basis, "answer_id": answer_id, "replacement": replacement}

    def changes(self):
        return [
            self.row(self.SPLIT, "reworded", self.REBALANCED),
            self.row(self.BOUNDARY, "permission_changed", self.NEW_BOUNDARY),
        ]

    def test_a_revision_applies_the_reviewers_request_to_the_planners_own_lines(self):
        state, after = self.inputs()
        before = copy.deepcopy(state)
        declared = revision_guard(state, after, self.changes(), "glm_revise")
        self.assertEqual(before, state)
        self.assertEqual([self.SPLIT, self.BOUNDARY], [row["item"] for row in declared])
        self.assertEqual([["C1"], ["C1"]], [row["review_concerns"] for row in declared], "shown at approval")

    def test_the_planners_own_constraint_may_also_be_dropped(self):
        state, after = self.inputs()
        after["constraints"] = [self.BRIEF_LINE]
        changes = [self.row(self.SPLIT, "removed"), self.row(self.BOUNDARY, "permission_changed", self.NEW_BOUNDARY)]
        self.assertEqual(["C1"], revision_guard(state, after, changes, "glm_revise")[0]["review_concerns"])

    def test_a_line_that_repeats_the_persons_words_stays_theirs(self):
        state, after = self.inputs()
        after["constraints"] = [self.BRIEF_LINE + " (Python 3.12)", self.REBALANCED]
        changes = [self.row(self.BRIEF_LINE, "reworded", self.BRIEF_LINE + " (Python 3.12)"), *self.changes()]
        with self.assertRaisesRegex(ValueError, "repeats the person's own words"):
            revision_guard(state, after, changes, "glm_revise")
        # Their feedback and their answers are their words too, whichever sentence of theirs the line repeats.
        for source in ("brief_feedback", "answers"):
            with self.subTest(source=source):
                event = {
                    "kind": "brief_feedback",
                    "id": "f1",
                    "text": "Fine. M2 adds only new files (practice.py). Go on.",
                }
                state, after = self.inputs(
                    **(
                        {"brief_feedback": [event], "user_events": [event]}
                        if source == "brief_feedback"
                        else {"answers": {"Q1": {"id": "Q1", "text": event["text"]}}}
                    )
                )
                with self.assertRaisesRegex(ValueError, "needs a saved user answer"):
                    revision_guard(state, after, self.changes(), "glm_revise")

    def test_without_the_reviewers_blocking_concern_or_a_declaration_the_rule_is_unchanged(self):
        cases = {
            "non-blocking concern": (self.inputs(blocking=False), self.changes(), "glm_revise", "needs a saved user"),
            "no review yet": (
                self.inputs(planning={"reports": {}}),
                self.changes(),
                "glm_revise",
                "needs a saved user",
            ),
            "undeclared": (self.inputs(), [], "glm_revise", "drops or changes"),
            "the final review": (self.inputs(), self.changes(), "astra_finalize", "needs a saved user"),
            "a forged answer": (
                self.inputs(),
                [self.row(self.SPLIT, "reworded", self.REBALANCED, "user_answer", "Q9"), self.changes()[1]],
                "glm_revise",
                "needs a saved user",
            ),
            "a dropped boundary": (
                self.inputs(),
                [self.changes()[0], self.row(self.BOUNDARY, "permission_changed")],
                "glm_revise",
                "needs a saved user",
            ),
        }
        for name, ((state, after), changes, origin, refusal) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, refusal):
                revision_guard(state, after, changes, origin)

    def test_an_approved_or_edited_contract_keeps_every_line_protected(self):
        for name, fields in {
            "approved": {"approval_status": "approved"},
            "approval event": {"approval_event": {"id": "e1"}},
        }.items():
            with self.subTest(name), self.assertRaises(ValueError):
                state, after = self.inputs()
                state["goal_contract"].update(fields)
                revision_guard(state, after, self.changes(), "glm_revise")
        with self.subTest("edited earlier"), self.assertRaises(ValueError):
            state, after = self.inputs(contract_history=[{"origin": "user_cli_edit", "approval_status": "draft"}])
            revision_guard(state, after, self.changes(), "glm_revise")

    def test_behaviors_exclusions_and_failure_cases_are_never_the_planners_own(self):
        state, after = self.inputs()
        after["required_behaviors"] = []
        changes = [*self.changes(), self.row("Students can start a practice session", "removed")]
        with self.assertRaisesRegex(ValueError, "'Students can start a practice session'"):
            revision_guard(state, after, changes, "glm_revise")
        # A constraint that is also a required behavior is the person's whichever list the Planner changes it in.
        state, after = self.inputs()
        state["goal_contract"]["body"]["required_behaviors"].append(self.SPLIT)
        after["required_behaviors"].append(self.SPLIT)
        with self.assertRaisesRegex(ValueError, "needs a saved user"):
            revision_guard(state, after, self.changes(), "glm_revise")

    def test_quoting_is_judged_on_the_persons_text_alone(self):
        state, _ = self.inputs()
        self.assertTrue(revision.quotes_person_text(state, "python standard library only."))
        self.assertTrue(revision.quotes_person_text(state, "standard library"), "part of the person's sentence")
        self.assertFalse(revision.quotes_person_text(state, self.SPLIT))
        self.assertTrue(revision.quotes_person_text(state, ""), "an empty line is nobody's to change")
        # A delegated answer stores the model's default: not the person's words.
        state["answers"] = {"Q1": {"kind": "delegated", "text": self.SPLIT}}
        self.assertFalse(revision.quotes_person_text(state, self.SPLIT))


class BaselineCorrectionTests(unittest.TestCase):
    """#2133: a criterion's current_behavior box holds the Investigator's observed behavior, not the agreed
    outcome. Set to exactly the bound diagnosis text, it is the runner's own correction with a receipt; any
    other change to it on a protected criterion still needs a saved user answer."""

    OBSERVED = "stdout is '2025-W10 ops 3.49h' for a 3h30m entry: the report drifts by a cent of an hour"
    WRONG = "stdout is '2025-W10 ops 3.51h' for a 3h30m entry"
    QUOTE = {"source_id": "task:0", "quote": "The weekly report must not drift."}

    def inputs(self, approved=True, box=WRONG):
        body = {
            "acceptance_criteria": [
                {
                    "id": "AC2",
                    "criterion": "The weekly report prints 3.50h for a 3h30m entry.",
                    "verification_method": "test: test_ac2_no_drift",
                    "human_review": False,
                    "sources": [self.QUOTE],
                    "current_behavior": box,
                }
            ],
            "required_behaviors": [],
            "scope_exclusions": [],
            "constraints": [],
            "important_failure_cases": [],
            "permission_boundaries": ["No network"],
        }
        contract = {
            "body": body,
            "revision": 2,
            "hash": "h2",
            "approval_status": "approved" if approved else "draft",
            "approval_event": {"id": "e1"} if approved else None,
            "origin": "glm_revise" if approved else "glm_draft",
        }
        state = {
            "goal_contract": contract,
            "investigation": {
                "outcome": "reproduced",
                "observed": self.OBSERVED,
                "output": "/run/iterations/investigator-01.jsonl",
                "output_hash": "f" * 64,
                "source_revision": "rev9",
                "probe_result": {"exit_code": 0},
            },
        }
        after = copy.deepcopy(body)
        after["acceptance_criteria"][0]["current_behavior"] = self.OBSERVED
        return state, after

    def receipt(self):
        return {
            "item": "AC2",
            "change": "current_behavior",
            "basis": "agent_proposed",
            "answer_id": "",
            "replacement": self.OBSERVED,
            "evidence": {"output_hash": "f" * 64, "source_revision": "rev9"},
        }

    def test_the_bound_diagnosis_text_is_accepted_with_a_receipt_and_no_question(self):
        state, after = self.inputs()
        before = copy.deepcopy(state)
        self.assertEqual([self.receipt()], revision_guard(state, after, [], "glm_revise"))
        self.assertEqual(before, state)

    def test_the_planners_rewording_row_for_the_box_alone_is_folded_into_the_receipt(self):
        state, after = self.inputs()
        declared = [
            {
                "item": "AC2",
                "change": "reworded",
                "basis": "agent_proposed",
                "answer_id": "",
                "replacement": self.OBSERVED,
            }
        ]
        self.assertEqual([self.receipt()], revision_guard(state, after, declared, "glm_revise"))

    def test_whitespace_in_the_copied_text_does_not_matter(self):
        state, after = self.inputs()
        after["acceptance_criteria"][0]["current_behavior"] = "  " + self.OBSERVED.replace(" for ", "  for\n")
        self.assertEqual("current_behavior", revision_guard(state, after, [], "glm_revise")[0]["change"])

    def test_another_text_on_a_protected_criterion_is_refused_with_the_evidence_to_copy(self):
        state, after = self.inputs()
        after["acceptance_criteria"][0]["current_behavior"] = "the report rounds down"
        with self.assertRaisesRegex(
            ValueError, r"'AC2' needs a saved user answer.*current_behavior.*copy bug_diagnosis\.observed exactly"
        ) as raised:
            revision_guard(state, after, [], "glm_revise")
        self.assertIn(self.OBSERVED[:60], str(raised.exception))
        self.assertIn(self.WRONG[:40], str(raised.exception))

    def test_the_criterion_itself_still_cannot_change_with_the_box(self):
        state, after = self.inputs()
        after["acceptance_criteria"][0]["criterion"] = "The weekly report prints 3.5h for a 3h30m entry."
        with self.assertRaisesRegex(ValueError, r"drops or changes 'AC2' without a user-backed contract change"):
            revision_guard(state, after, [], "glm_revise")

    def test_without_a_bound_diagnosis_the_box_is_not_evidence(self):
        for name, found in (
            ("no investigation", None),
            ("unbound report", {"outcome": "reproduced", "observed": self.OBSERVED, "output_hash": None}),
            ("not reproduced", {"outcome": "not_reproduced", "observed": self.OBSERVED, "output_hash": "f" * 64}),
            ("untestable", {"outcome": "reproduced", "observed": self.OBSERVED, "output_hash": "f" * 64}),
        ):
            with self.subTest(name):
                state, after = self.inputs()
                state.pop("investigation")
                if found:
                    state["investigation"] = found
                with self.assertRaisesRegex(ValueError, r"'AC2' needs a saved user answer.*no reproduced diagnosis"):
                    revision_guard(state, after, [], "glm_revise")

    def test_an_empty_box_may_be_filled_with_any_text(self):
        state, after = self.inputs(box="")
        after["acceptance_criteria"][0]["current_behavior"] = "the report rounds down"
        self.assertEqual([], revision_guard(state, after, [], "glm_revise"))

    def test_a_saved_user_answer_authorizes_another_text(self):
        state, after = self.inputs()
        state["answers"] = {"Q3": "It rounds down today"}
        after["acceptance_criteria"][0]["current_behavior"] = "the report rounds down"
        declared = [
            {
                "item": "AC2",
                "change": "reworded",
                "basis": "user_answer",
                "answer_id": "Q3",
                "replacement": "the report rounds down",
            }
        ]
        self.assertEqual(declared, revision_guard(state, after, declared, "glm_revise"))

    def test_the_planners_own_unapproved_criterion_may_change_its_box(self):
        state, after = self.inputs(approved=False)
        after["acceptance_criteria"][0]["current_behavior"] = "the report rounds down"
        self.assertEqual([], revision_guard(state, after, [], "glm_revise"))
        self.assertEqual([self.receipt()], revision_guard(*self.inputs(approved=False)[:2], [], "glm_revise"))
