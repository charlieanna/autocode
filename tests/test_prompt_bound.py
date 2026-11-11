"""A stage prompt over the size bound is refused before anything launches (#3121)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_goals as goals
import autocode_prompt_bound as prompt_bound
import autocode_support as support
from goal_fixtures import approve_fixture

# The shape of #3092's Resolver handoff: the proof's coverage carried every measured file.
COVERAGE = {f"tools/module_{n}.py": {"executed": list(range(1, 900)), "missing": []} for n in range(400)}
HANDOFF = {
    "stage": "resolve",
    "task": "Fix the delivery bug",
    "regression_proof": {"verdict": "UNVERIFIED", "diff_coverage": {"files": [], "measured_lines": COVERAGE}},
}
OVERSIZED = "Resolve the blocker.\nCURRENT HANDOFF DATA\n" + json.dumps(HANDOFF, indent=2)


class PromptBoundTests(unittest.TestCase):
    def test_an_oversized_prompt_names_the_field_that_holds_it(self):
        self.assertGreater(len(OVERSIZED.encode()), prompt_bound.STAGE_PROMPT_BYTES)
        reason = prompt_bound.refusal(OVERSIZED, job="Resolver")
        self.assertIn(f"Resolver's prompt is {len(OVERSIZED.encode())} bytes", reason)
        self.assertIn("handoff's regression_proof.diff_coverage.measured_lines", reason)
        self.assertIn("No request was launched", reason)

    def test_a_prompt_at_the_bound_or_without_a_handoff_is_judged_by_size_alone(self):
        exact = "x" * prompt_bound.STAGE_PROMPT_BYTES
        self.assertIsNone(prompt_bound.refusal(exact, job="Builder"))
        self.assertIsNone(prompt_bound.refusal("small\nCURRENT HANDOFF DATA\n{}", job="Builder"))
        reason = prompt_bound.refusal(exact + "x", job="Builder")
        self.assertIn("over the 2097152-byte bound", reason)
        self.assertNotIn("handoff's", reason)
        self.assertIsNone(prompt_bound.largest_field("CURRENT HANDOFF DATA\n{not json"))

    def test_the_largest_field_stops_where_no_child_holds_most_of_its_parent(self):
        handoff = {"task": "t", "evidence": {"a": "x" * 500, "b": "y" * 500}}
        self.assertEqual(
            ("evidence", len(json.dumps(handoff["evidence"]))),
            prompt_bound.largest_field("CURRENT HANDOFF DATA\n" + json.dumps(handoff)),
        )
        listed = {"rows": [{"text": "z" * 900}, {"text": "q"}]}
        self.assertEqual("rows.0.text", prompt_bound.largest_field("CURRENT HANDOFF DATA\n" + json.dumps(listed))[0])


class LaunchRefusalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)

    def test_run_role_refuses_an_oversized_prompt_before_the_provider_starts(self):
        state = {
            "version": 2,
            "workspace": str(self.root),
            "task": "Fixture",
            "status": "RUNNING",
            "iteration": 1,
            "sessions": {},
            "stages": [],
            "history": [],
            "settings": {
                "engine": "opencode",
                "roles": {
                    role: {"model": "fixture/" + role}
                    for role in ("task_planner", "builder", "tester", "completion", "resolver")
                },
            },
        }
        approve_fixture(state, goals)
        launches = []

        def launch(*args, **kwargs):
            launches.append(args)
            raise AssertionError("an oversized prompt must not reach the provider")

        snapshot = {"head": "h", "files": {}, "revision": "r"}
        with (
            patch.object(runner.opencode, "launch", return_value=(["fixture-provider"], {}, {})),
            patch.object(runner.readonly_events, "prepare_opencode_snapshots"),
            patch.object(runner.supervision, "launch", launch),
            patch.object(support, "snapshot", return_value=snapshot),
            patch.object(runner.processes, "preflight", return_value=None),
        ):
            with self.assertRaises(support.Paused) as caught:
                runner.run_role(
                    role="task_planner",
                    prompt=OVERSIZED,
                    sandbox="read-only",
                    workspace=self.root,
                    run_dir=self.run,
                    state=state,
                    schema=runner.SCHEMA_DIR / "v2/terra-report.schema.json",
                    model="fixture/resolver",
                    allow_write=False,
                    dry_run=False,
                )
        self.assertEqual("PAUSED_PROMPT_SIZE", caught.exception.status)
        self.assertIn("regression_proof.diff_coverage.measured_lines", str(caught.exception))
        self.assertEqual([], launches)
        self.assertNotIn("active_stage", state)
        self.assertEqual([], state["stages"])
        self.assertEqual([], list(self.run.rglob("*.prompt.md")))


if __name__ == "__main__":
    unittest.main()
