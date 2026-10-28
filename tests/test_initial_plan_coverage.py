"""Initial author policy and executable family/guard semantics; no model calls."""

import copy
import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_regression as regression
from goal_fixtures import body
from units import autoplanner as planner

from .test_planner_intent import handoff
from .test_planner_invariants import state
from .test_test_cases import feature_state
from .test_verify import Project


class InitialCoveragePromptTests(unittest.TestCase):
    def test_initial_prepare_keeps_known_obligations_and_allows_exact_families(self):
        task = "Fix partial allocation counts; preserve rejection of nonpositive widths and existing tests."
        current = state(task)
        current["settings"]["roles"]["glm"] = {"engine": "opencode", "model": "fixture-model"}
        requirements = handoff(task)
        requirements["required_behaviors"] = [
            "For all integer totals >= 0 and widths > 0, count returns the exact integer ceiling.",
            "Nonpositive widths still raise ValueError.",
        ]
        current["requirements_handoff"] = {"output": "requirements.json", "report": requirements}
        current["investigation"] = {
            "outcome": "reproduced",
            "note_path": "docs/bugs/allocation.json",
            "observed": "count(7, 3) returns 2, dropping the partial allocation.",
            "reproduction": "The local probe returned 2.",
            "root_cause": "The shared count rounds down.",
            "affected_paths": ["allocation.py"],
            "test_paths": ["test_allocation.py"],
            "invariant": requirements["required_behaviors"][0],
            "fix_plan": ["Use exact integer arithmetic; check nonpositive width rejection after the fix."],
            "questions": [],
            "conclusion": "A bounded correction restores partial allocations.",
            "test_cases": [{"id": "T1", "given": "total=7, width=3", "when": "count is called", "then": "returns 3"}],
        }
        original = copy.deepcopy(current)
        for stage in ("astra_discovery", "plan"):
            with (
                self.subTest(stage=stage),
                patch.object(planner, "workspace_inventory", return_value={"files": ["allocation.py"]}),
                patch.object(planner.artifacts, "verify_predecessor", return_value=None),
                patch.object(
                    planner.investigation_evidence, "context", return_value={"eligibility": "fresh_discovery_required"}
                ),
            ):
                drafting = copy.deepcopy(current)
                drafting["settings"]["planning_flow"] = "v2" if stage == "plan" else "legacy"
                request = planner.prepare(drafting, stage, Path("/absent-run/state.json"), None)
                instruction, raw = request.prompt.split("\nCURRENT HANDOFF DATA\n", 1)
                packet = json.loads(raw)
                self.assertEqual(original["requirements_handoff"], packet["requirements_handoff"])
                for field in ("invariant", "fix_plan", "test_cases"):
                    self.assertEqual(original["investigation"][field], packet["bug_diagnosis"][field])
                normalized = " ".join(instruction.split())
                for required in (
                    "a concrete case or a bounded family",
                    "explicit input domain",
                    "independently of the implementation",
                    "exact integer relations instead of enormous decimal literals",
                    "required_behaviors and bug_diagnosis.fix_plan",
                    "only the assertions actually present",
                    "uncovered preserved behavior",
                    "Keep guards separate from restore criteria",
                ):
                    self.assertTrue(required in normalized, required)
                self.assertNotIn("as one concrete example", normalized)
                self.assertIn("Test 2**63-1, 2**63, and 10**5000", normalized)
                self.assertIn("Do not expand the requested domain", normalized)
                self.assertIn("Keep existing tests unchanged", normalized)
                self.assertIn("acceptance_criteria", request.schema["properties"]["contract"]["properties"])


class ExactFamilyGuardProofTests(unittest.TestCase):
    """The existing public proof accepts bounded families and still enforces guards."""

    ORIGINAL = """def count(total, width):
    if width <= 0:
        raise ValueError("width must be positive")
    return total // width
"""
    EXISTING = """import unittest
from allocation import count

class AllocationTests(unittest.TestCase):
    def test_exact_multiple(self):
        self.assertEqual(2, count(6, 3))
"""
    ADDED = textwrap.dedent("""
        def test_c1_exact_count_family(self):
            inputs = [(total, width) for total in range(13) for width in (2, 3, 4)]
            inputs += [(2**63 - 1, 3), (2**63, 3), (10**5000 + 1, 10)]
            for total, width in inputs:
                quotient, remainder = divmod(total, width)
                self.assertTrue(count(total, width) == quotient + bool(remainder))

        def test_c2_nonpositive_width(self):
            for width in (0, -1):
                with self.assertRaises(ValueError):
                    count(7, width)
    """)
    CRITERIA = [
        {
            "id": "C1",
            "criterion": "Given integer totals 0..12 at widths 2,3,4, and (2**63-1,3), (2**63,3), "
            "(10**5000+1,10), when count is called, then its exact result is q + (r != 0), "
            "where q,r = divmod(total,width), without decimal rendering.",
            "verification_method": "test: test_c1_exact_count_family",
            "human_review": False,
        },
        {
            "id": "C2",
            "criterion": "Given total=7 and each width in [0,-1], when count is called, then ValueError is raised.",
            "verification_method": "guard: test_c2_nonpositive_width",
            "human_review": False,
        },
        {
            "id": "C3",
            "criterion": "Given total=6 and width=3, when count is called, then it still returns 2.",
            "verification_method": "guard: test_exact_multiple",
            "human_review": False,
        },
    ]

    def test_family_and_guard_criteria_pass_ready_contract_validation(self):
        current = state("Correct allocation counts and preserve nonpositive-width rejection.")
        draft = body()
        draft.update(
            intended_outcome="Return the exact number of allocations, retaining width validation.",
            required_behaviors=["Exact integer ceiling for valid inputs; reject nonpositive widths."],
            acceptance_criteria=copy.deepcopy(self.CRITERIA),
            deliverables=["allocation.py", "test_allocation.py"],
            technical_approach=["Use integer ceiling arithmetic and preserve the existing validation."],
        )
        draft["milestones"][0].update(
            objective="Correct and verify allocation counts.",
            acceptance_criteria=[row["id"] for row in self.CRITERIA],
            affected_paths=["allocation.py", "test_allocation.py"],
        )
        lifecycle.validate_body(current, draft, ready=True, origin="astra_discovery")
        lifecycle.install_draft(current, draft, origin="astra_discovery")
        self.assertEqual(self.CRITERIA, current["goal_contract"]["body"]["acceptance_criteria"])
        self.assertFalse(goals.approved(current))

    def prove(self, implementation, added_tests=None):
        project = Project({"allocation.py": self.ORIGINAL, "test_allocation.py": self.EXISTING})
        self.addCleanup(project.close)
        project.write(
            {
                "allocation.py": implementation,
                "test_allocation.py": self.EXISTING
                + textwrap.indent(self.ADDED if added_tests is None else added_tests, "    "),
            }
        )
        evidence = tempfile.TemporaryDirectory(prefix="initial-coverage-proof-")
        self.addCleanup(evidence.cleanup)
        return regression.prove(feature_state(project, self.CRITERIA), project.root, Path(evidence.name))

    def test_family_fails_before_and_passes_after_while_guard_passes_both(self):
        proof = self.prove(self.ORIGINAL.replace("total // width", "-(-total // width)"))
        self.assertEqual("PASS", proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertIn("test_allocation.AllocationTests.test_c1_exact_count_family", proof["fail_to_pass"])
        self.assertIn("test_allocation.AllocationTests.test_c2_nonpositive_width", proof["pass_to_pass"])
        self.assertEqual({"C1", "C2", "C3"}, set(proof["case_tests"]))
        self.assertEqual(["test_allocation.AllocationTests.test_exact_multiple"], proof["case_tests"]["C3"])

    def test_a_correct_count_that_breaks_preservation_is_rejected(self):
        changed = self.ORIGINAL.replace("total // width", "-(-total // width)")
        proof = self.prove(changed.replace('raise ValueError("width must be positive")', "return 0"))
        self.assertEqual("FAIL", proof["verdict"])
        self.assertTrue(any("test_c2_nonpositive_width" in item for item in proof["failures"]), proof["failures"])

    def test_small_examples_do_not_hide_float_narrowing(self):
        changed = self.ORIGINAL.replace("total // width", "__import__('math').ceil(total / width)")
        proof = self.prove(changed)
        self.assertEqual("FAIL", proof["verdict"])
        self.assertTrue(any("test_c1_exact_count_family" in item for item in proof["failures"]), proof["failures"])

    def test_a_passing_suite_without_the_promised_guard_is_rejected(self):
        changed = self.ORIGINAL.replace("total // width", "-(-total // width)")
        proof = self.prove(changed, self.ADDED.split("def test_c2_nonpositive_width")[0])
        self.assertEqual("FAIL", proof["verdict"])
        self.assertEqual([], proof["case_tests"]["C2"])
