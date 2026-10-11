"""Proof corrections in an unapproved plan do not change the agreed behavior."""

import copy
import unittest

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
