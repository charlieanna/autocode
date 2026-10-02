"""Proof corrections in an unapproved plan do not change the agreed behavior."""
import copy
import unittest

from autocode_goals import revision_guard


class DraftVerificationRevisionTests(unittest.TestCase):
    def inputs(self, **contract_fields):
        body = {"acceptance_criteria": [{"id": "AC5", "criterion": "The complete project suite passes.",
                 "verification_method": "Run python3 -m unittest nonexistent_test.py", "human_review": False}],
                "required_behaviors": ["Preserve the current output exactly"], "scope_exclusions": [],
                "constraints": [], "important_failure_cases": [], "permission_boundaries": ["No network"]}
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
        changes = [{"item": "AC5", "change": "reworded", "basis": "agent_proposed", "answer_id": "",
                    "replacement": after["acceptance_criteria"][0]["verification_method"]}]
        revision_guard(state, after, changes, "astra_finalize")

    def test_current_or_invalidated_user_approval_keeps_verification_protected(self):
        for fields in ({"approval_status": "approved"},
                       {"approval_status": "draft", "approval_event": {"kind": "goal_approval"}}):
            with self.subTest(fields=fields):
                state, after = self.inputs(**fields)
                with self.assertRaisesRegex(ValueError, "without a user-backed"):
                    revision_guard(state, after, [], "glm_revise")

    def test_clearing_the_current_receipt_does_not_erase_saved_user_approval(self):
        state, after = self.inputs()
        state["user_events"] = [{"kind": "goal_approval", "token": "r1:previously-approved"}]
        state["contract_history"] = [dict(copy.deepcopy(state["goal_contract"]), revision=1, hash="previously-approved")]
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
                prior["declared_changes"] = [{"item": "AC5", "basis": origin,
                                              "answer_id": "Q1" if origin == "user_answer" else "F1"}]
            state["contract_history"] = [prior]
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                revision_guard(state, after, [], "glm_revise")

    def test_runner_enforced_proof_cannot_be_replaced_by_prose(self):
        for mark in ("test:", "guard:"):
            state, after = self.inputs()
            state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"] = mark + " test_ac5_behavior"
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
        revision_guard(state, after, [{"item": "AC5", "change": "reworded", "basis": "user_answer",
                       "answer_id": "Q1", "replacement": after["acceptance_criteria"][0]["verification_method"]}], "glm_revise")

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


class DraftExampleRevisionTests(unittest.TestCase):
    def inputs(self):
        before = '{"rows": 1, "errors": []}\\n'
        after = '{"rows": 2, "errors": []}\\n'
        criterion = ('Given CSV `name,age,email\\nA,0,a@b\\nB,130,b@c\\n`, when '
                     '`python3 -m csvcheck PATH` runs, then it exits 0, writes exactly '
                     f'`{before}` to stdout, and writes empty stderr.')
        body = {"acceptance_criteria": [{"id": "AC1", "criterion": criterion,
                "verification_method": "test: test_ac1_rows", "human_review": False}],
                "required_behaviors": ["Count all logical data records"], "scope_exclusions": [],
                "constraints": [], "important_failure_cases": [], "permission_boundaries": ["No network"]}
        concern = {"id": "C_COUNT", "blocking": True,
                   "concern": f"AC1 counts two data records incorrectly: `{before}` must be `{after}`."}
        state = {"task": "Count all logical data records in the CSV.",
                 "goal_contract": {"body": body, "origin": "glm_draft", "approval_status": "draft",
                                   "approval_event": None},
                 "planning": {"reports": {"astra_challenge": {"report": {"concerns": [concern]}}}}}
        revised = copy.deepcopy(body)
        revised["acceptance_criteria"][0]["criterion"] = criterion.replace(before, after)
        receipt = [{"item": "AC1", "change": "reworded", "basis": "agent_proposed", "answer_id": "",
                    "replacement": revised["acceptance_criteria"][0]["criterion"],
                    "example_correction": {"concern_id": "C_COUNT", "before": before, "after": after}}]
        return state, revised, receipt

    def test_reviewer_supported_numeric_draft_example_correction_preserves_original_state(self):
        state, after, changes = self.inputs()
        snapshot = copy.deepcopy(state)
        revision_guard(state, after, changes, "glm_revise")
        self.assertEqual(snapshot, state)

    def test_approval_and_user_authorship_always_protect_the_example(self):
        for kind in ("approved", "receipt", "history", "user_edit", "user_answer", "literal", "literal_formatting", "feedback"):
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
                changes[0]["example_correction"]["after"] = changes[0]["example_correction"]["after"].replace('"errors": []', '"errors": null')
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
        old, new = 'red\\t1\\nblue\\t1\\n', 'red\\t2\\nblue\\t1\\n'
        for row in state["goal_contract"]["body"]["acceptance_criteria"]:
            row["criterion"] = row["criterion"].replace(receipt["before"], old)
        after["acceptance_criteria"][0]["criterion"] = after["acceptance_criteria"][0]["criterion"].replace(receipt["after"], new)
        receipt.update(before=old, after=new)
        changes[0]["replacement"] = after["acceptance_criteria"][0]["criterion"]
        state["planning"]["reports"]["astra_challenge"]["report"]["concerns"][0]["concern"] = f'AC1: `{old}` should be `{new}`.'
        revision_guard(state, after, changes, "glm_revise")
        after["acceptance_criteria"][0]["criterion"] = after["acceptance_criteria"][0]["criterion"].replace('red', 'green')
        changes[0]["replacement"] = after["acceptance_criteria"][0]["criterion"]
        with self.assertRaises(ValueError):
            revision_guard(state, after, changes, "glm_revise")

    def test_a_numeric_receipt_cannot_hide_duplicate_keys_or_change_non_numeric_output(self):
        for old, new in (('{"rows": 1, "errors": []}', '{"rows": 2, "rows": 3, "errors": []}'),
                         ('{"rows": 1, "ok": true}', '{"rows": 2, "ok": false}'),
                         ('{"rows": 1, "name": "A"}', '{"rows": 2, "name": "B"}'),
                         ('{"rows": 1, "errors": []}', '{"rows": 2, "errors": [], "extra": 1}')):
            state, after, changes = self.inputs()
            receipt = changes[0]["example_correction"]
            state["goal_contract"]["body"]["acceptance_criteria"][0]["criterion"] = state["goal_contract"]["body"]["acceptance_criteria"][0]["criterion"].replace(receipt["before"], old)
            after["acceptance_criteria"][0]["criterion"] = after["acceptance_criteria"][0]["criterion"].replace(receipt["after"], new)
            changes[0]["replacement"] = after["acceptance_criteria"][0]["criterion"]
            receipt.update(before=old, after=new)
            state["planning"]["reports"]["astra_challenge"]["report"]["concerns"][0]["concern"] = f'AC1: `{old}` should be `{new}`.'
            with self.subTest(new=new), self.assertRaises(ValueError):
                revision_guard(state, after, changes, "glm_revise")
