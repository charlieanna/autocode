"""Changed-line evidence returns weak tests to Builder through the public task-run API."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance

BRIEF = (
    "Fix adjust(n) so numbers above 10 return 9 and negative numbers return 1, "
    "while numbers from 0 through 10 remain unchanged. "
    "Preserve the public function and add regression tests."
)
SEED = "def adjust(n):\n    return n\n"
FIXED = "def adjust(n):\n    if n > 10:\n        return 9\n    if n < 0:\n        return 1\n    return n\n"
LOWER_RETURN_LINE = 5
EXISTING_TEST = (
    "import unittest\nfrom calc import adjust\n\n"
    "class ExistingTests(unittest.TestCase):\n"
    "    def test_middle_value_is_preserved(self):\n"
    "        self.assertEqual(3, adjust(3))\n"
)
WEAK_TEST = (
    "import unittest\nfrom calc import adjust\n\n"
    "class LimitTests(unittest.TestCase):\n"
    "    def test_t1_adjusts_limits(self):\n"
    "        self.assertEqual(9, adjust(50))\n"
)
STRONG_TEST = WEAK_TEST + (
    "        self.assertEqual(9, adjust(11))\n"
    "        self.assertEqual(10, adjust(10))\n"
    "        self.assertEqual(1, adjust(-1))\n"
    "        self.assertEqual(0, adjust(0))\n"
)
EQUIVALENT_SOURCE = "def adjust(n):\n    if n > 10:\n        return 10\n    return n\n"
EQUIVALENT_TEST = (
    "import unittest\nfrom calc import adjust\n\n"
    "class LimitTests(unittest.TestCase):\n"
    "    def test_t1_adjusts_limits(self):\n"
    "        self.assertEqual(10, adjust(50))\n"
    "        self.assertEqual(10, adjust(11))\n"
    "        self.assertEqual(10, adjust(10))\n"
    "        self.assertEqual(0, adjust(0))\n"
)
GUIDANCE = (
    "Add assertions for adjust(-1), adjust(0), adjust(10) and adjust(11) to test_t1_adjusts_limits; "
    "retain the upper assertion and the existing public behavior."
)


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised fake-provider workflow requires supported Linux")
class ChangedCodeEvidenceCliTests(unittest.TestCase):
    def start(self, *, ignored_result=False, equivalent=False, wait_for_investigator=False, checkpoint=False):
        temporary = tempfile.TemporaryDirectory(prefix="changed-code-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        seed = self.root / "seed"
        (seed / "tests").mkdir(parents=True)
        (seed / "calc.py").write_text(SEED)
        (seed / "tests/__init__.py").write_text("")
        (seed / "tests/test_existing.py").write_text(EXISTING_TEST)
        self.workspace = without_maintenance(materialize(seed, self.root / "project"))
        self.weak = self.root / "weak"
        self.strong = self.root / "strong"
        for folder, tests in (
            (self.weak, WEAK_TEST + ("        adjust(-1)  # Deliberately ignored result.\n" if ignored_result else "")),
            (self.strong, STRONG_TEST),
        ):
            (folder / "tests").mkdir(parents=True)
            source = EQUIVALENT_SOURCE if equivalent else FIXED
            tests = EQUIVALENT_TEST if equivalent else tests
            if checkpoint:
                source = source.replace("return 1\n", "return 0\n")
                tests = tests.replace("self.assertEqual(1, adjust(-1))", "self.assertEqual(0, adjust(-1))")
            (folder / "calc.py").write_text(source)
            (folder / "tests/test_limits.py").write_text(tests)
        brief = (
            "Fix adjust(n) so numbers above 10 return 10 while all other numbers remain unchanged. "
            "Preserve the public function and add regression tests."
            if equivalent
            else BRIEF
        )
        if checkpoint:
            brief = brief.replace("negative numbers return 1", "negative numbers return 0")
        scenario = dataclasses.replace(
            catalog.load("bugfix-trivial"),
            title="Changed lower branch lacks a proving assertion",
            brief=brief,
            fake_probe='python3 -c "from calc import adjust; assert adjust(50) == 50"',
        )
        flags, env = fake_setup(scenario, self.root, self.weak)
        self.trace = self.root / "provider-trace.jsonl"
        self.env = {
            **env,
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "CHANGED_CODE_TRACE": str(self.trace),
            "CHANGED_CODE_STRONG": str(self.strong),
            "CHANGED_CODE_WAIT_FOR_INVESTIGATOR": "1" if wait_for_investigator else "0",
        }
        paths = self.env["PATH"].split(os.pathsep)
        self.env["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        provider = self.root / "bin/codex"
        source = provider.read_text()
        amendment = textwrap.dedent("""
            FIXTURE_GUIDANCE = GUIDANCE_LITERAL
            _ordinary_report_for = report_for
            def report_for(stage, data):
                if stage == "investigate_stuck" and not data.get("report_repair"):
                    failure = data.get("builder_failure") or {}
                    changed = (failure.get("diagnosis") or {}).get("changed_code_evidence") or {}
                    if changed.get("status") == "UNVERIFIED" and any(
                        row.get("path") == "calc.py" and row.get("line") == 5
                        and row.get("status") == "reason_required"
                        for row in changed.get("obligations", [])):
                        return {
                            "diagnosis": "The saved mutation proof shows that the lower return at calc.py:5 "
                                         "can change without failing the selected regression test.",
                            "cause": "stage_output", "failure_class": "execution",
                            "failure_id": failure["failure_id"], "guidance": FIXTURE_GUIDANCE,
                            "recommendation": "retry", "user_question": "",
                            "evidence_refs": list(failure["evidence_refs"]),
                            "example": "The selected test still passes with the lower return changed from 1 to 0.",
                            "probe": "", "untestable": "The authenticated saved mutation experiment already "
                                "shows the test weakness; no additional contained probe is available in this fixture."}
                changed = data.get("changed_code_evidence") or {}
                guidance = (data.get("changed_code_investigation") or {}).get("guidance")
                repair = changed.get("status") == "UNVERIFIED" and (
                    os.environ["CHANGED_CODE_WAIT_FOR_INVESTIGATOR"] == "0" or guidance == FIXTURE_GUIDANCE)
                original = CONFIG["reference"]
                if stage == "terra" and repair:
                    CONFIG["reference"] = os.environ["CHANGED_CODE_STRONG"]
                try:
                    if stage == "astra_checkpoint":
                        return {"validation": _ordinary_report_for(stage, data),
                                "decision": _ordinary_report_for("astra_review", data),
                                "consult_sol": {"requested": False, "question": "", "reason": ""}}
                    return _ordinary_report_for(stage, data)
                finally:
                    CONFIG["reference"] = original
        """).replace("GUIDANCE_LITERAL", repr(GUIDANCE))
        entrypoint = 'if __name__ == "__main__":'
        self.assertEqual(1, source.count(entrypoint))
        source = source.replace(entrypoint, amendment + "\n" + entrypoint)
        marker = '    _report(report, sys.argv[sys.argv.index("-o") + 1])'
        witness = textwrap.dedent("""
            with Path(os.environ["CHANGED_CODE_TRACE"]).open("a") as stream:
                stream.write(json.dumps({"stage": stage,
                    "repair": bool(data.get("report_repair")),
                    "error": data.get("error"), "handoff": data,
                    "report": report, "output": sys.argv[sys.argv.index("-o") + 1]}) + "\\n")
        """)
        if equivalent or checkpoint:
            reason_line = 4 if checkpoint else 2
            reason = (
                "The strict-boundary mutant is equivalent: at n=0 both the lower return and the "
                "unchanged return produce 0. The tests cover the preserved middle value."
                if checkpoint
                else "The strict-boundary mutant is equivalent: at n=10 both the capped return and the "
                "unchanged return produce 10. The tests assert 10, 11 and 50."
            )
            witness = (
                textwrap.dedent("""
                if stage in ("sol", "astra_checkpoint") and not data.get("report_repair"):
                    changed = (data.get("regression_proof") or {}).get("changed_code_evidence") or {}
                    target = report["validation"] if stage == "astra_checkpoint" else report
                    target["changed_code_exceptions"] = [
                        {"gap_id": row["gap_id"], "decision": "accept", "reason_kind": "equivalent_mutant",
                         "reason": REASON_LITERAL,
                         "evidence_refs": ["calc.py", "tests/test_limits.py"]}
                        for row in changed.get("obligations", [])
                        if row.get("path") == "calc.py" and row.get("line") == REASON_LINE
                           and row.get("status") == "reason_required"]
            """)
                .replace("REASON_LITERAL", repr(reason))
                .replace("REASON_LINE", str(reason_line))
                + witness
            )
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, textwrap.indent(witness, "    ") + marker))
        self.run = None
        self.addCleanup(self.export)
        self.run = taskrun.TaskRun.start(
            self.workspace,
            brief,
            options=(*flags, "--workflow", "bugfix", "--max-iterations", "6", "--max-seconds", "900"),
            env=self.env,
            timeout=240,
            cwd=self.root,
        )
        # Workflow selection is a creation option, never an override on resume.
        saved_options = list(self.run.options)
        workflow = saved_options.index("--workflow")
        del saved_options[workflow : workflow + 2]
        self.run.options = tuple(saved_options)
        view = self.run.advance_until_input()
        if view["needs"]["kind"] == "answer":
            question = next(q for q in view["needs"]["questions"] if q["id"].startswith("requirements-review-"))
            self.run.answer(question["id"], "plan", resolver_token=view["needs"]["resolver_token"])
            view = self.run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], self.diagnostic())
        self.assertIn("adjust", self.run.show_goal())
        self.approved_token = view["needs"]["token"]
        self.routes = view["routes"]
        self.run.approve_plan(self.approved_token)
        self.configure_execution_fixture()
        return self.run.advance_until_input()

    def configure_execution_fixture(self):
        """The public fixtures keep the workflow selected by the ordinary CLI."""

    def attempts(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()] if self.trace.exists() else []

    def diagnostic(self):
        view = self.run.status() if self.run else {}
        return {
            "status": view.get("status"),
            "reason": view.get("stop_reason"),
            "needs": view.get("needs"),
            "attempts": [{key: row.get(key) for key in ("stage", "repair", "error")} for row in self.attempts()],
        }

    def export(self):
        if not (folder := os.environ.get("CHANGED_CODE_EVIDENCE_DIR")):
            return
        target = Path(folder) / self._testMethodName
        target.mkdir(parents=True)
        if self.trace.exists():
            shutil.copy2(self.trace, target / self.trace.name)
        if self.run:
            (target / "status.json").write_text(json.dumps(self.run.status(), indent=2))
            shutil.copytree(self.run.run_dir, target / "run")

    def assert_repaired(self, view, *, expected_builders=2):
        attempts = self.attempts()
        builders = [row for row in attempts if row["stage"] == "terra" and not row["repair"]]
        self.assertEqual(expected_builders, len(builders), self.diagnostic())
        feedback = json.dumps(builders[1]["handoff"])
        self.assertIn("calc.py", feedback)
        self.assertIn(f"calc.py:{LOWER_RETURN_LINE}", feedback)
        self.assertEqual("TASK_COMPLETE", view["status"], self.diagnostic())
        self.assertEqual(self.approved_token, view["approved_contract"]["token"])
        self.assertEqual(self.routes, view["routes"])
        self.assertEqual("PASS", view["evidence"]["check_replay"]["verdict"])
        for stage in ("sol", "astra_review"):
            self.assertTrue(any(row["stage"] == stage and not row["repair"] for row in attempts), stage)
        self.assertEqual(STRONG_TEST, (self.workspace / "tests/test_limits.py").read_text())
        self.assertEqual(FIXED, (self.workspace / "calc.py").read_text())
        self.assertFalse(any(row["repair"] for row in attempts), self.diagnostic())
        changed = view["verification"]["changed_code_evidence"]
        self.assertEqual("PASS", changed["status"], changed)
        self.assertEqual(view["evidence"]["regression_proof"]["source_revision"], changed["source_revision"])
        self.assertEqual(
            {("calc.py", line) for line in range(2, 6)}, {(row["path"], row["line"]) for row in changed["obligations"]}
        )
        self.assertTrue(all(row["status"] == "tested" for row in changed["obligations"]), changed)
        self.assertTrue(all(row["mutation_demonstrated"] for row in changed["obligations"]), changed)
        self.assertEqual({}, view["evidence"]["mutation_check"].get("survivors"), view["evidence"]["mutation_check"])
        if not view["evidence"]["diff_coverage"]["collected"]:
            self.assertEqual(["mutation_only"], changed["fallback"], changed)
            self.assertTrue(all(row["fallback"] == "mutation_only" for row in changed["obligations"]), changed)
        # Explicit saved proof artifacts retain the original real test results;
        # no private state is constructed or inspected by this fixture.
        proofs = [
            json.loads(path.read_text()) for path in self.run.run_dir.glob("regression/proof-*/verification.json")
        ]
        deficient = next(
            proof
            for proof in proofs
            if any(
                row.get("path") == "calc.py"
                and row.get("line") == LOWER_RETURN_LINE
                and row.get("status") == "reason_required"
                for row in (proof.get("changed_code_evidence") or {}).get("obligations", [])
            )
        )
        self.assertTrue(deficient["fail_to_pass"], deficient)
        lower_mutants = [
            row
            for file in deficient["mutation_check"]["files"]
            if file["path"] == "calc.py"
            for row in file["mutants"]
            if row["line"] == LOWER_RETURN_LINE
        ]
        self.assertTrue(any(row["outcome"] == "survived" for row in lower_mutants), lower_mutants)
        self.assertNotEqual(deficient["source_revision"], changed["source_revision"])
        self.assertFalse((self.workspace / "node_modules").exists())
        self.assertFalse((self.workspace / ".venv").exists())

    def test_independent_reviewer_accepts_a_current_equivalent_mutant_reason(self):
        view = self.start(equivalent=True)
        self.assertEqual("TASK_COMPLETE", view["status"], self.diagnostic())
        self.assertEqual(self.approved_token, view["approved_contract"]["token"])
        self.assertEqual(self.routes, view["routes"])
        attempts = self.attempts()
        self.assertEqual(1, sum(row["stage"] == "terra" for row in attempts))
        reviewer = next(row for row in attempts if row["stage"] == "sol")
        decisions = reviewer["report"]["changed_code_exceptions"]
        self.assertEqual(1, len(decisions), reviewer)
        self.assertEqual("equivalent_mutant", decisions[0]["reason_kind"])
        original = reviewer["handoff"]["regression_proof"]["changed_code_evidence"]
        gap = next(row for row in original["obligations"] if row["gap_id"] == decisions[0]["gap_id"])
        self.assertEqual(("calc.py", 2, "reason_required"), (gap["path"], gap["line"], gap["status"]))
        changed = view["verification"]["changed_code_evidence"]
        self.assertEqual("PASS", changed["status"], changed)
        accepted = next(row for row in changed["obligations"] if row["line"] == 2)
        self.assertEqual("excepted", accepted["status"], accepted)
        self.assertEqual(decisions[0]["reason"], accepted["exception"]["reason"])
        self.assertEqual(original["source_revision"], changed["source_revision"])
        self.assertEqual(original["proof_context"], changed["proof_context"])
        self.assertTrue(any(row["stage"] == "astra_review" for row in attempts))
        self.assertFalse(any(row["repair"] for row in attempts), self.diagnostic())
        self.assertEqual(EQUIVALENT_SOURCE, (self.workspace / "calc.py").read_text())

    def test_unexecuted_branch_returns_exact_line_to_builder_then_completes(self):
        self.assert_repaired(self.start())

    def test_executing_a_branch_without_asserting_its_result_is_not_proof(self):
        self.assert_repaired(self.start(ignored_result=True))

    def test_two_direct_returns_then_investigator_guidance_repair_without_person_question(self):
        view = self.start(wait_for_investigator=True)
        self.assert_repaired(view, expected_builders=4)
        attempts = self.attempts()
        investigations = [row for row in attempts if row["stage"] == "investigate_stuck"]
        self.assertEqual(1, len(investigations), self.diagnostic())
        investigation = investigations[0]
        before = attempts[: attempts.index(investigation)]
        self.assertEqual(3, sum(row["stage"] == "terra" for row in before))
        failure = investigation["handoff"]["builder_failure"]
        original = failure["diagnosis"]["changed_code_evidence"]
        self.assertEqual("UNVERIFIED", original["status"])
        self.assertTrue(
            any(
                row["path"] == "calc.py" and row["line"] == LOWER_RETURN_LINE and row["status"] == "reason_required"
                for row in original["obligations"]
            )
        )
        self.assertEqual(failure["failure_id"], investigation["report"]["failure_id"])
        self.assertEqual("", investigation["report"]["user_question"])
        final_builder = next(row for row in reversed(attempts) if row["stage"] == "terra")
        received = final_builder["handoff"]["changed_code_investigation"]
        self.assertEqual(GUIDANCE, received["guidance"])
        self.assertEqual(investigation["report"]["diagnosis"], received["diagnosis"])
        self.assertEqual(investigation["report"]["evidence_refs"], received["evidence_refs"])
        self.assertEqual(
            original["source_revision"], final_builder["handoff"]["changed_code_evidence"]["source_revision"]
        )


if __name__ == "__main__":
    unittest.main()
