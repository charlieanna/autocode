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
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_finding_cause as finding_cause
import autocode_finding_rescope as rescope
import autocode_findings as findings
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_milestones as m
import autocode_run_view as run_view
import autocode_support as support
import autocode_validation_rounds as validation_rounds
from goal_fixtures import body as fixture_body
from goal_fixtures import seed_greeting_workspace

from . import test_carryforward as carry_tests

# r1: M1 -> M2 -> M5, and AC15 is M2's. r2 moves AC15 to M5.
R1 = [("M1", ["AC1"], []), ("M2", ["AC10", "AC15"], ["M1"]), ("M5", ["AC20"], ["M2"])]
R2 = [("M1", ["AC1"], []), ("M2", ["AC10"], ["M1"]), ("M5", ["AC15", "AC20"], ["M2"])]
LOCAL = {"auth_mode": "fixture"}


def contract_body(milestones, wording=None):
    value = fixture_body()
    criteria = sorted({cid for _, owned, _ in milestones for cid in owned}, key=lambda cid: int(cid[2:]))
    value["acceptance_criteria"] = [
        {
            "id": cid,
            "criterion": (wording or {}).get(cid, f"Behavior {cid} holds"),
            "verification_method": f"Run the {cid} regression check",
            "human_review": False,
        }
        for cid in criteria
    ]
    value["milestones"] = [
        {
            "id": mid,
            "objective": f"Deliver {mid}",
            "acceptance_criteria": list(owned),
            "depends_on": list(depends),
            "affected_paths": ["greet.py"],
        }
        for mid, owned, depends in milestones
    ]
    return value


def validator(*texts, dispositions=(), passed=()):
    return {
        "findings": [
            {"severity": "high", "finding": text, "evidence": "event:check", "blocking": True} for text in texts
        ],
        "finding_dispositions": list(dispositions),
        "criterion_results": [{"id": cid, "status": "PASS", "evidence_refs": ["event:check"]} for cid in passed],
    }


def resolved(fid):
    return {"id": fid, "disposition": "resolved", "evidence": "Reran the check for this milestone; it passes"}


def git_workspace(test_class):
    """One committed Git workspace per test class: the approval's request binding snapshots it."""
    temp = tempfile.TemporaryDirectory()
    test_class.addClassCleanup(temp.cleanup)
    root = Path(temp.name).resolve()
    seed_greeting_workspace(root)
    for command in (
        ["init", "-q"],
        ["add", "greet.py", "test_greeting.py"],
        ["-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "fixture"],
    ):
        subprocess.run(["git", "-C", str(root), *command], check=True)
    with (root / ".git/info/exclude").open("a") as exclude:
        exclude.write("/registry-home/\n")
    return root


class Run:
    """A run that approved r1 and recorded an M2 Validator finding scoped to M2's r1 criteria."""

    def __init__(self, workspace):
        self.state = {
            "version": 2,
            "workspace": str(workspace),
            "task": "Stop the service cleanly",
            "status": "RUNNING",
            "iteration": 1,
            "sessions": {},
            "stages": [],
            "history": [],
            "next_stage": "terra",
            "acceptance_criteria": [],
            "settings": {
                "roles": {r: {"model": r, "reasoning_effort": "high"} for r in ("astra", "terra", "sol")},
                "transport_identity": LOCAL,
                "headroom": {"enabled": False},
                "context_soft_tokens": 10000,
                "limits": {"iteration_ceiling": 5, "max_seconds": None, "no_progress_batches": 3},
            },
        }
        lifecycle.migrate(self.state)
        self.approve(R1)
        self.review("M2", validator("Stop leaves the worker running"))
        self.finding = copy.deepcopy(findings.open_entries(self.state)[0])

    def draft(self, layout, *, publish=True, wording=None):
        lifecycle.install_draft(self.state, contract_body(layout, wording), origin="test")
        if publish:
            lifecycle.human.evaluate(self.state)
            lifecycle.present(self.state)

    def approve(self, layout, wording=None):
        self.draft(layout, wording=wording)
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))

    def review(self, mid, report):
        self.state["current_task"] = {"id": "task-" + mid, "milestone_id": mid}
        findings.record_validation(self.state, report, {"output": f"sol-{mid}.json"})

    def scope(self, mid):
        return next(row for row in self.state["goal_contract"]["body"]["milestones"] if row["id"] == mid)

    def blocking(self, mid):
        return [row["id"] for row in findings.blocking_for_milestone(self.state, self.scope(mid))]

    def rows(self):
        return {row["id"]: row for row in findings.ledger(self.state)}

    def moves(self):
        return run_view.view(self.state)["evidence"].get("finding_scope_moves")


class RevisionApprovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = git_workspace(cls)

    def setUp(self):
        self.run = Run(self.workspace)
        self.fid = self.run.finding["id"]

    def test_without_the_move_the_stale_scope_deadlocks_every_milestone(self):
        # What master did: the approval kept the row's {M2: [AC10, AC15]} under r2.
        with patch.object(rescope, "on_approval", return_value=[]):
            self.run.approve(R2)
        self.assertEqual({"milestone_id": "M2", "criteria": ["AC10", "AC15"]}, self.run.rows()[self.fid]["scope"])
        for mid in ("M1", "M2", "M5"):
            with self.subTest(milestone=mid):
                self.assertEqual([self.fid], self.run.blocking(mid))
                with self.assertRaisesRegex(ValueError, "did not review"):
                    self.run.review(
                        mid, validator(dispositions=[resolved(self.fid)], passed=["AC1", "AC10", "AC15", "AC20"])
                    )

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
        self.assertEqual(
            kept, {key: value for key, value in rows[self.fid].items() if key not in ("scope", "scope_history")}
        )
        self.assertEqual(
            {**{key: value for key, value in kept.items() if key != "id"}, "split_from": self.fid},
            {key: value for key, value in rows[part].items() if key not in ("id", "scope")},
        )
        for row in rows.values():
            self.assertEqual(("open", True), (row["status"], row["blocking"]))
            self.assertNotIn("resolved_in", row)
        self.assertEqual(
            [
                {
                    "finding": self.fid,
                    "from": {"milestone_id": "M2", "criteria": ["AC10", "AC15"]},
                    "to": [
                        {"id": self.fid, "milestone_id": "M2", "criteria": ["AC10"]},
                        {"id": part, "milestone_id": "M5", "criteria": ["AC15"]},
                    ],
                    "contract_token": token,
                    "at": run.state["goal_contract"]["approval_event"]["at"],
                }
            ],
            run.moves(),
        )

    def test_after_a_restart_a_later_unrelated_approval_changes_nothing_more(self):
        run = self.run
        run.approve(R2)
        moved, moves = copy.deepcopy(findings.ledger(run.state)), run.moves()
        with tempfile.TemporaryDirectory() as temp:
            support.atomic_json(Path(temp) / "state.json", run.state)
            run.state = support.read(Path(temp) / "state.json")
        run.approve([*R2, ("M6", ["AC30"], ["M5"])])
        self.assertEqual(moved, findings.ledger(run.state))
        self.assertEqual(moves, run.moves())

    def test_a_revision_that_moves_nothing_changes_nothing(self):
        run = self.run
        before = copy.deepcopy(findings.ledger(run.state))
        run.approve([("M1", ["AC1"], []), ("M2", ["AC10", "AC15"], ["M1"]), ("M5", ["AC20", "AC21"], ["M2"])])
        self.assertEqual(before, findings.ledger(run.state))
        self.assertIsNone(run.moves())

    def test_an_unapproved_revision_moves_nothing(self):
        run = self.run
        before = copy.deepcopy(findings.ledger(run.state))
        run.draft(R2)
        self.assertEqual(before, findings.ledger(run.state))
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

    def test_a_finding_citing_a_removed_criterion_is_left_as_it_was(self):
        # AC10 still exists, AC15 is removed. The whole finding stays for a person (--close-finding),
        # and as before #447 it blocks every milestone.
        self.run.approve([("M1", ["AC1"], []), ("M2", ["AC10"], ["M1"]), ("M5", ["AC20"], ["M2"])])
        self.assertEqual([self.run.finding], findings.ledger(self.run.state))
        for mid in ("M1", "M2", "M5"):
            self.assertEqual([self.fid], self.run.blocking(mid))
        self.assertIsNone(self.run.moves())

    def test_a_moved_criterion_the_revision_reworded_takes_its_finding_to_the_reviewer_of_the_new_wording(self):
        run = self.run
        run.approve(R2, {"AC15": "Behavior AC15 holds."})
        part = next(rid for rid in run.rows() if rid != self.fid)
        self.assertEqual({"milestone_id": "M5", "criteria": ["AC15"]}, run.rows()[part]["scope"])
        self.assertEqual([], run.blocking("M1"))
        run.review("M5", validator(dispositions=[resolved(part)], passed=["AC15", "AC20"]))
        self.assertEqual("resolved", run.rows()[part]["status"])

    def test_resolving_one_part_by_name_leaves_the_other_open(self):
        # resolve_named serves a permission answer whose question names findings; none does today.
        run = self.run
        run.approve(R2)
        part = next(rid for rid in run.rows() if rid != self.fid)
        closed = finding_cause.resolve_named(
            run.state, [self.fid], {"question_id": "Q1", "text": "Accept", "at": "now"}
        )
        self.assertEqual([self.fid], closed)
        self.assertEqual("open", run.rows()[part]["status"])


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
        argv = ["autocode", "--workspace", str(self.workspace), "--run-dir", str(self.run_dir), *args]
        with (
            patch.object(sys, "argv", argv),
            patch.object(support, "assert_no_legacy_process"),
            patch.object(support, "local_settings", return_value=LOCAL),
            patch.object(runner, "run_role", side_effect=AssertionError("No agent may launch")),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            code = runner.main()
        self.run.state = support.read(self.run_dir / "state.json")
        return code, stdout.getvalue(), stderr.getvalue()

    def evidence(self):
        code, stdout, stderr = self.invoke("--status")
        self.assertEqual(0, code, stderr)
        return json.loads(stdout)["view"]["evidence"]

    def approve_revision(self):
        self.run.draft(R2, publish=False)
        support.atomic_json(self.run_dir / "state.json", self.run.state)
        self.invoke("--show-goal")
        token = goals.token(self.run.state["goal_contract"])
        code, _, stderr = self.invoke("--approve-goal", token)
        self.assertEqual(0, code, stderr)
        return token

    def test_approving_the_revision_moves_the_finding_and_the_view_reports_it(self):
        fid = self.run.finding["id"]
        token = self.approve_revision()
        evidence = self.evidence()
        self.assertEqual({"open"}, {row["status"] for row in evidence["findings"]})
        [move] = evidence["finding_scope_moves"]
        part = next(row["id"] for row in evidence["findings"] if row["id"] != fid)
        self.assertEqual((fid, token), (move["finding"], move["contract_token"]))
        self.assertEqual(
            [
                {"id": fid, "milestone_id": "M2", "criteria": ["AC10"]},
                {"id": part, "milestone_id": "M5", "criteria": ["AC15"]},
            ],
            move["to"],
        )
        self.assertEqual([], self.run.blocking("M1"))

    def test_approving_the_same_revision_again_after_a_restart_moves_nothing_more(self):
        token = self.approve_revision()
        before = self.evidence()
        code, _, _ = self.invoke("--approve-goal", token)
        self.assertNotEqual(0, code)
        self.assertEqual(before, self.evidence())


class CarriedMilestoneTests(unittest.TestCase):
    """With milestone checkpoints, M1 [C1, C2] is accepted with a reuse manifest and M2 [C2, C3]
    depends on it. M2's Validator raises a finding on C2 and C3, then a revision takes C2 off M2."""

    fixture_setup = carry_tests.CarryForwardTests.setUp
    accept_fixture = carry_tests.CarryForwardTests.accept_fixture
    decision = carry_tests.CarryForwardTests.decision
    validate = carry_tests.CarryForwardTests.validate

    def setUp(self):
        self.fixture_setup()
        draft = fixture_body()
        draft["acceptance_criteria"] += [
            {
                "id": "C2",
                "criterion": "Reject invalid input",
                "verification_method": "Execute empty input",
                "human_review": False,
            },
            {
                "id": "C3",
                "criterion": "Preserve Unicode",
                "verification_method": "Execute Unicode input",
                "human_review": False,
            },
        ]
        draft["milestones"][0]["acceptance_criteria"] = ["C1", "C2"]
        draft["milestones"].append(
            {
                "id": "M2",
                "objective": "Unicode flow",
                "acceptance_criteria": ["C2", "C3"],
                "depends_on": ["M1"],
                "affected_paths": ["unicode.py"],
            }
        )
        self.approve(draft)
        self.state["settings"]["milestone_checkpoints"] = copy.deepcopy(m.DEFAULTS)
        self.assign("M1", ["C1", "C2"])
        self.accept_fixture("M1")
        self.assign("M2", ["C3"])
        findings.record_validation(
            self.state, validator("Empty input crashes the Unicode path"), {"output": "sol-M2.json"}
        )
        [row] = findings.open_entries(self.state)
        self.fid = row["id"]
        self.assertEqual({"milestone_id": "M2", "criteria": ["C2", "C3"]}, row["scope"])

    def approve(self, draft):
        lifecycle.install_draft(self.state, draft, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, self.state["displayed_goal"])

    def assign(self, milestone, criteria):
        decision = self.decision(milestone)
        decision["next_task"]["acceptance_criteria"] = criteria
        output = self.run / f"astra-{len(self.state['stages'])}.json"
        output.write_text(json.dumps(decision))
        record = {"output": str(output), "source_revision": support.snapshot(self.root)["revision"]}
        runner.apply_result(self.state, "astra_review", decision, record, self.root, self.run)

    def revise(self, *new_milestones):
        draft = copy.deepcopy(self.state["goal_contract"]["body"])
        next(row for row in draft["milestones"] if row["id"] == "M2")["acceptance_criteria"] = ["C3"]
        draft["milestones"] += list(new_milestones)
        self.approve(draft)
        self.assertEqual({"milestone_id": "M2", "criteria": ["C3"]}, self.scope_of(self.fid))
        return next(row["id"] for row in findings.open_entries(self.state) if row["id"] != self.fid)

    def scope_of(self, fid):
        return next(row["scope"] for row in findings.ledger(self.state) if row["id"] == fid)

    def blocking(self, mid):
        milestone = next(row for row in self.state["goal_contract"]["body"]["milestones"] if row["id"] == mid)
        return [row["id"] for row in findings.blocking_for_milestone(self.state, milestone)]

    def close(self, fid, passed):
        findings.record_validation(
            self.state, validator(dispositions=[resolved(fid)], passed=passed), {"output": f"sol-close-{fid}.json"}
        )

    def outcome(self, mid):
        return next(row for row in self.state["milestone_carry_forward"][-1]["outcomes"] if row["milestone_id"] == mid)

    def test_a_criterion_moved_to_a_new_milestone_goes_there_not_to_the_carried_prerequisite(self):
        part = self.revise(
            {
                "id": "M3",
                "objective": "Input hardening",
                "acceptance_criteria": ["C2"],
                "depends_on": ["M2"],
                "affected_paths": ["greet.py"],
            }
        )
        self.assertEqual({"milestone_id": "M3", "criteria": ["C2"]}, self.scope_of(part))
        self.assertEqual("carried", self.outcome("M1")["result"])
        self.assign("M2", ["C3"])
        self.assertEqual([self.fid], self.blocking("M2"))
        self.validate({"C1": "PASS", "C2": "PASS", "C3": "PASS"})
        with self.assertRaisesRegex(ValueError, "did not review"):
            self.close(part, ["C1", "C2", "C3"])
        self.close(self.fid, ["C3"])
        self.assign("M3", ["C2"])
        self.assertEqual("M3", self.state["current_task"]["milestone_id"])
        self.assertEqual([part], self.blocking("M3"))
        self.close(part, ["C2"])
        self.assertEqual([], findings.open_entries(self.state))

    def test_a_part_left_with_an_accepted_milestone_reopens_it_for_review(self):
        part = self.revise()
        self.assertEqual({"milestone_id": "M1", "criteria": ["C2"]}, self.scope_of(part))
        self.assertEqual(
            ("revalidate", "An open blocking finding is recorded against it; revalidate"),
            (self.outcome("M1")["result"], self.outcome("M1")["reason"]),
        )
        with self.assertRaisesRegex(ValueError, "prerequisites are accepted: M1"):
            m.require_prerequisites(self.state, "M2")
        self.assign("M1", ["C1", "C2"])
        self.assertEqual([part], self.blocking("M1"))
        self.validate({"C1": "PASS", "C2": "PASS", "C3": "NOT_VERIFIED"})
        self.close(part, ["C1", "C2"])
        self.assign("M2", ["C3"])
        self.assertEqual("M2", self.state["current_task"]["milestone_id"])
        self.assertEqual([self.fid], self.blocking("M2"))


class PlanTests(unittest.TestCase):
    """The pure policy over the previous contract, the approved one and the ledger."""

    def row(self, owner, criteria, **extra):
        return {"id": "F", "status": "open", "scope": {"milestone_id": owner, "criteria": criteria}, **extra}

    def plan(self, rows, new, old=R1, **options):
        return rescope.plan(rows, contract_body(old) if old else None, contract_body(new), **options)

    def test_a_criterion_moved_to_another_milestone_takes_the_finding_there(self):
        self.assertEqual(
            [
                {
                    "id": "F",
                    "from": {"milestone_id": "M2", "criteria": ["AC10", "AC15"]},
                    "to": [{"milestone_id": "M2", "criteria": ["AC10"]}, {"milestone_id": "M5", "criteria": ["AC15"]}],
                }
            ],
            self.plan([self.row("M2", ["AC10", "AC15"])], R2),
        )

    def test_the_first_owner_in_contract_order_keeps_the_row_when_its_milestone_owns_none(self):
        [move] = self.plan(
            [self.row("M2", ["AC10", "AC15"])],
            [("M1", ["AC1"], []), ("M4", ["AC15"], ["M1"]), ("M5", ["AC10", "AC20"], ["M4"])],
        )
        self.assertEqual(["M4", "M5"], [scope["milestone_id"] for scope in move["to"]])

    def test_a_criterion_several_milestones_list_goes_where_it_moved_else_where_it_costs_least_review(self):
        old = [
            ("M1", ["AC1", "AC15"], []),
            ("M2", ["AC10", "AC15"], ["M1"]),
            ("M5", ["AC20"], ["M2"]),
            ("M6", ["AC15"], ["M2"]),
            ("M7", ["AC15"], ["M1"]),
        ]
        kept = [("M1", ["AC1", "AC15"], []), ("M2", ["AC10"], ["M1"])]
        unmoved = [*kept, ("M5", ["AC20"], ["M2"]), ("M6", ["AC15"], ["M2"])]
        cases = {
            # M5 gained AC15; M1 and M6 already listed it.
            "moved there": ([*kept, ("M5", ["AC15", "AC20"], ["M2"]), ("M6", ["AC15"], ["M2"])], (), "M5"),
            # Nobody gained it: M6 was not accepted, so it is reviewed anyway; M1 would be reused.
            "not accepted": (unmoved, {"M1"}, "M6"),
            # Both accepted, but M6 depends on M2, which the revision changed: it is revalidated anyway.
            "revalidated anyway": (unmoved, {"M1", "M6"}, "M6"),
            # Both would be reused: M7, on which nothing depends, rather than M1, which M2, M5 and M7 wait on.
            "fewest dependents": ([*kept, ("M5", ["AC20"], ["M2"]), ("M7", ["AC15"], ["M1"])], {"M1", "M7"}, "M7"),
            # Two milestones gained it: the first in contract order.
            "contract order": (
                [*kept, ("M5", ["AC15", "AC20"], ["M2"]), ("M6", ["AC15"], ["M2"]), ("M7", ["AC15"], ["M2"])],
                (),
                "M5",
            ),
        }
        for name, (new, reusable, holder) in cases.items():
            with self.subTest(name):
                [move] = self.plan([self.row("M2", ["AC15"])], new, old, reusable=reusable)
                self.assertEqual([{"milestone_id": holder, "criteria": ["AC15"]}], move["to"])

    def test_rows_this_revision_did_not_move_are_left_alone(self):
        rows = [
            self.row("M2", ["AC10"]),  # still fits
            self.row("M2", ["AC10", "AC15"], status="resolved"),
            self.row("M2", ["AC10", "AC99"]),  # cites a criterion no milestone lists
            self.row("M5", ["AC15"]),  # did not fit r1: not moved by r2
            self.row("batch:abc", ["AC10", "AC15"]),
            self.row("M2", []),
            {"id": "F", "status": "open", "scope": None},
            "not a row",
        ]
        self.assertEqual([], self.plan(rows, R2))
        self.assertEqual(
            [], self.plan([self.row("M2", ["AC10", "AC15"])], [("M1", ["AC1"], []), ("M2", ["AC10"], ["M1"])])
        )
        self.assertEqual([], self.plan([self.row("M2", ["AC10", "AC15"])], R2, old=None))

    def test_a_moved_criterion_the_revision_reworded_still_takes_its_findings(self):
        # As when a reworded criterion stays with its milestone: its new owner's reviewer judges the
        # finding against the new wording. Leaving it stale would block every milestone instead.
        moved = rescope.plan(
            [self.row("M2", ["AC10", "AC15"])], contract_body(R1), contract_body(R2, {"AC15": "Behavior AC15 holds."})
        )
        self.assertEqual(self.plan([self.row("M2", ["AC10", "AC15"])], R2), moved)

    def test_apply_splits_copies_without_the_rows_bookkeeping_and_records_on_the_original_row(self):
        rows = [
            self.row(
                "M2",
                ["AC10", "AC15"],
                severity="high",
                scope_history=[{"from": "earlier"}],
                assigned_task="task-1",
                assigned_history=[{"task_id": "task-1", "at": "then"}],
                pending_resolution={"report": "r", "unverified_criteria": ["AC10", "AC15"]},
                scope_restored_from={"task_id": "task-0"},
            )
        ]
        moves = self.plan(rows, R2)
        records = rescope.apply(rows, moves, contract_token="r2:h", at="now", new_id=lambda: "F-new")
        self.assertEqual(["F", "F-new"], [row["id"] for row in rows])
        self.assertEqual(
            {
                "id": "F-new",
                "status": "open",
                "severity": "high",
                "split_from": "F",
                "assigned_task": None,
                "scope": {"milestone_id": "M5", "criteria": ["AC15"]},
            },
            rows[1],
        )
        self.assertEqual(
            {"report": "r", "unverified_criteria": ["AC10"], "moved_unverified_criteria": ["AC15"]},
            rows[0]["pending_resolution"],
        )
        self.assertEqual(("task-1", "task-0"), (rows[0]["assigned_task"], rows[0]["scope_restored_from"]["task_id"]))
        self.assertEqual(2, len(rows[0]["scope_history"]))
        self.assertEqual(records, rescope.history(rows)[1:])
        # Applying the same planned moves again finds the scopes already moved.
        after = copy.deepcopy(rows)
        self.assertEqual(
            [],
            rescope.apply(rows, moves, contract_token="r2:h", at="again", new_id=lambda: self.fail("no second split")),
        )
        self.assertEqual(after, rows)

    def test_a_pending_attempt_whose_only_gap_moved_away_says_so(self):
        rows = [
            self.row(
                "M2",
                ["AC10", "AC15"],
                source="sol",
                pending_resolution={
                    "report": "r",
                    "reason": "Finding scope lacks fully passing verification",
                    "unverified_criteria": ["AC15"],
                },
            )
        ]
        rescope.apply(rows, self.plan(rows, R2), contract_token="r2:h", at="now", new_id=lambda: "F-new")
        pending = rows[0]["pending_resolution"]
        self.assertEqual(([], ["AC15"]), (pending["unverified_criteria"], pending["moved_unverified_criteria"]))
        self.assertEqual(
            "F (Validator): its resolution was not accepted (Finding scope lacked fully passing "
            "verification only on AC15, which an approved revision then moved to another milestone; "
            "a fresh report must resolve it)",
            validation_rounds._why(rows[0]),
        )

    def test_a_part_split_again_names_the_original_finding(self):
        rows = [
            {
                "id": "F-part",
                "status": "open",
                "split_from": "F",
                "scope": {"milestone_id": "M5", "criteria": ["AC15", "AC20"]},
            }
        ]
        moves = self.plan(
            rows,
            [("M1", ["AC1"], []), ("M2", ["AC10"], ["M1"]), ("M5", ["AC15"], ["M2"]), ("M6", ["AC20"], ["M5"])],
            old=R2,
        )
        rescope.apply(rows, moves, contract_token="r3:h", at="now", new_id=lambda: "F-new")
        self.assertEqual(["F", "F"], [row["split_from"] for row in rows])


if __name__ == "__main__":
    unittest.main()
