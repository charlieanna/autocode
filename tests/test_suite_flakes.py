"""The flaky-test recheck before a suite failure counts as a regression (#2942)."""

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import autocode_completion_result as completion_result
import autocode_project_suite_health as project_suite_health
import autocode_proof_findings as proof_findings
import autocode_regression as regression
import autocode_suite_flakes as flakes
import autocode_verify as verify
import autocode_verify_judgment as judgment
import scenario_references as references
import task_scenarios as scenarios

from tests.test_verify import Project

CASES = [{"id": "C1", "text": "answer is 2", "test_name": "test_c1_answers_two"}]
FLAKY = "tests.test_flaky.FlakyTests.test_sometimes"
CLOCK = "test_clock.TestClock.test_tick"


def receipt(passed=(), failed=(), complete=True, exit_code=0):
    return {
        "exit_code": exit_code,
        "timed_out": False,
        "results": {
            "complete": complete,
            "passed": list(passed),
            "failed": list(failed),
            "skipped": [],
            "uncollected": [],
            "total": len(passed) + len(failed),
        },
    }


def scripted(outcomes):
    """A ``run`` that answers each side's reruns in order and records what it was asked."""
    calls = []
    queues = {side: list(values) for side, values in outcomes.items()}

    def run(command, side, label):
        calls.append((command, side, label))
        result = queues[side].pop(0)
        return result if "results" in result else receipt(**result)

    return run, calls


class SuspectTests(unittest.TestCase):
    def test_only_complete_base_passes_that_fail_now_and_no_case_or_changed_file_names(self):
        candidate = receipt(passed=["t.A.test_ok"], failed=["t.A.test_flaky", "t.A.test_named", "t.A.test_case"])
        base = receipt(passed=["t.A.test_ok", "t.A.test_flaky", "t.A.test_named", "t.A.test_case"])
        found = flakes.suspects(
            candidate, base, protected=["t.A.test_case"], changed_texts=["def test_named(self): ..."]
        )
        self.assertEqual(["t.A.test_flaky"], found)

    def test_an_incomplete_or_timed_out_run_rechecks_nothing(self):
        candidate = receipt(failed=["t.A.test_flaky"])
        self.assertEqual([], flakes.suspects(candidate, receipt(passed=["t.A.test_flaky"], complete=False)))
        self.assertEqual([], flakes.suspects({**candidate, "timed_out": True}, receipt(passed=["t.A.test_flaky"])))


class RecheckTests(unittest.TestCase):
    COMMAND = f"{sys.executable} -m unittest discover -v"

    def test_a_failure_on_every_candidate_run_stays_a_regression_and_never_reruns_base(self):
        run, calls = scripted({"candidate": [{"failed": [FLAKY]}, {"failed": [FLAKY]}]})
        record = flakes.recheck("unittest", self.COMMAND, [FLAKY], run)
        self.assertEqual(["candidate", "candidate"], [side for _, side, _ in calls])
        self.assertIn(FLAKY, calls[0][0])
        self.assertEqual("regression", record["tests"][0]["verdict"])
        self.assertEqual([], flakes.quarantined(record))
        self.assertEqual(([], []), flakes.explain(record))

    def test_a_test_that_also_fails_on_the_original_code_is_quarantined_with_its_runs(self):
        run, calls = scripted(
            {
                "candidate": [{"passed": [FLAKY]}, {"passed": [FLAKY]}],
                "base": [{"failed": [FLAKY]}, {"passed": [FLAKY]}],
            }
        )
        record = flakes.recheck("unittest", self.COMMAND, [FLAKY], run)
        self.assertEqual(["candidate", "candidate", "base", "base"], [side for _, side, _ in calls])
        self.assertEqual(
            {"test": FLAKY, "candidate": ["fail", "pass", "pass"], "base": ["pass", "fail", "pass"]},
            {key: record["tests"][0][key] for key in ("test", "candidate", "base")},
        )
        self.assertEqual([FLAKY], flakes.quarantined(record))
        self.assertEqual(flakes.QUARANTINE_REASON, record["quarantined"][0]["reason"])
        notes, reasons = flakes.explain(record)
        self.assertIn("Quarantined as flaky", notes[0])
        self.assertIn(FLAKY, reasons[0])

    def test_a_test_that_passes_every_run_on_the_original_code_stays_a_regression(self):
        run, _ = scripted({"candidate": [{"passed": [FLAKY]}, {"failed": [FLAKY]}], "base": [{"passed": [FLAKY]}] * 2})
        record = flakes.recheck("unittest", self.COMMAND, [FLAKY], run)
        self.assertEqual("regression", record["tests"][0]["verdict"])
        self.assertEqual([], flakes.quarantined(record))

    def test_a_test_a_prior_proof_saw_fail_on_both_sides_is_quarantined_when_it_passes_again(self):
        # A pre-existing row that was flaky on both sides is reused with its judged base run first: a failure.
        known = {FLAKY: {"base_runs": ["fail", "pass", "pass"], "base_commit": "abc123", "reason": "flaky"}}
        run, calls = scripted({"candidate": [{"passed": [FLAKY]}, {"failed": [FLAKY]}]})
        record = flakes.recheck("unittest", self.COMMAND, [FLAKY], run, quarantined_entries=known)
        self.assertEqual(["candidate", "candidate"], [side for _, side, _ in calls])
        self.assertEqual("quarantined", record["tests"][0]["verdict"])
        self.assertEqual([FLAKY], flakes.quarantined(record))

    def test_missing_evidence_never_quarantines(self):
        unreported = {"exit_code": 1, "timed_out": True, "results": None}
        run, _ = scripted({"candidate": [{"passed": [FLAKY]}, unreported], "base": [{"failed": [FLAKY]}] * 2})
        record = flakes.recheck("unittest", self.COMMAND, [FLAKY], run)
        self.assertEqual("regression", record["tests"][0]["verdict"])

    def test_without_a_selector_or_with_too_many_failures_nothing_reruns(self):
        run, calls = scripted({"candidate": [], "base": []})
        unselectable = flakes.recheck("unittest", "make test", [FLAKY], run)
        self.assertIn("cannot select", unselectable["note"])
        many = [f"t.A.test_{index}" for index in range(flakes.MAX_SUSPECTS + 1)]
        crowd = flakes.recheck("unittest", self.COMMAND, many, run)
        self.assertIn("judged as regressions", crowd["note"])
        self.assertEqual([], calls)
        self.assertEqual(([], []), (flakes.quarantined(unselectable), flakes.quarantined(crowd)))
        self.assertEqual(["Flaky-test recheck: " + crowd["note"]], flakes.explain(crowd)[0])


class PreExistingTests(unittest.TestCase):
    """A failure the original code's one suite run shared is rerun there before it is subtracted."""

    COMMAND = f"{sys.executable} -m unittest discover -v"

    def test_only_failures_on_both_sides_that_no_case_or_changed_file_names(self):
        candidate = receipt(failed=["t.A.test_both", "t.A.test_new", "t.A.test_named", "t.A.test_case"])
        base = receipt(passed=["t.A.test_new"], failed=["t.A.test_both", "t.A.test_named", "t.A.test_case"])
        found = flakes.masked(candidate, base, protected=["t.A.test_case"], changed_texts=["def test_named(self): ..."])
        self.assertEqual(["t.A.test_both"], found)
        self.assertEqual([], flakes.masked(candidate, receipt(failed=["t.A.test_both"], complete=False)))

    def test_a_test_failing_every_run_of_the_original_code_stays_pre_existing(self):
        run, calls = scripted({"base": [{"failed": [FLAKY]}, {"failed": [FLAKY]}]})
        record = flakes.recheck_pre_existing("unittest", self.COMMAND, [FLAKY], run)
        self.assertEqual(["base", "base"], [side for _, side, _ in calls])
        self.assertIn(FLAKY, calls[0][0])
        self.assertEqual("pre_existing", record["tests"][0]["verdict"])
        self.assertEqual([], record["regressed"])

    def test_a_chance_failure_on_the_original_code_does_not_hide_a_real_break(self):
        run, calls = scripted(
            {
                "base": [{"passed": [FLAKY]}, {"failed": [FLAKY]}],
                "candidate": [{"failed": [FLAKY]}, {"failed": [FLAKY]}],
            }
        )
        record = flakes.recheck_pre_existing("unittest", self.COMMAND, [FLAKY], run)
        self.assertEqual(["base", "base", "candidate", "candidate"], [side for _, side, _ in calls])
        self.assertEqual(
            {
                "test": FLAKY,
                "base": ["fail", "pass", "fail"],
                "candidate": ["fail", "fail", "fail"],
                "verdict": "regression",
            },
            record["tests"][0],
        )
        self.assertEqual([FLAKY], flakes.unmasked({"pre_existing": record}))

    def test_a_test_flaky_on_both_sides_stays_out_of_the_comparison(self):
        run, _ = scripted(
            {
                "base": [{"passed": [FLAKY]}, {"passed": [FLAKY]}],
                "candidate": [{"failed": [FLAKY]}, {"passed": [FLAKY]}],
            }
        )
        record = flakes.recheck_pre_existing("unittest", self.COMMAND, [FLAKY], run)
        self.assertEqual("flaky", record["tests"][0]["verdict"])
        self.assertEqual([], record["regressed"])

    def test_missing_evidence_keeps_the_pre_existing_failure(self):
        run, _ = scripted(
            {"base": [{"passed": [FLAKY]}, {"passed": [FLAKY]}], "candidate": [{"failed": []}, {"failed": []}]}
        )
        record = flakes.recheck_pre_existing("unittest", self.COMMAND, [FLAKY], run)
        self.assertEqual(["fail", "missing", "missing"], record["tests"][0]["candidate"])
        self.assertEqual("pre_existing", record["tests"][0]["verdict"])
        run, _ = scripted({"base": [{"failed": []}, {"failed": []}]})
        verdict = flakes.recheck_pre_existing("unittest", self.COMMAND, [FLAKY], run)["tests"][0]["verdict"]
        self.assertEqual("pre_existing", verdict)

    def test_without_a_selector_or_with_too_many_failures_nothing_reruns(self):
        run, calls = scripted({})
        self.assertIsNone(flakes.recheck_pre_existing("unittest", self.COMMAND, [], run))
        unselectable = flakes.recheck_pre_existing("unittest", "make test", [FLAKY], run)
        many = flakes.recheck_pre_existing(
            "unittest", self.COMMAND, [f"t.A.test_{n}" for n in range(flakes.MAX_SUSPECTS + 1)], run
        )
        self.assertEqual([], calls)
        for record in (unselectable, many):
            self.assertEqual([], record["regressed"])
            self.assertIn("pre-existing failures without a recheck", record["note"])
        notes, _ = flakes.explain({"pre_existing": many})
        self.assertTrue(notes[0].startswith("Pre-existing failure recheck: "))


class JudgmentTests(unittest.TestCase):
    def judge(self, quarantined):
        fail, unverified, notes = [], [], []
        candidate = {**receipt(passed=["t.A.test_ok"], failed=[FLAKY], exit_code=1), "results_expected": True}
        base = {"receipt": receipt(passed=["t.A.test_ok", FLAKY]), "health": "passing"}
        judgment.judge_suite(candidate, base, fail, unverified, notes, quarantined=quarantined)
        return fail, unverified

    def test_a_quarantined_test_is_neither_a_regression_nor_a_lost_test(self):
        self.assertTrue(any(FLAKY in reason for reason in self.judge(())[0]))
        self.assertEqual(([], []), self.judge([FLAKY]))


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class ProofRunTests(unittest.TestCase):
    """A real proof whose unchanged suite test fails on scripted runs, counted per tree."""

    def prove(self, fail_on):
        """``fail_on`` maps ``candidate`` and ``base`` to the 1-based runs, in that kind of tree, that fail."""
        temporary = tempfile.TemporaryDirectory(prefix="suite-flakes-")
        self.addCleanup(temporary.cleanup)
        root, evidence, log = (Path(temporary.name) / name for name in ("project", "evidence", "runs.log"))
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "__init__.py").write_text("")
        (root / "clocked.py").write_text("def answer():\n    return 1\n")
        (root / "tests" / "test_flaky.py").write_text(
            textwrap.dedent(
                f"""\
                import unittest
                from pathlib import Path

                LOG = Path({str(log)!r})
                FAIL_ON = {fail_on!r}


                class FlakyTests(unittest.TestCase):
                    def test_sometimes(self):
                        kind = "candidate" if "candidate" in Path(__file__).resolve().parts else "base"
                        with LOG.open("a") as handle:
                            handle.write(kind + "\\n")
                        count = LOG.read_text().split().count(kind)
                        self.assertNotIn(count, FAIL_ON[kind])

                    def test_steady(self):
                        pass
                """
            )
        )
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        base = git(root, "rev-parse", "HEAD")
        (root / "clocked.py").write_text("def answer():\n    return 2\n")
        (root / "tests" / "test_clocked.py").write_text(
            "import unittest\n\nimport clocked\n\n\nclass ClockedTests(unittest.TestCase):\n"
            "    def test_c1_answers_two(self):\n        self.assertEqual(2, clocked.answer())\n"
        )
        framework = verify.detect_framework(root, python=sys.executable)
        base_suite = verify.baseline(
            root, base, evidence, framework=framework, suite_command=framework.suite, timeout=60
        )
        result = verify.verify(
            root, base, evidence, framework=framework, base_suite=base_suite, timeout=60, new_behavior=True, cases=CASES
        )
        self.evidence = evidence
        return result, log.read_text().split()

    def test_a_test_flaky_on_the_original_code_does_not_hold_an_honest_change(self):
        result, runs = self.prove({"candidate": [1], "base": [2]})
        self.assertEqual("PASS", result["verdict"], result["failures"] + result["unverified"])
        self.assertEqual([FLAKY], [row["test"] for row in result["suite_flakes"]["quarantined"]])
        self.assertEqual(["base", "candidate", "candidate", "candidate", "base", "base"], runs)
        self.assertTrue(any("quarantined" in reason for reason in result["review_reasons"]))
        self.assertTrue(any("Quarantined as flaky" in note for note in result["notes"]))
        self.assertTrue(
            {"flake-candidate-2", "flake-candidate-3", "flake-base-2", "flake-base-3"} <= set(result["checks"])
        )
        self.assertIn("suite_flakes", regression.SUMMARY_KEYS)
        self.assertFalse((self.evidence / "scratch" / "flake-base").exists())

    def test_a_test_flaky_only_with_the_change_is_still_a_regression(self):
        result, runs = self.prove({"candidate": [1], "base": []})
        self.assertEqual("FAIL", result["verdict"])
        self.assertTrue(any(FLAKY in reason for reason in result["failures"]), result["failures"])
        self.assertEqual("regression", result["suite_flakes"]["tests"][0]["verdict"])
        self.assertEqual([], result["suite_flakes"]["quarantined"])

    def test_a_chance_failure_on_the_original_code_does_not_hide_a_change_that_breaks_the_test(self):
        result, runs = self.prove({"candidate": [1, 2, 3], "base": [1]})
        self.assertEqual("FAIL", result["verdict"], result["failures"] + result["unverified"])
        self.assertIn(flakes.UNMASKED + ": " + FLAKY, result["failures"])
        self.assertEqual(["base", "candidate", "base", "base", "candidate", "candidate"], runs)
        self.assertEqual([FLAKY], result["suite_flakes"]["pre_existing"]["regressed"])
        self.assertTrue(
            {"pre-existing-base-2", "pre-existing-base-3", "pre-existing-candidate-2", "pre-existing-candidate-3"}
            <= set(result["checks"])
        )
        self.assertFalse((self.evidence / "scratch" / "flake-base").exists())

    def test_a_test_that_fails_every_run_of_the_original_code_stays_a_pre_existing_failure(self):
        result, runs = self.prove({"candidate": [1, 2, 3], "base": [1, 2, 3]})
        self.assertEqual("PASS", result["verdict"], result["failures"] + result["unverified"])
        self.assertEqual(["base", "candidate", "base", "base"], runs)
        self.assertEqual("pre_existing", result["suite_flakes"]["pre_existing"]["tests"][0]["verdict"])

    def test_a_test_the_change_breaks_on_every_run_is_a_regression_without_touching_base_again(self):
        result, runs = self.prove({"candidate": [1, 2, 3], "base": [2, 3]})
        self.assertEqual("FAIL", result["verdict"])
        self.assertTrue(any(FLAKY in reason for reason in result["failures"]), result["failures"])
        self.assertEqual(["base", "candidate", "candidate", "candidate"], runs)


class FlakeFindingTests(unittest.TestCase):
    """The runner's proof finding on a source whose only failure was a suite test that then passed (#2942)."""

    FAILURE = proof_findings.REGRESSED + ": " + FLAKY

    def proof(self, candidate=("fail", "pass", "pass"), failures=None, unverified=(), test=FLAKY):
        return {
            "verdict": "FAIL",
            "failures": [self.FAILURE] if failures is None else failures,
            "unverified": list(unverified),
            "suite_flakes": {"tests": [{"test": test, "candidate": list(candidate), "verdict": "regression"}]},
        }

    def test_only_a_lone_suite_regression_whose_every_rerun_passed_counts(self):
        self.assertEqual([FLAKY], proof_findings.passed_again(self.proof()))
        for proof in (
            self.proof(candidate=("fail", "pass", "fail")),
            self.proof(candidate=("fail", "pass", "missing")),
            self.proof(candidate=("fail",)),
            self.proof(failures=[self.FAILURE, "The regression tests fail on the candidate: t.A.test_new"]),
            self.proof(failures=["The regression tests fail on the candidate: " + FLAKY]),
            self.proof(unverified=["The base suite was incomplete"]),
            self.proof(test="t.A.test_other"),
            {**self.proof(), "suite_flakes": None},
        ):
            self.assertEqual([], proof_findings.passed_again(proof), proof)

    def test_the_real_proofs_settle_the_finding_once_the_same_source_passes(self):
        mark = Path(tempfile.mkdtemp(prefix="clock-")) / "flaked"
        self.addCleanup(lambda: mark.unlink(missing_ok=True) or mark.parent.rmdir())
        clock = (
            "import os\nimport unittest\n\nfrom greet import main\n\n\n"
            "class TestClock(unittest.TestCase):\n"
            "    def test_tick(self):\n"
            f"        if main(['   ']) == 2 and not os.path.exists({str(mark)!r}):\n"
            f"            open({str(mark)!r}, 'w').close()\n"
            "            self.fail('timing flake')\n"
        )
        project = Project({**scenarios.BUGFIX_SEED, "test_clock.py": clock})
        self.addCleanup(project.close)
        project.write({name: references.BUGFIX_REFERENCE[name] for name in ("greet.py", "test_greet.py")})
        run_dir = project.root / ".autocode" / "runs" / "20261111-flake-run"
        run_dir.mkdir(parents=True)
        case = {
            "id": "T1",
            "criterion": "Given a blank name; when greet.py runs; then it prints usage and exits 2",
            "verification_method": "test: test_blank_name_rejected",
        }
        state = {
            "version": 3,
            "base_commit": project.base,
            "workspace": str(project.root),
            "settings": {},
            "iteration": 1,
            "stages": [],
            "history": [],
            "goal_contract": {
                "body": {"task_kind": "bugfix", "acceptance_criteria": [case], "milestones": [{"id": "M1"}]}
            },
        }
        first = regression.prove(state, project.root, run_dir)
        self.assertEqual([self.FAILURE.replace(FLAKY, CLOCK)], first["failures"], first)
        completion_result._reconcile_proof_finding(state, project.root)
        [row] = proof_findings.open_rows(state)
        revision = first["source_revision"]
        self.assertEqual([CLOCK], row["flaky_revisions"][revision]["tests"])
        second = regression.prove(state, project.root, run_dir)
        self.assertEqual(("PASS", revision), (second["verdict"], second["source_revision"]), second)
        completion_result._reconcile_proof_finding(state, project.root)
        self.assertEqual([], proof_findings.open_rows(state))
        self.assertEqual("resolved", row["status"])
        self.assertIn("Settled as a flaky test, not a regression: " + CLOCK, row["resolution_evidence"])
        self.assertIn(first["path"], row["resolution_evidence"])
        self.assertIsNone(
            completion_result._send_back_unproven(
                state, {"revision": revision}, {"stage": "review_completion"}, regression.complete
            )
        )


class ProjectSuiteHealthFlakeTests(unittest.TestCase):
    """Cross-run quarantine reuse, deterministic regression guards, invalidation, and pre-existing cache (#2942)."""

    def _setup_flaky_repo(self):
        temporary = tempfile.TemporaryDirectory(prefix="suite-health-flakes-")
        self.addCleanup(temporary.cleanup)
        tmp = Path(temporary.name).resolve()
        root = tmp / "project"
        sched_path = tmp / "schedule.json"
        log_path = tmp / "runs.log"
        sched_path.write_text(json.dumps({"candidate": [1], "base": [2]}), encoding="utf-8")

        (root / "tests").mkdir(parents=True)
        (root / "tests" / "__init__.py").write_text("")
        (root / "clocked.py").write_text("def answer(n):\n    if n > 1:\n        return n\n    return 1\n")
        (root / "tests" / "test_flaky.py").write_text(
            textwrap.dedent(
                f"""\
                import json
                import unittest
                from pathlib import Path

                SCHED = Path({str(sched_path)!r})
                LOG = Path({str(log_path)!r})


                class FlakyTests(unittest.TestCase):
                    def test_sometimes(self):
                        kind = "candidate" if "candidate" in Path(__file__).resolve().parts else "base"
                        with LOG.open("a", encoding="utf-8") as handle:
                            handle.write(kind + "\\n")
                        count = LOG.read_text(encoding="utf-8").split().count(kind)
                        fail_on = json.loads(SCHED.read_text(encoding="utf-8"))[kind]
                        self.assertNotIn(count, fail_on)

                    def test_steady(self):
                        pass
                """
            )
        )
        git(root, "init", "-q")
        git(root, "config", "maintenance.auto", "false")
        git(root, "config", "gc.auto", "0")
        git(root, "add", "-A")
        git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        return root, tmp, sched_path, log_path

    def _state(self, base: str, root: Path) -> dict:
        case = {
            "id": "C1",
            "criterion": "Given n=2; when answer(2); then returns 9",
            "verification_method": "test: test_c1_answers_nine",
        }
        return {
            "version": 3,
            "base_commit": base,
            "workspace": str(root),
            "settings": {},
            "iteration": 1,
            "stages": [],
            "history": [],
            "goal_contract": {
                "body": {"task_kind": "feature", "acceptance_criteria": [case], "milestones": [{"id": "M1"}]}
            },
        }

    def test_quarantine_reused_across_runs_catches_deterministic_break_and_invalidates_on_test_edit(self):
        root, tmp, sched_path, log_path = self._setup_flaky_repo()
        base1 = git(root, "rev-parse", "HEAD")

        # Apply honest feature change + strong test
        (root / "clocked.py").write_text("def answer(n):\n    if n > 1:\n        return 9\n    return 1\n")
        (root / "tests" / "test_clocked.py").write_text(
            "import unittest\n\nimport clocked\n\n\nclass ClockedTests(unittest.TestCase):\n"
            "    def test_c1_answers_nine(self):\n"
            "        self.assertEqual(9, clocked.answer(2))\n"
            "        self.assertEqual(1, clocked.answer(1))\n"
        )

        # Run 1: quarantines FLAKY after failing once on candidate and once on base rerun
        proof1 = regression.prove(self._state(base1, root), root, tmp / ".autocode" / "runs" / "20261115-run-1")
        self.assertEqual(verify.PASS, proof1["verdict"], proof1["failures"] + proof1["unverified"])
        self.assertIn("flake-base-2", proof1["checks"])
        self.assertNotIn(".autocode", git(root, "status", "--porcelain", "--untracked-files=all"))
        loaded = project_suite_health.load(root)
        self.assertEqual([FLAKY], [r["test"] for r in loaded["document"]["quarantine"]])

        # Commit an unrelated change to clocked.py -> new base commit, tests/test_flaky.py untouched
        (root / "clocked.py").write_text("def answer(n):\n    if n > 1:\n        return 5\n    return 1\n")
        git(root, "add", "clocked.py")
        git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "bump clocked")
        base2 = git(root, "rev-parse", "HEAD")
        (root / "clocked.py").write_text("def answer(n):\n    if n > 1:\n        return 9\n    return 1\n")

        # Run 2: base never fails now (`[]`), candidate fails once `[1]` -> reuses project_quarantine WITHOUT base reruns!
        log_path.unlink(missing_ok=True)
        sched_path.write_text(json.dumps({"candidate": [1], "base": []}), encoding="utf-8")
        proof2 = regression.prove(self._state(base2, root), root, tmp / ".autocode" / "runs" / "20261115-run-2")
        self.assertEqual(verify.PASS, proof2["verdict"], proof2["failures"] + proof2["unverified"])
        self.assertNotIn("flake-base-2", proof2["checks"])
        self.assertNotIn("flake-base-3", proof2["checks"])
        self.assertEqual("project_quarantine", proof2["suite_flakes"]["tests"][0].get("source"))
        self.assertEqual(["base", "candidate", "candidate", "candidate"], log_path.read_text().split())

        # Run 3: deterministic break on candidate `[1, 2, 3]` -> still FAILS as regression despite quarantine
        log_path.unlink(missing_ok=True)
        sched_path.write_text(json.dumps({"candidate": [1, 2, 3], "base": []}), encoding="utf-8")
        proof3 = regression.prove(self._state(base2, root), root, tmp / ".autocode" / "runs" / "20261115-run-3")
        self.assertEqual(verify.FAIL, proof3["verdict"])
        self.assertTrue(any(FLAKY in msg for msg in proof3["failures"]), proof3["failures"])

        # Commit a touch to tests/test_flaky.py -> quarantine is invalidated on Run 4
        text = (root / "tests" / "test_flaky.py").read_text(encoding="utf-8")
        (root / "tests" / "test_flaky.py").write_text(text + "# touched\n", encoding="utf-8")
        git(root, "add", "tests/test_flaky.py")
        git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "edit test_flaky")
        base3 = git(root, "rev-parse", "HEAD")

        log_path.unlink(missing_ok=True)
        sched_path.write_text(json.dumps({"candidate": [1], "base": []}), encoding="utf-8")
        proof4 = regression.prove(self._state(base3, root), root, tmp / ".autocode" / "runs" / "20261115-run-4")
        self.assertEqual(verify.FAIL, proof4["verdict"])
        self.assertIn("flake-base-2", proof4["checks"], "invalidated quarantine must rerun base cleanly")

    def test_confirmed_pre_existing_failure_skips_redundant_base_reruns_in_second_run(self):
        root, tmp, sched_path, log_path = self._setup_flaky_repo()
        base = git(root, "rev-parse", "HEAD")
        (root / "clocked.py").write_text("def answer(n):\n    if n > 1:\n        return 9\n    return 1\n")
        (root / "tests" / "test_clocked.py").write_text(
            "import unittest\n\nimport clocked\n\n\nclass ClockedTests(unittest.TestCase):\n"
            "    def test_c1_answers_nine(self):\n"
            "        self.assertEqual(9, clocked.answer(2))\n"
            "        self.assertEqual(1, clocked.answer(1))\n"
        )
        # Fails deterministically on base and candidate -> confirmed pre_existing
        sched_path.write_text(json.dumps({"candidate": [1, 2, 3], "base": [1, 2, 3]}), encoding="utf-8")

        proof1 = regression.prove(self._state(base, root), root, tmp / ".autocode" / "runs" / "20261115-pre-1")
        self.assertEqual(verify.PASS, proof1["verdict"], proof1["failures"] + proof1["unverified"])
        self.assertIn("pre-existing-base-2", proof1["checks"])
        self.assertEqual(["base", "candidate", "base", "base"], log_path.read_text().split())

        # Run 2 on fresh run_dir with same base restores baseline AND skips pre-existing-base reruns
        log_path.unlink(missing_ok=True)
        proof2 = regression.prove(self._state(base, root), root, tmp / ".autocode" / "runs" / "20261115-pre-2")
        self.assertEqual(verify.PASS, proof2["verdict"], proof2["failures"] + proof2["unverified"])
        self.assertNotIn("pre-existing-base-2", proof2["checks"])
        self.assertNotIn("pre-existing-base-3", proof2["checks"])
        self.assertEqual(["base", "candidate"], log_path.read_text().split())
        row = proof2["suite_flakes"]["pre_existing"]["tests"][0]
        self.assertEqual("project_baseline", row.get("source"))


if __name__ == "__main__":
    unittest.main()
