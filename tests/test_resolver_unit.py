import copy
import hashlib
import json
import pathlib
import unittest

from tools import autocode_resolver as r


def body():
    return {
        "intended_outcome": "Outcome", "intended_user": "User",
        "deliverables": ["Deliverable"], "required_behaviors": ["Behavior"],
        "important_failure_cases": ["Failure"], "scope_exclusions": ["Exclusion"],
        "constraints": ["Constraint"], "permission_boundaries": ["Boundary"],
        "accepted_assumptions": [], "delegated_decisions": [],
        "acceptance_criteria": [{"id": "AC1", "criterion": "Criterion", "verification_method": "Test", "human_review": False}],
        "open_blocking_questions": [], "end_to_end_flow": ["Flow"],
        "technical_approach": ["Approach"],
        "milestones": [{"id": "M1", "objective": "Objective", "acceptance_criteria": ["AC1"]}],
    }


def snapshot(contract_body=None, **changes):
    contract_body = body() if contract_body is None else contract_body
    values = {"task_id": "task", "revision": 1, "body": contract_body, "approval_status": "approved"}
    values.update(changes)
    values["hash"] = values.get("hash", r.sealed_hash(values["task_id"], values["revision"], contract_body))
    values["approval_event"] = values.get("approval_event", {"kind": "goal_approval", "actor": "user_cli", "token": f"r{values['revision']}:{values['hash']}"})
    return r.ContractSnapshot(**values)


def request(**changes):
    values = {
        "blocker": r.Blocker("B1", "implementation", "Blocked"),
        "contract": snapshot(), "context": {"x": 1}, "evidence": ["proof"],
        "boundaries": r.Boundaries(frozenset(r.ACTIONS), frozenset(r.ALLOWED_FIELDS)),
        "ledger": r.Ledger(),
        "proposed_resolution": r.Proposal("continue", {"guidance": "go"}, "within scope"),
        "clock": lambda: "now",
    }
    values.update(changes)
    return r.ResolverRequest(**values)


class ResolverTests(unittest.TestCase):
    def test_malformed_requests_escalate_without_dispatch_or_consumption(self):
        cases = [
            {"clock": "not-callable"},
            {"blocker": None},
            {"boundaries": r.Boundaries({"continue"}, frozenset())},
            {"context": object()},
            {"propose": lambda _: None, "review": None, "proposed_resolution": None},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                calls = []
                changes.setdefault("propose", lambda _: calls.append("proposal"))
                if "propose" in changes and changes["propose"] is not None:
                    changes.setdefault("review", lambda _, __: r.Review(True, "ok"))
                result = r.resolve(request(**changes))
                self.assertEqual(result[0].action, "escalate")
                self.assertIsNone(result[1].attempt)
                self.assertFalse(calls)
        self.assertEqual(r.resolve("not a request")[0].action, "escalate")

    def test_approval_attestation_and_classifier_fail_closed(self):
        calls = []
        callback_request = {"propose": lambda _: calls.append("proposal"), "review": lambda _, __: r.Review(True, "ok"), "proposed_resolution": None}
        bad_contracts = [
            snapshot(approval_status="draft"),
            snapshot(approval_event=None),
            snapshot(approval_event={"kind": "goal_approval", "actor": "model", "token": "wrong"}),
            snapshot(hash="not-sealed"),
        ]
        for contract in bad_contracts:
            denied = request(contract=contract, **callback_request)
            self.assertEqual(r.resolve(denied)[0].action, "escalate")
            self.assertFalse(denied.ledger.attempts)
        for kind in r.SAFE_KINDS:
            self.assertEqual(r.resolve(request(blocker=r.Blocker("B-" + kind, kind, "x")))[0].action, "continue")
        for kind in r.HUMAN_ONLY_KINDS | {"", "unknown"}:
            denied = request(blocker=r.Blocker("B-" + (kind or "empty"), kind, "x"), **callback_request)
            self.assertEqual(r.resolve(denied)[0].action, "escalate")
            self.assertFalse(denied.ledger.attempts)
        for flag in r.RISK_FLAGS | {"unknown"}:
            denied = request(blocker=r.Blocker("B-" + flag, "implementation", "x", risk_flags=(flag,)), **callback_request)
            self.assertEqual(r.resolve(denied)[0].action, "escalate")
            self.assertFalse(denied.ledger.attempts)
        self.assertFalse(calls)

    def test_exact_replay_and_budget_keys_are_distinct_and_immutable(self):
        stamp = {"value": "original"}
        first_request = request(clock=lambda: stamp)
        first = r.resolve(first_request)
        stamp["value"] = "mutated"
        replay = r.resolve(first_request)
        self.assertIs(first, replay)
        self.assertEqual(first[1].decided_at["value"], "original")
        with self.assertRaises(TypeError):
            first[0].payload["guidance"] = "mutate"
        varied = request(ledger=first_request.ledger, context={"changed": True}, proposed_resolution=r.Proposal("continue", {"guidance": "different"}, "new"))
        second = r.resolve(varied)
        exhausted = r.resolve(request(ledger=first_request.ledger, context={"again": True}, proposed_resolution=r.Proposal("continue", {"guidance": "x"}, "x")))
        self.assertEqual(second[1].attempt, 2)
        self.assertEqual(exhausted[0].action, "escalate")
        self.assertEqual(first[1].budget_key, second[1].budget_key)
        self.assertNotEqual(first[1].idempotency_key, second[1].idempotency_key)
        expected_budget = hashlib.sha256(json.dumps({"blocker_id": "B1", "task_id": "task", "revision": 1, "hash": first_request.contract.hash}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
        self.assertEqual(first[1].budget_key, expected_budget)
        independent = r.resolve(request(blocker=r.Blocker("B2", "implementation", "Blocked"), ledger=first_request.ledger))
        self.assertEqual(independent[1].attempt, 1)

    def test_budget_ignores_non_identity_declarative_changes(self):
        attested = snapshot()
        changed_event = r.ContractSnapshot(**{**attested.__dict__, "approval_event": {**attested.approval_event, "at": "later"}})
        variations = [
            {"blocker": r.Blocker("B1", "implementation", "Reworded")},
            {"context": {"changed": True}},
            {"evidence": ["other proof"]},
            {"boundaries": r.Boundaries(frozenset(r.ACTIONS), frozenset())},
            {"contract": changed_event},
            {"proposed_resolution": r.Proposal("continue", {"guidance": "different"}, "different")},
        ]
        for changes in variations:
            with self.subTest(changes=changes):
                ledger = r.Ledger()
                first = r.resolve(request(ledger=ledger))
                second = r.resolve(request(ledger=ledger, **changes))
                self.assertEqual((first[1].budget_key, second[1].budget_key, second[1].attempt), (first[1].budget_key, first[1].budget_key, 2))

    def test_mode_exclusivity_and_budget_exhaustion_precede_evaluation(self):
        calls = []
        propose = lambda _: calls.append("proposal") or r.Proposal("continue", {"guidance": "go"}, "rationale")
        for changes in (
            {"proposed_resolution": None},
            {"propose": propose, "review": lambda _, __: r.Review(True, "ok")},
            {"propose": propose, "review": None, "proposed_resolution": None},
        ):
            denied = request(**changes)
            self.assertEqual(r.resolve(denied)[0].action, "escalate")
            self.assertFalse(denied.ledger.attempts)
        ledger = r.Ledger()
        first = r.resolve(request(ledger=ledger, context={"try": 1}, propose=propose, review=lambda _, __: r.Review(False, "no"), proposed_resolution=None))
        second = r.resolve(request(ledger=ledger, context={"try": 2}, propose=propose, review=lambda _, __: r.Review(False, "no"), proposed_resolution=None))
        third = r.resolve(request(ledger=ledger, context={"try": 3}, propose=propose, review=lambda _, __: r.Review(False, "no"), proposed_resolution=None))
        self.assertEqual((first[0].action, second[0].action, third[0].action, calls), ("retry", "escalate", "escalate", ["proposal", "proposal"]))

    def test_callbacks_validate_review_and_preserve_audit_details(self):
        calls = []
        proposal = r.Proposal("continue", {"guidance": "go"}, "proposal rationale")
        valid = request(propose=lambda _: calls.append("proposal") or proposal, review=lambda _, __: calls.append("review") or r.Review(True, "accepted"), proposed_resolution=None)
        decision, receipt = r.resolve(valid)
        self.assertEqual((decision.action, calls, receipt.classifier_outcome, receipt.in_scope_reason), ("continue", ["proposal", "review"], "safe blocker", "proposal within boundaries"))
        for review in (r.Review(True, 7), r.Review(1, "approved"), r.Review(True, " ")):
            with self.subTest(review=review):
                malformed = request(propose=lambda _: proposal, review=lambda _, __: review, proposed_resolution=None)
                malformed_result = r.resolve(malformed)
                self.assertEqual((malformed_result[0].action, malformed_result[1].rationale), ("retry", "malformed review"))
                self.assertTrue(malformed.ledger.attempts)
        raising = request(propose=lambda _: proposal, review=lambda _, __: (_ for _ in ()).throw(RuntimeError("review failed")), proposed_resolution=None)
        failure = r.resolve(raising)
        self.assertEqual((failure[0].action, failure[1].proposal_rationale), ("retry", "proposal rationale"))
        veto = request(propose=lambda _: proposal, review=lambda _, __: r.Review(False, "no"), proposed_resolution=None)
        self.assertEqual((r.resolve(veto)[0].action, r.resolve(veto)[1].review_verdict), ("retry", "vetoed"))

    def test_replan_scope_payload_and_semantic_invariants(self):
        candidate = body()
        candidate["technical_approach"] = ["New approach"]
        accepted = r.resolve(request(proposed_resolution=r.Proposal("replan", {"body": candidate}, "change plan")))
        self.assertEqual((accepted[0].action, accepted[1].new_lineage["revision"]), ("replan", 2))
        for field, changed in (("end_to_end_flow", ["New flow"]), ("technical_approach", ["New approach"]), ("milestones", [{"id": "M1", "objective": "New objective", "acceptance_criteria": ["AC1"]}])):
            with self.subTest(field=field):
                updated = body()
                updated[field] = changed
                self.assertEqual(r.resolve(request(proposed_resolution=r.Proposal("replan", {"body": updated}, "change plan")))[0].action, "replan")
        protected = copy.deepcopy(candidate)
        protected["intended_user"] = "Changed"
        self.assertEqual(r.resolve(request(proposed_resolution=r.Proposal("replan", {"body": protected}, "bad")))[0].action, "retry")
        protected = copy.deepcopy(candidate)
        protected["acceptance_criteria"][0]["human_review"] = True
        self.assertEqual(r.resolve(request(proposed_resolution=r.Proposal("replan", {"body": protected}, "bad")))[0].action, "retry")
        restricted = r.resolve(request(boundaries=r.Boundaries(frozenset(r.ACTIONS), frozenset()), proposed_resolution=r.Proposal("replan", {"body": candidate}, "bad")))
        self.assertEqual(restricted[0].action, "retry")
        for key in ("deliverables", "required_behaviors", "permission_boundaries"):
            invalid = body()
            invalid[key] = []
            denied = request(contract=snapshot(invalid))
            self.assertEqual(r.resolve(denied)[0].action, "escalate")
            self.assertFalse(denied.ledger.attempts)
        invalid = body()
        invalid["milestones"][0]["acceptance_criteria"] = ["missing"]
        self.assertEqual(r.resolve(request(proposed_resolution=r.Proposal("replan", {"body": invalid}, "bad")))[0].action, "retry")
        invalid = body()
        invalid["acceptance_criteria"] = []
        self.assertEqual(r.resolve(request(contract=snapshot(invalid)))[0].action, "escalate")
        legacy = body()
        legacy.pop("technical_approach")
        self.assertEqual(r.resolve(request(contract=snapshot(legacy)))[0].action, "escalate")
        unknown = body()
        unknown["role_output"] = "bad"
        self.assertEqual(r.resolve(request(contract=snapshot(unknown)))[0].action, "escalate")

    def test_semantic_mutations_fail_for_baselines_and_candidates(self):
        baseline_mutations = []
        duplicate = body()
        duplicate["acceptance_criteria"].append(copy.deepcopy(duplicate["acceptance_criteria"][0]))
        baseline_mutations.append(duplicate)
        delegated = body()
        delegated["delegated_decisions"] = [{"text": "Decision", "basis": "agent_proposed", "answer_id": ""}]
        baseline_mutations.append(delegated)
        for invalid in baseline_mutations:
            with self.subTest(baseline=invalid):
                denied = request(contract=snapshot(invalid))
                self.assertEqual(r.resolve(denied)[0].action, "escalate")
                self.assertFalse(denied.ledger.attempts)
        cycle = body()
        cycle["milestones"] = [
            {"id": "M1", "objective": "First", "acceptance_criteria": ["AC1"], "depends_on": ["M2"]},
            {"id": "M2", "objective": "Second", "acceptance_criteria": ["AC1"], "depends_on": ["M1"]},
        ]
        result = r.resolve(request(proposed_resolution=r.Proposal("replan", {"body": cycle}, "cycle")))
        self.assertEqual((result[0].action, result[1].rationale), ("retry", "invalid candidate body"))

    def test_non_replan_contract_payloads_and_malformed_proposals_consume_attempts(self):
        for action in ("continue", "retry", "escalate"):
            with self.subTest(action=action):
                result = r.resolve(request(proposed_resolution=r.Proposal(action, {"guidance": "x", "nested": {"contract_revision": 2}}, "bad")))
                self.assertEqual((result[0].action, result[1].attempt), ("retry", 1))
        typed = r.resolve(request(proposed_resolution=r.Proposal("continue", {"guidance": 3}, "bad")))
        self.assertEqual((typed[0].action, typed[1].attempt), ("retry", 1))
        malformed = r.resolve(request(proposed_resolution=r.Proposal("continue", {"guidance": "x"}, "")))
        self.assertEqual((malformed[0].action, malformed[1].attempt), ("retry", 1))

    def test_malformed_ledger_fails_before_attempt_or_callback(self):
        for count in ("bad", -1, True, 1.5):
            with self.subTest(count=count):
                calls = []
                req = request(propose=lambda _: calls.append("propose"),
                              review=lambda _, __: r.Review(True, "ok"),
                              proposed_resolution=None)
                key = r._digest({"blocker_id": req.blocker.id, "task_id": req.contract.task_id,
                                 "revision": req.contract.revision, "hash": req.contract.hash})
                req.ledger.attempts[key] = count
                decision, receipt = r.resolve(req)
                self.assertEqual((decision.action, receipt.attempt, calls), ("escalate", None, []))
                self.assertEqual(req.ledger.attempts[key], count)
        for mutation in (
            lambda ledger: ledger.outcomes.update({"key": 3}),
            lambda ledger: ledger.cache.update({"key": "not a decision pair"}),
        ):
            req = request()
            mutation(req.ledger)
            self.assertEqual(r.resolve(req)[0].action, "escalate")
            self.assertFalse(req.ledger.attempts)

    def test_initial_task_must_be_a_ready_task_for_its_own_milestone(self):
        valid = body()
        valid["initial_task"] = {"objective": "Build", "affected_paths": ["resolver.py"],
                                 "kind": "implement", "milestone_id": "M1",
                                 "requirements": ["Implement API"], "acceptance_criteria": ["AC1"],
                                 "validation_plan": ["Run tests"]}
        self.assertEqual(r.resolve(request(contract=snapshot(valid)))[0].action, "continue")
        invalid_bodies = []
        for field, value in (("kind", "none"), ("kind", []), ("milestone_id", "missing"),
                             ("requirements", []), ("validation_plan", []),
                             ("acceptance_criteria", ["AC1", "AC1"])):
            invalid = copy.deepcopy(valid)
            invalid["initial_task"][field] = value
            invalid_bodies.append(invalid)
        cross_milestone = copy.deepcopy(valid)
        cross_milestone["acceptance_criteria"].append({"id": "AC2", "criterion": "Second",
                                                        "verification_method": "Test", "human_review": False})
        cross_milestone["milestones"].append({"id": "M2", "objective": "Second",
                                               "acceptance_criteria": ["AC2"]})
        cross_milestone["initial_task"]["milestone_id"] = "M2"
        invalid_bodies.append(cross_milestone)
        for invalid in invalid_bodies:
            with self.subTest(initial_task=invalid["initial_task"]):
                req = request(contract=snapshot(invalid))
                decision, receipt = r.resolve(req)
                self.assertEqual((decision.action, receipt.attempt), ("escalate", None))
                self.assertFalse(req.ledger.attempts)

    def test_source_purity_and_constants(self):
        source = pathlib.Path(r.__file__).read_text()
        self.assertNotIn("import tools", source)
        self.assertNotIn("gpt-", source.lower())
        self.assertEqual(r.ALLOWED_FIELDS, frozenset({"end_to_end_flow", "technical_approach", "milestones"}))
        self.assertEqual(r.HUMAN_ONLY_KINDS, frozenset({"permission", "external_permission", "destructive", "security", "access", "protected_data"}))
        self.assertEqual(r.SAFE_KINDS, frozenset({"implementation", "validation", "milestone", "tooling", "plan_detail", "model_output"}))
        self.assertEqual(r.RISK_FLAGS, frozenset({"permission", "external-system", "destructive", "security-sensitive", "access", "protected-data"}))


if __name__ == "__main__":
    unittest.main()
