"""Which reported external parts of a request the runner keeps (autocode_workspace_scope, #2066)."""

import tempfile
import unittest
from pathlib import Path

import autocode_scope_question as scope_question
import autocode_workspace_scope as workspace_scope
from autocode_util import validate_schema

# A saved Requirements answer for the DTI-12398 shape: one ticket, two separately versioned services.
TASK = (
    "DTI-12398: add the delivery window to the order response in fulfillment-rg-api "
    "and keep its history in fulfillment-rg-epp-history."
)
SAVED_SCOPE = {
    "spans_repositories": True,
    "external_parts": [
        {
            "target": "fulfillment-rg-epp-history",
            "work": "Store the delivery window with each order history entry",
            "source_quote": "keep its history in fulfillment-rg-epp-history",
            "evidence": "",
        }
    ],
}


def validated(scope, *, sources=(TASK,), source_refs=(), workspace=None, member_names=()):
    """The saved scope with the dropped targets beside it, so one assertion can read both."""
    kept, dropped = workspace_scope.validated(
        scope,
        sources=list(sources),
        source_refs=list(source_refs),
        workspace=workspace,
        member_names=member_names,
    )
    validate_schema(kept, workspace_scope.SCHEMA)  # the saved report is checked against its schema again
    return {**kept, "dropped": dropped}


class WorkspaceScopeTests(unittest.TestCase):
    def test_public_scope_preserves_grounding_quote_and_cited_file_for_coordination(self):
        state = {"workspace_scope": {"turn": 0, **SAVED_SCOPE}}
        shown = scope_question.view(state)
        self.assertEqual(
            [
                {
                    "target": "fulfillment-rg-epp-history",
                    "work": "Store the delivery window with each order history entry",
                    "source_quote": "keep its history in fulfillment-rg-epp-history",
                    "evidence": "",
                }
            ],
            shown["external_parts"],
        )

    def test_a_part_the_request_states_verbatim_is_kept(self):
        scope = validated(SAVED_SCOPE)
        self.assertTrue(scope["spans_repositories"])
        self.assertEqual(["fulfillment-rg-epp-history"], [part["target"] for part in scope["external_parts"]])
        self.assertEqual("keep its history in fulfillment-rg-epp-history", scope["external_parts"][0]["source_quote"])
        self.assertEqual([], scope["dropped"])

    def test_a_claim_with_no_quote_and_no_cited_file_is_dropped(self):
        # A model inferring a second repository the request never names, with nothing read to show it.
        claim = {
            "spans_repositories": True,
            "external_parts": [
                {
                    "target": "billing-service",
                    "work": "Charge for the delivery window",
                    "source_quote": "the billing service must charge for it",
                    "evidence": "",
                }
            ],
        }
        self.assertEqual(
            {"spans_repositories": False, "external_parts": [], "dropped": ["billing-service"]}, validated(claim)
        )

    def test_a_cited_file_inside_the_workspace_is_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "clients").mkdir()
            (Path(directory) / "clients" / "epp_history.py").write_text("BASE = 'https://epp-history'\n")
            part = {
                "target": "fulfillment-rg-epp-history",
                "work": "Accept the delivery window field",
                "source_quote": "",
                "evidence": "clients/epp_history.py:1",
            }
            scope = validated(
                {"spans_repositories": True, "external_parts": [part]},
                source_refs=["clients/epp_history.py:1"],
                workspace=directory,
            )
            self.assertTrue(scope["spans_repositories"])
            self.assertEqual("clients/epp_history.py:1", scope["external_parts"][0]["evidence"])

    def test_evidence_must_be_cited_exist_and_stay_inside_the_workspace(self):
        with tempfile.TemporaryDirectory() as parent:
            workspace = Path(parent) / "api"
            workspace.mkdir()
            (Path(parent) / "outside.py").write_text("")
            (workspace / "real.py").write_text("")
            for evidence, refs in (
                ("real.py", []),  # read but not cited in source_refs
                ("missing.py", ["missing.py"]),
                ("../outside.py", ["../outside.py"]),
                (str(Path(parent) / "outside.py"), [str(Path(parent) / "outside.py")]),
            ):
                part = {"target": "other", "work": "Change it", "source_quote": "", "evidence": evidence}
                with self.subTest(evidence=evidence):
                    scope = validated(
                        {"spans_repositories": True, "external_parts": [part]}, source_refs=refs, workspace=workspace
                    )
                    self.assertFalse(scope["spans_repositories"])
                    self.assertEqual(["other"], scope["dropped"])

    def test_line_and_range_evidence_uses_the_same_cited_workspace_file(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "contract.md").write_text("Client\nReceipt\nSeparate history repository\n")
            for evidence, reference in (
                ("contract.md:3", "contract.md:2"),
                ("contract.md:2-3", "contract.md"),
                ("contract.md", "contract.md:1-3"),
                ("./contract.md:3", "contract.md:1"),
            ):
                with self.subTest(evidence=evidence, reference=reference):
                    part = {"target": "history", "work": "Fix serialization", "source_quote": "", "evidence": evidence}
                    found = validated(
                        {"spans_repositories": True, "external_parts": [part]},
                        source_refs=[reference],
                        workspace=directory,
                    )
                    self.assertEqual(["history"], [p["target"] for p in found["external_parts"]])
                    self.assertEqual([], found["dropped"])

    def test_line_bounds_and_symlink_escape_never_ground_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            (root / "real.py").write_text("one\ntwo\n")
            outside = Path(directory) / "outside.py"
            outside.write_text("secret\n")
            (root / "link.py").symlink_to(outside)
            for evidence, refs in (
                ("real.py:0", ["real.py"]),
                ("real.py:3", ["real.py"]),
                ("real.py:2-1", ["real.py"]),
                ("real.py:1-3", ["real.py"]),
                ("real.py", ["real.py:99"]),
                ("link.py:1", ["link.py"]),
                ("../outside.py:1", ["../outside.py"]),
            ):
                with self.subTest(evidence=evidence, refs=refs):
                    part = {"target": "history", "work": "Fix it", "source_quote": "", "evidence": evidence}
                    found = validated(
                        {"spans_repositories": True, "external_parts": [part]},
                        source_refs=refs,
                        workspace=root,
                    )
                    self.assertEqual(["history"], found["dropped"])

    def test_a_report_that_finds_nothing_external_spans_nothing(self):
        for scope in ({"spans_repositories": False, "external_parts": []}, None, "yes", {"external_parts": [7]}):
            with self.subTest(scope=scope):
                self.assertEqual({"spans_repositories": False, "external_parts": [], "dropped": []}, validated(scope))

    def test_spans_repositories_follows_the_kept_parts_not_the_report(self):
        claimed_false = {**SAVED_SCOPE, "spans_repositories": False}
        self.assertTrue(validated(claimed_false)["spans_repositories"])


ROLE_TASK = "Fix checkout showing '100.0 USD' for a 100 USD subtotal when the expected total is '$110.00'."


class CoordinatedMemberTargetTests(unittest.TestCase):
    """A coordinated member names external owners exactly (#2420)."""

    MEMBERS = ("checkout", "history", "tax")

    def part(self, target, *, quote=ROLE_TASK, evidence=""):
        return {
            "target": target,
            "work": "Produce the final '$110.00' display",
            "source_quote": quote,
            "evidence": evidence,
        }

    def test_a_role_label_is_refused_for_the_report_repair_never_dropped(self):
        # The live attempt-5 labels: the quote grounds the part, the target names no member.
        for target in ("Checkout display member", "Receipt-handling member"):
            with (
                self.subTest(target=target),
                self.assertRaisesRegex(
                    ValueError,
                    "role label.*coordinated members are: checkout, history, tax.*ask the person in questions",
                ),
            ):
                validated(
                    {"spans_repositories": True, "external_parts": [self.part(target)]},
                    sources=(ROLE_TASK,),
                    member_names=self.MEMBERS,
                )

    def test_a_canonical_member_name_is_kept(self):
        found = validated(
            {"spans_repositories": True, "external_parts": [self.part("checkout")]},
            sources=(ROLE_TASK,),
            member_names=self.MEMBERS,
        )
        self.assertEqual(["checkout"], [row["target"] for row in found["external_parts"]])
        self.assertEqual([], found["dropped"])

    def test_a_literally_grounded_role_name_is_still_a_real_repository(self):
        # A repository genuinely called "Payments member": the authorized quote names it.
        task = "The Payments member repository owns the final receipt display."
        found = validated(
            {"spans_repositories": True, "external_parts": [self.part("Payments member", quote=task)]},
            sources=(task,),
            member_names=self.MEMBERS,
        )
        self.assertEqual(["Payments member"], [row["target"] for row in found["external_parts"]])

    def test_a_role_label_name_exemption_needs_a_real_citation_with_tight_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            # The name sits on line 1; the report's citations decide whether it counts.
            (Path(directory) / "contract.md").write_text("Payments member client lives here\nsecond line\n")
            (Path(directory) / "uncited.md").write_text("The Receipt-handling member owns receipts.\n")
            cases = [
                # A quote-grounded part cannot borrow a name from a file the report never cites.
                (
                    "uncited file cannot exempt a quote-grounded part",
                    self.part("Receipt-handling member", evidence="uncited.md"),
                    ["contract.md:1"],
                    False,
                ),
                # Whole-file evidence with a line-2 citation stays inside line 2.
                (
                    "source_refs line bound clips a whole-file evidence",
                    self.part("Payments member", quote="", evidence="contract.md"),
                    ["contract.md:2"],
                    False,
                ),
                # The evidence's own line bound clips a whole-file source_ref.
                (
                    "evidence line bound clips a whole-file citation",
                    self.part("Payments member", quote="", evidence="contract.md:2"),
                    ["contract.md"],
                    False,
                ),
                # A citation whose intersection covers the naming line grounds the name.
                (
                    "the intersecting cited lines ground the name",
                    self.part("Payments member", quote="", evidence="contract.md:1"),
                    ["contract.md:1-2"],
                    True,
                ),
            ]
            for name, part, refs, accepted in cases:
                with self.subTest(case=name):
                    scope = {"spans_repositories": True, "external_parts": [part]}
                    sources = (ROLE_TASK,) if part["source_quote"] else ()
                    if accepted:
                        found = validated(
                            scope, sources=sources, source_refs=refs, workspace=directory, member_names=self.MEMBERS
                        )
                        self.assertEqual([part["target"]], [row["target"] for row in found["external_parts"]])
                    else:
                        with self.assertRaisesRegex(ValueError, "role label"):
                            validated(
                                scope, sources=sources, source_refs=refs, workspace=directory, member_names=self.MEMBERS
                            )

    def test_runs_outside_coordination_keep_free_form_targets(self):
        found = validated(
            {"spans_repositories": True, "external_parts": [self.part("Checkout display member")]},
            sources=(ROLE_TASK,),
        )
        self.assertEqual(["Checkout display member"], [row["target"] for row in found["external_parts"]])


if __name__ == "__main__":
    unittest.main()
