"""A real Requirements child replaces only its own paid access probe (#2345).

A run that plans in a task worktree of its own (#3081) keeps the access it proved in the person's folder:
seen 2026-11-11 on Kilo (#3219), the worktree probed every model again, Requirements' own route included.
"""

import json
import shutil
import sys
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from tests import test_subprocess
from tests.test_provider_credentials import CANARY, WRAPPER, provider_config
from tests.test_taskrun import BRIEF, HERE


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class AccessEvidenceCLI(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp

    def test_confirmed_requirements_reuses_its_route_and_probes_unseen_builder(self):
        self.check_requirements_access(repaired=False)

    def test_accepted_requirements_repair_reuses_its_own_access_not_original_report(self):
        self.check_requirements_access(repaired=True)

    def test_fresh_joint_requirements_defers_only_its_exact_probe_before_same_invocation_planning(self):
        self.check_requirements_access(repaired=False, fresh=True)

    def test_a_run_that_moves_to_its_worktree_keeps_the_access_it_proved_in_the_folder(self):
        self.check_requirements_access(repaired=False, worktree=True)

    def test_failed_requirements_auth_is_charged_and_does_not_launch_planning(self):
        self.check_requirements_access(repaired=False, fresh=True, failure="auth")

    def test_failed_requirements_budget_is_charged_and_does_not_launch_planning(self):
        self.check_requirements_access(repaired=False, fresh=True, failure="budget")

    def test_uncertain_requirements_is_charged_and_does_not_launch_planning(self):
        self.check_requirements_access(repaired=False, fresh=True, failure="uncertain")

    def check_requirements_access(self, *, repaired, fresh=False, failure=None, worktree=False):
        binary = self.root / "fixture-bin"
        shutil.copy2(HERE / "live_fixture_provider.py", binary / "codex-fixture")
        if repaired:
            fixture = binary / "codex-fixture"
            source = fixture.read_text()
            anchor = "    output.write_text(json.dumps(report))"
            self.assertEqual(1, source.count(anchor))
            fixture.write_text(
                source.replace(
                    anchor,
                    """    if stage == 'requirements_gather' and not repairing:
        report['proposed_assumptions'][0].pop('category')
"""
                    + anchor,
                )
            )
        wrapper = binary / "credentialfixture"
        probe = """    schema = json.load(open(sys.argv[sys.argv.index('--output-schema') + 1]))
    if 'ok' in schema.get('properties', {}):
        with open(sys.argv[sys.argv.index('-o') + 1], 'w') as report:
            json.dump({'ok': True}, report)
        print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}}))
        raise SystemExit(0)
    failure = os.environ.get('AUTOCODE_TEST_REQUIREMENTS_FAILURE')
    if failure and 'requirements-gather' in sys.argv[sys.argv.index('-o') + 1]:
        error = {
            'auth': {'message': 'Unauthorized', 'data': {'statusCode': 401}},
            'budget': {'message': 'Add credits to continue', 'data': {'statusCode': 402}},
            'uncertain': {'code': 'incomplete_turn', 'message': 'fixture partial turn'},
        }[failure]
        print(json.dumps({'type': 'turn.failed', 'error': error, 'usage': {'input_tokens': 2, 'output_tokens': 1}}))
        raise SystemExit(1)
"""
        wrapper.write_text(
            f"#!{sys.executable}\n"
            + WRAPPER.replace("    fixture = os.path.join", probe + "    fixture = os.path.join")
        )
        wrapper.chmod(0o755)
        config = Path(self.env["XDG_CONFIG_HOME"]) / "autocode/providers/credentialfixture.toml"
        provider_config(config, wrapper)
        config.write_text(config.read_text().replace('"{model}", "-"', '"{model}", "--variant", "{effort}", "-"'))
        log = self.root / "calls.jsonl"
        self.env.update(AUTOCODE_TEST_ENV_LOG=str(log), FIXTURE_API_KEY=CANARY, AUTOCODE_PASS_ENV="FIXTURE_API_KEY")
        if failure:
            self.env["AUTOCODE_TEST_REQUIREMENTS_FAILURE"] = failure
        options = (
            "--engine",
            "opencode",
            "--provider",
            "credentialfixture",
            "--joint-planning",
            *(("--spec-first",) if not fresh else ()),
            "--no-adaptive-planning",
            "--evidence-provenance",
            "fake",
            "--no-auto-select",
            "--plan-reviewer-model",
            "demo/builder",
            "--reasoning-effort",
            "high",
            *(
                argument
                for role in (
                    "task_planner",
                    "builder",
                    "tester",
                    "planner",
                    "plan_reviewer",
                    "requirements",
                    "completion",
                    "resolver",
                )
                for argument in (f"--{role.replace('_', '-')}-reasoning-effort", "high")
            ),
            "--max-cost-usd",
            "0",
        )
        run = taskrun.TaskRun.start(
            self.project,
            BRIEF,
            options=options,
            start_options=("--build-in-worktree",) if worktree else (),
            command=self.entry,
            env=self.env,
            timeout=180,
            cwd=self.root,
        )
        view = run.status()
        if not fresh:
            self.assertEqual("answer", view["needs"]["kind"], view.get("stop_reason"))
            question = view["needs"]["questions"][0]
            self.assertTrue(question["id"].startswith("requirements-review-"), question)
            self.assertFalse((run.run_dir / "model-preflight.json").exists())
            run.answer(question["id"], view["chat"]["review"]["answer"], resolver_token=view["needs"]["resolver_token"])
            view = run.advance_until_input()
        if failure:
            self.assertEqual("WAITING_FOR_USER", view["status"], view.get("stop_reason"))
            self.assertIn("no automatic replay", view["stop_reason"])
            self.assertNotEqual("approve_plan", view["needs"]["kind"])
            if failure == "auth":
                self.assertIn("provider authentication failed (HTTP 401)", view["stop_reason"])
            if failure == "budget":
                self.assertIn("provider_or_spending_guard", view["stop_reason"])
        else:
            self.assertEqual("approve_plan", view["needs"]["kind"], view.get("stop_reason"))
        if worktree:
            moved = Path(view["run_dir"])
            self.assertEqual(self.project / ".autocode" / "worktrees", Path(view["workspace"]).parent)
            self.assertNotEqual(run.run_dir.resolve(), moved.resolve(), "the run plans in its own worktree")
            self.assertFalse((moved / "model-preflight").exists(), "the worktree probes nothing again")
            attempts = view["usage"]["accounting"]["attempts"]
            self.assertEqual(["demo/builder"], [row["model"] for row in attempts if row["stage"] == "model_preflight"])
        calls = [json.loads(line)["args"] for line in log.read_text().splitlines()]
        launches = [args for args in calls if args[:1] == ["exec"]]
        probe_models = []
        requirements = []
        for args in launches:
            self.assertEqual("high", args[args.index("--variant") + 1])
            report = args[args.index("-o") + 1]
            if "model-preflight" in report:
                probe_models.append(args[args.index("--model") + 1])
            elif "requirements-gather" in report:
                requirements.append(report)
        self.assertEqual(2 if repaired else 1, len(requirements), requirements)
        if repaired:
            self.assertTrue(any("report-repair" in name for name in requirements), requirements)
            archived = list(
                run.run_dir.glob(f"iterations/*/archived-requirements-gather-*/{Path(requirements[0]).name}")
            )
            self.assertEqual(1, len(archived), "The exact rejected report must remain retained")
            self.assertNotIn("category", json.loads(archived[0].read_text())["proposed_assumptions"][0])
        self.assertEqual(["demo/builder"], probe_models, "Requirements already paid for demo/reviewer access")
        receipts = json.loads((run.run_dir / "model-preflight.json").read_text())["receipts"]
        reused = [row["probed"].get("demo/reviewer", {}) for row in receipts]
        if fresh:
            self.assertFalse(any(reused), "Deferral must not fabricate a successful receipt")
            self.assertIn("model-preflight", launches[0][launches[0].index("-o") + 1])
            attempts = view["usage"]["accounting"]["attempts"]
            real = [row for row in attempts if row["stage"] == "requirements_gather"]
            self.assertEqual(1, len(real), real)
            self.assertEqual(
                ("demo/reviewer", "high", 1 if failure else 0),
                (real[0]["model"], real[0]["reasoning_effort"], real[0]["exit_code"]),
            )
            self.assertTrue(real[0]["finished_at"])
            self.assertGreater(real[0]["duration_seconds"], 0)
            probes = [row for row in attempts if row["stage"] == "model_preflight"]
            self.assertEqual(["demo/builder"], [row["model"] for row in probes])
            self.assertFalse(any(row["active"] or row["rejected"] for row in probes))
            if failure:
                self.assertEqual(2, len(launches), "A failed Requirements request must stop before any Planner")
                self.assertEqual(
                    {"model_preflight", "requirements_gather"},
                    {row["stage"] for row in attempts if not row["runner_owned"]},
                )
            else:
                self.assertFalse(real[0]["active"] or real[0]["rejected"])
        else:
            self.assertTrue(any(row.get("source") == "completed_stage" for row in reused), reused)
        if repaired:
            self.assertTrue(any(row.get("stage") == "requirements_gather_report_repair" for row in reused), reused)
