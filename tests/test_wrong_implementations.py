"""Wrong implementations must fail the acceptance tests (#3977): the per-variant verdicts and the record."""

import sys
import unittest

import autocode_wrong_implementations as wrong
from autocode_wrong_implementations import Runners, new_run_setting, save, skip, study, survivors_note


def variant(case_id="C1", patch="--- a/src/x.py\n+++ b/src/x.py\n@@\n-a\n+b\n", demo=("check",), **extra):
    return {
        "case_id": case_id,
        "mistake": extra.pop("mistake", "returns the documented example only"),
        "category": extra.pop("category", "hardcoded"),
        "patch": patch,
        "demo": list(demo),
        **extra,
    }


def runners(*, demo_clean=0, demo_patched=1, applies=True, failed=(), error=None, calls=None):
    """Scripted runners: a patch of None is the clean candidate."""
    log = calls if calls is not None else []

    def demo(patch, argv):
        log.append(("demo", patch is not None))
        if patch is None:
            return {"exit_code": demo_clean, "error": ""}
        if not applies:
            return {"exit_code": None, "error": "patch does not apply"}
        return {"exit_code": demo_patched, "error": ""}

    def tests(patch, files):
        log.append(("tests", tuple(files)))
        return {"failed": list(failed), "error": error or ""}

    return Runners(demo=demo, tests=tests)


FILES = {"C1": ["tests/test_one.py"]}


class VerdictTests(unittest.TestCase):
    def outcome(self, row, **kw):
        return study([row], FILES, runners(**kw))["variants"][0]["outcome"]

    def test_a_variant_the_tests_still_pass_survives(self):
        self.assertEqual(self.outcome(variant()), "survived")

    def test_a_variant_a_test_fails_on_is_killed_and_names_the_test(self):
        result = study([variant()], FILES, runners(failed=["tests/test_one.py::test_a"]))
        self.assertEqual(result["variants"][0]["outcome"], "killed")
        self.assertEqual(result["variants"][0]["killed_by"], ["tests/test_one.py::test_a"])
        self.assertEqual(result["survivors"], [])

    def test_the_demo_must_pass_on_the_candidate(self):
        self.assertEqual(self.outcome(variant(), demo_clean=1), "demo_unproven")

    def test_the_demo_must_fail_with_the_patch_so_a_survivor_is_really_wrong(self):
        self.assertEqual(self.outcome(variant(), demo_patched=0), "not_shown_wrong")

    def test_a_demo_that_cannot_run_is_not_shown_wrong(self):
        self.assertEqual(self.outcome(variant(), demo_patched=None), "not_shown_wrong")

    def test_a_patch_that_edits_a_test_file_never_reaches_a_runner(self):
        calls = []
        patch = "diff --git a/tests/x.py b/tests/x.py\n--- a/tests/x.py\n+++ b/tests/x.py\n"
        study([variant(patch=patch)], FILES, runners(calls=calls))
        self.assertEqual(calls, [])

    def test_a_patch_that_does_not_apply_is_invalid(self):
        self.assertEqual(self.outcome(variant(), applies=False), "invalid")

    def test_a_patch_that_edits_a_test_file_is_invalid_and_never_run(self):
        calls = []
        patch = "--- a/tests/test_one.py\n+++ b/tests/test_one.py\n@@\n-a\n+b\n"
        result = study([variant(patch=patch)], FILES, runners(calls=calls))
        self.assertEqual(result["variants"][0]["outcome"], "invalid")
        self.assertEqual(calls, [])

    def test_a_variant_naming_no_due_case_is_invalid(self):
        self.assertEqual(self.outcome(variant(case_id="G9")), "invalid")

    def test_a_test_runner_error_is_not_a_survivor(self):
        self.assertEqual(self.outcome(variant(), error="runner did not start"), "not_run")

    def test_the_budget_stops_the_study_without_running_the_rest(self):
        calls = []
        result = study([variant(), variant()], FILES, runners(calls=calls), budget=-1)
        self.assertEqual([r["outcome"] for r in result["variants"]], ["not_run", "not_run"])
        self.assertEqual(calls, [])

    def test_only_the_tests_of_the_variants_own_case_are_run(self):
        calls = []
        files = {"C1": ["tests/test_one.py"], "C2": ["tests/test_two.py"]}
        study([variant(case_id="C2")], files, runners(calls=calls))
        self.assertIn(("tests", ("tests/test_two.py",)), calls)
        self.assertNotIn(("tests", ("tests/test_one.py",)), calls)

    def test_counts_and_the_variant_cap(self):
        rows = [variant(mistake=f"m{i}") for i in range(12)]
        result = study(rows, FILES, runners())
        self.assertEqual(len(result["variants"]), 8)
        self.assertEqual(result["counts"]["survived"], 8)

    def test_the_record_identifies_a_patch_by_its_digest_not_a_path(self):
        row = study([variant()], FILES, runners())["variants"][0]
        self.assertEqual(len(row["patch_sha256"]), 64)
        self.assertNotIn("patch", row)


class RecordTests(unittest.TestCase):
    def test_the_study_is_recorded_under_the_acceptance_tests_key(self):
        state = {}
        result = study([variant()], FILES, runners())
        save(state, result, source_revision="abc", batch="b1", contract_hash="h")
        saved = state["acceptance_tests"]["wrong_implementations"]
        self.assertEqual((saved["source_revision"], saved["batch"], saved["contract_hash"]), ("abc", "b1", "h"))
        self.assertEqual(saved["counts"]["survived"], 1)

    def test_a_skip_records_its_reason_and_no_survivors(self):
        state = {}
        skip(state, "no test: cases", source_revision="abc", batch="b1", contract_hash=None)
        saved = state["acceptance_tests"]["wrong_implementations"]
        self.assertEqual(saved["skipped_reason"], "no test: cases")
        self.assertEqual(saved["survivors"], [])

    def test_survivors_reach_the_tester_as_evidence_and_killed_ones_do_not(self):
        state = {}
        result = study([variant(mistake="hard-codes the example")], FILES, runners())
        save(state, result, source_revision="abc", batch="b1", contract_hash=None)
        self.assertIn("hard-codes the example", survivors_note(state))
        killed = study([variant()], FILES, runners(failed=["t"]))
        save(state, killed, source_revision="abc", batch="b1", contract_hash=None)
        self.assertEqual(survivors_note(state), "")


class SettingTests(unittest.TestCase):
    def test_it_needs_spec_first(self):
        with self.assertRaises(ValueError):
            new_run_setting(True, spec_first=False)

    def test_it_is_off_unless_asked_for(self):
        self.assertFalse(new_run_setting(None, spec_first=True))
        self.assertTrue(new_run_setting(True, spec_first=True))


def run_state(**overrides):
    """A spec-first run about to be independently tested, with one test: case and one guard: case written."""
    state = {
        "task": "add a parser",
        "settings": {"spec_first": True, "wrong_implementations": True},
        "next_stage": "tester",
        "regression_proof": {"verdict": "PASS", "source_revision": "rev1"},
        "goal_contract": {
            "hash": "h1",
            "approval_status": "approved",
            "body": {
                "acceptance_criteria": [
                    {"id": "C1", "criterion": "parses 1.2.3", "verification_method": "test: test_parse"},
                    {"id": "C2", "criterion": "old CLI works", "verification_method": "guard: test_old"},
                ]
            },
        },
        "acceptance_tests": {
            "receipts": {"b": {"contract_hash": "h1", "case_ids": ["C1", "C2"], "files": {"tests/test_parse.py": {}}}}
        },
    }
    state.update(overrides)
    return state


class RunTests(unittest.TestCase):
    def test_only_test_cases_with_current_receipts_are_studied(self):
        self.assertEqual(wrong.case_files(run_state()), {"C1": ["tests/test_parse.py"]})
        stale = run_state(goal_contract={**run_state()["goal_contract"], "hash": "h2"})
        self.assertEqual(wrong.case_files(stale), {})

    def test_the_writer_sees_the_cases_and_no_test_file(self):
        text, _ = wrong.prompt(run_state())
        self.assertIn("parses 1.2.3", text)
        self.assertNotIn("tests/test_parse.py", text)
        self.assertNotIn("old CLI works", text)

    def test_a_passing_proof_before_the_tester_routes_to_the_study_once(self):
        state = run_state()
        wrong.route(state, "rev1")
        self.assertEqual(state["next_stage"], wrong.STAGE)
        wrong.apply(state, {"variants": []}, {"changed_files": []}, "/nowhere")
        self.assertEqual(state["next_stage"], "tester")
        wrong.route(state, "rev1")
        self.assertEqual(state["next_stage"], "tester")

    def test_a_proof_of_an_earlier_source_does_not_route(self):
        # The previous round's proof is still saved when the Tester of a new source is about to launch (#3977).
        state = run_state()
        self.assertFalse(wrong.route(state, "rev2"))
        self.assertEqual(state["next_stage"], "tester")
        self.assertNotIn("wrong_implementations", state["acceptance_tests"])

    def test_nothing_routes_when_off_not_spec_first_or_the_proof_did_not_pass(self):
        for state in (
            run_state(settings={"spec_first": True}),
            run_state(settings={"wrong_implementations": True}),
            run_state(regression_proof={"verdict": "FAIL", "source_revision": "rev1"}),
            run_state(next_stage="builder"),
        ):
            wrong.route(state, "rev1")
            self.assertNotEqual(state["next_stage"], wrong.STAGE)

    def test_a_batch_with_no_test_case_is_recorded_as_skipped_without_a_model_call(self):
        state = run_state(acceptance_tests={"receipts": {}})
        wrong.route(state, "rev1")
        self.assertEqual(state["next_stage"], "tester")
        self.assertTrue(state["acceptance_tests"]["wrong_implementations"]["skipped_reason"])

    def test_apply_records_survivors_from_the_runners_own_runs(self):
        state = run_state()
        wrong.route(state, "rev1")
        script = runners()
        wrong.apply(
            state,
            {"variants": [variant()]},
            {"changed_files": []},
            "/nowhere",
            run_demo=script.demo,
            run_tests=script.tests,
        )
        saved = state["acceptance_tests"]["wrong_implementations"]
        self.assertEqual(saved["source_revision"], "rev1")
        self.assertEqual([r["outcome"] for r in saved["survivors"]], ["survived"])

    def test_without_runners_nothing_is_claimed(self):
        state = run_state()
        wrong.route(state, "rev1")
        wrong.apply(state, {"variants": [variant()]}, {"changed_files": []}, "/nowhere")
        self.assertEqual(state["acceptance_tests"]["wrong_implementations"]["survivors"], [])

    def test_a_study_that_changed_the_workspace_is_rejected(self):
        state = run_state()
        wrong.route(state, "rev1")
        with self.assertRaises(wrong.stray_writes.StrayWrites):
            wrong.apply(state, {"variants": []}, {"changed_files": ["src/x.py"]}, "/nowhere")

    def test_the_status_view_shows_survivors_without_patches(self):
        import autocode_acceptance_tests as acceptance

        state = run_state()
        wrong.route(state, "rev1")
        script = runners()
        wrong.apply(
            state,
            {"variants": [variant()]},
            {"changed_files": []},
            "/nowhere",
            run_demo=script.demo,
            run_tests=script.tests,
        )
        shown = (acceptance.view(state) or {}).get("wrong_implementations")
        self.assertEqual(shown["counts"]["survived"], 1)
        self.assertEqual(shown["survivors"][0]["case_id"], "C1")
        self.assertNotIn("patch_sha256", shown["survivors"][0])


class TesterLaunchTests(unittest.TestCase):
    """The step before every Tester launch, in both dispatch paths (#3977).

    The runner proves the current source for the Tester in this same step, so on a run whose first build
    passes no proof exists earlier. The study was routed from the build loop before it, and was skipped on
    such a run without a record; the second live run on #3977 went from the Builder straight to the Tester."""

    def setUp(self):
        import subprocess
        import tempfile
        from pathlib import Path

        self.workspace = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        git = ["git", "-C", str(self.workspace), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test"]
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        (self.workspace / "m.py").write_text("def f(x):\n    return x + 1\n")
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "base"], check=True)

    def before_tester(self, state, proof_revision=None):
        """completion_result.before_review for the Tester, the runner's proof replaced by a recorded PASS."""
        from unittest.mock import patch

        import autocode_completion_result as completion_result
        import autocode_regression as regression
        import autocode_source_scope as source_scope

        def prove(state, stage, workspace, run_dir):
            revision = proof_revision or source_scope.snapshot(workspace, state)["revision"]
            state["regression_proof"] = {"verdict": "PASS", "source_revision": revision}

        with patch.object(regression, "before_review", side_effect=prove):
            return completion_result.before_review(state, "tester", self.workspace, self.workspace / "run")

    def test_the_first_build_s_proof_sends_the_run_to_the_study_before_the_tester(self):
        state = run_state(workspace=str(self.workspace))
        del state["regression_proof"]  # nothing proved before this step: the run's first build
        self.assertIs(self.before_tester(state), True)  # launch nothing; the loop restarts at the study
        self.assertEqual(state["next_stage"], wrong.STAGE)
        state["acceptance_tests"]["wrong_implementations"] = {
            "source_revision": state["regression_proof"]["source_revision"],
            "batch": wrong._batch(state),
        }
        state["next_stage"] = "tester"
        self.assertIs(self.before_tester(state), False)  # studied once: the Tester launches

    def test_a_proof_of_another_source_launches_the_tester(self):
        state = run_state(workspace=str(self.workspace))
        self.assertIs(self.before_tester(state, proof_revision="earlier"), False)
        self.assertEqual(state["next_stage"], "tester")


class RealRunnerTests(unittest.TestCase):
    """The unit's glue on the real scratch runner: a variant's patch text reaches ``git apply`` as a file."""

    def test_variants_are_judged_in_real_scratch_copies(self):
        import shlex
        import subprocess
        import tempfile
        from pathlib import Path

        import autocode_verify as verify
        from units import autoreview

        with tempfile.TemporaryDirectory() as tmp:
            ws, run = Path(tmp, "ws"), Path(tmp, "run")
            (ws / "tests").mkdir(parents=True)
            run.mkdir()
            (ws / "m.py").write_text("def f(x):\n    return x + 1\n\n\ndef g(x):\n    return x\n")
            (ws / "tests" / "test_m.py").write_text(
                "import unittest\nfrom m import f\n\n\nclass T(unittest.TestCase):\n"
                "    def test_f(self):\n        self.assertEqual(f(1), 2)\n"
            )
            git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
            subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
            subprocess.run([*git, "add", "-A"], cwd=ws, check=True)
            subprocess.run([*git, "commit", "-qm", "init"], cwd=ws, check=True)

            def body(line):
                return f"--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n def f(x):\n-    return x + 1\n+    {line}\n"

            demo = [sys.executable, "-c", "import sys; sys.path.insert(0, '.'); from m import f; sys.exit(f(5) != 6)"]
            runners = Runners(
                demo=lambda patch, argv: autoreview._study_run(
                    verify.scratch_run, ws, run, patch, command=shlex.join(argv)
                ),
                tests=lambda patch, files: autoreview._study_tests(
                    {"settings": {}}, verify.scratch_run, ws, run, patch, files
                ),
            )
            rows = [
                variant(mistake="off by one", patch=body("return x + 2"), demo=demo),
                variant(mistake="hard-codes the example", patch=body("return 2 if x == 1 else x"), demo=demo),
                variant(mistake="no such file", patch="--- a/zz.py\n+++ b/zz.py\n@@ -1 +1 @@\n-a\n+b\n", demo=demo),
                # As the live writer sent them (#3977): no context lines, and a header whose counts are wrong.
                # Plain ``git apply`` refuses both; ``patch -p1``, which the writer checks with, takes both.
                variant(
                    mistake="zero context",
                    patch="--- a/m.py\n+++ b/m.py\n@@ -2 +2 @@\n-    return x + 1\n+    return x + 2\n",
                    demo=demo,
                ),
                variant(
                    mistake="miscounted header",
                    patch="--- a/m.py\n+++ b/m.py\n@@ -1,5 +1,5 @@\n def f(x):\n-    return x + 1\n"
                    "+    return 2 if x == 1 else x\n",
                    demo=demo,
                ),
                variant(
                    mistake="deletes the module",
                    patch="--- a/m.py\n+++ /dev/null\n@@ -1,6 +0,0 @@\n"
                    "-def f(x):\n-    return x + 1\n-\n-\n-def g(x):\n-    return x\n",
                    demo=demo,
                ),
            ]
            result = study(rows, {"C1": ["tests/test_m.py"]}, runners)
            self.assertEqual(
                subprocess.run(["git", "status", "--porcelain"], cwd=ws, capture_output=True, text=True).stdout, ""
            )
        self.assertEqual(
            [r["outcome"] for r in result["variants"]],
            ["killed", "survived", "invalid", "killed", "survived", "invalid"],
        )
        self.assertIn("deletes 'm.py'", result["variants"][5]["reason"])


if __name__ == "__main__":
    unittest.main()
