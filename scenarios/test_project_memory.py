"""Project memory through fresh public task runs, with real product oracles (#719)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import catalog, project_memory_provider
from harness.driver import Driver, default_autocode, fake_setup
from harness.processes import run_cli
from harness.project import git, materialize

REPO = Path(__file__).resolve().parents[1]


class ProjectMemoryScenarioTests(unittest.TestCase):
    def setUp(self):
        output = Path(
            os.environ.get("AUTOCODE_PROJECT_MEMORY_TEST_OUT", REPO / ".scenario-runs/issue-719-qualification")
        )
        output.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + "-", dir=output)).resolve()
        self.test_command = "python3 -m unittest discover -s tests -t . -v"
        self.scenario = replace(
            catalog.load("feature-timesheet-by-project"),
            fake_fault="project_memory",
            fake_check=self.test_command,
        )
        self.project = materialize(self.scenario.seed, self.root / "project")
        self.seed_head = git(self.project, "rev-parse", "HEAD").strip()
        self.memory_path = self.project / ".autocode/project.json"

    def new_driver(self, name, *, explicit_command=False):
        root = self.root / name
        root.mkdir()
        flags, env = fake_setup(self.scenario, root, self.scenario.reference)
        flags += [
            "--no-adaptive-planning",
            "--spec-first",
            "--max-iterations",
            "2",
            "--max-seconds",
            "600",
            "--max-stage-seconds",
            "120",
        ]
        if explicit_command:
            flags += ["--test-command", self.test_command]
        return Driver(
            self.project,
            root,
            flags,
            env,
            autocode=default_autocode(),
            max_steps=60,
            timeout_seconds=600,
            explicit_answers={project_memory_provider.QUESTION_ID: project_memory_provider.ANSWER},
            fixture_root=self.root,
        )

    def public(self, driver, name, *arguments):
        result = run_cli([*driver.autocode, *arguments], env=driver.env, cwd=driver.root, timeout=60)
        with (driver.root / "public-calls.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "name": name,
                        "arguments": arguments,
                        "exit_code": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    }
                )
                + "\n"
            )
        return result

    def start(self, driver):
        driver.call("start", task=self.scenario.brief)
        listed = self.public(driver, "discover-runs", "runs", "--workspace", str(self.project), "--json")
        self.assertEqual(0, listed.returncode, listed.stdout + listed.stderr)
        rows = json.loads(listed.stdout)["runs"]
        self.assertEqual(1, len(rows), "No old private run may enter the next fresh task")
        self.assertEqual("available", rows[0]["availability"], rows)
        driver.run_dir = Path(rows[0]["run_dir"])
        view = driver.view()
        (driver.root / "public-start.json").write_text(json.dumps(view, indent=2) + "\n")
        return view

    def approval(self, driver, *, previous_token=None):
        view = driver.until_stopped("needs:approve_plan")
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertFalse(view["done"], view)
        plan = view["displayed_plan"]
        (driver.root / "public-plan.json").write_text(json.dumps(plan, indent=2) + "\n")
        self.assertEqual(view["needs"]["token"], plan["token"])
        self.assertEqual(
            self.scenario.seed.joinpath("timesheet/report.py").read_bytes(),
            self.project.joinpath("timesheet/report.py").read_bytes(),
            "Memory must not authorize a Builder before fresh approval",
        )
        refused = driver.call("continue-without-approval")
        self.assertEqual(2, refused.returncode, refused.stdout + refused.stderr)
        self.assertEqual(view["needs"], driver.view()["needs"])
        if previous_token is not None:
            self.assertNotEqual(previous_token, plan["token"], "New tasks have independent approval tokens")
            stale = driver.call("reject-previous-task-approval", "--approve-goal", previous_token)
            self.assertEqual(2, stale.returncode, stale.stdout + stale.stderr)
            self.assertEqual(view["needs"], driver.view()["needs"])
        return plan

    def complete(self, driver, plan):
        driver.serve({"kind": "approve_plan", "token": plan["token"]})
        view = driver.until_stopped()
        (driver.root / "public-complete.json").write_text(json.dumps(view, indent=2) + "\n")
        self.assertTrue(view["done"], view)
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        approvals = [row for row in driver.approvals if row["gate"] == "approve_plan"]
        self.assertEqual([{"gate": "approve_plan", "token": plan["token"]}], approvals)
        checks = self.scenario.oracle()(self.project, self.scenario)
        self.assertTrue(checks)
        self.assertEqual([], [(row.name, row.detail) for row in checks if not row.ok], str(self.root))
        replay = view["evidence"]["check_replay"]
        self.assertEqual("PASS", replay["verdict"], replay)
        executed = [row for row in replay["checks"] if row["command"] == self.test_command]
        self.assertTrue(executed, "The exact portable suite must actually run in independent replay")
        for check in executed:
            self.assertEqual(0, check["exit_code"], check)
            self.assertFalse(check["timed_out"], check)
            self.assertFalse(check["error"], check)
            self.assertEqual("approved_execution", check["purpose"], check)
            self.assertRegex(check["output_sha256"], r"\A[0-9a-f]{64}\Z")
            results = check["results"]
            self.assertIsInstance(results, dict, "Verbose unittest replay must retain real named results")
            self.assertTrue(results["complete"], results)
            self.assertGreater(results["total"], 0, results)
            self.assertEqual(results["total"], len(set(results["passed"])), results)
            for key in ("failed", "skipped", "collection_errors", "uncollected"):
                self.assertFalse(results.get(key), results)
        self.report_identity(view)
        return view

    def report_identity(self, view):
        report = view["evidence_report"]
        self.assertEqual("current", report["availability"], report)
        for key in ("binding", "json_sha256", "markdown_sha256"):
            self.assertRegex(report[key], r"\A[0-9a-f]{64}\Z")
        for key in ("json_path", "markdown_path"):
            self.assertTrue(report[key], report)
        return {key: report[key] for key in ("binding", "json_sha256", "markdown_sha256", "json_path", "markdown_path")}

    def reset_to_seed(self, name, *, preserve_memory):
        memory = self.memory_path.read_bytes() if preserve_memory else None
        archive = self.root / name
        archive.mkdir()
        # Preserve the repository and its seed commit. Retain our own completed
        # products/run artifacts elsewhere; carry no run snapshot into the next task.
        for entry in list(self.project.iterdir()):
            if entry.name != ".git":
                shutil.move(str(entry), str(archive / entry.name))
        shutil.copytree(self.scenario.seed, self.project, dirs_exist_ok=True)
        if memory is not None:
            self.memory_path.parent.mkdir(parents=True)
            self.memory_path.write_bytes(memory)
        (archive / "relocation.json").write_text(
            json.dumps(
                {
                    "original_project": str(self.project),
                    "retained_files": str(archive),
                    "scope": "Owned completed artifacts retained after public inspection; no relocated current-proof claim",
                },
                indent=2,
            )
            + "\n"
        )
        self.assertFalse((self.project / ".autocode/runs").exists())
        self.assertEqual(self.seed_head, git(self.project, "rev-parse", "HEAD").strip())

    @staticmethod
    def requests(driver):
        path = driver.root / "project-memory-requests.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def test_second_fresh_run_reuses_only_human_convention_and_forget_restores_question(self):
        started, status = time.monotonic(), "NONPASS"
        summary = {}
        try:
            first = self.new_driver("first", explicit_command=True)
            first_start = self.start(first)
            first_plan = self.approval(first)
            first_view = self.complete(first, first_plan)
            self.assertEqual([project_memory_provider.QUESTION_ID], [row["id"] for row in first.answers])
            self.assertEqual("person", first.answers[0]["source"])
            self.assertEqual(project_memory_provider.ANSWER, first.answers[0]["answer"])
            self.assertTrue(self.requests(first)[0]["asked"])
            self.assertFalse(self.requests(first)[0]["memory_convention_used"])
            memory = json.loads(self.memory_path.read_text())
            self.assertEqual(1, memory["version"])
            self.assertEqual("unittest", memory["framework"])
            self.assertEqual(self.test_command, memory["test_command"])
            observed_routes = {
                (row.get("engine"), row.get("model"))
                for row in first_view["usage"]["accounting"]["attempts"]
                if row.get("started_at") and not row.get("runner_owned")
            }
            self.assertTrue(observed_routes, "The completed public run must record actual model attempts")
            self.assertTrue(memory["routes"], "Empty learned routes would discard an issue acceptance fact")
            for route in memory["routes"]:
                self.assertEqual({"role", "engine", "provider", "model"}, set(route))
                self.assertTrue(route["role"])
                self.assertEqual("codex", route["engine"])
                self.assertTrue(route["model"])
                self.assertIn((route["engine"], route["model"]), observed_routes)
            self.assertTrue(memory["human_pause_states"], "Real question and approval pauses must be remembered")
            self.assertIn(first_start["status"], memory["human_pause_states"])
            self.assertIn("AWAITING_GOAL_APPROVAL", memory["human_pause_states"])
            for pause in memory["human_pause_states"]:
                self.assertRegex(pause, r"\A(?:WAITING_FOR_USER|AWAITING_GOAL_APPROVAL|PAUSED_[A-Z0-9_]+)\Z")
            self.assertEqual(
                [
                    {
                        "question": project_memory_provider.QUESTION,
                        "answer": project_memory_provider.ANSWER,
                    }
                ],
                memory["conventions"],
            )
            self.reset_to_seed("retained-first", preserve_memory=True)

            second = self.new_driver("second")
            self.start(second)
            second_plan = self.approval(second, previous_token=first_plan["token"])
            first_criteria = [
                {key: value for key, value in criterion.items() if key != "sources"}
                for criterion in first_plan["acceptance_criteria"]
            ]
            second_criteria = [
                {key: value for key, value in criterion.items() if key != "sources"}
                for criterion in second_plan["acceptance_criteria"]
            ]
            self.assertEqual(first_criteria, second_criteria)
            self.assertNotIn(
                "answer:PROJECT_TEST_NAMES",
                json.dumps(second_plan["acceptance_criteria"]),
                "A remembered convention is not a current answer in the next run",
            )
            self.assertLess(len(second.answers), len(first.answers))
            self.assertEqual([], second.answers, "Historical data is not a fabricated answer in the new run")
            observed = self.requests(second)
            self.assertTrue(observed)
            self.assertTrue(all(row["memory_convention_used"] and not row["asked"] for row in observed))
            self.assertTrue(all(row["memory_test_command"] == self.test_command for row in observed))
            second_view = self.complete(second, second_plan)
            self.assertEqual(memory["conventions"], json.loads(self.memory_path.read_text())["conventions"])

            forgotten = self.public(second, "forget", "forget", "--workspace", str(self.project), "--json")
            self.assertEqual(0, forgotten.returncode, forgotten.stdout + forgotten.stderr)
            self.assertEqual(
                {
                    "forgotten": True,
                    "workspace": str(self.project),
                    "path": ".autocode/project.json",
                },
                json.loads(forgotten.stdout),
            )
            self.assertFalse(self.memory_path.exists())
            after_forget = second.view()
            self.assertEqual(
                (second_view["status"], second_view["done"], second_view["needs"]),
                (after_forget["status"], after_forget["done"], after_forget["needs"]),
            )
            self.assertEqual(second_view["evidence"]["check_replay"], after_forget["evidence"]["check_replay"])
            self.assertEqual(self.report_identity(second_view), self.report_identity(after_forget))
            self.reset_to_seed("retained-second", preserve_memory=False)

            third = self.new_driver("after-forget")
            view = self.start(third)
            self.assertEqual("answer", view["needs"]["kind"], view)
            questions = view["needs"]["questions"]
            self.assertIn(project_memory_provider.QUESTION_ID, [row["id"] for row in questions])
            self.assertTrue(view["needs"].get("resolver_token"), "Question needs a real issued request token")
            self.assertFalse(view["done"])
            self.assertEqual([], third.approvals)
            self.assertEqual([], third.answers)
            self.assertTrue(self.requests(third)[0]["asked"])
            self.assertFalse(self.requests(third)[0]["memory_convention_used"])
            summary = {
                "question_counts": [len(first.answers), len(second.answers)],
                "criteria_identical": first_plan["acceptance_criteria"] == second_plan["acceptance_criteria"],
                "first_status": first_view["status"],
                "second_status": second_view["status"],
                "after_forget_question": project_memory_provider.QUESTION_ID,
            }
            status = "PASS"
        finally:
            (self.root / "qualification.json").write_text(
                json.dumps(
                    {
                        "status": status,
                        "wall_seconds": time.monotonic() - started,
                        **summary,
                    },
                    indent=2,
                )
                + "\n"
            )


class ProjectMemoryProviderControls(unittest.TestCase):
    def test_question_is_suppressed_only_by_the_matching_answer_not_arbitrary_memory(self):
        with tempfile.TemporaryDirectory(prefix="project-memory-provider-") as directory:
            previous = os.environ.get("SCENARIO_FAKE_CONFIG")
            os.environ["SCENARIO_FAKE_CONFIG"] = str(Path(directory) / "fake-config.json")
            self.addCleanup(self.restore_config, previous)
            base = {"open_questions": [], "constraints": ["preserve the feature scope"]}
            wrong = {
                "project_memory": {
                    "conventions": [
                        {
                            "question": project_memory_provider.QUESTION,
                            "answer": "Use numeric issue names.",
                        }
                    ]
                }
            }
            missing = project_memory_provider.requirements(base, {})
            different = project_memory_provider.requirements(base, wrong)
            exact = project_memory_provider.requirements(
                base,
                {
                    "project_memory": {
                        "conventions": [
                            {
                                "question": project_memory_provider.QUESTION,
                                "answer": project_memory_provider.ANSWER,
                            }
                        ]
                    }
                },
            )
            for report in (missing, different):
                self.assertEqual([project_memory_provider.QUESTION_ID], [row["id"] for row in report["open_questions"]])
                self.assertEqual([project_memory_provider.QUESTION_ID], report["project_conventions"])
            self.assertEqual([], exact["open_questions"])
            self.assertEqual([], exact["project_conventions"], "Historical memory is not a newly answered question")
            self.assertEqual(base["constraints"], exact["constraints"])
            self.assertNotIn("saved_answers", exact)

    @staticmethod
    def restore_config(previous):
        if previous is None:
            os.environ.pop("SCENARIO_FAKE_CONFIG", None)
        else:
            os.environ["SCENARIO_FAKE_CONFIG"] = previous


if __name__ == "__main__":
    unittest.main()
