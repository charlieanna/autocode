"""Pure completion reader tests with manually constructed writer checkpoints."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tools import autocode_completion as completion
from tools import autocode_contract_identity as contracts
from tools import autocode_progressive_artifacts as artifacts
from tools import autocode_progressive_completion as progressive
from tools import autocode_progressive_plan as plan
from tools import autocode_util as util
from tools import autocode_verification_plan as verification_plan


class ProgressiveCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.evidence = Path(self.temp.name) / "replay.txt"
        self.evidence.write_text("runner executed A and B successfully", encoding="ascii")
        self.check_a = {"id": "A", "relation": "contributes_to", "criterion_ids": ["C1"],
                        "method": "python -m unittest tests.test_a"}
        self.check_b = {"id": "B", "relation": "fully_verify", "criterion_ids": ["C1", "C2"],
                        "method": "python -m unittest tests.test_b"}
        first = {"id": "S1", "intended_result": "Read and persist progress", "criterion_ids": ["C1"],
                 "paths": ["src/app.py"], "depends_on": [], "tentative": False, "checks": [self.check_a]}
        second = {**deepcopy(first), "id": "S2", "criterion_ids": ["C1", "C2"],
                  "tentative": True, "checks": [self.check_b]}
        initial = {"version": 1, "needed_because": "Two useful deliveries", "shared_decisions": [],
                   "outstanding_criteria": [], "done_slices": [], "slices": [first, second]}
        body = {"acceptance_criteria": [{"id": "C1"}, {"id": "C2"}], "open_blocking_questions": [],
                **plan.disclosure(initial, ["C1", "C2"])}
        contract = {"task_id": "T", "revision": 1, "body": body, "approval_status": "approved"}
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        self.token = contracts.token(contract)
        event = {"actor": "user_cli", "token": self.token}
        contract["approval_event"] = event
        self.current = {"revision": "source-final"}
        self.proposal = {**deepcopy(initial), "done_slices": ["S1"],
                         "slices": [{**deepcopy(second), "tentative": False, "depends_on": ["S1"]}]}
        self.record = {"version": 1, "delegation": plan.seal_delegation(initial, self.token),
                       "initial_plan": {"proposal": deepcopy(initial), "plan_hash": plan.plan_identity(initial)},
                       "future": [], "outstanding_criteria": [],
                       "required_checks": [self.check_a, self.check_b]}
        self.state = {"run_dir": self.temp.name, "goal_contract": contract, "user_events": [event],
                      "progressive": self.record}
        self.publish_active()
        old = self.payload("S1", "old-plan", "source-before-S2", [self.check_a], ["S1"], [])
        old_artifact = self.persist_checkpoint(old, "initial-artifact")
        self.record["history"] = [{"slice_id": "S1", "plan_hash": "old-plan",
                                   "artifact": old_artifact, "required_checks": [self.check_a]}]
        self.proof = self.payload("S2", self.record["active"]["plan_hash"], self.current["revision"],
                                  self.record["required_checks"], ["S1", "S2"], ["C1", "C2"])
        self.publish_proof()

    def receipt(self, check, source):
        pins = {str(self.evidence): util.file_hash(self.evidence)}
        return {"status": "PASS", "exit_code": 0, "check_hash": plan.check_identity(check),
                "contract_token": self.token, "source_revision": source, "evidence_hashes": pins,
                "replayed": True, "executions": [{"command": command, "status": "PASS", "exit_code": 0,
                                                  "evidence_hashes": dict(pins)}
                                                 for command in plan.check_commands(check)]}

    def payload(self, sid, plan_hash, source, checks, slices, criteria):
        return {"version": 1, "contract_token": self.token, "plan_hash": plan_hash,
                "source_revision": source, "slice_id": sid, "required_checks": deepcopy(checks),
                "results": {check["id"]: self.receipt(check, source) for check in checks},
                "verified_slices": slices, "criterion_ids": criteria}

    def persist_checkpoint(self, payload, predecessor):
        envelope, _ = artifacts.prepare("checkpoint", contract_token=payload["contract_token"],
            predecessor_identity=predecessor, candidate_identity=payload["plan_hash"],
            plan_identity=payload["plan_hash"], source_snapshot_identity=payload["source_revision"], report=payload)
        return artifacts.persist(self.temp.name, envelope)

    def publish_active(self):
        ph = plan.plan_identity(self.proposal)
        bindings = {"contract_token": self.token, "predecessor_identity": "old-plan",
                    "candidate_identity": ph, "plan_identity": ph, "source_snapshot_identity": "review-source"}
        envelope, _ = artifacts.prepare("revision", report={"proposal": self.proposal}, **bindings)
        candidate = artifacts.persist(self.temp.name, envelope)
        envelope, _ = artifacts.prepare("review", report={"accepted": True,
                                                         "candidate_sha256": candidate["sha256"]}, **bindings)
        review = artifacts.persist(self.temp.name, envelope)
        self.record["active"] = {"definition": deepcopy(self.proposal["slices"][0]), "plan_hash": ph,
                                 "artifact": candidate, "review": review}

    def publish_proof(self):
        artifact = self.persist_checkpoint(self.proof, self.record["active"]["artifact"]["sha256"])
        self.record["completion_proof"] = {**deepcopy(self.proof), "artifact": artifact}
        entry = {"slice_id": "S2", "plan_hash": self.proof["plan_hash"], "artifact": artifact,
                 "required_checks": deepcopy(self.proof["required_checks"])}
        self.record["history"] = self.record["history"][:1] + [entry]

    def approve_product_revision(self, *, verification_method=None, scope_exclusions=None, show_retirement=True):
        """Construct a new real approval without rewriting the earlier checkpoint."""
        old = deepcopy(self.state["goal_contract"])
        self.state["contract_history"] = [*self.state.get("contract_history", []), old]
        current = deepcopy(old)
        current["revision"] += 1
        current["body"]["constraints"].append("Approved product revision: retain progress across lesson updates")
        if verification_method is not None:
            current["body"]["acceptance_criteria"][0]["verification_method"] = verification_method
        if scope_exclusions is not None:
            current["body"]["scope_exclusions"] = scope_exclusions
            if show_retirement:
                current["body"]["constraints"] += [line for line in scope_exclusions
                                                     if line.startswith("Progressive check retirement: ")]
        current["hash"] = util.digest({key: current[key] for key in ("task_id", "revision", "body")})
        self.token = contracts.token(current)
        event = {"actor": "user_cli", "token": self.token}
        current["approval_event"] = event
        self.state["goal_contract"] = current
        self.state["user_events"].append(event)
        self.record["delegation"] = plan.seal_delegation(self.record["initial_plan"]["proposal"], self.token)
        self.current = {"revision": "source-after-product-change"}
        self.publish_active()
        self.proof = self.payload("S2", self.record["active"]["plan_hash"], self.current["revision"],
                                  self.record["required_checks"], ["S1", "S2"], ["C1", "C2"])
        self.publish_proof()
        return old

    def prepare_retirement(self, *, claimed_check=None, canonical=True, show_retirement=True):
        """Preserve A and an old removed demonstration under the prior approval."""
        removed = {"id": "removed-exercise", "relation": "contributes_to", "criterion_ids": ["C1"],
                   "method": "python -m unittest tests.test_removed_exercise"}
        past = self.payload("S1", "old-plan", "source-before-S2", [self.check_a, removed], ["S1"], [])
        self.record["history"][0] = {"slice_id": "S1", "plan_hash": "old-plan",
                                     "artifact": self.persist_checkpoint(past, "initial-artifact"),
                                     "required_checks": deepcopy(past["required_checks"])}
        claimed = removed if claimed_check is None else claimed_check
        retirement = {"check_id": claimed["id"], "check_hash": plan.check_identity(claimed),
                      "removes": "Remove the exercise demonstration; retain learner progress and the full journey"}
        line = "Progressive check retirement: " + json.dumps(retirement, sort_keys=True, separators=(",", ":"))
        if not canonical:
            line = "Progressive check retirement: " + json.dumps(retirement)
        old = self.approve_product_revision(scope_exclusions=[line], show_retirement=show_retirement)
        grant = {"kind": "product_change", **retirement, "contract_token": self.token, "visible_removal": line}
        return removed, old, grant

    def publish_retirement(self, removed, old, grant, *, report_changes=None, bindings=None):
        proposal = deepcopy(self.record["initial_plan"]["proposal"])
        report = {"proposal": proposal, "contract_body": deepcopy(self.state["goal_contract"]["body"]),
                  "retired_checks": [deepcopy(removed)], "predecessor_contract_token": contracts.token(old),
                  **(report_changes or {})}
        bindings = {"contract_token": grant["contract_token"], "predecessor_identity": None,
                    "candidate_identity": plan.plan_identity(proposal), "plan_identity": plan.plan_identity(proposal),
                    "source_snapshot_identity": "reviewed-renewal-source", **(bindings or {})}
        envelope, _ = artifacts.prepare("proposal", report=report, **bindings)
        grant = {**grant, "artifact": artifacts.persist(self.temp.name, envelope)}
        self.record["retirements"] = [grant]
        return grant

    def test_visible_retirement_line_alone_is_not_a_grant(self):
        self.prepare_retirement()
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_retirement_hidden_inside_complete_plan_does_not_authorize_removal(self):
        removed, old, grant = self.prepare_retirement(show_retirement=False)
        self.publish_retirement(removed, old, grant)
        self.assertTrue(contracts.approved(self.state))
        self.assertIn(grant["visible_removal"], self.state["goal_contract"]["body"]["scope_exclusions"])
        self.assertNotIn(grant["visible_removal"], self.state["goal_contract"]["body"]["constraints"])
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_exact_approved_retirement_preserves_history_unrelated_checks_and_findings(self):
        removed, old, grant = self.prepare_retirement()
        self.publish_retirement(removed, old, grant)
        self.state["findings_ledger"] = {"entries": [{"id": "existing-product-blocker", "blocking": True}]}
        before = deepcopy(self.state)
        self.assertTrue(progressive.ready(self.state, self.current))
        self.assertEqual(self.state, before)
        self.assertEqual(set(self.proof["results"]), {"A", "B"})
        past = artifacts.verify(self.temp.name, self.record["history"][0]["artifact"])["report"]
        self.assertEqual(past["contract_token"], contracts.token(old))
        self.assertEqual(set(past["results"]), {"A", removed["id"]})
        self.assertEqual(past["results"][removed["id"]]["source_revision"], "source-before-S2")
        with patch("tools.autocode_findings.blocking_entries", return_value=[{"id": "existing-product-blocker"}]):
            self.state["version"] = 2
            self.assertFalse(completion.completion_ready(self.state, {"status": "COMPLETE"}, self.current))

    def test_previously_approved_retirement_remains_authenticated_after_later_goal_revision(self):
        removed, old, grant = self.prepare_retirement()
        approved = self.publish_retirement(removed, old, grant)
        self.approve_product_revision()
        self.assertNotEqual(approved["contract_token"], self.token)
        self.assertTrue(progressive.ready(self.state, self.current))
        retirement_contract = self.state["contract_history"][-1]
        self.state["user_events"].remove(retirement_contract["approval_event"])
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_forged_missing_changed_or_unauthenticated_retirement_grants_refuse(self):
        removed, old, grant = self.prepare_retirement()
        valid = self.publish_retirement(removed, old, grant)
        for changes in ({"kind": "model"}, {"check_id": "unknown"}, {"check_hash": "0" * 64},
                        {"removes": "another obligation"}, {"contract_token": contracts.token(old)},
                        {"contract_token": "unapproved-token"}, {"visible_removal": grant["visible_removal"] + " "},
                        {"artifact": {}}, {"artifact": self.record["history"][0]["artifact"]},
                        {"approved_by": "user"}, {"authenticated": True}):
            self.record["retirements"] = [{**valid, **changes}]
            self.assertFalse(progressive.ready(self.state, self.current))
        for grants in (None, True, [True], [grant], [valid, valid]):
            self.record["retirements"] = grants
            self.assertFalse(progressive.ready(self.state, self.current))
        self.record["retirements"] = [valid]
        self.state["goal_contract"]["body"]["scope_exclusions"][0] += "changed"
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_retirement_artifact_must_bind_exact_body_old_definition_and_approved_predecessor(self):
        removed, old, grant = self.prepare_retirement()
        changed = {**deepcopy(removed), "method": "python -m unittest tests.test_other"}
        for report_changes in ({"contract_body": {}}, {"retired_checks": []}, {"retired_checks": [changed]},
                               {"predecessor_contract_token": self.token},
                               {"predecessor_contract_token": "unknown-old-approval"},
                               {"proposal": {**self.proposal, "shared_decisions": ["tampered"]}}):
            self.publish_retirement(removed, old, grant, report_changes=report_changes)
            self.assertFalse(progressive.ready(self.state, self.current))
        for bindings in ({"contract_token": contracts.token(old)}, {"candidate_identity": "changed-candidate"},
                         {"plan_identity": "changed-plan"}):
            self.publish_retirement(removed, old, grant, bindings=bindings)
            self.assertFalse(progressive.ready(self.state, self.current))
        valid = self.publish_retirement(removed, old, grant)
        path = Path(self.temp.name) / valid["artifact"]["path"]
        contents = path.read_bytes()
        path.write_bytes(contents + b" ")
        self.assertFalse(progressive.ready(self.state, self.current))
        path.unlink()
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_retirement_cannot_remove_unrelated_check_or_current_full_product_proof(self):
        removed, old, grant = self.prepare_retirement()
        self.publish_retirement(removed, old, grant)
        valid = deepcopy(self.proof)
        for check_id in ("A", "B"):
            self.record["required_checks"] = [check for check in valid["required_checks"] if check["id"] != check_id]
            self.proof = deepcopy(valid)
            self.proof["required_checks"] = deepcopy(self.record["required_checks"])
            self.proof["results"].pop(check_id)
            self.publish_proof()
            self.assertFalse(progressive.ready(self.state, self.current))
        self.record["required_checks"] = deepcopy(valid["required_checks"])
        self.proof = deepcopy(valid)
        self.proof["results"]["B"]["status"] = "FAIL"
        self.publish_proof()
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_retirement_cannot_apply_to_kept_check_or_changed_definition_of_same_id(self):
        removed, old, grant = self.prepare_retirement()
        self.publish_retirement(removed, old, grant)
        for check in (removed, {**removed, "method": "python -m unittest tests.test_replacement"}):
            self.record["required_checks"] = [self.check_a, self.check_b, check]
            self.proof = self.payload("S2", self.record["active"]["plan_hash"], self.current["revision"],
                                      self.record["required_checks"], ["S1", "S2"], ["C1", "C2"])
            self.publish_proof()
            self.assertFalse(progressive.ready(self.state, self.current))

    def test_approved_line_and_consistent_artifact_cannot_retire_unknown_or_stale_definition(self):
        for claimed in ({"id": "unknown-check", "relation": "contributes_to", "criterion_ids": ["C1"],
                         "method": "python -m unittest tests.test_unknown"},
                        {"id": "removed-exercise", "relation": "contributes_to", "criterion_ids": ["C1"],
                         "method": "python -m unittest tests.test_changed_definition"}):
            removed, old, grant = self.prepare_retirement(claimed_check=claimed)
            self.publish_retirement(claimed, old, grant)
            self.assertFalse(progressive.ready(self.state, self.current))

    def test_criterion_rename_alone_cannot_discard_unrelated_historical_obligation(self):
        removed, old, grant = self.prepare_retirement()
        self.publish_retirement(removed, old, grant)
        self.record["required_checks"] = [{**self.check_a, "criterion_ids": ["C2"]}, self.check_b]
        self.proof = self.payload("S2", self.record["active"]["plan_hash"], self.current["revision"],
                                  self.record["required_checks"], ["S1", "S2"], ["C1", "C2"])
        self.publish_proof()
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_noncanonical_approved_line_and_workflow_policy_are_not_user_retirement_authority(self):
        removed, old, grant = self.prepare_retirement(canonical=False)
        self.publish_retirement(removed, old, grant)
        self.assertFalse(progressive.ready(self.state, self.current))
        removed, old, grant = self.prepare_retirement()
        self.publish_retirement(removed, old, grant)
        event = {"actor": "workflow_policy", "token": self.token}
        self.state["goal_contract"].update(origin="bugfix_small_correction", approval_event=event)
        self.state["user_events"].append(event)
        self.assertTrue(contracts.approved(self.state))
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_uncheckpointed_obligation_requires_authentic_archived_reviewed_definition(self):
        removed, old, grant = self.prepare_retirement()
        self.publish_retirement(removed, old, grant)
        initial = deepcopy(self.record["initial_plan"])
        proposal = deepcopy(initial["proposal"])
        proposal["slices"][0]["checks"].append(removed)
        ph = plan.plan_identity(proposal)
        bindings = {"contract_token": contracts.token(old), "predecessor_identity": initial["plan_hash"],
                    "candidate_identity": ph, "plan_identity": ph, "source_snapshot_identity": "old-reviewed-source"}
        envelope, _ = artifacts.prepare("revision", report={"proposal": proposal}, **bindings)
        candidate = artifacts.persist(self.temp.name, envelope)
        envelope, _ = artifacts.prepare("review", report={"accepted": True,
                                                         "candidate_sha256": candidate["sha256"]}, **bindings)
        reviewed = artifacts.persist(self.temp.name, envelope)
        archived = {"initial_plan": initial, "delegation": plan.seal_delegation(initial["proposal"], contracts.token(old)),
                    "active": {"definition": proposal["slices"][0], "plan_hash": ph,
                               "artifact": candidate, "review": reviewed}}
        entry = self.record["history"][0]
        past = artifacts.verify(self.temp.name, entry["artifact"])["report"]
        past["required_checks"] = [self.check_a]
        past["results"].pop(removed["id"])
        self.record["history"][0] = {**entry, "required_checks": [self.check_a],
                                    "artifact": self.persist_checkpoint(past, "initial-artifact")}
        self.assertFalse(progressive.ready(self.state, self.current))
        self.record["execution_history"] = [archived]
        before = deepcopy(self.state)
        self.assertTrue(progressive.ready(self.state, self.current))
        self.assertEqual(self.state, before)
        archived["active"]["review"] = self.record["active"]["review"]
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_complete_current_cumulative_product_proof_is_read_only(self):
        before = deepcopy(self.state)
        self.assertTrue(progressive.ready(self.state, self.current))
        self.assertEqual(self.state, before)

    def test_approved_product_revision_preserves_old_checkpoint_token_and_source(self):
        entry = deepcopy(self.record["history"][0])
        path = Path(self.temp.name) / entry["artifact"]["path"]
        original_bytes = path.read_bytes()
        old = self.approve_product_revision()
        before = deepcopy(self.state)
        self.assertTrue(progressive.ready(self.state, self.current))
        self.assertEqual(self.state, before)
        self.assertEqual(self.record["history"][0], entry)
        self.assertEqual(path.read_bytes(), original_bytes)
        past = artifacts.verify(self.temp.name, entry["artifact"])["report"]
        self.assertEqual(past["contract_token"], contracts.token(old))
        self.assertEqual(past["results"]["A"]["contract_token"], contracts.token(old))
        self.assertEqual(past["results"]["A"]["source_revision"], "source-before-S2")
        self.assertNotEqual(past["contract_token"], self.token)
        self.assertNotEqual(past["source_revision"], self.current["revision"])

    def test_historical_token_requires_sealed_approved_contract_and_actual_user_event(self):
        old = self.approve_product_revision()
        changed_body = deepcopy(old["body"])
        changed_body["constraints"].append("unapproved mutation")
        for change in ({"approval_status": "proposed"}, {"hash": "tampered"},
                       {"body": changed_body},
                       {"approval_event": {"actor": "model", "token": contracts.token(old)}},
                       {"approval_event": {"actor": "user_cli", "token": "wrong-approval-token"}},
                       {"approval_event": True}, {"revision": True}):
            with self.subTest(change=change):
                altered = {**deepcopy(old), **change}
                self.state["contract_history"] = [altered]
                self.state["user_events"] = [old["approval_event"], self.state["goal_contract"]["approval_event"],
                                              altered.get("approval_event")]
                self.assertFalse(progressive.ready(self.state, self.current))
        self.state["contract_history"] = [old]
        self.state["user_events"] = [self.state["goal_contract"]["approval_event"]]
        self.assertFalse(progressive.ready(self.state, self.current))
        self.state["user_events"].append(old["approval_event"])
        self.assertTrue(progressive.ready(self.state, self.current))
        for history in (None, True, [True], [contracts.token(old)], [{"past_token": contracts.token(old)}], []):
            self.state["contract_history"] = history
            self.assertFalse(progressive.ready(self.state, self.current))

    def test_approved_other_task_or_nonprevious_contract_cannot_authenticate_history(self):
        old = self.approve_product_revision()
        original = deepcopy(self.record["history"][0])
        report = artifacts.verify(self.temp.name, original["artifact"])["report"]
        for changes in ({"task_id": "unrelated-task"}, {"revision": 2}, {"revision": 3}):
            contract = {**deepcopy(old), **changes}
            contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
            token = contracts.token(contract)
            event = {"actor": "user_cli", "token": token}
            contract["approval_event"] = event
            self.state["contract_history"] = [contract]
            self.state["user_events"] = [event, self.state["goal_contract"]["approval_event"]]
            self.assertTrue(contracts.approved({"goal_contract": contract, "user_events": self.state["user_events"]}))
            past = deepcopy(report)
            past["contract_token"] = token
            past["results"]["A"]["contract_token"] = token
            self.record["history"][0] = {**original, "artifact": self.persist_checkpoint(past, "initial-artifact")}
            self.assertFalse(progressive.ready(self.state, self.current))

    def test_unknown_or_stale_historical_token_is_not_authority_even_with_consistent_receipts(self):
        old = self.approve_product_revision()
        original = deepcopy(self.record["history"][0])
        report = artifacts.verify(self.temp.name, original["artifact"])["report"]
        for stale in ("arbitrary-past-token", "r0:" + old["hash"], "r1:" + "0" * 64):
            past = deepcopy(report)
            past["contract_token"] = stale
            past["results"]["A"]["contract_token"] = stale
            artifact = self.persist_checkpoint(past, "initial-artifact")
            self.record["history"][0] = {**original, "artifact": artifact}
            self.assertFalse(progressive.ready(self.state, self.current))

    def test_historical_receipt_is_checked_against_own_token_and_source_not_current(self):
        self.approve_product_revision()
        original = deepcopy(self.record["history"][0])
        report = artifacts.verify(self.temp.name, original["artifact"])["report"]
        for changes in ({"contract_token": self.token}, {"source_revision": self.current["revision"]}):
            past = deepcopy(report)
            past["results"]["A"].update(changes)
            artifact = self.persist_checkpoint(past, "initial-artifact")
            self.record["history"][0] = {**original, "artifact": artifact}
            self.assertFalse(progressive.ready(self.state, self.current))

    def test_current_proof_still_requires_current_contract_source_and_fresh_cumulative_replay(self):
        old = self.approve_product_revision()
        valid = deepcopy(self.proof)
        past = artifacts.verify(self.temp.name, self.record["history"][0]["artifact"])["report"]
        for changes in ({"contract_token": contracts.token(old)}, {"source_revision": past["source_revision"]}):
            self.proof = {**deepcopy(valid), **changes}
            for receipt in self.proof["results"].values():
                receipt.update(changes)
            self.publish_proof()
            self.assertFalse(progressive.ready(self.state, self.current))
        self.proof = deepcopy(valid)
        self.proof["results"]["A"] = deepcopy(past["results"]["A"])
        self.publish_proof()
        self.assertFalse(progressive.ready(self.state, self.current))
        self.proof = valid
        self.publish_proof()
        self.assertTrue(progressive.ready(self.state, self.current))

    def test_product_change_cannot_drop_old_obligations_without_authenticated_retirement_integration(self):
        self.approve_product_revision()
        self.record["required_checks"] = [self.check_b]
        self.proof["required_checks"] = [self.check_b]
        self.proof["results"].pop("A")
        self.publish_proof()
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_original_explicit_product_command_cannot_be_omitted_by_local_full_verification(self):
        self.approve_product_revision(verification_method="python -m unittest tests.test_product")
        self.assertEqual(progressive.ledger.require_active(self.state), self.record["active"])
        self.assertTrue(all(receipt["status"] == "PASS" for receipt in self.proof["results"].values()))
        missing = verification_plan.product_checks(self.state["goal_contract"]["body"], self.record["required_checks"])
        self.assertEqual([check["method"] for check in missing], ["python -m unittest tests.test_product"])
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_canonical_product_check_requires_persisted_current_replay_receipt(self):
        self.approve_product_revision(verification_method="python -m unittest tests.test_product")
        body = self.state["goal_contract"]["body"]
        product = verification_plan.product_checks(body, self.record["required_checks"])
        self.record["required_checks"].extend(product)
        self.assertEqual(verification_plan.product_checks(body, self.record["required_checks"]), [])
        self.proof = self.payload("S2", self.record["active"]["plan_hash"], self.current["revision"],
                                  self.record["required_checks"], ["S1", "S2"], ["C1", "C2"])
        self.publish_proof()
        before = deepcopy(self.state)
        self.assertTrue(progressive.ready(self.state, self.current))
        self.assertEqual(self.state, before)
        report = artifacts.verify(self.temp.name, self.record["completion_proof"]["artifact"])["report"]
        self.assertEqual(report["required_checks"][-1], product[0])
        receipt = report["results"][product[0]["id"]]
        self.assertEqual(receipt["check_hash"], plan.check_identity(product[0]))
        self.assertEqual(receipt["contract_token"], self.token)
        self.assertEqual(receipt["source_revision"], self.current["revision"])
        self.assertEqual(receipt["executions"][0]["command"], "python -m unittest tests.test_product")
        self.proof["results"].pop(product[0]["id"])
        self.publish_proof()
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_original_product_command_already_represented_needs_no_duplicate_check(self):
        self.approve_product_revision(verification_method=self.check_b["method"])
        self.assertEqual(verification_plan.product_checks(self.state["goal_contract"]["body"],
                                                        self.record["required_checks"]), [])
        self.assertTrue(progressive.ready(self.state, self.current))

    def test_original_prose_method_is_not_guessed_as_product_command(self):
        self.approve_product_revision(verification_method="Inspect the complete learner journey and saved progress")
        self.assertEqual(verification_plan.product_checks(self.state["goal_contract"]["body"],
                                                        self.record["required_checks"]), [])
        self.assertTrue(progressive.ready(self.state, self.current))

    def test_ordinary_absence_and_candidate_only_are_not_completion_success(self):
        for state in ({}, {"progressive": {"version": 1}},
                      {"progressive": {"version": 1, "candidate": {"proposal": self.proposal}}}):
            with self.subTest(state=state):
                self.assertTrue(progressive.ready(state, {}))
                self.assertFalse(completion.completion_ready(state, {}, {}))

    def test_disclosure_without_ledger_or_grant_refuses(self):
        for field in ("constraints", "technical_approach"):
            for marker in (plan.DISCLOSURE_DELEGATION, plan.DISCLOSURE_SLICE, plan.DISCLOSURE_OUTSTANDING):
                state = {"goal_contract": {"body": {field: [marker + " visible"]}}}
                self.assertFalse(progressive.ready(state, self.current))

    def test_unknown_ledger_and_missing_approved_delegation_refuse(self):
        for record in (None, [], True, {"version": 2}, {"version": 1, "unknown": True},
                       {**self.record, "delegation": {}}, {**self.record, "version": True}):
            with self.subTest(record=record):
                self.assertFalse(progressive.ready({**self.state, "progressive": record}, self.current))
        self.state["user_events"] = []
        self.assertFalse(progressive.ready(self.state, self.current))
        self.assertFalse(progressive.ready({"progressive": {"version": True}}, self.current))

    def test_future_or_outstanding_map_cannot_be_hidden(self):
        for key in ("future", "outstanding_criteria"):
            saved = self.record.pop(key)
            self.assertFalse(progressive.ready(self.state, self.current))
            self.record[key] = ["unbuilt"]
            self.assertFalse(progressive.ready(self.state, self.current))
            self.record[key] = saved
        self.proposal["slices"].append({**deepcopy(self.proposal["slices"][0]), "id": "S3", "tentative": True})
        self.publish_active()
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_every_state_and_persisted_proof_identity_must_match(self):
        for field in ("contract_token", "plan_hash", "source_revision", "slice_id", "version"):
            saved = self.record["completion_proof"][field]
            self.record["completion_proof"][field] = "stale"
            self.assertFalse(progressive.ready(self.state, self.current))
            self.record["completion_proof"][field] = saved
        self.assertFalse(progressive.ready(self.state, {"revision": "changed-source"}))
        self.assertFalse(progressive.ready(self.state, {}))
        for field in ("contract_token", "plan_hash", "source_revision"):
            saved = self.proof[field]
            self.proof[field] = "stale-but-persisted"
            self.publish_proof()
            self.assertFalse(progressive.ready(self.state, self.current))
            self.proof[field] = saved
        self.publish_proof()
        self.assertTrue(progressive.ready(self.state, self.current))

    def test_false_skipped_failed_and_incomplete_receipts_refuse_even_when_persisted(self):
        original = deepcopy(self.proof["results"]["A"])
        for changes in ({"status": "FAIL"}, {"status": "SKIPPED"}, {"status": True}, {"status": {"PASS": True}},
                        {"exit_code": False}, {"exit_code": 1}, {"replayed": False}, {"replayed": {}},
                        {"executions": []}, {"evidence_hashes": {}}, {"check_hash": "stale"},
                        {"source_revision": "prior-pass"}, {"contract_token": "old-grant"}):
            with self.subTest(changes=changes):
                self.proof["results"]["A"] = {**original, **changes}
                self.publish_proof()
                self.assertFalse(progressive.ready(self.state, self.current))
        self.proof["results"]["A"] = original
        for changes in ({"status": "SKIPPED"}, {"status": "FAIL"}, {"exit_code": False},
                        {"evidence_hashes": {}}, {"command": "different-command"}):
            receipt = deepcopy(original)
            receipt["executions"][0].update(changes)
            self.proof["results"]["A"] = receipt
            self.publish_proof()
            self.assertFalse(progressive.ready(self.state, self.current))

    def test_last_slice_cannot_drop_previous_demonstration_or_product_check(self):
        self.proof["results"].pop("A")
        self.publish_proof()
        self.assertFalse(progressive.ready(self.state, self.current))
        self.proof["required_checks"] = [self.check_b]
        self.record["required_checks"] = [self.check_b]
        self.publish_proof()
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_contributions_and_partial_or_extra_criterion_claims_are_insufficient(self):
        for claimed in (["C1"], ["C1", "C2", "unknown"], ["C1", "C1", "C2"]):
            self.proof["criterion_ids"] = claimed
            self.publish_proof()
            self.assertFalse(progressive.ready(self.state, self.current))
        self.proof["criterion_ids"] = ["C1", "C2"]
        self.check_b["relation"] = "contributes_to"
        self.proposal["slices"][0]["checks"] = [deepcopy(self.check_b)]
        self.publish_active()
        self.proof = self.payload("S2", self.record["active"]["plan_hash"], self.current["revision"],
                                  self.record["required_checks"], ["S1", "S2"], ["C1", "C2"])
        self.publish_proof()
        self.assertEqual(progressive.ledger.require_active(self.state), self.record["active"])
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_missing_writer_and_empty_or_duplicate_required_checklists_refuse(self):
        self.record.pop("completion_proof")
        self.assertFalse(progressive.ready(self.state, self.current))
        for checks in ([], [self.check_a, self.check_b, self.check_a]):
            self.record["required_checks"] = checks
            self.proof["required_checks"] = deepcopy(checks)
            self.publish_proof()
            self.assertFalse(progressive.ready(self.state, self.current))

    def test_each_command_requires_its_own_successful_replay(self):
        self.check_b["method"] = "Run `python -m unittest tests.test_b` and `python -m unittest tests.test_c`"
        self.proposal["slices"][0]["checks"] = [deepcopy(self.check_b)]
        self.publish_active()
        self.proof = self.payload("S2", self.record["active"]["plan_hash"], self.current["revision"],
                                  self.record["required_checks"], ["S1", "S2"], ["C1", "C2"])
        self.publish_proof()
        self.assertEqual(len(self.proof["results"]["B"]["executions"]), 2)
        self.assertTrue(progressive.ready(self.state, self.current))
        self.proof["results"]["B"]["executions"][1]["status"] = "SKIPPED"
        self.publish_proof()
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_missing_duplicate_or_unverified_history_refuses(self):
        saved = deepcopy(self.record["history"])
        for history in ([], saved[1:], saved + saved[:1], [saved[0]]):
            self.record["history"] = history
            self.assertFalse(progressive.ready(self.state, self.current))
        self.record["history"] = saved
        self.proof["verified_slices"] = ["S2"]
        self.publish_proof()
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_missing_and_tampered_checkpoint_review_and_evidence_files_refuse(self):
        for identity in (self.record["completion_proof"]["artifact"], self.record["active"]["artifact"],
                         self.record["active"]["review"], self.record["history"][0]["artifact"]):
            path = Path(self.temp.name) / identity["path"]
            contents = path.read_bytes()
            path.write_bytes(contents + b" ")
            self.assertFalse(progressive.ready(self.state, self.current))
            path.unlink()
            self.assertFalse(progressive.ready(self.state, self.current))
            path.write_bytes(contents)
        self.evidence.write_text("tampered", encoding="ascii")
        self.assertFalse(progressive.ready(self.state, self.current))
        self.evidence.unlink()
        self.assertFalse(progressive.ready(self.state, self.current))

    def test_shared_gate_checks_progressive_before_any_optional_operator_bypass(self):
        self.record.pop("completion_proof")
        with patch.object(completion, "criteria_definition", side_effect=AssertionError("ordinary gate reached")):
            for independent in (False, True):
                for human in (False, True):
                    self.assertFalse(completion.completion_ready(self.state, {"status": "COMPLETE"}, self.current,
                                     require_independent=independent, require_human_reviews=human))

    def test_valid_progressive_proof_never_overrides_ordinary_failure_or_findings(self):
        self.assertTrue(progressive.ready(self.state, self.current))
        self.state["version"] = 2
        self.state["findings_ledger"] = {"entries": [{"id": "blocker"}]}
        with patch("tools.autocode_findings.blocking_entries", return_value=[{"id": "blocker"}]) as blockers:
            self.assertFalse(completion.completion_ready(self.state, {"status": "COMPLETE"}, self.current))
            blockers.assert_called_once_with(self.state)


if __name__ == "__main__":
    unittest.main()
