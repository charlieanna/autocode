"""An approved revision that moves a criterion re-attributes the open findings that cite it (#447).

docs/bugs/2026-10-05-stale-finding-scope-after-milestone-reassignment.md: a Validator finding
recorded as {M2: [AC10, AC15]} went stale when an approved revision moved AC15 to M5. It blocked
every milestone and no milestone's reviewer could close it.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import autocode as runner
import autocode_findings as findings
import autocode_finding_rescope as rescope
import autocode_finding_scope as finding_scope
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_run_view as run_view
import autocode_support as support
from goal_fixtures import body as fixture_body, seed_greeting_workspace

# r1: M1 -> M2 -> M5, and AC15 is M2's. r2 moves AC15 to M5.
R1 = [("M1", ["AC1"], []), ("M2", ["AC10", "AC15"], ["M1"]), ("M5", ["AC20"], ["M2"])]
R2 = [("M1", ["AC1"], []), ("M2", ["AC10"], ["M1"]), ("M5", ["AC15", "AC20"], ["M2"])]
LOCAL = {"auth_mode": "fixture"}


def contract_body(milestones):
    value = fixture_body()
    criteria = sorted({cid for _, owned, _ in milestones for cid in owned}, key=lambda cid: int(cid[2:]))
    value["acceptance_criteria"] = [{"id": cid, "criterion": f"Behavior {cid} holds",
                                     "verification_method": f"Run the {cid} regression check", "human_review": False}
                                    for cid in criteria]
    value["milestones"] = [{"id": mid, "objective": f"Deliver {mid}", "acceptance_criteria": list(owned),
                            "depends_on": list(depends), "affected_paths": ["greet.py"]}
                           for mid, owned, depends in milestones]
    return value


def milestones(layout):
    return contract_body(layout)["milestones"]


def validator(*texts, dispositions=(), passed=()):
    return {"findings": [{"severity": "high", "finding": text, "evidence": "event:check", "blocking": True}
                         for text in texts],
            "finding_dispositions": list(dispositions),
            "criterion_results": [{"id": cid, "status": "PASS", "evidence_refs": ["event:check"]} for cid in passed]}


def resolved(fid):
    return {"id": fid, "disposition": "resolved", "evidence": "Reran the check for this milestone; it passes"}


def git_workspace(test_class):
    """One committed Git workspace per test class: the approval's request binding snapshots it."""
    temp = tempfile.TemporaryDirectory()
    test_class.addClassCleanup(temp.cleanup)
    root = Path(temp.name).resolve()
    seed_greeting_workspace(root)
    for command in (["init", "-q"], ["add", "greet.py", "test_greeting.py"],
                    ["-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "fixture"]):
        subprocess.run(["git", "-C", str(root), *command], check=True)
    with (root / ".git/info/exclude").open("a") as exclude:
        exclude.write("/registry-home/\n")
    return root


class Run:
    """A run that approved r1 and recorded an M2 Validator finding scoped to M2's r1 criteria."""

    def __init__(self, workspace):
        self.state = {"version": 2, "workspace": str(workspace), "task": "Stop the service cleanly",
                      "status": "RUNNING", "iteration": 1, "sessions": {}, "stages": [], "history": [],
                      "next_stage": "terra", "acceptance_criteria": [],
                      "settings": {"roles": {r: {"model": r, "reasoning_effort": "high"} for r in ("astra", "terra", "sol")},
                                   "transport_identity": LOCAL, "headroom": {"enabled": False},
                                   "context_soft_tokens": 10000,
                                   "limits": {"iteration_ceiling": 5, "max_seconds": None, "no_progress_batches": 3}}}
        lifecycle.migrate(self.state)
        self.approve(R1)
        self.review("M2", validator("Stop leaves the worker running"))
        self.finding = copy.deepcopy(findings.open_entries(self.state)[0])

    def draft(self, layout, *, publish=True):
        lifecycle.install_draft(self.state, contract_body(layout), origin="test")
        if publish:
            lifecycle.human.evaluate(self.state)
            lifecycle.present(self.state)

    def approve(self, layout):
        self.draft(layout)
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))

    def review(self, mid, report):
        self.state["current_task"] = {"id": "task-" + mid, "milestone_id": mid}
        findings.record_validation(self.state, report, {"output": f"sol-{mid}.json"})

    def scope(self, mid):
        return next(row for row in self.state["goal_contract"]["body"]["milestones"] if row["id"] == mid)

    def blocking(self, mid):
        return [row["id"] for row in findings.blocking_for_milestone(self.state, self.scope(mid))]

    def rows(self):
        return {row["id"]: row for row in self.state["findings_ledger"]}


class RevisionApprovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = git_workspace(cls)

    def setUp(self):
        self.run = Run(self.workspace)
        self.fid = self.run.finding["id"]

    def test_the_stale_scope_deadlocks_every_milestone_without_the_move(self):
        # What master did: the row kept {M2: [AC10, AC15]} under r2.
        self.assertEqual({"milestone_id": "M2", "criteria": ["AC10", "AC15"]}, self.run.finding["scope"])
        approved = milestones(R2)
        for current in approved:
            with self.subTest(milestone=current["id"]):
                self.assertEqual([self.run.finding], finding_scope.relevant_blockers([self.run.finding], current, approved))
                reviewed = {"milestone_id": current["id"], "criteria": current["acceptance_criteria"]}
                self.assertFalse(findings._covers(reviewed, self.run.finding["scope"], {"AC1", "AC10", "AC15", "AC20"}))

    def test_after_the_move_it_blocks_only_the_milestones_owning_its_criteria_and_their_reviewers_close_it(self):
        run = self.run
        run.approve(R2)
        self.assertEqual([], run.blocking("M1"))
        rows = run.rows()
        self.assertEqual(2, len(rows))
        part = next(rid for rid in rows if rid != self.fid)
        self.assertEqual({"milestone_id": "M2", "criteria": ["AC10"]}, rows[self.fid]["scope"])
        self.assertEqual({"milestone_id": "M5", "criteria": ["AC15"]}, rows[part]["scope"])
        self.assertEqual(self.fid, rows[part]["split_from"])
        self.assertEqual([self.fid], run.blocking("M2"))
        self.assertEqual({self.fid, part}, set(run.blocking("M5")))  # M2 is M5's prerequisite
        # Neither milestone's reviewer may close the other's part.
        for mid, other in (("M2", part), ("M5", self.fid)):
            with self.subTest(reviewer=mid), self.assertRaisesRegex(ValueError, "did not review"):
                run.review(mid, validator(dispositions=[resolved(other)], passed=["AC10", "AC15", "AC20"]))
        # M2's reviewer closes the AC10 part; the AC15 part stays open for M5's reviewer.
        run.review("M2", validator(dispositions=[resolved(self.fid)], passed=["AC10"]))
        self.assertEqual([part], [row["id"] for row in findings.open_entries(run.state)])
        self.assertEqual([], run.blocking("M2"))
        run.review("M5", validator(dispositions=[resolved(part)], passed=["AC15", "AC20"]))
        self.assertEqual([], findings.open_entries(run.state))

    def test_the_move_keeps_the_defect_open_and_records_itself_once(self):
        run = self.run
        run.approve(R2)
        token = goals.token(run.state["goal_contract"])
        rows = run.rows()
        part = next(rid for rid in rows if rid != self.fid)
        kept = {key: value for key, value in self.run.finding.items() if key != "scope"}
        self.assertEqual(kept, {key: value for key, value in rows[self.fid].items()
                                if key not in ("scope", "scope_history")})
        self.assertEqual({**{key: value for key, value in kept.items() if key != "id"}, "split_from": self.fid},
                         {key: value for key, value in rows[part].items() if key not in ("id", "scope")})
        for row in rows.values():
            self.assertEqual(("open", True), (row["status"], row["blocking"]))
            self.assertNotIn("resolved_in", row)
        move = {"from": {"milestone_id": "M2", "criteria": ["AC10", "AC15"]},
                "to": [{"id": self.fid, "milestone_id": "M2", "criteria": ["AC10"]},
                       {"id": part, "milestone_id": "M5", "criteria": ["AC15"]}],
                "contract_token": token, "at": run.state["goal_contract"]["approval_event"]["at"]}
        self.assertEqual([move], rows[self.fid]["scope_history"])
        self.assertNotIn("scope_history", rows[part])
        self.assertEqual([{"finding": self.fid, **move}], run_view.view(run.state)["evidence"]["finding_scope_moves"])

    def test_restart_and_a_later_unrelated_approval_change_nothing_more(self):
        run = self.run
        run.approve(R2)
        moved = copy.deepcopy(run.state["findings_ledger"])
        seq = run.state["findings_seq"]
        # A restart replays the approval step against the same saved contracts.
        previous = run.state["contract_history"][-1]
        self.assertEqual([], rescope.on_approval(run.state, previous, contract_token="replay", at="later",
                                                 new_id=lambda: self.fail("no second split")))
        run.approve([*R2, ("M6", ["AC30"], ["M5"])])
        self.assertEqual(moved, run.state["findings_ledger"])
        self.assertEqual(seq, run.state["findings_seq"])

    def test_a_revision_that_moves_nothing_changes_nothing(self):
        run = self.run
        before, seq = copy.deepcopy(run.state["findings_ledger"]), run.state.get("findings_seq")
        run.approve([("M1", ["AC1"], []), ("M2", ["AC10", "AC15"], ["M1"]), ("M5", ["AC20", "AC21"], ["M2"])])
        self.assertEqual(before, run.state["findings_ledger"])
        self.assertEqual(seq, run.state.get("findings_seq"))
        self.assertNotIn("finding_scope_moves", run_view.view(run.state)["evidence"])

    def test_an_unapproved_revision_moves_nothing(self):
        run = self.run
        before = copy.deepcopy(run.state["findings_ledger"])
        run.draft(R2)
        self.assertEqual(before, run.state["findings_ledger"])
        # A draft marked approved without the user's approval event is not a previous approval either.
        run.state["goal_contract"]["approval_status"] = "approved"
        self.assertEqual(run.state["contract_history"][-1], rescope.previous_approved(run.state))

    def test_all_criteria_moving_to_one_milestone_keeps_the_row(self):
        run = self.run
        run.approve([("M1", ["AC1"], []), ("M2", ["AC2"], ["M1"]), ("M5", ["AC10", "AC15", "AC20"], ["M2"])])
        self.assertEqual([self.fid], list(run.rows()))
        self.assertEqual({"milestone_id": "M5", "criteria": ["AC10", "AC15"]}, run.rows()[self.fid]["scope"])
        self.assertEqual([], run.blocking("M1"))
        self.assertEqual([], run.blocking("M2"))
        self.assertEqual([self.fid], run.blocking("M5"))

    def test_a_finding_citing_a_removed_criterion_stays_fail_closed(self):
        run = self.run
        run.approve([("M1", ["AC1"], []), ("M2", ["AC10"], ["M1"]), ("M5", ["AC20"], ["M2"])])
        self.assertEqual([self.run.finding], run.state["findings_ledger"])
        for mid in ("M1", "M2", "M5"):
            self.assertEqual([self.fid], run.blocking(mid))
        self.assertNotIn("finding_scope_moves", run_view.view(run.state)["evidence"])


class ApprovalThroughTheCliTests(unittest.TestCase):
    """The user approves the revision with --approve-goal; the status view reports the move."""

    @classmethod
    def setUpClass(cls):
        cls.workspace = git_workspace(cls)

    def setUp(self):
        home = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.workspace / "registry-home")})
        home.start()
        self.addCleanup(home.stop)
        self.run_dir = self.workspace / ".autocode/runs" / self._testMethodName
        self.run_dir.mkdir(parents=True)
        self.run = Run(self.workspace)

    def invoke(self, *args):
        support.atomic_json(self.run_dir / "state.json", self.run.state)
        argv = ["autocode", "--workspace", str(self.workspace), "--run-dir", str(self.run_dir), *args]
        with patch.object(sys, "argv", argv), patch.object(support, "assert_no_legacy_process"), \
             patch.object(support, "local_settings", return_value=LOCAL), \
             patch.object(runner, "run_role", side_effect=AssertionError("No agent may launch")), \
             contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = runner.main()
        self.run.state = support.read(self.run_dir / "state.json")
        return code, stdout.getvalue(), stderr.getvalue()

    def test_approving_the_revision_moves_the_finding_and_the_view_reports_it(self):
        fid = self.run.finding["id"]
        self.run.draft(R2, publish=False)
        self.invoke("--show-goal")
        token = goals.token(self.run.state["goal_contract"])
        code, _, stderr = self.invoke("--approve-goal", token)
        self.assertEqual(0, code, stderr)
        code, stdout, stderr = self.invoke("--status")
        self.assertEqual(0, code, stderr)
        evidence = json.loads(stdout)["view"]["evidence"]
        self.assertEqual({"open"}, {row["status"] for row in evidence["findings"]})
        [move] = evidence["finding_scope_moves"]
        part = next(row["id"] for row in evidence["findings"] if row["id"] != fid)
        self.assertEqual((fid, token), (move["finding"], move["contract_token"]))
        self.assertEqual([{"id": fid, "milestone_id": "M2", "criteria": ["AC10"]},
                          {"id": part, "milestone_id": "M5", "criteria": ["AC15"]}], move["to"])
        self.assertEqual([], self.run.blocking("M1"))


class PlanTests(unittest.TestCase):
    """The pure policy over the previous milestones, the approved ones and the ledger."""

    def row(self, owner, criteria, **extra):
        return {"id": "F", "status": "open", "scope": {"milestone_id": owner, "criteria": criteria}, **extra}

    def test_a_criterion_moved_to_another_milestone_takes_the_finding_there(self):
        self.assertEqual([{"id": "F", "from": {"milestone_id": "M2", "criteria": ["AC10", "AC15"]},
                           "to": [{"milestone_id": "M2", "criteria": ["AC10"]},
                                  {"milestone_id": "M5", "criteria": ["AC15"]}]}],
                         rescope.plan([self.row("M2", ["AC10", "AC15"])], milestones(R1), milestones(R2)))

    def test_the_first_owner_in_contract_order_keeps_the_row_when_its_milestone_owns_none(self):
        new = milestones([("M1", ["AC1"], []), ("M4", ["AC15"], ["M1"]), ("M5", ["AC10", "AC20"], ["M4"])])
        [move] = rescope.plan([self.row("M2", ["AC10", "AC15"])], milestones(R1), new)
        self.assertEqual(["M4", "M5"], [scope["milestone_id"] for scope in move["to"]])

    def test_a_criterion_several_milestones_list_goes_to_the_first_of_them(self):
        new = milestones([("M1", ["AC1"], []), ("M2", ["AC10"], ["M1"]), ("M5", ["AC15", "AC20"], ["M2"]),
                          ("M6", ["AC15"], ["M2"])])
        [move] = rescope.plan([self.row("M2", ["AC15"])], milestones(R1), new)
        self.assertEqual([{"milestone_id": "M5", "criteria": ["AC15"]}], move["to"])

    def test_rows_this_revision_did_not_move_are_left_alone(self):
        rows = [self.row("M2", ["AC10"]),                       # still fits
                self.row("M2", ["AC10", "AC15"], status="resolved"),
                self.row("M2", ["AC10", "AC99"]),               # cites a criterion no milestone lists
                self.row("M5", ["AC15"]),                       # did not fit r1: not moved by r2
                self.row("batch:abc", ["AC10", "AC15"]),
                self.row("M2", []), {"id": "F", "status": "open", "scope": None}, "not a row"]
        self.assertEqual([], rescope.plan(rows, milestones(R1), milestones(R2)))
        self.assertEqual([], rescope.plan([self.row("M2", ["AC10", "AC15"])], milestones(R1),
                                          milestones([("M1", ["AC1"], []), ("M2", ["AC10"], ["M1"])])))
        self.assertEqual([], rescope.plan([self.row("M2", ["AC10", "AC15"])], None, milestones(R2)))

    def test_apply_splits_copies_and_records_on_the_original_row(self):
        rows = [self.row("M2", ["AC10", "AC15"], severity="high", scope_history=[{"from": "earlier"}])]
        moves = rescope.plan(rows, milestones(R1), milestones(R2))
        records = rescope.apply(rows, moves, contract_token="r2:h", at="now", new_id=lambda: "F-new")
        self.assertEqual(["F", "F-new"], [row["id"] for row in rows])
        self.assertEqual({"id": "F-new", "status": "open", "severity": "high", "split_from": "F",
                          "scope": {"milestone_id": "M5", "criteria": ["AC15"]}}, rows[1])
        self.assertEqual(2, len(rows[0]["scope_history"]))
        self.assertEqual(records, rescope.history(rows)[1:])
        # Applying the same planned moves again finds the scopes already moved.
        after = copy.deepcopy(rows)
        self.assertEqual([], rescope.apply(rows, moves, contract_token="r2:h", at="again",
                                           new_id=lambda: self.fail("no second split")))
        self.assertEqual(after, rows)


if __name__ == "__main__":
    unittest.main()
