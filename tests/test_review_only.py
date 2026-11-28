"""Review only (owner decision 2026-11-27): a run that reviews a change and changes nothing but its findings."""

import argparse
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_chat_history as chat_history
import autocode_review_only as review_only
import autocode_run_modes as run_modes
import autocode_taskrun as taskrun
import autocode_util as util
from autocode_stray_writes import StrayWrites

from scenarios.harness import catalog
from tests.test_agent_step import review_workspace

BRIEF = "Review the uncommitted change in this workspace (git diff HEAD) before I merge it."


def args(**given):
    return argparse.Namespace(**{"review_only": True, "workflow": None, "plan_only": None, "run_dir": None, **given})


class NewRunTests(unittest.TestCase):
    def test_review_only_is_the_review_job_and_refuses_what_cannot_hold(self):
        self.assertTrue(review_only.new_run_setting(args(), joint=True))
        self.assertFalse(review_only.new_run_setting(args(review_only=None), joint=True))
        self.assertEqual("review", review_only.workflow(args(), {}))
        self.assertIsNone(review_only.workflow(args(review_only=None), {}))
        self.assertEqual("build", review_only.workflow(args(workflow="build"), {}), "a typed --workflow stays exact")
        for refused, joint in (
            (args(), False),
            (args(workflow="build"), True),
            (args(plan_only=True), True),
            (args(skip_to_build=True), True),
        ):
            with self.subTest(refused=refused, joint=joint), self.assertRaises(ValueError):
                review_only.new_run_setting(refused, joint)

    def test_a_saved_run_keeps_its_choice(self):
        self.assertTrue(review_only.resume_refused({}, args()))
        self.assertTrue(review_only.resume_refused({"review_only": True}, args(review_only=False)))
        self.assertFalse(review_only.resume_refused({"review_only": True}, args()))
        self.assertFalse(review_only.resume_refused({}, args(review_only=None)))


class WriteGuardTests(unittest.TestCase):
    """A Builder attempt that changes a file outside review/ is put back and sent again, then rejected."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="review-only-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        (self.workspace / "app.py").write_text("print('original')\n")
        git = ["git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.test"]
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "base"], check=True)
        self.state = {"settings": {"review_only": True}, "goal_contract": {"hash": "plan-1"}}

    def attempt(self, number):
        before = util.snapshot(self.workspace)
        (self.workspace / "app.py").write_text(f"print('edited {number}')\n")
        (self.workspace / "review").mkdir(exist_ok=True)
        (self.workspace / "review" / "findings.json").write_text('{"verdict": "approve", "findings": []}')
        after = util.snapshot(self.workspace)
        refs = {}
        for name, snapshot in (("before", before), ("after", after)):
            path = self.root / f"{name}-{number}.json"
            path.write_text(json.dumps(snapshot))
            refs[f"{name}_ref"] = str(path)
        return {"changed_files": util.changed_paths(before, after), **refs}

    def test_the_runner_puts_other_files_back_twice_then_rejects_the_attempt(self):
        for number in (1, 2):
            self.assertTrue(review_only.check_writes(self.state, self.attempt(number), self.workspace))
            self.assertEqual("print('original')\n", (self.workspace / "app.py").read_text())
            self.assertTrue((self.workspace / "review" / "findings.json").is_file(), "the findings are kept")
            self.assertEqual("builder", self.state["next_stage"])
            self.assertIn("app.py", review_only.builder_note(self.state))
        with self.assertRaises(StrayWrites) as raised:
            review_only.check_writes(self.state, self.attempt(3), self.workspace)
        self.assertIn("app.py", str(raised.exception))
        self.assertEqual("print('original')\n", (self.workspace / "app.py").read_text())

    def test_an_uncommitted_change_under_review_is_put_back_as_the_review_found_it(self):
        (self.workspace / "app.py").write_text("print('the change under review')\n")
        (self.workspace / "new.py").write_text("added by the change\n")
        review_only.capture_start(self.state, self.workspace)
        review_only.capture_start(self.state, self.workspace)  # once: a second Builder prompt keeps the first start
        self.assertTrue(self.state["review_only"]["start"])
        record = self.attempt(1)
        (self.workspace / "new.py").unlink()
        (self.workspace / "stray.txt").write_text("written by the Builder\n")
        record["changed_files"] = [*record["changed_files"], "new.py", "stray.txt"]
        self.assertTrue(review_only.check_writes(self.state, record, self.workspace))
        self.assertEqual("print('the change under review')\n", (self.workspace / "app.py").read_text())
        self.assertEqual("added by the change\n", (self.workspace / "new.py").read_text())
        self.assertFalse((self.workspace / "stray.txt").exists())
        self.assertTrue((self.workspace / "review" / "findings.json").is_file())

    def test_an_attempt_that_keeps_to_review_passes_and_other_runs_are_untouched(self):
        record = {"changed_files": ["review/findings.json", ".autocode/scratch/x"]}
        self.assertFalse(review_only.check_writes(self.state, record, self.workspace))
        self.assertFalse(review_only.check_writes({"settings": {}}, self.attempt(1), self.workspace))
        self.assertEqual("print('edited 1')\n", (self.workspace / "app.py").read_text(), "only Review only guards")

    def test_completion_is_refused_while_the_turn_changed_a_file_outside_review(self):
        before = util.snapshot(self.workspace)
        ref = self.root / "start.json"
        ref.write_text(json.dumps(before))
        state = {**self.state, "stages": [{"before_ref": str(ref)}]}
        (self.workspace / "review").mkdir()
        (self.workspace / "review" / "findings.json").write_text("{}")
        self.assertTrue(review_only.ready(state, util.snapshot(self.workspace)))
        (self.workspace / "app.py").write_text("changed\n")
        current = util.snapshot(self.workspace)
        self.assertFalse(review_only.ready(state, current))
        self.assertIn("app.py", review_only.rejection(state, current))
        self.assertTrue(review_only.ready({"settings": {}, "stages": state["stages"]}, current))


class ResultTests(unittest.TestCase):
    def test_the_chat_shows_the_verdict_and_each_blocking_finding(self):
        reference = catalog.load("review-planted-defects").dir / "reference"
        found = review_only.summary(reference / review_only.REPORT)
        state = {"settings": {"review_only": True}, "status": "TASK_COMPLETE", "review_only": {"findings": found}}
        text = review_only.response(state)
        self.assertIn("request changes. 2 blocking, 1 advisory", text)
        self.assertIn("- F1 (regclient/client.py:26-28)", text)
        self.assertIn("- F2 (regclient/policies.py:33-39)", text)
        self.assertNotIn("S1", text, "advisory findings are counted, not listed")
        self.assertIsNone(review_only.summary(Path("/nonexistent/findings.json")))
        self.assertIsNone(review_only.response({**state, "settings": {}}))

    def test_a_review_on_todays_path_without_review_only_ends_as_a_build_does(self):
        # Before, every review since #3010 read the retired Code Reviewer's record and said "0 blocking findings".
        state = {
            "workflow": {"kind": "review"},
            "status": "TASK_COMPLETE",
            "final_decision": {"summary": "Two regressions block the merge; see review/findings.json."},
        }
        self.assertEqual("Two regressions block the merge; see review/findings.json.", chat_history.response(state))
        saved = {**state, "review": {"blocking": 2, "advisory": 1, "report_path": "r.json"}}
        self.assertIn("2 blocking findings", chat_history.response(saved), "a saved Code Reviewer record still shows")


class ReviewRun(unittest.TestCase):
    """The listed Review only mode's start_args, as a client passes them, on the planted-defects review."""

    reference = "reference"

    def setUp(self):
        self.workspace, self.env, self.flags = review_workspace(self)
        config_path = Path(self.env["SCENARIO_FAKE_CONFIG"])
        config = json.loads(config_path.read_text())
        config["brief"] = BRIEF
        if self.reference != "reference":
            config["reference"] = str(catalog.load("review-planted-defects").dir / "broken" / self.reference)
            config["paths"] = ["regclient/client.py", review_only.REPORT]
        config_path.write_text(json.dumps(config))

    def tracked(self):
        files = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=self.workspace
        ).decode()
        names = [name for name in files.split("\0") if name and not name.startswith(".autocode/")]
        return {name: (self.workspace / name).read_bytes() for name in names}

    def approved(self):
        """Start the review, confirm what Requirements understood, approve the plan; the run's next stop."""
        mode = next(mode for mode in run_modes.view()["modes"] if mode["id"] == "review")
        run = taskrun.TaskRun.start(
            self.workspace, BRIEF, options=(*self.flags, *mode["start_args"]), env=self.env, timeout=300
        )
        view = run.advance_until_input()
        if view["needs"]["kind"] == "answer":
            run.answer(view["needs"]["questions"][-1]["id"], "plan")
            view = run.advance_until_input()
        self.assertEqual(("approve_plan", "review"), (view["needs"]["kind"], view["workflow"]), view)
        run.approve_plan(view["needs"]["token"])
        return run, run.advance_until_input()


class ReviewModeCliTests(ReviewRun):
    def test_review_mode_writes_only_its_findings_and_shows_them_as_the_result(self):
        before = self.tracked()
        run, done = self.approved()
        self.assertEqual("TASK_COMPLETE", done["status"], done.get("stop_reason"))
        self.assertEqual(before, {name: self.tracked()[name] for name in before}, "no project file changed")
        self.assertEqual({review_only.REPORT}, set(self.tracked()) - set(before))
        state = json.loads((run.run_dir / "state.json").read_text())
        self.assertEqual(True, state["settings"]["review_only"])
        self.assertEqual(("review", "user"), (state["workflow"]["kind"], state["workflow"]["source"]))
        self.assertEqual("request_changes", state["review_only"]["findings"]["verdict"])
        for slug in ("requirements-gather", "builder", "validator"):
            prompts = sorted(run.run_dir.glob(f"iterations/*/{slug}-*.prompt.md"))
            self.assertTrue(prompts, slug)
            self.assertIn("REVIEW ONLY RUN", prompts[0].read_text(), f"{slug} reads the Review only rule")
        shown = [row["text"] for row in done["chat"]["messages"] if row.get("role") == "assistant"]
        self.assertIn("request changes. 2 blocking", shown[-1])


class ReviewModeStrayWriteCliTests(ReviewRun):
    """The planted-defects review whose scripted Builder also edits the code under review (its edits-code variant)."""

    reference = "edits-code"

    def test_a_builder_that_edits_the_code_under_review_is_put_back_and_never_completes(self):
        before = self.tracked()
        run, stopped = self.approved()
        self.assertNotEqual("TASK_COMPLETE", stopped["status"])
        self.assertEqual(before, {name: self.tracked()[name] for name in before}, "the code under review is put back")
        state = json.loads((run.run_dir / "state.json").read_text())
        edits = state["review_only"]["builder_edits"]
        self.assertEqual([["regclient/client.py"]] * review_only.DIRECT_RETRIES, [row["paths"] for row in edits])
        self.assertIn("the runner put them back", (run.run_dir / "iterations/001/builder-02.prompt.md").read_text())
        # The third attempt is rejected and archived; the Investigator takes it, never the person.
        self.assertIn("A Review only run changes nothing but review/findings.json", stopped["stop_reason"])
        self.assertEqual(1, len(list(run.run_dir.glob("iterations/*/archived-builder-*"))))
        self.assertEqual(1, len(list(run.run_dir.glob("iterations/*/stuck-investigation-*.jsonl"))))


if __name__ == "__main__":
    unittest.main()
