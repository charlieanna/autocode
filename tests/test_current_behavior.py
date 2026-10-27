"""A bug-fix criterion's "today" text lives in its own box, filled from the bound diagnosis (#2133)."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import autocode_approval_view as approval_view
import autocode_bridge_relay as relay
import autocode_bug_job as bug_job
import autocode_chat_client as chat
import autocode_current_behavior as current_behavior
import autocode_goal_lifecycle as lifecycle

from tests import test_bug_job

OBSERVED = test_bug_job.diagnosis()["observed"]


def investigation(**overrides):
    return {
        **test_bug_job.diagnosis(),
        "output": "/nowhere/investigator-01.jsonl",
        "output_hash": "a" * 64,
        "source_revision": "abc123",
        "probe_result": {"exit_code": 0, "counterexample": {"exit_code": 1}},
        **overrides,
    }


def criterion(cid, text, method, **fields):
    return {"id": cid, "criterion": text, "verification_method": method, "human_review": False, **fields}


def plan(*criteria):
    return {
        "acceptance_criteria": list(criteria),
        "required_behaviors": [],
        "scope_exclusions": [],
        "constraints": [],
        "important_failure_cases": [],
        "permission_boundaries": ["No network"],
        "open_blocking_questions": [],
    }


class EvidenceTests(unittest.TestCase):
    def test_a_bound_reproduced_diagnosis_is_the_evidence(self):
        self.assertEqual(
            {"text": OBSERVED, "output_hash": "a" * 64, "source_revision": "abc123"},
            current_behavior.evidence({"investigation": investigation()}),
        )

    def test_whitespace_differences_do_not_matter(self):
        found = current_behavior.evidence(
            {"investigation": investigation(observed="  Renewed   twice\nafter a timeout ")}
        )
        self.assertEqual(OBSERVED, found["text"])
        self.assertTrue(current_behavior.matches("Renewed twice after a timeout", found))
        self.assertFalse(current_behavior.matches("Renewed once after a timeout", found))

    def test_no_evidence_without_a_bound_reproduced_diagnosis(self):
        for name, found in (
            ("no investigation", None),
            ("not reproduced", investigation(outcome="not_reproduced")),
            ("unbound report", investigation(output_hash=None)),
            ("untestable: the runner saw no probe", investigation(probe_result=None)),
            ("probe did not show the bug", investigation(probe_result={"exit_code": 1})),
            ("nothing observed", investigation(observed="  ")),
        ):
            with self.subTest(name):
                self.assertIsNone(current_behavior.evidence({"investigation": found} if found else {}))
                self.assertFalse(current_behavior.matches(OBSERVED, None))

    def test_an_earlier_turns_diagnosis_is_not_this_turns_evidence(self):
        found = investigation(output="/run/iterations/investigator-01.jsonl")
        state = {
            "investigation": found,
            "stages": [{"stage": "investigate_bug", "output": found["output"]}],
            "turns": [{"stage_index": 1}],
        }
        self.assertIsNone(current_behavior.evidence(state))

    def test_an_applied_investigation_binds_its_observed_text(self):
        workspace = tempfile.mkdtemp()
        probe = "python3 -c 'from epp.client import renew; assert renew.calls == 2'"
        report = test_bug_job.diagnosis(
            fix_size="large",
            probe=probe,
            untestable="",
            counterexample={"file": "epp/client.py", "find": "fresh_cl_trid()", "replace": "cl_trid"},
        )
        output = Path(workspace) / "investigator-01.json"
        output.write_text(json.dumps(report))
        state = test_bug_job.state_for(workspace)
        record = {"changed_files": [], "output": str(output), "source_revision": "rev1"}
        bug_job.apply(
            state,
            report,
            record,
            workspace,
            run_probe=lambda command: {"exit_code": 0, "stdout": "", "stderr": ""},
            run_counterexample=lambda command, edit: {"exit_code": 1, "stdout": "", "stderr": "AssertionError"},
        )
        found = current_behavior.evidence(state)
        self.assertEqual(OBSERVED, found["text"])
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), found["output_hash"])
        self.assertEqual("rev1", found["source_revision"])
        # An untestable reproduction (no probe the runner could see) is the Investigator's word, not evidence.
        state = test_bug_job.state_for(workspace)
        bug_job.apply(state, test_bug_job.diagnosis(fix_size="large"), record, workspace)
        self.assertIsNone(current_behavior.evidence(state))


class PrepareBodyTests(unittest.TestCase):
    def test_an_empty_box_on_a_regression_criterion_gets_the_diagnosis_text(self):
        state = {"investigation": investigation()}
        body = plan(
            criterion("C1", "renew() records exactly one mutation", "test: test_c1_one_mutation"),
            criterion("C2", "a successful renew still records one", "guard: test_c2_success"),
            criterion("C3", "the README documents retries", "Read README.md"),
            criterion(
                "C4", "a retry keeps the cl_trid", "test: test_c4_cl_trid", current_behavior="Planner's own words"
            ),
        )
        before = copy.deepcopy(body)
        after = current_behavior.prepare_body(state, body, "glm_revise")
        self.assertEqual(before, body, "the body given is not changed in place")
        boxes = {row["id"]: row.get("current_behavior") for row in after["acceptance_criteria"]}
        self.assertEqual({"C1": OBSERVED, "C2": None, "C3": None, "C4": "Planner's own words"}, boxes)

    def test_without_evidence_the_previous_drafts_text_is_carried_forward(self):
        previous = plan(criterion("C1", "one mutation", "test: test_c1", current_behavior="Renewed twice"))
        state = {"goal_contract": {"body": previous, "approval_status": "draft", "approval_event": None}}
        after = current_behavior.prepare_body(
            state, plan(criterion("C1", "one mutation", "test: test_c1")), "glm_revise"
        )
        self.assertEqual("Renewed twice", after["acceptance_criteria"][0]["current_behavior"])

    def test_the_diagnosis_text_wins_over_the_previous_drafts_text(self):
        previous = plan(criterion("C1", "one mutation", "test: test_c1", current_behavior="Renewed thrice"))
        state = {
            "investigation": investigation(),
            "goal_contract": {"body": previous, "approval_status": "draft", "approval_event": None},
        }
        after = current_behavior.prepare_body(
            state, plan(criterion("C1", "one mutation", "test: test_c1")), "glm_revise"
        )
        self.assertEqual(OBSERVED, after["acceptance_criteria"][0]["current_behavior"])

    def test_nothing_to_fill_returns_the_same_body(self):
        body = plan(criterion("C1", "one mutation", "Run the suite"))
        self.assertIs(body, current_behavior.prepare_body({}, body, "glm_revise"))
        self.assertIs(body, current_behavior.prepare_body({"investigation": investigation()}, body, "glm_revise"))

    def test_the_persons_own_edit_is_kept_as_written(self):
        state = {"investigation": investigation()}
        body = plan(criterion("C1", "one mutation", "test: test_c1"))
        self.assertIs(body, current_behavior.prepare_body(state, body, "user_cli_edit"))


class DisplayTests(unittest.TestCase):
    ROW = criterion(
        "C1", "renew() records exactly one mutation", "test: test_c1_one_mutation", current_behavior=OBSERVED
    )

    def test_the_approval_summary_shows_today_under_the_criterion(self):
        body = {**plan(self.ROW, criterion("C2", "docs updated", "Read README.md")), "intended_outcome": "Fix renew"}
        lines = approval_view.summary(body, 3, [])
        start = lines.index("  [C1] renew() records exactly one mutation")
        self.assertEqual(
            [
                "  [C1] renew() records exactly one mutation",
                f"    Today: {OBSERVED}",
                "    Checked by: test: test_c1_one_mutation",
                "  [C2] docs updated",
                "    Checked by: Read README.md",
            ],
            lines[start : start + 5],
        )

    def test_the_chat_plan_and_the_channel_plan_show_today(self):
        shown = {
            "acceptance_criteria": [self.ROW, criterion("C2", "docs updated", "Read README.md")],
            "constraints": [],
            "permission_boundaries": [],
        }
        self.assertIn(
            f"  [C1] renew() records exactly one mutation\n      Today: {OBSERVED}\n      Checked by: test: test_c1_one_mutation\n  [C2]",
            chat.render_plan(shown),
        )
        self.assertNotIn("Today:", chat.render_plan({**shown, "acceptance_criteria": shown["acceptance_criteria"][1:]}))
        self.assertIn(f"  - today: {OBSERVED}\n  - checked by: test: test_c1_one_mutation", relay.plan_markdown(shown))

    def test_the_approval_screen_lists_the_receipt(self):
        found = current_behavior.evidence({"investigation": investigation()})
        state = {
            **test_bug_job.state_for(),
            "task_id": "t",
            "goal_contract": {
                "task_id": "t",
                "revision": 2,
                "hash": "h",
                "body": {**plan(self.ROW), "intended_outcome": "Fix renew"},
                "approval_status": "draft",
                "approval_event": None,
                "origin": "glm_revise",
                "declared_changes": [current_behavior.receipt("C1", OBSERVED, found)],
            },
        }
        rendered = lifecycle.render(state)
        self.assertIn(f"  - current_behavior: C1 -> {OBSERVED} (basis: agent_proposed)", rendered)


if __name__ == "__main__":
    unittest.main()
