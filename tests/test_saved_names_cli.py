"""A chat saved under the old code names carries on through the public interface (autocode_saved_names).

Before, every such chat ended with "This chat can't continue": right after the person approved its
plan, and when they asked a follow-up after it completed. This drives a fake-provider run through
both moments, rewriting its saved state to the old names each time (what the AutoCode before the
rename saved), and continues it with this version. ``test_saved_names`` reads real saved runs.
"""

import dataclasses
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_names as names
import autocode_saved_names as saved_names
import autocode_taskrun as taskrun

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance

SEED_TESTS = (
    "import unittest\nfrom totals import sum_positive\n"
    "class Totals(unittest.TestCase):\n"
    " def test_positive(self): self.assertEqual(sum_positive([2]), 2)\n"
)
TESTS = SEED_TESTS + (
    " def test_old_negative(self):\n"
    "  self.assertEqual(sum_positive([-3, 2]), 2)\n"
    "  self.assertEqual(sum_positive([-1, 0.5]), 0.5)\n"
    " def test_empty(self): self.assertEqual(sum_positive([]), 0)\n"
)
BRIEF = (
    "Exclude negative input values in sum_positive. Preserve positive sums and empty input. "
    "Keep the current tests and add focused regression tests. Change only totals.py and test_totals.py."
)
OLD = {
    **{new: old for old, new in names.ROUTES.items()},
    **{new: old for old, new in names.STAGES.items()},
    **{new: old for old, new in saved_names.OLD_ORIGINS.items()},
}


def old_spelling(node, sealed_body=False):
    """``node`` (a saved state) as the AutoCode before the rename wrote it, where migrate reads it."""
    if isinstance(node, list):
        return [old_spelling(item) for item in node]
    if not isinstance(node, dict):
        return node

    def old(name):
        if not isinstance(name, str):
            return name
        base = name.removesuffix(saved_names.REPAIR)
        return OLD[base] + name[len(base) :] if base in OLD else name

    sealed = {"task_id", "revision", "body", "hash"} <= node.keys()
    out = {}
    for key, value in node.items():
        if key in saved_names.KEPT or (sealed and key == "body"):
            out[key] = value
        elif key in saved_names.NAMED_FIELDS and isinstance(value, str):
            out[key] = old(value)
        elif key in saved_names.NAMED_TABLES and isinstance(value, dict):
            out[key] = {old(name): old_spelling(item) for name, item in value.items()}
        else:
            out["astra_calls" if key == "plan_review_calls" else key] = old_spelling(value)
    return out


class OldNamesCli(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="old-names-cli-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        seed, self.reference = self.root / "seed", self.root / "reference"
        seed.mkdir()
        self.reference.mkdir()
        (seed / "totals.py").write_text("def sum_positive(values): return sum(values)\n")
        (seed / "test_totals.py").write_text(SEED_TESTS)
        (self.reference / "totals.py").write_text("def sum_positive(values): return sum(v for v in values if v >= 0)\n")
        (self.reference / "test_totals.py").write_text(TESTS)

    def save_with_old_names(self, run):
        path = Path(run.run_dir) / "state.json"
        state = json.loads(path.read_text())
        old = old_spelling(state)
        self.assertEqual({"glm", "astra", "terra", "sol"}, set(old["settings"]["roles"]) & set(names.ROUTES))
        path.write_text(json.dumps(old, indent=2))
        # This is the run that used to end with "This chat can't continue".
        view = run.status()
        self.assertFalse((view.get("needs") or {}).get("new_run_required"), view.get("needs"))
        self.assertNotEqual("This chat can't continue", (view.get("recovery") or {}).get("title"))

    def drive(self, run, *, stop_after_approval=False):
        for _ in range(12):
            view = run.advance_until_input()
            need = view.get("needs") or {}
            if view["done"]:
                return view
            if need.get("kind") == "approve_plan":
                run.approve_plan(need["token"])
                if stop_after_approval:
                    return run.status()
            elif need.get("kind") == "review":
                for criterion in need["criteria"]:
                    run.approve_review(criterion, need["token"])
            elif need.get("kind") == "answer":
                question = need["questions"][0]
                run.answer(
                    question["id"], question.get("proposed_default") or "yes", resolver_token=need["resolver_token"]
                )
            else:
                self.fail(str({key: view.get(key) for key in ("status", "next_stage", "needs", "stop_reason")}))
        self.fail("The run exceeded its twelve explicit human gates")

    def test_an_approved_plan_and_a_finished_chat_saved_under_the_old_names_carry_on(self):
        scenario = dataclasses.replace(
            catalog.load("feature-stock-refusals"),
            dir=self.root,
            brief=BRIEF,
            fake_fault="",
            fake_criteria={"C1": "test: test_old_negative", "C0": "guard: test_empty"},
            fake_milestones=(),
            fake_turn_paths=(),
            turns=(),
            fake_check="python3 -m unittest -v test_totals.py",
            fake_flags=(),
            fake_plan_review_call_limit=None,
        )
        project = without_maintenance(materialize(self.root / "seed", self.root / "project"))
        flags, environment = fake_setup(scenario, self.root, self.reference)
        environment.update(
            AUTOCODE_HOME=str(self.root / "registry"),
            PYTHONDONTWRITEBYTECODE="1",
            SCENARIO_FAKE_BLOCKING_REVIEW="1",
            SCENARIO_FAKE_EXISTING_SOURCE="1",
        )
        paths = environment["PATH"].split(os.pathsep)
        environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        run = taskrun.TaskRun.start(
            project, BRIEF, options=(*flags, "--spec-first"), env=environment, timeout=180, cwd=self.root
        )
        approved = self.drive(run, stop_after_approval=True)
        token = approved["approved_contract"]["revision"], approved["approved_contract"]["hash"]

        # Saved by the old version right after the person approved the plan: it builds that plan.
        self.save_with_old_names(run)
        first = self.drive(run)
        self.assertEqual("TASK_COMPLETE", first["status"])
        self.assertEqual(token, (first["approved_contract"]["revision"], first["approved_contract"]["hash"]))
        self.assertIn("v >= 0", (Path(run.workspace) / "totals.py").read_text())

        # A finished chat saved by the old version takes a follow-up.
        self.save_with_old_names(run)
        run.follow_up("Also keep zero values out of the sum. Change only totals.py and test_totals.py.")
        second = self.drive(run)
        self.assertEqual("TASK_COMPLETE", second["status"])
        saved = json.loads((Path(run.run_dir) / "state.json").read_text())
        self.assertFalse(saved_names.needed(saved))
        self.assertFalse(set(saved["settings"]["roles"]) & set(names.RETIRED_ROUTES))


if __name__ == "__main__":
    unittest.main()
