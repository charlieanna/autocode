"""A real Requirements child replaces only its own paid access probe (#2345)."""

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

    def check_requirements_access(self, *, repaired):
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
"""
        wrapper.write_text(
            f"#!{sys.executable}\n"
            + WRAPPER.replace("    fixture = os.path.join", probe + "    fixture = os.path.join")
        )
        wrapper.chmod(0o755)
        provider_config(Path(self.env["XDG_CONFIG_HOME"]) / "autocode/providers/credentialfixture.toml", wrapper)
        log = self.root / "calls.jsonl"
        self.env.update(AUTOCODE_TEST_ENV_LOG=str(log), FIXTURE_API_KEY=CANARY, AUTOCODE_PASS_ENV="FIXTURE_API_KEY")
        options = (
            "--engine",
            "opencode",
            "--provider",
            "credentialfixture",
            "--joint-planning",
            "--spec-first",
            "--no-adaptive-planning",
            "--evidence-provenance",
            "fake",
            "--no-auto-select",
            "--plan-reviewer-model",
            "demo/builder",
        )
        run = taskrun.TaskRun.start(
            self.project, BRIEF, options=options, command=self.entry, env=self.env, timeout=180, cwd=self.root
        )
        view = run.advance_until_input()
        self.assertEqual("answer", view["needs"]["kind"], view.get("stop_reason"))
        question = view["needs"]["questions"][0]
        self.assertTrue(question["id"].startswith("requirements-review-"), question)
        self.assertFalse((run.run_dir / "model-preflight.json").exists())
        run.answer(question["id"], view["chat"]["review"]["answer"], resolver_token=view["needs"]["resolver_token"])
        view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view.get("stop_reason"))
        calls = [json.loads(line)["args"] for line in log.read_text().splitlines()]
        launches = [args for args in calls if args[:1] == ["exec"]]
        probe_models = []
        requirements = []
        for args in launches:
            report = args[args.index("-o") + 1]
            if "model-preflight" in report:
                probe_models.append(args[args.index("--model") + 1])
            elif "requirements-gather" in report:
                requirements.append(report)
        self.assertEqual(2 if repaired else 1, len(requirements), requirements)
        if repaired:
            self.assertTrue(any("report-repair" in name for name in requirements), requirements)
            archived = list(run.run_dir.glob("iterations/*/archived-requirements-gather-*/requirements-gather-*.json"))
            self.assertTrue(archived, "The rejected original must remain retained")
            self.assertNotIn("category", json.loads(archived[0].read_text())["proposed_assumptions"][0])
        self.assertEqual(["demo/builder"], probe_models, "Requirements already paid for demo/reviewer access")
        receipts = json.loads((run.run_dir / "model-preflight.json").read_text())["receipts"]
        reused = [row["probed"].get("demo/reviewer", {}) for row in receipts]
        self.assertTrue(any(row.get("source") == "completed_stage" for row in reused), reused)
        if repaired:
            self.assertTrue(any(row.get("stage") == "requirements_gather_report_repair" for row in reused), reused)
