"""Progressive planning rules: proposal validation, sealed delegation, coverage
across slice revisions, cumulative check obligations and evidence bindings."""

import copy
import unittest

import autocode_progressive_plan as progressive


def check(cid="K1", method="python -m pytest tests/test_journey.py -q", relation="contributes_to", criteria=("C1",)):
    return {"id": cid, "method": method, "relation": relation, "criterion_ids": list(criteria)}


def slice_row(sid="S1", criteria=("C1",), checks=None, tentative=False, depends_on=(), paths=("app/",)):
    return {
        "id": sid,
        "intended_result": f"{sid} delivers one useful end-to-end result",
        "criterion_ids": list(criteria),
        "paths": list(paths),
        "depends_on": list(depends_on),
        "checks": checks if checks is not None else [check()],
        "tentative": tentative,
    }


def proposal():
    return {
        "version": 1,
        "needed_because": "lessons, exercises and progress must land as verified slices",
        "shared_decisions": ["progress persists per student"],
        "slices": [
            slice_row(),
            slice_row(
                "S2",
                criteria=("C2", "C1"),
                checks=[
                    check("K2", "python -m pytest tests/test_recs.py -q", "fully_verify", ("C2",)),
                    check("K3", "python -m pytest tests/test_journey.py -q", "fully_verify", ("C1",)),
                ],
                tentative=True,
                depends_on=("S1",),
                paths=("app/", "recs/"),
            ),
        ],
        "outstanding_criteria": [],
    }


def receipt(row, **changes):
    return {
        "status": "PASS",
        "exit_code": 0,
        "check_hash": progressive.check_identity(row),
        "contract_token": "r1:approved",
        "source_revision": "current-source",
        "evidence_hashes": {"checks/result.json": "evidence-hash"},
        **changes,
    }


def retirement(row, **changes):
    return {
        "kind": "product_change",
        "check_id": row["id"],
        "check_hash": progressive.check_identity(row),
        "removes": "The approved visible product change removes the old demonstration",
        "contract_token": "r2:changed",
        **changes,
    }


class ProposalValidationTests(unittest.TestCase):
    def test_protocol_version_is_not_a_boolean_or_float_alias(self):
        for version in (True, False, 1.0, 2):
            with self.subTest(version=version):
                value = proposal()
                value["version"] = version
                with self.assertRaisesRegex(ValueError, "version must be 1"):
                    progressive.validate_proposal(value, ["C1", "C2"], initial=True)

    def test_only_absence_or_empty_version_zero_is_not_a_declaration(self):
        self.assertFalse(progressive.declares(None))
        self.assertFalse(progressive.declares({"version": 0, "needed_because": "", "slices": []}))
        for value in (
            False,
            [],
            "unknown",
            {"version": True},
            {"version": 1},
            {"version": 2},
            {"version": 0, "needed_because": 7},
        ):
            with self.subTest(value=value):
                self.assertTrue(progressive.declares(value))

    def test_valid_initial_proposal_is_accepted(self):
        parsed = progressive.validate_proposal(proposal(), ["C1", "C2"], initial=True)
        self.assertEqual(["S1", "S2"], parsed["slices"] and [row["id"] for row in parsed["slices"]])
        self.assertEqual(["S1"], parsed["planned"]["C1"][:1])
        self.assertEqual([], parsed["outstanding"])

    def test_check_that_extracts_no_command_is_invalid(self):
        row = check(method="the validator reads the diff and confirms behavior")
        with self.assertRaisesRegex(ValueError, "extracts no executable command"):
            progressive.validate_check(row)
        prose = check(method="`not a runner tool` is mentioned")
        with self.assertRaisesRegex(ValueError, "extracts no executable command"):
            progressive.validate_check(prose)

    def test_valid_future_command_needs_no_written_implementation(self):
        row = check(method="python -m pytest tests/not_written_yet.py -q")
        self.assertEqual(["python -m pytest tests/not_written_yet.py -q"], progressive.check_commands(row))

    def test_session_state_and_traversing_operands_are_rejected(self):
        for method, message in (
            ("python -m pytest .autocode/runs/r/test_x.py", "session state"),
            ("python -m pytest ../../other/test_x.py", "traversing"),
            ("python -m pytest /tmp/test_x.py", "absolute"),
        ):
            with self.subTest(method=method):
                with self.assertRaisesRegex(ValueError, message):
                    progressive.validate_check(check(method=method))

    def test_bare_nested_and_git_session_operands_are_rejected(self):
        for operand in (
            ".autocode",
            ".git",
            ".git/config",
            "tests/.tmp-autopilot-testkit/test_x.py",
            "fixtures/.autocode/data",
            "--fixture=.scenario-runs",
        ):
            with self.subTest(operand=operand):
                with self.assertRaisesRegex(ValueError, "session state"):
                    progressive.validate_check(check(method="python -m pytest " + operand))

    def test_session_paths_cannot_be_writable_slice_paths(self):
        for path in (".git", "tests/.tmp-autopilot-testkit", "fixtures/.autocode"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "session state"):
                    progressive.validate_slice(slice_row(paths=(path,)), ["C1"])

    def test_head_cannot_depend_on_future_slice(self):
        bad = proposal()
        bad["slices"][0]["depends_on"] = ["S2"]
        with self.assertRaisesRegex(ValueError, "head slice dependencies"):
            progressive.validate_proposal(bad, ["C1", "C2"])

    def test_future_dependency_graph_must_be_cycle_free(self):
        bad = proposal()
        bad["slices"][1]["depends_on"] = ["S3"]
        bad["slices"].append(slice_row("S3", tentative=True, depends_on=("S2",)))
        with self.assertRaisesRegex(ValueError, "cycle"):
            progressive.validate_proposal(bad, ["C1", "C2"])

    def test_claimed_done_slices_are_not_completion_authority(self):
        bad = proposal()
        bad["done_slices"] = ["S0"]
        bad["slices"][0]["depends_on"] = ["S0"]
        with self.assertRaisesRegex(ValueError, "independently verified completion"):
            progressive.validate_proposal(bad, ["C1", "C2"])
        parsed = progressive.validate_proposal(bad, ["C1", "C2"], verified_done=["S0"])
        self.assertEqual(["S0"], parsed["done"])

    def test_earlier_verified_criteria_can_cover_an_unmapped_criterion(self):
        later = {**proposal(), "slices": [slice_row("S2", criteria=("C2",), checks=[check("K2", criteria=("C2",))])]}
        with self.assertRaisesRegex(ValueError, "unmapped: C1"):
            progressive.validate_proposal(later, ["C1", "C2"])
        parsed = progressive.validate_proposal(later, ["C1", "C2"], verified_criteria=["C1"])
        self.assertEqual(["C1"], parsed["verified_criteria"])
        with self.assertRaisesRegex(ValueError, "unknown product criteria"):
            progressive.validate_proposal(later, ["C1", "C2"], verified_criteria=["C9"])
        with self.assertRaisesRegex(ValueError, "earlier verified proof"):
            progressive.validate_proposal(proposal(), ["C1", "C2"], initial=True, verified_criteria=["C1"])

    def test_first_slice_must_be_dispatchable_and_future_slices_tentative(self):
        bad = proposal()
        bad["slices"][0]["tentative"] = True
        with self.assertRaisesRegex(ValueError, "head slice must be concrete"):
            progressive.validate_proposal(bad, ["C1", "C2"], initial=True)
        bad = proposal()
        bad["slices"][1]["tentative"] = False
        with self.assertRaisesRegex(ValueError, "must stay tentative"):
            progressive.validate_proposal(bad, ["C1", "C2"], initial=True)

    def test_capability_map_must_cover_every_product_criterion(self):
        bad = proposal()
        bad["slices"][1]["criterion_ids"] = []
        bad["slices"][1]["checks"] = [check("K2", "python -m pytest tests/test_recs.py -q", "fully_verify", ())]
        with self.assertRaisesRegex(ValueError, "at least one product criterion"):
            progressive.validate_proposal(bad, ["C1", "C2"], initial=True)
        bad = proposal()
        bad["slices"][1] = slice_row(
            "S2",
            criteria=("C1",),
            checks=[check("K2", "python -m pytest tests/test_recs.py -q", "fully_verify", ("C1",))],
            tentative=True,
            paths=("recs/",),
        )
        with self.assertRaisesRegex(ValueError, "unmapped: C2"):
            progressive.validate_proposal(bad, ["C1", "C2"], initial=True)

    def test_outstanding_and_planned_cannot_overlap(self):
        bad = proposal()
        bad["outstanding_criteria"] = ["C1"]
        with self.assertRaisesRegex(ValueError, "both planned"):
            progressive.validate_proposal(bad, ["C1", "C2"], initial=True)

    def test_slice_needs_paths_criteria_and_checks(self):
        for field, value, message in (
            ("paths", [], "bounded writable paths"),
            ("checks", [], "concrete checks"),
            ("criterion_ids", [], "product criterion"),
        ):
            with self.subTest(field=field):
                bad = proposal()
                bad["slices"][0][field] = value
                with self.assertRaisesRegex(ValueError, message):
                    progressive.validate_proposal(bad, ["C1", "C2"], initial=True)


class DisclosureTests(unittest.TestCase):
    def test_default_limits_leave_existing_disclosure_unchanged(self):
        card = progressive.disclosure(proposal(), ["C1", "C2"])
        self.assertEqual(card, progressive.disclosure(proposal(), ["C1", "C2"], limits=progressive.default_limits()))
        self.assertIn("2 plan-review calls and 5400 stage-seconds", card["constraints"][0])
        self.assertIn(
            "43200 whole-run accumulated provider-stage seconds, raised only by explicit user increase",
            card["constraints"][0],
        )

    def test_disclosure_puts_delegation_in_constraints_and_slices_in_the_approach(self):
        card = progressive.disclosure(proposal(), ["C1", "C2"])
        delegation = [line for line in card["constraints"] if line.startswith(progressive.DISCLOSURE_DELEGATION)]
        self.assertEqual(1, len(delegation))
        self.assertIn("delegates continuation", delegation[0])
        self.assertIn("Plan identity: " + progressive.plan_identity(proposal()), delegation[0])
        self.assertIn(f"{progressive.DEFAULT_SLICE_REVIEW_CALLS} plan-review calls", delegation[0])
        self.assertIn(f"{progressive.DEFAULT_RUN_MAX_SECONDS} whole-run", delegation[0])
        self.assertEqual(2, len(card["technical_approach"]))
        first = card["technical_approach"][0]
        self.assertIn("S1 (first slice, dispatchable after approval)", first)
        self.assertIn("criteria C1", first)
        self.assertIn("K1 [python -m pytest tests/test_journey.py -q] contributes_to C1", first)
        self.assertIn("S2 (future slice, tentative, not dispatchable)", card["technical_approach"][1])

    def test_mismatched_or_invented_disclosure_is_rejected(self):
        card = progressive.disclosure(proposal(), ["C1", "C2"])
        body = {**card, "technical_approach": list(card["technical_approach"])}
        progressive.check_disclosure(body, proposal(), ["C1", "C2"])
        body["constraints"] = [
            line for line in card["constraints"] if not line.startswith(progressive.DISCLOSURE_DELEGATION)
        ]
        with self.assertRaisesRegex(ValueError, "does not show the generated disclosure"):
            progressive.check_disclosure(body, proposal(), ["C1", "C2"])
        body = {**card, "constraints": card["constraints"] + [progressive.DISCLOSURE_DELEGATION + " unlimited"]}
        with self.assertRaisesRegex(ValueError, "does not generate"):
            progressive.check_disclosure(body, proposal(), ["C1", "C2"])


class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.token = "r1:xyz"
        self.seal = progressive.seal_delegation(proposal(), self.token)
        self.held = {
            "version": 1,
            "delegation": self.seal,
            "initial_plan": {"proposal": proposal(), "plan_hash": self.seal["plan_hash"]},
        }
        self.body = {
            **progressive.disclosure(proposal(), ["C1", "C2"]),
            "acceptance_criteria": [{"id": "C1"}, {"id": "C2"}],
        }
        self.auth = {
            "contract_token": self.token,
            "contract_body": self.body,
            "contract_approved": True,
            "contract_sealed": True,
        }

    def test_seal_binds_plan_identity_to_the_displayed_contract_token(self):
        seal = progressive.seal_delegation(proposal(), "r2:abc123")
        self.assertEqual(progressive.plan_identity(proposal()), seal["plan_hash"])
        self.assertEqual("r2:abc123", seal["contract_token"])
        self.assertEqual(progressive.default_limits(), seal["limits"])

    def test_only_an_explicit_sealed_delegation_authorizes_continuation(self):
        self.assertIs(self.seal, progressive.require_delegation(self.held, **self.auth))
        for state, message in (
            ({}, "ordinary path"),
            ({**self.held, "delegation": None}, "ordinary path"),
            ({**self.held, "delegation": {**self.seal, "contract_token": "r1:other"}}, "different contract token"),
            ({**self.held, "delegation": {**self.seal, "plan_hash": ""}}, "sealed plan identity"),
        ):
            with self.subTest(state=state):
                with self.assertRaisesRegex(ValueError, message):
                    progressive.require_delegation(state, **self.auth)

    def test_missing_or_model_asserted_authentication_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "authenticated current"):
            progressive.require_delegation(self.held)
        for key in ("contract_approved", "contract_sealed"):
            for value in (False, None, 1, "true", {"approved": True}):
                with self.subTest(key=key, value=value):
                    with self.assertRaisesRegex(ValueError, "authenticated current"):
                        progressive.require_delegation(self.held, **{**self.auth, key: value})

    def test_initial_identity_disclosure_and_limits_cannot_be_tampered(self):
        for mutate in (
            lambda row: row["initial_plan"]["proposal"]["slices"][0].update(paths=["other"]),
            lambda row: row["initial_plan"].update(plan_hash="invented"),
            lambda row: row["delegation"].update(plan_hash="invented"),
            lambda row: row["delegation"]["limits"].update(slice_review_calls=999),
            lambda row: row.update(version=2),
        ):
            held = copy.deepcopy(self.held)
            mutate(held)
            with self.assertRaises(ValueError):
                progressive.require_delegation(held, **self.auth)
        hidden = {**self.body, "constraints": []}
        with self.assertRaisesRegex(ValueError, "disclosure"):
            progressive.require_delegation(self.held, **{**self.auth, "contract_body": hidden})

    def test_evolving_execution_plan_does_not_replace_initial_approval_identity(self):
        held = {**self.held, "plan": {"plan_hash": "reviewed-later-plan", "proposal": {}}}
        self.assertIs(self.seal, progressive.require_delegation(held, **self.auth))
        with self.assertRaisesRegex(ValueError, "different contract token"):
            progressive.require_delegation(held, **{**self.auth, "contract_token": "r2:new"})

    def test_configured_limits_match_visible_card_and_frozen_grant(self):
        configured = {
            "slice_review_calls": 5,
            "slice_stage_seconds": 8000,
            "run_max_seconds": 50000,
            "run_max_seconds_explicit_only": True,
        }
        card = progressive.disclosure(proposal(), ["C1", "C2"], limits=configured)
        self.assertIn("5 plan-review calls and 8000 stage-seconds", card["constraints"][0])
        self.assertIn(
            "50000 whole-run accumulated provider-stage seconds, raised only by explicit user increase",
            card["constraints"][0],
        )
        sealed = progressive.seal_delegation(proposal(), self.token, limits=configured)
        self.assertEqual(configured, sealed["limits"])
        self.assertEqual(self.seal["plan_hash"], sealed["plan_hash"])
        held = {**self.held, "delegation": sealed}
        body = {**self.body, **card}
        progressive.check_disclosure(body, proposal(), ["C1", "C2"], limits=configured)
        self.assertIs(sealed, progressive.require_delegation(held, **{**self.auth, "contract_body": body}))
        configured["slice_review_calls"] = 999
        self.assertEqual(5, sealed["limits"]["slice_review_calls"])
        self.assertIs(sealed, progressive.require_delegation(held, **{**self.auth, "contract_body": body}))

    def test_tampered_configured_limits_or_default_card_are_refused(self):
        configured = {
            **progressive.default_limits(),
            "slice_review_calls": 5,
            "slice_stage_seconds": 8000,
            "run_max_seconds": 50000,
        }
        sealed = progressive.seal_delegation(proposal(), self.token, limits=configured)
        held = {**self.held, "delegation": sealed}
        card = progressive.disclosure(proposal(), ["C1", "C2"], limits=configured)
        body = {**self.body, **card}
        with self.assertRaisesRegex(ValueError, "disclosure"):
            progressive.require_delegation(held, **self.auth)
        with self.assertRaisesRegex(ValueError, "disclosure"):
            progressive.check_disclosure(body, proposal(), ["C1", "C2"])
        for key in ("slice_review_calls", "slice_stage_seconds", "run_max_seconds"):
            changed = copy.deepcopy(held)
            changed["delegation"]["limits"][key] += 1
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "disclosure"):
                    progressive.require_delegation(changed, **{**self.auth, "contract_body": body})
        hidden = {**body, "constraints": [card["constraints"][0].replace("8000", "5400")]}
        with self.assertRaisesRegex(ValueError, "disclosure"):
            progressive.require_delegation(held, **{**self.auth, "contract_body": hidden})
        for missing in (None, {}):
            with self.subTest(missing=missing):
                with self.assertRaises(ValueError):
                    progressive.require_delegation(
                        {**held, "delegation": {**sealed, "limits": missing}}, **{**self.auth, "contract_body": body}
                    )

    def test_explicit_unlimited_limits_are_visible_and_canonical(self):
        for unlimited in (0, None):
            configured = {
                "slice_review_calls": unlimited,
                "slice_stage_seconds": unlimited,
                "run_max_seconds": unlimited,
                "run_max_seconds_explicit_only": True,
            }
            with self.subTest(unlimited=unlimited):
                card = progressive.disclosure(proposal(), ["C1", "C2"], limits=configured)
                self.assertIn("unlimited plan-review calls and unlimited stage-seconds", card["constraints"][0])
                self.assertIn(
                    "unlimited whole-run accumulated provider-stage seconds, raised only by explicit user increase",
                    card["constraints"][0],
                )
                sealed = progressive.seal_delegation(proposal(), self.token, limits=configured)
                self.assertEqual(
                    {**configured, "slice_review_calls": None, "slice_stage_seconds": None, "run_max_seconds": None},
                    sealed["limits"],
                )
                self.assertIs(
                    sealed,
                    progressive.require_delegation(
                        {**self.held, "delegation": sealed}, **{**self.auth, "contract_body": {**self.body, **card}}
                    ),
                )


class RevisionTests(unittest.TestCase):
    def revision(self, mutate):
        before = progressive.validate_proposal(proposal(), ["C1", "C2"], initial=True)
        after = proposal()
        mutate(after)
        result = progressive.validate_revision(
            proposal(),
            after,
            ["C1", "C2"],
            verified_done=["S1"],
            established=progressive.retain(before["slices"][0]["checks"], "S1"),
        )
        return before, result

    def test_split_and_reorder_keep_coverage(self):
        def mutate(after):
            after["done_slices"] = ["S1"]
            after["slices"] = [
                slice_row(
                    "S2a",
                    criteria=("C2",),
                    checks=[check("K2", "python -m pytest tests/test_recs.py -q", "fully_verify", ("C2",))],
                    tentative=False,
                    depends_on=("S1",),
                    paths=("recs/",),
                ),
                slice_row(
                    "S2b",
                    criteria=("C2", "C1"),
                    checks=[check("K3", "python -m pytest tests/test_journey.py -q", "fully_verify", ("C1",))],
                    tentative=True,
                    depends_on=("S2a",),
                    paths=("app/",),
                ),
            ]

        _, result = self.revision(mutate)
        self.assertEqual({}, result["retired"])
        self.assertEqual(["K1"], [row["id"] for row in result["carried"]])

    def test_a_revision_cannot_drop_a_product_criterion(self):
        def mutate(after):
            after["slices"] = [
                slice_row(
                    "S2", criteria=("C1",), checks=[check("K3", criteria=("C1",))], tentative=False, paths=("app/",)
                )
            ]

        with self.assertRaisesRegex(ValueError, "unmapped: C2"):
            self.revision(mutate)

    def test_revision_preserves_coverage_with_authenticated_earlier_product_proof(self):
        before = proposal()
        after = {
            **before,
            "done_slices": ["S1"],
            "slices": [
                slice_row(
                    "S2",
                    criteria=("C2",),
                    depends_on=("S1",),
                    checks=[check("K2", relation="fully_verify", criteria=("C2",))],
                )
            ],
        }
        established = progressive.retain(before["slices"][0]["checks"], "S1")
        with self.assertRaisesRegex(ValueError, "unmapped: C1"):
            progressive.validate_revision(before, after, ["C1", "C2"], established=established, verified_done=["S1"])
        result = progressive.validate_revision(
            before, after, ["C1", "C2"], established=established, verified_done=["S1"], verified_criteria=["C1"]
        )
        self.assertEqual(["K1"], [row["id"] for row in result["carried"]])

    def test_check_obligations_need_an_explicit_approved_retirement(self):
        after = proposal()
        after["slices"] = [slice_row("S2", criteria=("C2", "C1"), checks=[check("K3", criteria=("C1",))])]
        old = proposal()["slices"][0]["checks"][0]
        auth = {"retirement_grants_authenticated": True, "contract_token": "r2:changed"}
        with self.assertRaisesRegex(ValueError, "silently drop check obligations: K1"):
            progressive.validate_revision(proposal(), after, ["C1", "C2"])
        result = progressive.validate_revision(
            proposal(), after, ["C1", "C2"], retirement_grants=[retirement(old)], **auth
        )
        self.assertIn("K1", result["retired"])

        established = [
            {
                "id": "K9",
                "relation": "fully_verify",
                "criterion_ids": ["C2"],
                "method": "python -m pytest tests/test_old.py -q",
                "origin": "S0",
            }
        ]
        result = progressive.validate_revision(
            proposal(), after, ["C1", "C2"], established=established, retirement_grants=[retirement(old)], **auth
        )
        self.assertEqual(["K9"], [row["id"] for row in result["carried"]])
        result = progressive.validate_revision(
            proposal(),
            after,
            ["C1", "C2"],
            established=established,
            retirement_grants=[retirement(old), retirement(established[0])],
            **auth,
        )
        self.assertEqual([], result["carried"])
        for grant, message in (
            (retirement(old, check_id="K8"), "does not remove"),
            (retirement(old, check_id="K3"), "does not remove"),
            (retirement(old, removes=""), "removes"),
            (retirement(old, contract_token="r1:stale"), "another product-change approval"),
            (retirement(old, check_hash="unrelated"), "removed obligation"),
            (retirement(old, kind="technical_revision"), "product-change grant"),
            ({"check_id": "K1", "removes": "old path", "approved_by": "r3:token"}, "product-change grant"),
        ):
            with self.subTest(grant=grant):
                with self.assertRaisesRegex(ValueError, message):
                    progressive.validate_revision(
                        proposal(), after, ["C1", "C2"], established=established, retirement_grants=[grant], **auth
                    )
        with self.assertRaisesRegex(ValueError, "authenticated"):
            progressive.validate_revision(
                proposal(), after, ["C1", "C2"], retirement_grants=[retirement(old)], contract_token="r2:changed"
            )
        with self.assertRaisesRegex(ValueError, "normalized"):
            progressive.validate_revision(proposal(), after, ["C1", "C2"], retirement_grants=["r2:changed"], **auth)
        with self.assertRaisesRegex(ValueError, "unique"):
            progressive.validate_revision(
                proposal(), after, ["C1", "C2"], retirement_grants=[retirement(old), retirement(old)], **auth
            )

    def test_technical_replacement_keeps_the_obligation_and_demands_new_evidence(self):
        def mutate(after):
            after["slices"] = [
                slice_row(),
                slice_row(
                    "S2",
                    criteria=("C2", "C1"),
                    tentative=True,
                    depends_on=("S1",),
                    checks=[
                        check("K2", "python -m pytest tests/test_recs.py -q", "fully_verify", ("C2",)),
                        check("K3", "python -m pytest tests/test_journey.py -q", "fully_verify", ("C1",)),
                    ],
                    paths=("app/",),
                ),
            ]
            after["slices"][0]["checks"] = [check("K1", "python -m pytest tests/test_journey2.py -q")]

        _, result = self.revision(mutate)
        self.assertEqual(["K1"], result["replaced"])
        self.assertEqual("python -m pytest tests/test_journey2.py -q", result["carried"][0]["method"])
        self.assertFalse(result["carried"][0]["verified_once"])
        after = proposal()
        mutate(after)
        rows = progressive.cumulative_checks(after, result["carried"])
        self.assertEqual("python -m pytest tests/test_journey2.py -q", rows[0]["method"])
        self.assertNotEqual(
            progressive.check_identity(proposal()["slices"][0]["checks"][0]), progressive.check_identity(rows[0])
        )

        def relabel(after):
            after["slices"][0]["checks"] = [check("K1", relation="fully_verify")]

        with self.assertRaisesRegex(ValueError, "changes its obligation"):
            self.revision(relabel)


class CumulativeProofTests(unittest.TestCase):
    def proof(self, obligations, results, **kwargs):
        return progressive.criterion_proof(
            obligations,
            results,
            contract_token="r1:approved",
            source_revision="current-source",
            receipts_authenticated=True,
            **kwargs,
        )

    def test_checkpoint_reruns_the_entire_cumulative_required_set(self):
        first = progressive.cumulative_checks(proposal(), [])
        self.assertEqual(["K1"], [row["id"] for row in first])
        established = progressive.retain(first, "S1")
        later = {"slices": proposal()["slices"][1:]}
        rows = progressive.cumulative_checks(later, established)
        self.assertEqual(["K1", "K2", "K3"], [row["id"] for row in rows])
        again = progressive.cumulative_checks(later, rows)
        self.assertEqual(["K1", "K2", "K3"], [row["id"] for row in again])

    def test_a_contribution_never_proves_a_product_criterion(self):
        obligations = [
            check("K1"),
            check("K3", relation="fully_verify"),
            check("K2", relation="fully_verify", criteria=("C2",)),
        ]
        results = {row["id"]: receipt(row) for row in obligations if row["id"] != "K3"}
        proof = self.proof(obligations, results)
        self.assertFalse(proof.get("C1", False))
        self.assertTrue(proof["C2"])
        results["K3"] = receipt(obligations[1])
        proof = self.proof(obligations, results)
        self.assertTrue(proof["C1"])

    def test_every_fully_verify_obligation_needs_current_proof(self):
        obligations = [
            check("K2", relation="fully_verify", criteria=("C2",)),
            check("K4", relation="fully_verify", criteria=("C2",)),
        ]
        self.assertFalse(self.proof(obligations, {"K2": receipt(obligations[0])})["C2"])

    def test_contribution_only_proof_explicitly_remains_false(self):
        row = check("A")
        self.assertEqual({"C1": False}, self.proof([row], {"A": receipt(row)}))
        self.assertEqual({"C1": False}, self.proof([row], {}))
        self.assertEqual({}, self.proof([], {}))

    def test_full_target_cannot_hide_missing_failed_or_stale_contribution(self):
        contribution = progressive.retain([check("A")], "S1")[0]
        full = check("B", relation="fully_verify")
        independent = check("C", relation="fully_verify", criteria=("C2",))
        obligations = [contribution, full, independent]
        good = {row["id"]: receipt(row) for row in obligations}
        self.assertEqual({"C1": True, "C2": True}, self.proof(obligations, good))
        invalid = [
            None,
            {"ok": True},
            receipt(contribution, status="FAIL", exit_code=1),
            receipt(contribution, status="SKIPPED"),
            receipt(contribution, source_revision="historical-source"),
            receipt(contribution, contract_token="r0:old"),
            receipt(contribution, check_hash="previous-definition"),
            receipt(contribution, evidence_hashes={}),
        ]
        for result in invalid:
            with self.subTest(result=result):
                self.assertEqual({"C1": False, "C2": True}, self.proof(obligations, {**good, "A": result}))
        missing = {key: value for key, value in good.items() if key != "A"}
        self.assertEqual({"C1": False, "C2": True}, self.proof(obligations, missing))

    def test_shared_required_contribution_blocks_each_associated_full_target(self):
        shared = check("A", criteria=("C1", "C2"))
        obligations = [
            shared,
            check("B", relation="fully_verify"),
            check("C", relation="fully_verify", criteria=("C2",)),
        ]
        results = {row["id"]: receipt(row) for row in obligations if row["id"] != "A"}
        self.assertEqual({"C1": False, "C2": False}, self.proof(obligations, results))
        results["A"] = receipt(shared)
        self.assertEqual({"C1": True, "C2": True}, self.proof(obligations, results))

    def test_nonempty_failed_stale_or_unbound_receipts_never_prove(self):
        row = check(relation="fully_verify")
        invalid = [
            None,
            {},
            {"ok": True},
            {"status": "FAIL", "stale": True},
            "PASS",
            receipt(row, status="FAIL"),
            receipt(row, status="SKIPPED"),
            receipt(row, exit_code=1),
            receipt(row, exit_code=False),
            receipt(row, check_hash="another-check"),
            receipt(row, contract_token="r0:old"),
            receipt(row, source_revision="previous-source"),
            receipt(row, evidence_hashes={}),
            receipt(row, evidence_hashes={"log": ""}),
        ]
        for field in ("status", "exit_code", "check_hash", "contract_token", "source_revision", "evidence_hashes"):
            missing = receipt(row)
            missing.pop(field)
            invalid.append(missing)
        for result in invalid:
            with self.subTest(result=result):
                self.assertFalse(self.proof([row], {row["id"]: result})["C1"])

    def test_receipts_require_explicit_boundary_authentication(self):
        row = check(relation="fully_verify")
        for authenticated in (False, None, 1, "true", {"runner": True}):
            with self.subTest(authenticated=authenticated):
                self.assertFalse(
                    progressive.criterion_proof(
                        [row],
                        {row["id"]: receipt(row)},
                        contract_token="r1:approved",
                        source_revision="current-source",
                        receipts_authenticated=authenticated,
                    )["C1"]
                )
        self.assertFalse(progressive.criterion_proof([row], {row["id"]: receipt(row)})["C1"])

    def test_receipt_normalization_requires_current_success_and_retains_evidence(self):
        row = check(relation="fully_verify")
        original = receipt(row)
        normalized = progressive.normalize_receipt(
            original, row, contract_token="r1:approved", source_revision="current-source", authenticated=True
        )
        self.assertEqual(original, normalized)
        original["evidence_hashes"]["checks/result.json"] = "tampered"
        self.assertEqual("evidence-hash", normalized["evidence_hashes"]["checks/result.json"])
        with self.assertRaisesRegex(ValueError, "authenticated"):
            progressive.normalize_receipt(
                normalized, row, contract_token="r1:approved", source_revision="current-source"
            )

    def test_old_receipt_cannot_prove_a_replaced_command(self):
        old = check(relation="fully_verify")
        new = {**old, "method": "python -m pytest tests/replacement.py -q"}
        self.assertFalse(self.proof([new], {old["id"]: receipt(old)})["C1"])
        self.assertTrue(self.proof([new], {new["id"]: receipt(new)})["C1"])

    def test_conflicting_cumulative_definitions_are_not_silently_deduplicated(self):
        old = proposal()["slices"][0]["checks"][0]
        changed = proposal()
        changed["slices"][0]["checks"][0]["method"] = "python -m pytest tests/replacement.py -q"
        with self.assertRaisesRegex(ValueError, "conflicting cumulative"):
            progressive.cumulative_checks(changed, [old])

    def test_check_identity_covers_obligation_and_command_not_history(self):
        row = check()
        identity = progressive.check_identity(row)
        self.assertEqual(identity, progressive.check_identity({**row, "origin": "S0", "verified_once": True}))
        for field, value in (
            ("method", "python -m pytest tests/other.py"),
            ("id", "K2"),
            ("relation", "fully_verify"),
            ("criterion_ids", ["C2"]),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(identity, progressive.check_identity({**row, field: value}))


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.binding = progressive.bind_attempt(
            "S1", "t1", 1, plan_hash="ph", assignment_source="a", contract_token="r1:approved"
        )
        self.report = {**self.binding, "validated_source": "v"}
        self.expected = {
            "plan_hash": "ph",
            "slice_id": "S1",
            "task_id": "t1",
            "attempt": 1,
            "validated_source": "v",
            "contract_token": "r1:approved",
        }

    def test_binding_requires_the_sealed_plan_identity(self):
        with self.assertRaisesRegex(ValueError, "sealed plan identity"):
            progressive.bind_attempt("S1", "t1", 1, plan_hash="", assignment_source="a")
        binding = progressive.bind_attempt("S1", "t1", 1, plan_hash="ph", assignment_source="a", validated_source="v")
        self.assertEqual(1, binding["attempt"])

    def test_an_old_report_cannot_acquire_a_new_identity(self):
        self.assertIs(self.binding, progressive.check_binding(self.binding, self.report, **self.expected))
        for key, value in (("plan_hash", "ph2"), ("slice_id", "S2"), ("task_id", "t2"), ("attempt", 2)):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, key):
                    progressive.check_binding(self.binding, self.report, **{**self.expected, key: value})

    def test_normalized_report_needs_every_exact_identity(self):
        for key in self.report:
            for changed in (
                {k: v for k, v in self.report.items() if k != key},
                {**self.report, key: 99 if key == "attempt" else "different"},
            ):
                with self.subTest(key=key, report=changed):
                    with self.assertRaises(ValueError):
                        progressive.check_binding(self.binding, changed, **self.expected)
        with self.assertRaisesRegex(ValueError, "attempt"):
            progressive.check_binding(self.binding, {**self.report, "attempt": True}, **self.expected)

    def test_missing_current_token_or_validated_source_fails_closed(self):
        for key in ("contract_token", "validated_source"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    progressive.check_binding(self.binding, self.report, **{**self.expected, key: None})
        unbound = {**self.binding, "contract_token": None}
        with self.assertRaisesRegex(ValueError, "contract_token"):
            progressive.check_binding(unbound, self.report, **self.expected)

    def test_receipts_do_not_transplant_across_validated_sources(self):
        binding = {**self.binding, "validated_source": "v1"}
        with self.assertRaisesRegex(ValueError, "different source"):
            progressive.check_binding(
                binding, {**self.report, "validated_source": "v2"}, **{**self.expected, "validated_source": "v2"}
            )

    def test_binding_rejects_noninteger_attempts(self):
        for attempt in (0, -1, True, "1", 1.5):
            with self.subTest(attempt=attempt):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    progressive.bind_attempt("S1", "t1", attempt, plan_hash="ph", assignment_source="a")

    def test_binding_rejects_missing_or_nonstring_snapshots_and_plan_identity(self):
        for key in ("plan_hash", "assignment_source", "validated_source", "contract_token"):
            for value in ("", " ", 1, {"hash": "value"}):
                with self.subTest(key=key, value=value):
                    kwargs = {
                        "plan_hash": "ph",
                        "assignment_source": "a",
                        "validated_source": "v",
                        "contract_token": "r1:approved",
                        key: value,
                    }
                    with self.assertRaises(ValueError):
                        progressive.bind_attempt("S1", "t1", 1, **kwargs)


class LimitTests(unittest.TestCase):
    def test_defaults_match_the_agreed_budgets(self):
        limits = progressive.default_limits()
        self.assertEqual(2, limits["slice_review_calls"])
        self.assertEqual(5400, limits["slice_stage_seconds"])
        self.assertEqual(43200, limits["run_max_seconds"])
        self.assertTrue(limits["run_max_seconds_explicit_only"])

    def test_seconds_accept_finite_fractions_and_normalize_integral_floats(self):
        configured = {**progressive.default_limits(), "slice_stage_seconds": 8000.0, "run_max_seconds": 50000.5}
        normalized = progressive.normalize_limits(configured)
        self.assertEqual(8000, normalized["slice_stage_seconds"])
        self.assertIs(type(normalized["slice_stage_seconds"]), int)
        self.assertEqual(50000.5, normalized["run_max_seconds"])
        card = progressive.disclosure(proposal(), ["C1", "C2"], limits=configured)
        self.assertIn("8000 stage-seconds", card["constraints"][0])
        self.assertIn("50000.5 whole-run", card["constraints"][0])

    def test_malformed_limit_values_fail_at_disclosure_and_sealing(self):
        for key in ("slice_review_calls", "slice_stage_seconds", "run_max_seconds"):
            invalid = [True, False, -1, -0.5, float("inf"), float("-inf"), float("nan"), "5000", []]
            if key == "slice_review_calls":
                invalid += [5.0, 5.5]
            for value in invalid:
                configured = {**progressive.default_limits(), key: value}
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        progressive.disclosure(proposal(), ["C1", "C2"], limits=configured)
                    with self.assertRaises(ValueError):
                        progressive.seal_delegation(proposal(), "r1:approved", limits=configured)

    def test_provided_limits_require_complete_shape_and_exact_provenance_flag(self):
        invalid = [{}, {"slice_review_calls": 5}, {**progressive.default_limits(), "extra": 1}, "defaults"]
        for key in progressive.default_limits():
            missing = progressive.default_limits()
            missing.pop(key)
            invalid.append(missing)
        for flag in (False, None, 1, "true"):
            invalid.append({**progressive.default_limits(), "run_max_seconds_explicit_only": flag})
        for limits in invalid:
            with self.subTest(limits=limits):
                with self.assertRaises(ValueError):
                    progressive.normalize_limits(limits)

    def test_normalization_does_not_mutate_caller_configuration(self):
        configured = {**progressive.default_limits(), "slice_stage_seconds": 0}
        normalized = progressive.normalize_limits(configured)
        self.assertEqual(0, configured["slice_stage_seconds"])
        self.assertIsNone(normalized["slice_stage_seconds"])
        normalized["slice_review_calls"] = 99
        self.assertEqual(2, configured["slice_review_calls"])


if __name__ == "__main__":
    unittest.main()
