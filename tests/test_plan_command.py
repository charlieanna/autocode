"""`autocode plan`: export a run's plan for review, check it, regenerate its text (#720).

The catalog test drives real fake-provider runs to the plan they ask a person to approve, exports
that plan through the CLI before and after approval, and checks the file gives back the run's own
contract body and goal hash. It covers two catalog scenarios by default (the brief's exact-output
examples; `test:` names); AUTOCODE_TEST_PLAN_CATALOG=all covers every catalog scenario that stops
at a plan approval.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import autocode_plan_file as plan_file
import autocode_subcommands as subcommands
import autocode_util as util

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import Driver, default_autocode, fake_setup
from harness.project import materialize

DEFAULT_SCENARIOS = ("greenfield-todo-cli", "feature-refund-window")
BODY = {
    "intended_outcome": "Greet a user by name",
    "intended_user": "A terminal user",
    "deliverables": ["greet.py"],
    "required_behaviors": ["`greet.py Ada` prints `Hello, Ada`"],
    "important_failure_cases": [],
    "scope_exclusions": ["Localization"],
    "constraints": [],
    "permission_boundaries": ["Edit greet.py only"],
    "accepted_assumptions": [],
    "delegated_decisions": [],
    "acceptance_criteria": [
        {"id": "C1", "criterion": "Greets by name", "verification_method": "test: test_greets", "human_review": False}
    ],
    "open_blocking_questions": [],
    "end_to_end_flow": ["Run greet.py"],
    "technical_approach": ["One script"],
    "milestones": [{"id": "M1", "objective": "Greeting", "acceptance_criteria": ["C1"]}],
}


def saved_run(root: Path, **written) -> Path:
    run_dir = root / ".autocode" / "runs" / "saved"
    run_dir.mkdir(parents=True)
    contract = {"task_id": "task-1", "revision": 2, "body": BODY}
    contract.update(hash=util.digest(contract), approval_status="draft", approval_event=None, origin="plan")
    state = {
        "version": 3,
        "workspace": str(root),
        "task": "greet",
        "status": "AWAITING_GOAL_APPROVAL",
        "user_events": [],
        "goal_contract": contract,
        **written,
    }
    (run_dir / "state.json").write_text(json.dumps(state))
    return run_dir


class PlanCommandTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="plan-command-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        (self.root / ".git").mkdir()

    def plan(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = subcommands.dispatch(["plan", *argv])
            except SystemExit as exit:
                code = exit.code
        return code, out.getvalue(), err.getvalue()

    def test_export_writes_the_runs_plan_and_check_reads_back_its_goal_hash(self):
        saved_run(self.root)
        path = self.root / "plans" / "greet.md"
        code, out, err = self.plan("--export", str(path), "--workspace", str(self.root))
        self.assertEqual((0, ""), (code, err))
        goal = util.digest(BODY)
        self.assertEqual(f"Wrote plan revision 2 (not approved) to {path}; goal hash {goal}\n", out)
        self.assertEqual(BODY, plan_file.read(path.read_text()).body)
        code, out, err = self.plan("--check", str(path))
        self.assertEqual((0, ""), (code, err))
        self.assertEqual(f"{path}: goal hash {goal}; the plan exported from revision 2\n", out)

    def test_export_masks_a_secret_from_the_environment_and_says_the_goal_hash_differs(self):
        saved_run(self.root)
        path = self.root / "plan.md"
        secret = "Greet a user"  # the start of the plan's intended outcome stands in for a leaked key
        with mock.patch.dict(os.environ, {"DEPLOY_API_KEY": secret}):
            code, out, err = self.plan("--export", str(path), "--workspace", str(self.root))
        self.assertEqual((0, ""), (code, err))
        self.assertIn("with credential-shaped text masked, so its goal hash", out)
        self.assertIn(f"differs from the run's plan, {util.digest(BODY)}", out)
        self.assertNotIn(secret, path.read_text())

    def test_a_run_without_a_plan_and_a_check_given_a_run_are_refused(self):
        saved_run(self.root, goal_contract={})
        path = self.root / "plan.md"
        code, out, err = self.plan("--export", str(path), "--workspace", str(self.root))
        self.assertEqual((2, ""), (code, out))
        self.assertIn("has no plan yet", err)
        self.assertFalse(path.exists())
        code, _, err = self.plan("--check", str(path), "--run-dir", str(self.root))
        self.assertEqual(2, code)
        self.assertIn("they go with --export", err)

    def test_check_names_stale_text_and_render_regenerates_it_from_the_edited_front_matter(self):
        saved_run(self.root)
        path = self.root / "plan.md"
        self.assertEqual(0, self.plan("--export", str(path), "--workspace", str(self.root))[0])
        exported = path.read_text()
        path.write_text(exported.replace('"Localization"', '"Localization and time zones"', 1))
        code, out, err = self.plan("--check", str(path))
        self.assertEqual((2, ""), (code, out))
        self.assertIn("no longer matches it (line", err)
        self.assertIn(f"autocode plan --render {path}", err)
        code, out, err = self.plan("--render", str(path))
        self.assertEqual((0, ""), (code, err))
        edited = dict(BODY, scope_exclusions=["Localization and time zones"])
        self.assertIn(f"goal hash {util.digest(edited)}", out)
        code, out, err = self.plan("--check", str(path))
        self.assertEqual((0, ""), (code, err))
        self.assertIn(f"differs from revision 2 as exported, goal hash {util.digest(BODY)}", out)
        self.assertIn("- Localization and time zones", path.read_text().split("\n"))

    def test_an_unreadable_plan_file_is_refused_without_a_traceback(self):
        path = self.root / "plan.md"
        path.write_text("# Plan\n")
        code, out, err = self.plan("--check", str(path))
        self.assertEqual((2, ""), (code, out))
        self.assertTrue(err.startswith("autocode plan: A plan file starts with its front matter"), err)


class CatalogPlanExportTests(unittest.TestCase):
    """A real run's plan survives export and check before and after approval, through the CLI."""

    def test_catalog_plans_round_trip_through_the_cli(self):
        chosen = os.environ.get("AUTOCODE_TEST_PLAN_CATALOG")
        if chosen == "all":
            names = sorted(
                path.name for path in (SCENARIOS / "catalog").iterdir() if (path / "scenario.toml").is_file()
            )
        else:
            names = list(DEFAULT_SCENARIOS)
        with tempfile.TemporaryDirectory(prefix="plan-catalog-") as directory:
            with ThreadPoolExecutor(max_workers=min(8, len(names))) as pool:
                results = dict(
                    zip(names, pool.map(lambda name: self.attempt(name, Path(directory)), names), strict=True)
                )
        for name, result in results.items():
            with self.subTest(scenario=name):
                if result == "no plan approval" and chosen == "all":
                    continue
                self.assertEqual("round trip", result)
        reached = sorted(name for name, result in results.items() if result == "round trip")
        self.assertTrue(reached, results)
        if chosen == "all":
            print(
                f"\n{len(reached)} of {len(names)} catalog scenarios showed a plan that round-tripped", file=sys.stderr
            )

    def attempt(self, name: str, base: Path) -> str:
        """One scenario's outcome as text, so one failure does not hide the others."""
        try:
            return self.round_trip(name, base)
        except Exception as error:  # noqa: BLE001 - reported per scenario by the subtest
            return f"{type(error).__name__}: {error}"

    def round_trip(self, name: str, base: Path) -> str:
        scenario = catalog.load(name)
        if scenario.category in ("components", "program", "conformance") or not scenario.fake_check:
            return "no plan approval"
        if scenario.fake_live_calls or scenario.missing_tools():
            return "no plan approval"
        root = base / name
        root.mkdir()
        project = materialize(scenario.seed, root / "project", npm_setup=scenario.npm_setup)
        flags, env = fake_setup(scenario, root, scenario.reference)
        driver = Driver(
            project,
            root,
            flags,
            env,
            autocode=default_autocode(),
            max_steps=scenario.max_steps,
            timeout_seconds=600,
            explicit_answers=scenario.fake_answers,
            acknowledge=scenario.fake_acknowledge,
        )
        driver.call("start", task=scenario.brief)
        driver.run_dir = next((project / ".autocode" / "runs").glob("*/state.json")).parent
        need = driver.until_stopped(say_at="needs:approve_plan").get("needs") or {}
        if need.get("kind") != "approve_plan":
            return "no plan approval"
        problems = self.exported(driver, root / "shown.md", approved=False)
        driver.serve(need)
        problems += self.exported(driver, root / "approved.md", approved=True)
        return "; ".join(problems) or "round trip"

    def exported(self, driver: Driver, path: Path, *, approved: bool) -> list[str]:
        """Export the run's plan through the CLI and compare the file with the run's own contract."""

        def plan(*argv):
            return subprocess.run(
                [*default_autocode(), "plan", *argv], capture_output=True, text=True, env=driver.env, timeout=120
            )

        export = plan("--export", str(path), "--run-dir", str(driver.run_dir))
        check = plan("--check", str(path))
        if export.returncode or check.returncode:
            return [
                f"{path.name}: export {export.returncode} {export.stderr} / check {check.returncode} {check.stderr}"
            ]
        body = driver.state()["goal_contract"]["body"]
        read = plan_file.read(path.read_text())
        problems = []
        if read.body != body:
            problems.append(f"{path.name}: the file's contract differs from the run's")
        if f"goal hash {util.digest(body)}; the plan exported from" not in check.stdout:
            problems.append(f"{path.name}: check printed {check.stdout!r}")
        if read.approval_status != ("approved" if approved else "not approved"):
            problems.append(f"{path.name}: approval status {read.approval_status!r}")
        return problems


if __name__ == "__main__":
    unittest.main()
