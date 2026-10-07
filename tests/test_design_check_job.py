"""Implementing an approved design: the Architect checks it against the repository first, and the
run either stops with the conflicts written down or plans with the design as a constraint."""
import json
import tempfile
import unittest
from pathlib import Path

import autocode_design_check_job as check_job
import autocode_jobs as jobs
import autocode_workflows as workflows
import autopilot
from units import autoplanner, autoreview

DESIGN = "docs/design/rate-limiter.md"


def workspace_with_design(root):
    (Path(root) / "docs/design").mkdir(parents=True)
    (Path(root) / DESIGN).write_text("# Rate limiter\nOne bucket per key; only the injected clock tells time.\n")
    (Path(root) / "README.md").write_text("try_acquire returns bool for all of 1.x.\n")
    return root


def state_for(workspace, task=f"Implement the approved design in {DESIGN} exactly as written."):
    return {"version": 3, "task": task, "workspace": workspace, "status": "RUNNING", "stages": [],
            "workflow": {"kind": "build", "then": "requirements_gather", "design_document": DESIGN},
            "settings": {"joint_planning": True, "roles": {
                "requirements": {"model": "r"}, "glm": {"model": "g"}, "plan_reviewer": {"model": "p", "engine": "codex"},
                "astra": {"model": "a", "engine": "codex"}, "terra": {"model": "t"}, "sol": {"model": "s"}}}}


def report(conflicts=(), constraints=("Bucket lives in ratelimit/bucket.py",), **overrides):
    value = {"design_document": DESIGN, "summary": "A token bucket per key.",
             "constraints": list(constraints), "conflicts": list(conflicts)}
    value.update(overrides)
    return value


CONFLICT = {"design_says": "try_acquire raises when empty", "conflicts_with": "README freezes the bool return for 1.x",
            "files": ["README.md"], "options": ["Ship it in 2.0", "Keep bool and add a raising variant"],
            "example": "Given a 1.x caller checking the bool; when the bucket is empty; then it gets an exception",
            "probe": ""}


class RecognitionTests(unittest.TestCase):
    def recognize(self, workspace, value):
        state = state_for(workspace)
        workflows.begin(state, "requirements_gather")
        workflows.apply(state, {"reason": "", "signals": [], **value}, {})
        return state

    def test_a_build_naming_an_existing_design_starts_with_the_check(self):
        with tempfile.TemporaryDirectory() as root:
            state = self.recognize(workspace_with_design(root), {"workflow": "build", "design_document": DESIGN})
        self.assertEqual(check_job.STAGE, state["next_stage"])
        self.assertEqual(DESIGN, state["workflow"]["design_document"])
        self.assertEqual("requirements_gather", state["workflow"]["then"])
        self.assertEqual("autoreview", autopilot.unit_for(check_job.STAGE))
        self.assertIn(check_job.STAGE, jobs.STAGES)

    def test_a_design_path_is_ignored_unless_it_is_a_build_of_an_existing_file_in_the_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            workspace_with_design(root)
            for value, first in (({"workflow": "build", "design_document": ""}, "requirements_gather"),
                                 ({"workflow": "build", "design_document": "docs/design/missing.md"}, "requirements_gather"),
                                 ({"workflow": "build", "design_document": "../outside.md"}, "requirements_gather"),
                                 ({"workflow": "build", "design_document": root + "/" + DESIGN}, "requirements_gather"),
                                 ({"workflow": "design", "design_document": DESIGN}, workflows.DESIGN_STAGE)):
                with self.subTest(value=value):
                    state = self.recognize(root, value)
                    self.assertEqual(first, state["next_stage"])
                    self.assertNotIn("design_document", state["workflow"])

    def test_older_recognizer_reports_without_the_field_still_apply(self):
        with tempfile.TemporaryDirectory() as root:
            state = self.recognize(root, {"workflow": "build"})
        self.assertEqual("requirements_gather", state["next_stage"])


class PrepareTests(unittest.TestCase):
    def test_the_architect_checks_the_named_design_on_its_own_route(self):
        with tempfile.TemporaryDirectory() as root:
            state = state_for(workspace_with_design(root))
            request = autoreview.prepare(state, check_job.STAGE, "/run/state.json", None)
        self.assertEqual(("astra", "architect", True), (request.role, request.route_role, request.allow_write))
        self.assertEqual("p", state["settings"]["roles"]["architect"]["model"])
        self.assertEqual(check_job.SCHEMA, request.schema)
        self.assertIn(f'"design_document": "{DESIGN}"', request.prompt)


class ApplyTests(unittest.TestCase):
    def test_conflicts_are_written_beside_the_design_and_stop_the_run(self):
        with tempfile.TemporaryDirectory() as root:
            state = state_for(workspace_with_design(root))
            autoreview.apply_job(check_job.STAGE, state, report([CONFLICT], constraints=()), {"output": "o"}, root)
            blockers = json.loads((Path(root) / "docs/design/rate-limiter.blockers.json").read_text())
        self.assertEqual({"conflicts": [{**CONFLICT, "proven_by": ""}]}, blockers)
        self.assertEqual((check_job.STOP_STATUS, "PAUSED_OR_BLOCKED"), (state["status"], state["phase"]))
        self.assertIn("rate-limiter.blockers.json", state["stop_reason"])
        self.assertNotIn("design_constraint", state)
        self.assertNotIn("next_stage", state)

    def test_no_conflicts_hands_the_design_to_the_planner_as_a_constraint(self):
        with tempfile.TemporaryDirectory() as root:
            state = state_for(workspace_with_design(root))
            autoreview.apply_job(check_job.STAGE, state, report(), {}, root)
            self.assertFalse((Path(root) / "docs/design/rate-limiter.blockers.json").exists())
        self.assertEqual(("RUNNING", "PLANNING", "astra_discovery"),
                         (state["status"], state["phase"], state["next_stage"]))
        self.assertEqual({"design_document": DESIGN, "summary": "A token bucket per key.",
                          "constraints": ["Bucket lives in ratelimit/bucket.py"]}, state["design_constraint"])
        self.assertIsNone(jobs.ended_in(state))

    def test_a_build_of_the_earlier_turn_s_design_is_planned_afresh(self):
        # Live runs of discuss-then-design-then-build (#185): "Build it." revised the design turn's contract
        # ("no code is written") and had to carry or drop 27-28 design-only items, one row each.
        contract = {"task_id": "t", "revision": 4, "hash": "h4", "approval_status": "approved",
                    "body": {"required_behaviors": ["Analysis/design only: no code is written"]}}
        nit = {"id": "F-1", "status": "open", "blocking": False, "finding": "Line 119 miscounts fetches"}
        blocker = {"id": "F-2", "status": "open", "blocking": True, "finding": "Never left open by a finished turn"}
        settled = {"id": "F-3", "status": "resolved", "blocking": True, "finding": "Lock rule"}

        def checked(conflicts, turns):
            with tempfile.TemporaryDirectory() as root:
                state = {**state_for(workspace_with_design(root)), "turns": turns, "goal_contract": dict(contract),
                         "requirements_handoff": {"report": {"requirements": [{"id": "R2"}]}},
                         "planning": {"astra_calls": 2, "reports": {"astra_challenge": {}}}, "current_task": {"id": "T9"},
                         "findings_ledger": [dict(nit), dict(blocker), dict(settled)]}
                autoreview.apply_job(check_job.STAGE, state, report(conflicts, constraints=() if conflicts else
                                     ("Bucket lives in ratelimit/bucket.py",)), {"output": "o"}, root)
            return state

        follow_up = lambda: [{"say": "Build it.", "event_id": "feedback-1",
                              "previous": {"workflow": "design", "design": {"mode": "propose", "documents": [DESIGN]}}}]
        state = checked((), follow_up())
        self.assertEqual("astra_discovery", state["next_stage"])
        for key, history, kept in (("goal_contract", "contract_history", contract),
                                   ("requirements_handoff", "requirements_history", {"report": {"requirements": [{"id": "R2"}]}}),
                                   ("planning", "planning_history", {"astra_calls": 2, "reports": {"astra_challenge": {}}}),
                                   ("current_task", "task_archive", {"id": "T9"})):
            self.assertNotIn(key, state)
            self.assertEqual([kept], state[history])
        # The design turn's open nit moves with its contract, unresolved; nothing else leaves the ledger.
        self.assertEqual([blocker, settled], state["findings_ledger"])
        self.assertEqual({"archived": ["goal_contract", "requirements_handoff", "planning", "current_task"],
                          "contract": "r4:h4", "findings": [nit]}, state["turns"][-1]["fresh_plan"])
        # The check runs once per turn.
        turns = state["turns"]
        state = checked((), turns)
        self.assertEqual(contract, state["goal_contract"])
        for name, conflicts, turns in (("conflicts stop the run", [CONFLICT], follow_up()), ("a first request", (), [])):
            with self.subTest(name):
                state = checked(conflicts, turns)
                self.assertEqual(contract, state["goal_contract"])
                self.assertEqual(3, len(state["findings_ledger"]))
                self.assertNotIn("contract_history", state)

    def test_the_runner_rejects_reports_it_cannot_act_on(self):
        cases = {
            "changed the workspace": (report(), ["ratelimit/bucket.py"]),
            "checked another design": (report(design_document="docs/design/other.md"), []),
            "conflict cites no existing file": (report([dict(CONFLICT, files=["docs/nowhere.md"])]), []),
            "conflict cites no file at all": (report([dict(CONFLICT, files=[])]), []),
            "conflict offers no options": (report([dict(CONFLICT, options=[])]), []),
            "conflict names no constraint": (report([dict(CONFLICT, conflicts_with=" ")]), []),
            "clean check states no decisions": (report(constraints=()), []),
        }
        for name, (value, changed) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                state = state_for(workspace_with_design(root))
                with self.assertRaises(ValueError):
                    check_job.apply(state, value, {"changed_files": changed}, root)
                self.assertEqual([], list((Path(root) / "docs/design").glob("*.blockers.json")))

    def test_a_conflict_may_cite_a_line_in_an_existing_file(self):
        with tempfile.TemporaryDirectory() as root:
            state = state_for(workspace_with_design(root))
            check_job.apply(state, report([dict(CONFLICT, files=["README.md:1"])]), {}, root)
        self.assertEqual(check_job.STOP_STATUS, state["status"])


class PlannerTests(unittest.TestCase):
    def test_the_planner_gets_the_design_as_a_constraint_only_when_one_was_checked(self):
        with tempfile.TemporaryDirectory() as root:
            state = state_for(workspace_with_design(root))
            state["settings"]["roles"]["plan_reviewer"] = {"model": "p"}
            plain, _ = autoplanner.context(state, "astra_discovery", Path(root) / "state.json")
            state["design_constraint"] = {"design_document": DESIGN, "summary": "s", "constraints": ["c1"]}
            constrained, _ = autoplanner.context(state, "astra_discovery", Path(root) / "state.json")
        self.assertNotIn(autoplanner.APPROVED_DESIGN_RULE, plain)
        self.assertNotIn('"approved_design"', plain)
        self.assertIn(autoplanner.APPROVED_DESIGN_RULE, constrained)
        self.assertIn('"approved_design"', constrained)
        self.assertIn('"c1"', constrained)


if __name__ == "__main__":
    unittest.main()


class ProbeTests(unittest.TestCase):
    """A conflict with what the code enforces today carries a probe the runner runs; no model."""

    def apply(self, conflicts):
        import subprocess
        root = Path(tempfile.mkdtemp(prefix="design-check-probe-"))
        workspace_with_design(root)
        (root / "ratelimit").mkdir()
        (root / "ratelimit" / "bucket.py").write_text("def try_acquire(key):\n    return True\n")
        for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.test",
                                                      "commit", "-qm", "seed"]):
            subprocess.run(["git", *args], cwd=root, check=True)
        state = state_for(str(root))
        autoreview.apply_job(check_job.STAGE, state, report(conflicts, constraints=()),
                             {"output": str(root / "o.json")}, root)
        return state, root

    def test_a_conflict_whose_probe_exits_0_is_recorded_beside_the_design(self):
        probe = "python3 -c 'from ratelimit.bucket import try_acquire; assert try_acquire(\"k\") is True'"
        state, root = self.apply([{**CONFLICT, "files": ["README.md", "ratelimit/bucket.py"], "probe": probe}])
        self.assertEqual(check_job.STOP_STATUS, state["status"])
        self.assertEqual([("try_acquire raises when empty", 0)],
                         [(row["design_says"], row["exit_code"]) for row in state["design_check"]["probes"]])
        blockers = json.loads((root / "docs/design/rate-limiter.blockers.json").read_text())
        self.assertEqual(probe, blockers["conflicts"][0]["proven_by"])

    def test_a_conflict_whose_probe_fails_rejects_the_report(self):
        probe = "python3 -c 'from ratelimit.bucket import try_acquire; assert try_acquire(\"k\") is False'"
        with self.assertRaisesRegex(ValueError, "conflicts' probes did not exit 0"):
            self.apply([{**CONFLICT, "probe": probe}])

    def test_a_conflict_needs_its_example(self):
        with self.assertRaisesRegex(ValueError, "example of what would break"):
            self.apply([{**CONFLICT, "example": ""}])
