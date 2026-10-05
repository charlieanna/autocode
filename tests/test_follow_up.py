"""A follow-up to a finished run: the next turn of the same conversation (issue #51).

The whole conversation (review, then "Fix them.") runs end to end in the
review-then-fix scenario; these are the pure rules behind it, and the CLI's
answer to a finished run (docs/cli.md, "Waiting or finished").
"""
import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_follow_up as follow_up
import autocode_contract_revision as revision
import autocode_workflows as workflows
from autocode_taskrun import AUTOCODE, TaskRun, TaskRunError
from units import autoplanner

FINDINGS = [
    {"id": "F1", "severity": "blocking", "file": "regclient/client.py", "lines": [26, 28],
     "summary": "Timeout resends without asking the registry", "evidence": "README Retries", "proven_by": []},
    {"id": "F2", "severity": "blocking", "file": "regclient/policies.py", "lines": [33, 39],
     "summary": ".de is retried", "evidence": "README .de", "proven_by": []},
    {"id": "S1", "severity": "advisory", "file": "regclient/policies.py", "lines": [18, 25],
     "summary": "_errors is clumsy", "evidence": "reading"},
]


def finished_review(workspace: Path) -> dict:
    (workspace / "review").mkdir()
    (workspace / "review" / "findings.json").write_text(json.dumps({"verdict": "request_changes",
                                                                    "findings": FINDINGS}))
    return {"status": "TASK_COMPLETE", "phase": "COMPLETE", "next_stage": None, "completed_at": "t0",
            "task": "Review pr-184.patch before I merge it.", "settings": {}, "workspace": str(workspace),
            "workflow": {"kind": "review", "reason": "a patch", "signals": [], "source": "model",
                         "then": "requirements_gather"},
            "review": {"report_path": "review/findings.json", "verdict": "request_changes",
                       "change_under_review": "pr-184.patch", "change_patch": "pr-184.patch"}}


def finished_design(workspace: Path, stages: list[dict], *, turns=None) -> dict:
    """A run whose design turn asked for a new design: the Builder wrote it in the build pipeline."""
    return {"status": "TASK_COMPLETE", "phase": "COMPLETE", "next_stage": None, "completed_at": "t0",
            "task": "Shared it is; design it.", "settings": {}, "workspace": str(workspace), "stages": stages,
            "workflow": {"kind": "design", "reason": "a design", "signals": [], "source": "model",
                         "then": "requirements_gather"},
            "design_review": {"mode": "propose", "output": "o"}, **({"turns": turns} if turns else {})}


def stage(name: str, *changed: str, **extra) -> dict:
    return {"stage": name, "changed_files": list(changed), **extra}


class FollowUpTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.state = finished_review(self.workspace)

    def test_a_follow_up_to_a_review_reopens_the_run_to_recognize_the_new_message(self):
        follow_up.accept(self.state, "  Fix them.  ", self.workspace, "t1")
        self.assertEqual(("RUNNING", workflows.STAGE, None), (self.state["status"], self.state["next_stage"],
                                                              workflows.kind(self.state)))
        self.assertNotIn("completed_at", self.state)
        self.assertTrue(self.state["task"].startswith("Fix them.\n"))
        self.assertIn("Review pr-184.patch before I merge it.", self.state["task"])
        turn = follow_up.current(self.state)
        self.assertEqual(("Fix them.", "review", "Review pr-184.patch before I merge it."),
                         (turn["say"], turn["previous"]["workflow"], turn["previous"]["task"]))
        self.assertEqual((["F1", "F2"], ["S1"]), ([f["id"] for f in turn["previous"]["review"]["blocking"]],
                                                  [f["id"] for f in turn["previous"]["review"]["advisory"]]))
        # The findings stand in for requirements: a build recognized next goes straight to the Planner.
        self.assertEqual(workflows.planner_stage(self.state), self.state["workflow"]["then"])

    def test_the_recognizer_reads_the_new_message_with_the_earlier_turn_as_context(self):
        follow_up.accept(self.state, "Fix them.", self.workspace, "t1")
        packet = workflows.packet(self.state)
        self.assertEqual("Fix them.", packet["task"])
        self.assertEqual({"message": "Fix them.", "previous_workflow": "review",
                          "previous_request": "Review pr-184.patch before I merge it.",
                          "previous_review": {"verdict": "request_changes", "blocking": 2, "advisory": 1}},
                         packet["follow_up"])
        workflows.apply(self.state, {"workflow": "build", "reason": "act on the findings", "signals": []},
                        {"output": "r.json"})
        self.assertEqual(workflows.planner_stage(self.state), self.state["next_stage"])
        self.assertNotIn("follow_up", workflows.packet(self.state))

    def test_the_planner_gets_the_findings_only_when_the_next_job_builds_or_fixes(self):
        self.assertIsNone(follow_up.review_findings(self.state))
        follow_up.accept(self.state, "Fix them.", self.workspace, "t1")
        self.assertIsNone(follow_up.review_findings(self.state))  # not recognized yet
        for kind, wanted in (("build", True), ("bugfix", True), ("discuss", False), ("review", False)):
            state = copy.deepcopy(self.state)
            workflows.apply(state, {"workflow": kind, "reason": "", "signals": []}, {})
            findings = follow_up.review_findings(state)
            with self.subTest(kind=kind):
                self.assertEqual(wanted, findings is not None)
                if findings:
                    self.assertEqual(("review/findings.json", "pr-184.patch", ["F1", "F2"]),
                                     (findings["report_path"], findings["change_patch"],
                                      [f["id"] for f in findings["blocking"]]))

    def test_the_planner_plans_from_the_findings(self):
        subprocess.run(["git", "init", "-q"], cwd=self.workspace, check=True)
        self.state.update(version=3, stages=[], task_id="t", iteration=1, answers={}, user_events=[], history=[],
                          sessions={}, settings={"joint_planning": True, "roles": {
                              role: {"model": role[0]} for role in ("requirements", "glm", "plan_reviewer", "terra", "sol")}
                              | {"astra": {"model": "a", "engine": "codex"}}})
        follow_up.accept(self.state, "Fix them.", self.workspace, "t1")
        workflows.apply(self.state, {"workflow": "build", "reason": "", "signals": []}, {})
        prompt, _ = autoplanner.context(self.state, "astra_discovery", self.workspace / "state.json")
        self.assertIn(autoplanner.REVIEW_FINDINGS_RULE, prompt)
        self.assertIn('"review_findings"', prompt)
        self.assertIn("Timeout resends without asking the registry", prompt)
        workflows.apply(self.state, {"workflow": "discuss", "reason": "", "signals": []}, {})
        other, _ = autoplanner.context(self.state, "astra_discovery", self.workspace / "state.json")
        self.assertNotIn(autoplanner.REVIEW_FINDINGS_RULE, other)

    def test_only_a_finished_run_takes_a_follow_up_and_the_message_must_say_something(self):
        for status in ("RUNNING", "WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL", "PAUSED_INVALID_OUTPUT",
                       "PAUSED_DESIGN_CONFLICT", "BLOCKED_HUMAN"):
            state = {**copy.deepcopy(self.state), "status": status}
            before = copy.deepcopy(state)
            # The refusal names what such a run takes instead, an answer first.
            with self.subTest(status=status), self.assertRaisesRegex(
                    ValueError, "continues a finished run.*--answer.*--feedback.*--resume-paused"):
                follow_up.accept(state, "Fix them.", self.workspace, "t1")
            self.assertEqual(before, state)
        with self.assertRaisesRegex(ValueError, "nonempty"):
            follow_up.accept(self.state, "   ", self.workspace, "t1")

    def test_a_follow_up_to_another_job_keeps_the_run_s_first_stage(self):
        state = {**copy.deepcopy(self.state), "workflow": {"kind": "discuss", "then": "requirements_gather"}}
        state.pop("review")
        follow_up.accept(state, "Now build it.", self.workspace, "t1")
        self.assertEqual("requirements_gather", state["workflow"]["then"])
        self.assertNotIn("review", follow_up.current(state)["previous"])

    def test_a_later_follow_up_goes_back_to_the_run_s_first_stage(self):
        follow_up.accept(self.state, "Fix them.", self.workspace, "t1")
        workflows.apply(self.state, {"workflow": "build", "reason": "", "signals": []}, {})
        self.state.update(status="TASK_COMPLETE", completed_at="t2")
        follow_up.accept(self.state, "Now add a retry for .org.", self.workspace, "t3")
        self.assertEqual("requirements_gather", self.state["workflow"]["then"])
        self.assertEqual(2, len(self.state["turns"]))


    def test_follow_up_supplies_saved_user_basis_without_approving_or_relaxing_other_changes(self):
        body = {"required_behaviors": ["Preserve exact case."], "scope_exclusions": [], "constraints": [],
                "important_failure_cases": [], "permission_boundaries": ["No network."],
                "acceptance_criteria": [{"id": "C1", "criterion": "Original behavior passes.",
                    "verification_method": "guard: test_original", "human_review": False}]}
        approval = {"kind": "goal_approval", "token": "r3:old"}
        self.state.update(goal_contract={"body": body, "revision": 3, "hash": "old",
            "approval_status": "approved", "approval_event": approval}, user_events=[approval],
            automatic_capacity_recoveries=[{"attempt": "kept"}], settings={"max_iterations": 6})
        original = copy.deepcopy(self.state)
        message = "Add ignore_case=True for casefolding; preserve exact case by default."
        follow_up.accept(self.state, message, self.workspace, "t1")
        self.assertEqual(1, len(self.state.get("brief_feedback", [])), "Follow-up lacks saved provenance")
        [event] = self.state.get("brief_feedback", [])
        self.assertEqual((message, "r3:old", "t1"), (event["text"], event["contract_token"], event["at"]))
        self.assertTrue(revision.saved_user_basis(self.state, "user_feedback", event["id"]))
        self.assertEqual([approval, event], self.state["user_events"])
        for key in ("goal_contract", "automatic_capacity_recoveries", "settings"):
            self.assertEqual(original[key], self.state[key])
        after = copy.deepcopy(body)
        after["required_behaviors"] = ["Preserve exact case by default; casefold when ignore_case=True."]
        change = {"item": body["required_behaviors"][0], "change": "reworded", "basis": "user_feedback",
                  "answer_id": event["id"], "replacement": after["required_behaviors"][0]}
        self.assertEqual([change], revision.revision_guard(self.state, after, [change], "astra_discovery"))
        for fault in ("forged_id", "missing_event", "undeclared_permission", "undeclared_criterion"):
            state, proposed, declared = copy.deepcopy((self.state, after, [change]))
            if fault == "forged_id":
                declared[0]["answer_id"] = "feedback-invented"
            elif fault == "missing_event":
                state["user_events"] = [approval]
            elif fault == "undeclared_permission":
                proposed["permission_boundaries"] = ["Allow network."]
            else:
                proposed["acceptance_criteria"] = []
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                revision.revision_guard(state, proposed, declared, "astra_discovery")

    def test_a_turn_records_where_its_stages_start_and_its_receipt(self):
        self.state["stages"] = [stage("recognize_workflow"), stage("review_change")]
        follow_up.accept(self.state, "Fix them.", self.workspace, "t1")
        turn = follow_up.current(self.state)
        self.assertEqual((2, self.state["brief_feedback"][-1]["id"]), (turn["stage_index"], turn["event_id"]))
        self.assertTrue(turn["event_id"].startswith("feedback-"))

    def test_a_turn_s_changes_are_its_own_stages_measured_files_without_rejected_attempts(self):
        earlier = [stage("terra", "app/old.py"), stage("sol")]
        design = [stage("recognize_workflow"), stage("review_design"),
                  stage("terra", "docs/design/cache.md", "app/stray.py", rejected=True),
                  stage("terra", "docs/design/cache.md", "docs/design/README.md", ".autocode/x.json"),
                  stage("sol", "docs/design/cache.md"), {"stage": "orchestrator", "runner_owned": True}]
        turn = {"at": "t0", "say": "Design it.", "stage_index": len(earlier), "previous": {}}
        state = finished_design(self.workspace, earlier + design, turns=[turn])
        self.assertEqual(["docs/design/README.md", "docs/design/cache.md"], follow_up.turn_changes(state))
        # The first request's stages start at 0; a turn saved before stage_index existed has no known start.
        self.assertEqual(["app/old.py", "docs/design/README.md", "docs/design/cache.md"],
                         follow_up.turn_changes(finished_design(self.workspace, earlier + design)))
        state["turns"][0].pop("stage_index")
        self.assertEqual([], follow_up.turn_changes(state))

    def test_the_next_turn_knows_what_the_previous_job_wrote_and_its_task_names_it(self):
        discussion = {**finished_design(self.workspace, [stage("recognize_workflow"), stage("answer_question")]),
                      "workflow": {"kind": "discuss", "then": "requirements_gather"},
                      "answer": {"answer": "shared", "note_path": "docs/decisions/cache.json", "questions": []},
                      "task": "In-process or shared?"}
        discussion.pop("design_review")
        (self.workspace / "docs" / "design").mkdir(parents=True)
        (self.workspace / "docs" / "design" / "cache.md").write_text("# Cache\n")
        built = [stage("terra", "docs/design/cache.md")]
        many = [stage("terra", *[f"app/m{n}.py" for n in range(10)])]
        cases = {"discussion": (discussion, ["docs/decisions/cache.json"],
                                "(discuss; it wrote docs/decisions/cache.json): In-process or shared?"),
                 "review": (self.state, ["review/findings.json"], "(review; it wrote review/findings.json): Review"),
                 "design": (finished_design(self.workspace, built), ["docs/design/cache.md"],
                            "(design; it wrote docs/design/cache.md): Shared it is"),
                 # At most eight paths are named.
                 "build": ({**finished_design(self.workspace, many), "workflow": {"kind": "build"}},
                           [f"app/m{n}.py" for n in range(10)],
                           "(build; it wrote app/m0.py, app/m1.py, app/m2.py, app/m3.py, app/m4.py, app/m5.py, "
                           "app/m6.py, app/m7.py and 2 more): Shared it is")}
        for name, (state, wrote, said) in cases.items():
            with self.subTest(name):
                state = copy.deepcopy(state)
                follow_up.accept(state, "Go on.", self.workspace, "t1")
                self.assertEqual(wrote, follow_up.current(state)["previous"]["wrote"])
                self.assertIn(said, state["task"])

    def test_a_design_turn_carries_the_documents_it_wrote_and_a_review_its_report(self):
        design_dir = self.workspace / "docs" / "design"
        design_dir.mkdir(parents=True)
        for name in ("cache.md", "README.md", "notes.txt"):
            (design_dir / name).write_text("x\n")
        built = [stage("review_design"), stage("terra", "docs/design/README.md", "docs/design/cache.md",
                                                   "docs/design/notes.txt", "docs/design/gone.md")]
        state = finished_design(self.workspace, built)
        follow_up.accept(state, "Build it.", self.workspace, "t1")
        self.assertEqual({"mode": "propose", "documents": ["docs/design/cache.md"]},
                         follow_up.current(state)["previous"]["design"])
        (self.workspace / "review").mkdir(exist_ok=True)
        (self.workspace / "review" / "design-review.json").write_text(json.dumps({
            "design_under_review": "docs/design/cache.md", "verdict": "request_changes",
            "concerns": [{"id": "F1", "area": "ordering", "severity": "blocking", "summary": "order lost",
                          "evidence": "e", "example": "x", "probe": ""},
                         {"id": "F2", "area": "ops", "severity": "advisory", "summary": "no owner", "evidence": "e"}],
            "questions": [{"id": "Q1", "question": "Per domain or per registry?", "options": ["a", "b"]}]}))
        review = {**finished_design(self.workspace, [stage("review_design")]),
                  "design_review": {"mode": "review", "report_path": "review/design-review.json", "blocking": 1,
                                    "advisory": 1, "questions": 1, "verdict": "request_changes"}}
        before = copy.deepcopy(review)
        follow_up.accept(review, "Ordering is per-domain.", self.workspace, "t1")
        self.assertEqual({"mode": "review", "report_path": "review/design-review.json",
                          "design_under_review": "docs/design/cache.md", "verdict": "request_changes",
                          "blocking": [{"id": "F1", "area": "ordering", "summary": "order lost"}],
                          "advisory": [{"id": "F2", "area": "ops", "summary": "no owner"}],
                          "questions": [{"id": "Q1", "question": "Per domain or per registry?"}]},
                         follow_up.current(review)["previous"]["design"])
        for unreadable in ("{not json", "[]"):
            (self.workspace / "review" / "design-review.json").write_text(unreadable)
            unread = copy.deepcopy(before)
            with self.subTest(unreadable), self.assertRaisesRegex(
                    ValueError, "design review's report review/design-review.json cannot be read"):
                follow_up.accept(unread, "Ordering is per-domain.", self.workspace, "t1")
            self.assertEqual(before, unread)
        # Another job's run carries no design.
        self.assertIsNone(follow_up.carried_design(self.state, self.workspace))

    def test_an_unreadable_review_report_is_refused(self):
        (self.workspace / "review" / "findings.json").write_text("{not json")
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "cannot be read"):
            follow_up.accept(self.state, "Fix them.", self.workspace, "t1")
        self.assertEqual(before, self.state)


# A codex stand-in whose only job is the Architect's: a design review with one question for the user.
ARCHITECT = r'''#!/usr/bin/env python3
import json, subprocess, sys, uuid
from pathlib import Path
if sys.argv[1:3] == ["sandbox", "--help"]:
    print("--config -- macos linux"); raise SystemExit(0)
if sys.argv[1:2] == ["sandbox"]:
    raise SystemExit(subprocess.call(sys.argv[sys.argv.index("--") + 1:]))
if sys.argv[1:] in (["login", "status"], ["--version"]):
    print("codex fixture"); raise SystemExit(0)
sys.stdin.read()
print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}), flush=True)
Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps({
    "mode": "review", "design_under_review": "docs/design/cache.md", "verdict": "approve",
    "summary": "Sound, with one open choice", "satisfied": ["Workers share one cache"],
    "concerns": [], "questions": [{"id": "Q1", "question": "Per worker or per host?", "options": ["worker", "host"]}]}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}), flush=True)
'''


class FinishedRunCliTests(unittest.TestCase):
    """The rule through the public CLI (docs/cli.md, "Waiting or finished"): a finished design review
    waits for no answer, so --answer is refused with the way to reply, and --follow-up is the reply."""

    def test_a_finished_review_s_questions_are_answered_with_a_follow_up_not_an_answer(self):
        temp = tempfile.TemporaryDirectory(prefix="follow-up-cli-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        workspace, bindir = root / "project", root / "bin"
        (workspace / "docs" / "design").mkdir(parents=True)
        bindir.mkdir()
        (workspace / ".gitignore").write_text(".autocode/\n")
        (workspace / "docs" / "design" / "cache.md").write_text("# Cache\nOne cache directory per host.\n")
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        subprocess.run(["git", "-C", str(workspace), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@example.test",
                        "commit", "-qm", "seed"], check=True)
        (bindir / "codex").write_text(ARCHITECT)
        (bindir / "codex").chmod(0o755)
        env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "AUTOCODE_HOME": str(root / "registry"),
               "PYTHONDONTWRITEBYTECODE": "1"}
        run = TaskRun.start(workspace, "Review the design in docs/design/cache.md.", env=env, timeout=120,
                            options=("--engine", "codex", "--astra-model", "gpt-6-astra", "--sol-model", "gpt-5.6-sol"),
                            start_options=("--workflow", "design"))
        view = run.status()
        self.assertEqual((True, "design", 1), (view["done"], view["workflow"], view["turn"]), view)
        self.assertIn("Reply with --follow-up TEXT", run.last_advance.stdout)
        saved = (run.run_dir / "state.json").read_bytes()
        answered = subprocess.run([*AUTOCODE, "--answer", "Q1=host", "--workspace", str(workspace),
                                   "--run-dir", str(run.run_dir)], capture_output=True, text=True, timeout=120,
                                  env={**os.environ, **env})
        self.assertEqual(2, answered.returncode, answered.stdout + answered.stderr)
        self.assertIn("finished and waits for no answer; reply to the questions in its report with --follow-up",
                      answered.stderr)
        self.assertEqual(saved, (run.run_dir / "state.json").read_bytes())
        with self.assertRaisesRegex(TaskRunError, "not waiting for an answer to Q1"):
            run.answer("Q1", "host")
        view = run.follow_up("Per host.")
        self.assertEqual((2, "RUNNING", workflows.STAGE), (view["turn"], view["status"], view["next_stage"]), view)


if __name__ == "__main__":
    unittest.main()
