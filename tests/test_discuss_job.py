"""The discuss workflow's Analyst: answer from the repository with evidence, change nothing,
and write only the note the request asks for."""
import json
import tempfile
import unittest
from pathlib import Path

import autocode_discuss_job as discuss_job
import autocode_jobs as jobs
import autocode_run_view as run_view
import autocode_workflows as workflows
import autopilot
from units import autoresolver, common


def workspace_with(*files):
    root = Path(tempfile.mkdtemp())
    for name in files:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("x\n")
    return root


def state_for(workspace, task="Should the metadata cache stay in-process or move to a shared file cache?"):
    return {"version": 3, "task": task, "workspace": str(workspace), "status": "RUNNING", "stages": [],
            "workflow": {"kind": "discuss", "then": "requirements_gather"},
            "settings": {"joint_planning": True, "roles": {
                "requirements": {"model": "r"}, "glm": {"model": "g"}, "plan_reviewer": {"model": "p"},
                "astra": {"model": "a", "engine": "codex", "reasoning_effort": "high"},
                "terra": {"model": "t"}, "sol": {"model": "s"}}}}


def report(**overrides):
    value = {"answer": "Move to a shared cache: four workers refill separately against a 60/hour limit.",
             "evidence": [{"claim": "4 gunicorn workers share nothing", "source": "deploy/gunicorn.conf.py:3"},
                          {"claim": "upstream allows 60 requests/hour", "source": "app/metadata.py"}],
             "questions": ["How many hosts run the service?"],
             "note_path": "docs/decisions/metadata-cache.json",
             "note_content": json.dumps({"recommendation": "shared-file"})}
    value.update(overrides)
    return value


class RoutingTests(unittest.TestCase):
    def test_a_recognized_question_goes_to_the_analyst(self):
        state = state_for(workspace_with())
        workflows.begin(state, "requirements_gather")
        workflows.apply(state, {"workflow": "discuss", "reason": "", "signals": []}, {})
        self.assertEqual(discuss_job.STAGE, state["next_stage"])
        self.assertEqual("autoresolver", autopilot.unit_for(discuss_job.STAGE))
        self.assertIn(discuss_job.STAGE, jobs.STAGES)


class PrepareTests(unittest.TestCase):
    def test_analyst_gets_its_own_route_with_effort_capped(self):
        workspace = workspace_with("app/metadata.py")
        state = state_for(workspace)
        request = autoresolver.prepare(state, discuss_job.STAGE, "/run/state.json", None)
        self.assertEqual(("astra", "analyst", True), (request.role, request.route_role, request.allow_write))
        self.assertEqual(("p", "medium"), (state["settings"]["roles"]["analyst"]["model"],
                                           state["settings"]["roles"]["analyst"]["reasoning_effort"]))
        self.assertEqual("high", state["settings"]["roles"]["astra"]["reasoning_effort"])
        without = state_for(workspace)
        del without["settings"]["roles"]["plan_reviewer"]
        autoresolver.prepare(without, discuss_job.STAGE, "/run/state.json", None)
        self.assertEqual("a", without["settings"]["roles"]["analyst"]["model"])  # the Resolver's, capped
        self.assertEqual(discuss_job.SCHEMA, request.schema)
        self.assertIn("metadata cache", request.prompt)

    def test_capped_route_lowers_only_efforts_above_the_cap(self):
        for given, expected in (("max", "medium"), ("high", "medium"), ("medium", "medium"),
                                ("low", "low"), (None, "medium")):
            with self.subTest(given=given):
                self.assertEqual(expected, common.capped_route({"reasoning_effort": given})["reasoning_effort"])


class ApplyTests(unittest.TestCase):
    def apply(self, value, changed=()):
        workspace = workspace_with("deploy/gunicorn.conf.py", "app/metadata.py")
        state = state_for(workspace)
        autoresolver.apply_job(discuss_job.STAGE, state, value, {"changed_files": list(changed), "output": "o"},
                               workspace)
        return state, workspace

    def test_an_answer_with_a_requested_note_writes_it_and_completes(self):
        state, workspace = self.apply(report())
        self.assertEqual({"recommendation": "shared-file"},
                         json.loads((workspace / "docs/decisions/metadata-cache.json").read_text()))
        view = run_view.view(state)
        self.assertTrue(view["done"])
        self.assertEqual("discuss", view["workflow"])
        self.assertIs(discuss_job, jobs.ended_in(state))
        rendered = jobs.render(state, lambda _: "build")
        self.assertIn("ANSWER", rendered)
        self.assertIn("Question for you: How many hosts", rendered)

    def test_the_reply_hint_appears_only_when_the_answer_asks_something(self):
        # The run is finished and waits for nothing, so its questions are answered in the next turn.
        hint = "Reply with --follow-up TEXT to answer them in this run."
        asked, _ = self.apply(report())
        self.assertIn(hint, discuss_job.render(asked))
        settled, _ = self.apply(report(questions=[]))
        self.assertNotIn("--follow-up", discuss_job.render(settled))

    def test_an_answer_without_a_note_changes_nothing(self):
        state, workspace = self.apply(report(note_path="", note_content=""))
        self.assertFalse((workspace / "docs").exists())
        self.assertEqual("TASK_COMPLETE", state["status"])

    def test_an_answer_that_changed_the_repository_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not change the repository.*app/shared_cache.py"):
            self.apply(report(), changed=["app/shared_cache.py", "docs/decisions/metadata-cache.json"])

    def test_evidence_must_cite_files_that_exist(self):
        with self.assertRaisesRegex(ValueError, "not found.*app/missing.py"):
            self.apply(report(evidence=[{"claim": "c", "source": "app/missing.py:4"}]))
        with self.assertRaisesRegex(ValueError, "needs evidence"):
            self.apply(report(evidence=[]))

    def test_the_note_must_be_a_json_md_or_txt_file_under_docs(self):
        for path in ("app/note.json", "docs/note.py", "docs/../../note.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "note_path"):
                self.apply(report(note_path=path))
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            self.apply(report(note_content="{not json"))
        with self.assertRaisesRegex(ValueError, "without a note_path"):
            self.apply(report(note_path=""))


if __name__ == "__main__":
    unittest.main()


class ProbeTests(unittest.TestCase):
    """A claim may be shown by running code: the runner runs its probe in a scratch copy, no model."""

    def apply(self, evidence):
        import subprocess
        root = Path(tempfile.mkdtemp(prefix="discuss-probe-"))
        (root / "app").mkdir()
        (root / "app" / "metadata.py").write_text("TTL = 3600\nLIMIT_PER_HOUR = 60\n")
        for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.test",
                                                      "commit", "-qm", "seed"]):
            subprocess.run(["git", *args], cwd=root, check=True)
        state = state_for(root)
        autoresolver.apply_job(discuss_job.STAGE, state, report(evidence=evidence, note_path="", note_content=""),
                               {"changed_files": [], "output": str(root / "o.json")}, root)
        return state, root

    def claim(self, probe, example="Given app/metadata.py; when LIMIT_PER_HOUR is read; then it is 60"):
        return {"claim": "upstream allows 60 requests/hour", "source": "app/metadata.py", "example": example,
                "probe": probe}

    def test_a_claim_whose_probe_exits_0_is_recorded_as_shown(self):
        probe = "python3 -c 'from app.metadata import LIMIT_PER_HOUR; assert LIMIT_PER_HOUR == 60'"
        state, _ = self.apply([self.claim(probe), {"claim": "cache lives in-process", "source": "app/metadata.py",
                                                   "example": "", "probe": ""}])
        self.assertEqual([("upstream allows 60 requests/hour", 0)],
                         [(row["claim"], row["exit_code"]) for row in state["answer"]["probes"]])
        self.assertIn("shown by running: " + probe, discuss_job.render(state))

    def test_a_claim_whose_probe_fails_rejects_the_answer(self):
        probe = "python3 -c 'from app.metadata import LIMIT_PER_HOUR; assert LIMIT_PER_HOUR == 600'"
        with self.assertRaisesRegex(ValueError, "probes did not exit 0.*60 requests/hour"):
            self.apply([self.claim(probe)])

    def test_a_probe_needs_its_example_in_plain_english(self):
        with self.assertRaisesRegex(ValueError, "needs its example"):
            self.apply([self.claim("true", example=" ")])

    def test_a_probe_runs_in_a_scratch_copy_and_cannot_touch_the_workspace(self):
        state, root = self.apply([self.claim("echo changed > app/metadata.py && touch made-by-probe")])
        self.assertEqual("TTL = 3600\nLIMIT_PER_HOUR = 60\n", (root / "app" / "metadata.py").read_text())
        self.assertFalse((root / "made-by-probe").exists())
        self.assertEqual(1, len(state["answer"]["probes"]))
