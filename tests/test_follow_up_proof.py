"""Separate current-turn guard evidence from original-reference regressions."""

import copy
import unittest

import autocode_code_checkpoints as checkpoints
import autocode_contract_identity as identity
import autocode_follow_up_proof as follow_proof
import autocode_regression as regression
import autocode_util as util
import autocode_verify as verify

from tests.test_verify import Project

OLD = {"id": "AC1", "criterion": "Negative values are excluded", "verification_method": "test: test_old"}
GUARD = {"id": "AC2", "criterion": "Keep delivered negative filtering", "verification_method": "guard: test_guard"}
CASE = {"id": "AC2", "kind": "preserve", "test_name": "test_guard"}


def contract(revision, criteria):
    row = {
        "task_id": "run",
        "revision": revision,
        "body": {"task_kind": "build", "acceptance_criteria": criteria, "open_blocking_questions": []},
    }
    row["hash"] = util.digest(row)
    approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(row)}
    row.update(origin="astra_discovery", approval_status="approved", approval_event=approval)
    return row


def fixture(project, *, prior_criteria=None):
    run = project.root / ".autocode/runs/fixture"
    run.mkdir(parents=True, exist_ok=True)
    (project.root / ".autocode/.gitignore").write_text("*\n")
    prior = contract(1, [OLD] if prior_criteria is None else prior_criteria)
    current = contract(2, [OLD, GUARD])
    source = util.snapshot(project.root)
    commit = checkpoints.capture_tree(project.root, source, run)
    row = {
        "id": "checkpoint",
        "commit": commit,
        "contract_hash": prior["hash"],
        "source_revision": source["revision"],
        "available": True,
    }
    saved = {**row, "source": source, "run_dir": str(run), "workspace": str(project.root)}
    path = run / "checkpoint.json"
    util.atomic_json(path, saved)
    row.update(receipt=str(path), receipt_sha256=util.file_hash(path))
    event = {
        "id": "feedback-next",
        "kind": "brief_feedback",
        "actor": "user_cli",
        "text": "Add start",
        "contract_token": identity.token(prior),
    }
    state = {
        "goal_contract": current,
        "contract_history": [prior],
        "code_checkpoints": [row],
        "stages": [{"source_revision": source["revision"]}],
        "user_events": [prior["approval_event"], event, current["approval_event"]],
        "brief_feedback": [event],
        "turns": [
            {"event_id": event["id"], "say": event["text"], "stage_index": 1, "previous": {"status": "TASK_COMPLETE"}}
        ],
    }
    return state, run


class BaselineIdentity(unittest.TestCase):
    def setUp(self):
        self.project = Project({"totals.py": "def sum_positive(v): return sum(x for x in v if x >= 0)\n"})
        self.addCleanup(self.project.close)
        self.state, self.run = fixture(self.project)

    def context(self):
        return follow_proof.context(self.state, self.project.root, self.run, [CASE])

    def test_exact_completed_source_is_bound_without_reusing_a_past_verdict(self):
        before = copy.deepcopy(self.state)
        found = self.context()
        self.assertEqual(self.state["code_checkpoints"][0]["commit"], found["commit"])
        self.assertEqual([CASE], found["cases"])
        self.assertNotIn("error", found)
        self.assertEqual(before, self.state)

    def test_missing_damaged_or_foreign_checkpoint_cannot_supply_guard_authority(self):
        original = copy.deepcopy(self.state)
        for change in ("absent", "source", "receipt", "foreign"):
            with self.subTest(change=change):
                self.state = copy.deepcopy(original)
                row = self.state["code_checkpoints"][0]
                if change == "absent":
                    self.state["code_checkpoints"] = []
                elif change == "source":
                    self.state["stages"][0]["source_revision"] = "other"
                elif change == "receipt":
                    row["receipt_sha256"] = "changed"
                else:
                    row["receipt"] = str(self.project.root / "totals.py")
                self.assertIn("error", self.context())

    def test_an_unchanged_prior_guard_keeps_its_original_reference(self):
        self.state, self.run = fixture(self.project, prior_criteria=[OLD, GUARD])
        self.assertIsNone(self.context())

    def test_rewording_an_existing_guard_does_not_change_its_reference(self):
        self.state, self.run = fixture(self.project, prior_criteria=[OLD, {**GUARD, "criterion": "Earlier wording"}])
        self.assertIsNone(self.context())


class FreshGuardEvidence(unittest.TestCase):
    def apply(self, *, passed=("test_guard",), failed=("test_new_feature",), **extra):
        proof = {
            "verdict": "PASS",
            "fail_to_pass": ["test_old", "test_guard", "test_new_feature"],
            "pass_to_pass": [],
            "case_tests": {"AC1": ["test_old"]},
            "failures": [],
            "unverified": [],
        }
        receipt = {
            "exit_code": 1 if failed else 0,
            "timed_out": False,
            "results": {"complete": True, "passed": list(passed), "failed": list(failed)},
        }
        receipt.update(extra)
        follow_proof.apply(proof, {"cases": [CASE], "commit": "completed"}, receipt, {})
        return proof

    def test_inherited_restore_stays_red_to_green_while_new_guard_is_freshly_green(self):
        proof = self.apply()
        self.assertEqual("PASS", proof["verdict"])
        self.assertEqual(["test_old"], proof["case_tests"]["AC1"])
        self.assertEqual(["test_guard"], proof["case_tests"]["AC2"])
        self.assertIn("test_guard", proof["fail_to_pass"])

    def test_a_failing_before_variant_is_not_hidden_by_a_passing_variant(self):
        proof = self.apply(passed=("test_guard[one]",), failed=("test_guard[two]",))
        self.assertEqual("FAIL", proof["verdict"])
        self.assertEqual([], proof["case_tests"]["AC2"])

    def test_a_wrapper_refusal_cannot_become_a_follow_up_guard(self):
        proof = {
            "verdict": "PASS",
            "fail_to_pass": ["test_guard"],
            "pass_to_pass": [],
            "failures": [],
            "unverified": [],
        }
        receipt = {"exit_code": 0, "results": {"complete": True, "passed": ["test_guard"], "failed": []}}
        follow_proof.apply(
            proof, {"cases": [CASE]}, receipt, {}, refused={"test_guard": "only launches another runner"}
        )
        self.assertEqual("FAIL", proof["verdict"])
        self.assertEqual([], proof["case_tests"]["AC2"])

    def test_every_candidate_variant_needs_fresh_before_evidence(self):
        proof = {
            "verdict": "PASS",
            "fail_to_pass": ["test_guard[a]", "test_guard[b]"],
            "pass_to_pass": [],
            "failures": [],
            "unverified": [],
        }
        receipt = {"exit_code": 0, "results": {"complete": True, "passed": ["test_guard[a]"], "failed": []}}
        follow_proof.apply(proof, {"cases": [CASE]}, receipt, {})
        self.assertEqual("UNVERIFIED", proof["verdict"])
        self.assertEqual([], proof["case_tests"]["AC2"])

    def test_skip_import_error_timeout_or_incomplete_output_is_not_fresh_pass(self):
        for extra in (
            {"timed_out": True},
            {"results": {"complete": False}},
            {"results": {"complete": True, "passed": [], "skipped": ["test_guard"]}},
            {"results": {"complete": True, "passed": ["test_guard"], "collection_errors": ["bad"]}},
            {"exit_code": 2},
            {"supervision": {}},
        ):
            with self.subTest(extra=extra):
                proof = self.apply(**extra)
                self.assertNotEqual("PASS", proof["verdict"])
                self.assertEqual([], proof["case_tests"]["AC2"])


class FreshBaselineExecution(unittest.TestCase):
    def test_full_proof_keeps_original_red_to_green_and_fresh_follow_up_guard(self):
        project = Project(
            {
                "totals.py": "def sum_positive(v): return sum(v)\n",
                "test_existing.py": "import unittest\nfrom totals import sum_positive\n"
                "class Existing(unittest.TestCase):\n"
                " def test_positive(self): self.assertEqual(sum_positive([2]), 2)\n",
            }
        )
        self.addCleanup(project.close)
        project.write({"totals.py": "def sum_positive(v): return sum(x for x in v if x >= 0)\n"})
        state, run = fixture(project)
        state.update(base_commit=project.base, settings={}, iteration=1, history=[])
        project.write(
            {
                "totals.py": "def sum_positive(v, *, start=0): return start + sum(x for x in v if x >= 0)\n",
                "test_totals.py": "import unittest\nfrom totals import sum_positive\n"
                "class Totals(unittest.TestCase):\n"
                " def test_old(self): self.assertEqual(sum_positive([-3, 2]), 2)\n"
                " def test_guard(self): self.assertEqual(sum_positive([-4, 2]), 2)\n"
                " def test_new_feature(self): self.assertEqual(sum_positive([2], start=10), 12)\n",
            }
        )
        proof = regression.prove(state, project.root, run)
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertEqual(project.base, proof["base"])
        self.assertTrue(any("test_old" in test for test in proof["case_tests"]["AC1"]))
        self.assertTrue(any("test_guard" in test for test in proof["case_tests"]["AC2"]))
        self.assertIn("follow_up_baseline", proof["checks"])
        checkpoint = state["code_checkpoints"][0]
        path = project.root / ".autocode/runs/fixture/checkpoint.json"
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        broken = regression.prove(state, project.root, run)
        self.assertEqual("UNVERIFIED", broken["verdict"])
        self.assertNotEqual(proof["path"], broken["path"], "a changed baseline must invalidate saved PASS")
        path.write_bytes(original)
        self.assertEqual(checkpoint["receipt_sha256"], util.file_hash(path))
        project.write({"totals.py": "def sum_positive(v, *, start=0): return start + sum(v)\n"})
        reverted = regression.prove(state, project.root, run)
        self.assertEqual("FAIL", reverted["verdict"], "restored prior bugs must still fail the original proof")
        self.assertFalse(reverted["case_tests"]["AC1"])

    def test_current_tests_run_on_completed_source_without_copying_feature_code(self):
        project = Project({"totals.py": "def sum_positive(v): return sum(x for x in v if x >= 0)\n"})
        self.addCleanup(project.close)
        state, run = fixture(project)
        context = follow_proof.context(state, project.root, run, [CASE])
        project.write(
            {
                "totals.py": "def sum_positive(v, *, start=0): return start + sum(x for x in v if x >= 0)\n",
                "test_totals.py": "import unittest\nfrom totals import sum_positive\n"
                "class Totals(unittest.TestCase):\n"
                " def test_guard(self): self.assertEqual(sum_positive([-3, 2]), 2)\n"
                " def test_new_feature(self): self.assertEqual(sum_positive([2], start=10), 12)\n",
            }
        )
        framework = verify.detect_framework(project.root)
        result = {"test_files": ["test_totals.py"], "commands": {"regression": framework.targeted(["test_totals.py"])}}
        receipt = follow_proof.collect(
            context,
            project.root,
            run,
            result,
            framework,
            verify=verify,
            dependencies=project.root,
            inputs=None,
            timeout=30,
        )
        self.assertFalse(receipt["timed_out"])
        self.assertEqual(1, receipt["exit_code"])
        self.assertTrue(any("test_guard" in test for test in receipt["results"]["passed"]))
        self.assertTrue(any("test_new_feature" in test for test in receipt["results"]["failed"]))
        self.assertIn("start=0", (project.root / "totals.py").read_text())
        self.assertFalse((run / "follow-up-baseline").exists())
