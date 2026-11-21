"""Wrong implementations must fail the acceptance tests (#3977): the per-variant verdicts and the record."""

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
        wrong.route(state)
        self.assertEqual(state["next_stage"], wrong.STAGE)
        wrong.apply(state, {"variants": []}, {"changed_files": []}, "/nowhere")
        self.assertEqual(state["next_stage"], "tester")
        wrong.route(state)
        self.assertEqual(state["next_stage"], "tester")

    def test_nothing_routes_when_off_not_spec_first_or_the_proof_did_not_pass(self):
        for state in (
            run_state(settings={"spec_first": True}),
            run_state(settings={"wrong_implementations": True}),
            run_state(regression_proof={"verdict": "FAIL", "source_revision": "rev1"}),
            run_state(next_stage="builder"),
        ):
            wrong.route(state)
            self.assertNotEqual(state["next_stage"], wrong.STAGE)

    def test_a_batch_with_no_test_case_is_recorded_as_skipped_without_a_model_call(self):
        state = run_state(acceptance_tests={"receipts": {}})
        wrong.route(state)
        self.assertEqual(state["next_stage"], "tester")
        self.assertTrue(state["acceptance_tests"]["wrong_implementations"]["skipped_reason"])

    def test_apply_records_survivors_from_the_runners_own_runs(self):
        state = run_state()
        wrong.route(state)
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
        wrong.route(state)
        wrong.apply(state, {"variants": [variant()]}, {"changed_files": []}, "/nowhere")
        self.assertEqual(state["acceptance_tests"]["wrong_implementations"]["survivors"], [])

    def test_a_study_that_changed_the_workspace_is_rejected(self):
        state = run_state()
        wrong.route(state)
        with self.assertRaises(wrong.stray_writes.StrayWrites):
            wrong.apply(state, {"variants": []}, {"changed_files": ["src/x.py"]}, "/nowhere")

    def test_the_status_view_shows_survivors_without_patches(self):
        import autocode_acceptance_tests as acceptance

        state = run_state()
        wrong.route(state)
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


if __name__ == "__main__":
    unittest.main()
