"""A run saved under AutoCode's old code names continues on this version (autocode_saved_names).

The fixtures in ``fixtures/old-names-run/`` are real saved runs: the AutoCode before the code-name
rename (the parent of #3090) ran a fake-provider task and saved it under the routes ``astra``,
``glm``, ``terra`` and ``sol`` and the stages ``astra_discovery`` and so on.

- ``requirements-question.json``: waiting for the person's answer to the Requirements question.
- ``approved-plan.json``: the person approved the plan (revision 3) after five finished planning
  attempts, and the worker stopped before the build began, its status still ``RUNNING``.

Such a run used to end its chat with "This chat can't continue".
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_contract_identity as contract_identity
import autocode_goals as goals
import autocode_names as names
import autocode_resolver_human as resolver_human
import autocode_run_records as run_records
import autocode_run_view as run_view
import autocode_saved_names as saved_names
import autocode_source_trace as source_trace
import autocode_util as util
import autocode_workflows as workflows

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "old-names-run"
FIXTURE = FIXTURES / "requirements-question.json"
APPROVED = FIXTURES / "approved-plan.json"
OLD = set(saved_names.RENAMED) | set(saved_names.OLD_ORIGINS)


def saved(path=FIXTURE):
    return json.loads(path.read_text(encoding="utf-8"))


def old_names_outside_evidence(node, where="state", sealed_body=False):
    """Every place ``migrate`` owns that still names a route or stage the old way."""
    found = []
    if isinstance(node, list):
        for item in node:
            found += old_names_outside_evidence(item, where + "[]")
    elif isinstance(node, dict):
        sealed = {"task_id", "revision", "body", "hash"} <= node.keys()
        for key, value in node.items():
            if key in saved_names.KEPT or (sealed and key == "body"):
                continue
            path = f"{where}.{key}"
            if key == names.OLD_PLANNING_COUNTER:
                found.append(path)
            if key in saved_names.NAMED_FIELDS and isinstance(value, str):
                if value.removesuffix(saved_names.REPAIR) in OLD:
                    found.append(f"{path}={value}")
            elif key in saved_names.NAMED_TABLES and isinstance(value, dict):
                found += [f"{path}[{name}]" for name in value if name.removesuffix(saved_names.REPAIR) in OLD]
                found += old_names_outside_evidence(value, path)
            else:
                found += old_names_outside_evidence(value, path)
    return found


class SavedRunTests(unittest.TestCase):
    def test_the_fixture_is_a_run_saved_under_the_old_names(self):
        state = saved()
        self.assertEqual({"astra", "glm", "sol", "terra"}, set(saved_names.old_names(state)))
        self.assertEqual(("WAITING_FOR_USER", "astra_discovery"), (state["status"], state["next_stage"]))
        self.assertTrue(old_names_outside_evidence(state))

    def test_the_run_is_read_with_the_job_names_and_can_continue(self):
        state = saved()
        self.assertTrue(saved_names.migrate(state))
        self.assertEqual([], old_names_outside_evidence(state))
        roles = state["settings"]["roles"]
        self.assertLessEqual({"task_planner", "planner", "builder", "tester"}, set(roles))
        self.assertFalse(set(roles) & set(names.RETIRED_ROUTES))
        self.assertEqual("draft_plan", state["next_stage"])
        self.assertEqual("draft_plan", state["workflow"]["then"])
        self.assertIsNone(workflows.retired_stage(state))
        # A second read changes nothing.
        before = copy.deepcopy(state)
        self.assertFalse(saved_names.migrate(state))
        self.assertEqual(before, state)

    def test_evidence_filed_under_a_digest_keeps_its_words(self):
        original = saved()
        state = copy.deepcopy(original)
        saved_names.migrate(state)
        self.assertEqual(original["resolver"], state["resolver"])
        for request_id, entry in state["resolver"]["human_escalations"].items():
            self.assertEqual(request_id, util.digest(entry["identity"]))

    def test_the_open_question_is_asked_again_at_the_stage_the_run_now_names(self):
        # The person's pending Requirements question was filed before the rename, at astra_discovery.
        state = saved()
        saved_names.migrate(state)
        public = state[resolver_human.PUBLIC]
        entry = state["resolver"]["human_escalations"][public["request_id"]]
        self.assertEqual("astra_discovery", entry["identity"]["proposal"]["next_stage"])
        pins = entry["identity"]["proposal"]["evidence"]["hashes"]
        with mock.patch.object(
            resolver_human,
            "_evidence_valid",
            side_effect=lambda candidate: (candidate.get("evidence") or {}).get("hashes") == pins,
        ):
            self.assertTrue(resolver_human.restage_pending(state))
        self.assertEqual("draft_plan", state["next_stage"])
        proposal = state[resolver_human.PRIVATE]
        self.assertEqual("draft_plan", proposal["next_stage"])
        self.assertEqual(entry["identity"]["proposal"]["questions"], proposal["questions"])
        self.assertEqual([], old_names_outside_evidence(proposal))

    def test_reading_state_json_renames_it_and_reading_another_file_does_not(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ("state.json", "state.pre-v3.json"):
                (Path(temp) / name).write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual("draft_plan", run_records.read_json(Path(temp) / "state.json")["next_stage"])
            self.assertEqual("astra_discovery", run_records.read_json(Path(temp) / "state.pre-v3.json")["next_stage"])
            # The file itself is untouched until the run saves under its lock.
            self.assertEqual("astra_discovery", json.loads((Path(temp) / "state.json").read_text())["next_stage"])

    def test_a_state_naming_a_route_both_ways_is_left_as_saved_and_refused_in_plain_words(self):
        state = saved()
        state["settings"]["roles"]["builder"] = state["settings"]["roles"]["terra"]
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            saved_names.migrate(state)
        self.assertEqual(before, state)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            self.assertEqual(before, run_records.read_json(path))
        message = workflows.retired_stage(state)
        self.assertIn("start a new chat to carry on with the task", message)
        for word in ("terra", "astra", "glm", "route", "runtime"):
            self.assertNotIn(word, message)

    def test_a_current_run_is_not_touched(self):
        state = {"next_stage": "builder", "settings": {"roles": {"builder": {}, "tester": {}}}, "stages": []}
        before = copy.deepcopy(state)
        self.assertFalse(saved_names.migrate(state))
        self.assertEqual(before, state)


class ApprovedPlanTests(unittest.TestCase):
    """A run stopped right after its plan was approved continues from that approved plan."""

    def test_the_fixture_is_an_approved_plan_saved_under_the_old_names(self):
        state = saved(APPROVED)
        self.assertEqual({"astra", "astra_calls", "glm", "sol", "terra"}, set(saved_names.old_names(state)))
        self.assertEqual("approved", state["goal_contract"]["approval_status"])
        self.assertIn("astra_calls", state["planning"])
        self.assertEqual(
            ["requirements_gather", "astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"],
            [row["stage"] for row in state["stages"]],
        )

    def test_the_approved_plan_and_its_history_survive_the_rename(self):
        original = saved(APPROVED)
        state = copy.deepcopy(original)
        self.assertTrue(saved_names.migrate(state))
        self.assertEqual([], old_names_outside_evidence(state))
        # The plan the person approved is the same plan, under the same token and approval; only the
        # name of the stage it came from, kept beside the seal, is renamed.
        for key in ("task_id", "revision", "body", "hash", "approval_status", "approval_event"):
            self.assertEqual(original["goal_contract"][key], state["goal_contract"][key], key)
        self.assertEqual(
            ("astra_finalize", "finalize_plan"), (original["goal_contract"]["origin"], state["goal_contract"]["origin"])
        )
        self.assertTrue(contract_identity.sealed(state["goal_contract"]))
        self.assertEqual(goals.token(original["goal_contract"]), goals.token(state["goal_contract"]))
        self.assertEqual(
            ["requirements_gather", "draft_plan", "review_plan", "revise_plan", "finalize_plan"],
            [row["stage"] for row in state["stages"]],
        )
        self.assertEqual(original["planning"]["astra_calls"], state["planning"]["plan_review_calls"])
        self.assertLessEqual({"draft_plan", "review_plan", "finalize_plan"}, set(state["planning"]["reports"]))
        self.assertIsNone(workflows.retired_stage(state))

    def test_the_status_view_offers_to_carry_on_instead_of_a_new_chat(self):
        for path in (FIXTURE, APPROVED):
            with self.subTest(fixture=path.name):
                state = saved(path)
                refused = run_view.needs(state)
                self.assertTrue(refused and refused.get("new_run_required"))
                saved_names.migrate(state)
                need = run_view.needs(state) or {}
                self.assertFalse(need.get("new_run_required"), need)


class RenameTests(unittest.TestCase):
    def test_each_old_spelling_has_its_job_name(self):
        for old, new in (
            ("glm", "planner"),
            ("astra", "task_planner"),
            ("terra", "builder"),
            ("sol", "tester"),
            ("astra_discovery", "draft_plan"),
            ("astra_challenge_report_repair", "review_plan_report_repair"),
            ("glm_draft", "planner_draft"),
            ("draft_plan", "draft_plan"),
            ("gpt-5.6-sol", "gpt-5.6-sol"),
            (None, None),
        ):
            with self.subTest(old=old):
                self.assertEqual(new, saved_names.rename(old))
        self.assertTrue(saved_names.same_stage("astra_challenge", "review_plan"))
        self.assertFalse(saved_names.same_stage("astra_challenge", "finalize_plan"))

    def test_the_planning_counter_is_renamed_where_it_is_kept(self):
        state = {
            "planning": {"astra_calls": 2, "reports": {"astra_challenge": {}}},
            "planning_history": [{"astra_calls": 1}],
        }
        saved_names.migrate(state)
        self.assertEqual({"plan_review_calls": 2, "reports": {"review_plan": {}}}, state["planning"])
        self.assertEqual([{"plan_review_calls": 1}], state["planning_history"])


class SealedPlanTests(unittest.TestCase):
    def test_a_plan_sealed_before_the_rename_keeps_its_review_provenance(self):
        # The approved plan's body is sealed by its hash, so it keeps the old stage name of its review.
        with tempfile.TemporaryDirectory() as temp:
            events, output = Path(temp) / "events.jsonl", Path(temp) / "output.json"
            events.write_text("{}\n")
            output.write_text(json.dumps({source_trace.KEY: source_trace.DEFAULT_TRACE}))
            for stage in ("astra_challenge", "review_plan", "astra_finalize"):
                with self.subTest(stage=stage):
                    wrapper = {
                        "review": {
                            "stage": stage,
                            "events": str(events),
                            "events_sha256": util.file_hash(events),
                            "output": str(output),
                            "output_sha256": util.file_hash(output),
                            "trace_sha256": util.digest(source_trace.DEFAULT_TRACE),
                        }
                    }
                    wrapper["review"] = {key: wrapper["review"].get(key) for key in source_trace.REVIEW_KEYS}
                    self.assertEqual(source_trace.DEFAULT_TRACE, source_trace._verify_review(wrapper))
            wrapper["review"]["stage"] = "astra_discovery"
            with self.assertRaisesRegex(ValueError, "provenance is missing or changed"):
                source_trace._verify_review(wrapper)


if __name__ == "__main__":
    unittest.main()
