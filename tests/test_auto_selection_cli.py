"""Default selection through the public TaskRun/CLI boundary, with local fake tools."""

import unittest

import autocode_taskrun as taskrun

from . import test_taskrun as fixtures

MODELS = [
    "zai-coding-plan/glm-5.3",
    "xiaomi-token-plan-sgp/mimo-v2.6-pro",
    "openai/gpt-6-sol",
    "openai/gpt-6-astra",
]


class AutomaticSelectionCLI(unittest.TestCase):
    def setUp(self):
        fixtures.MixedRoleTransportTaskRunTests.setUp(self)
        self.options = (
            "--evidence-provenance",
            "fake",
            "--provider",
            "opencode",
            "--no-adaptive-planning",
            "--max-parallel-builders",
            "1",
            "--allow-uncontained-tools",
        )
        self.write_tool()

    def write_tool(
        self, models=MODELS, auth="● Z.AI Coding Plan api\n● Xiaomi Token Plan (Singapore) api\n● OpenAI oauth"
    ):
        script = fixtures._MIXED_TRANSPORT_FIXTURE.replace("__LOG__", str(self.log)).replace(
            "__FIXTURE__", str(fixtures.HERE / "live_fixture_provider.py")
        )
        # Selection tests use the qualified version; the shared fixture keeps its version-refusal control.
        script = script.replace("print('1.16.0')", "print('1.18.33')")
        begin = script.index("models = (")
        end = script.index("def record", begin)
        script = script[:begin] + "models = " + repr(models) + "\n" + script[end:]
        script = script.replace("if sys.argv[1:] == ['models']:", "if sys.argv[1:2] == ['models']:")
        script = script.replace(
            "print('\\n'.join(models))",
            "print('\\n'.join(m + ('\\n' + json.dumps({'variants': {}}) if '--verbose' in sys.argv else '') for m in models))",
        )
        script = script.replace(
            "'operation': 'models', 'models': models",
            "'operation': 'models', 'models': models, 'metadata': '--verbose' in sys.argv",
        )
        script = script.replace("print('● Anthropic api')", "print(" + repr(auth) + ")")
        script = script.replace(
            "data = json.loads(prompt.split",
            """if 'CURRENT HANDOFF DATA\\n' not in prompt:
    record({'operation': 'probe', 'argv': sys.argv[1:]})
    print(json.dumps({'type': 'text', 'part': {'text': 'OK'}}))
    print(json.dumps({'type': 'step_finish', 'part': {'reason': 'stop', 'tokens': {'input': 1, 'output': 1}}}))
    raise SystemExit(0)
data = json.loads(prompt.split""",
        )
        script = script.replace(
            "effort = sys.argv[sys.argv.index(effort_flag) + 1]",
            "effort = sys.argv[sys.argv.index(effort_flag) + 1] if effort_flag in sys.argv else None",
        )
        (self.root / "bin/opencode").write_text(script)
        (self.root / "bin/fixture_command").write_text(
            script.replace("models = " + repr(models), "models = ['fixture-claude']")
        )

    def operations(self, kind=None):
        return fixtures.MixedRoleTransportTaskRunTests.operations(self, kind)

    def start(self, extra=()):
        return taskrun.TaskRun.start(
            self.workspace, fixtures.BRIEF, options=self.options, start_options=extra, env=self.env, timeout=300
        )

    def test_default_start_resume_and_completion_without_model_flags(self):
        run = self.start()
        before = run.status()
        self.assertEqual("approve_plan", before["needs"]["kind"], before)
        routes = before["routes"]
        self.assertEqual("auto_selected", routes["terra"]["origin"])
        # No Luna in this catalogue, so the Builder is GPT-6 Sol (#1729). The fixture advertises
        # no reasoning levels ({"variants": {}}), so the table's medium is vetoed: the model's own default.
        self.assertEqual("openai/gpt-6-sol", routes["terra"]["model"])
        self.assertEqual("zai-coding-plan/glm-5.3", routes["sol"]["model"])
        self.assertIsNone(routes["terra"]["reasoning_effort"])
        self.assertTrue(self.operations("probe"))
        # Changing the local catalogue cannot reselect a saved run.
        listings = sum(row.get("metadata", False) for row in self.operations("models"))
        self.write_tool(models=MODELS + ["unused/new-model"])
        run.approve_plan(before["needs"]["token"])
        held = run.advance_until_input()
        self.assertEqual("answer", held["needs"]["kind"], held)
        run.answer(held["needs"]["questions"][0]["id"], "Allow")
        result = run.advance_until_input()
        self.assertEqual("TASK_COMPLETE", result["status"], result)
        self.assertEqual(routes, result["routes"])
        self.assertEqual(listings, sum(row.get("metadata", False) for row in self.operations("models")))
        self.assertTrue((self.workspace / "greet.py").is_file())

    def test_explicit_model_and_tool_win_over_default_selection(self):
        run = self.start(("--sol-model", "fixture-claude", "--role-transport", "sol=fixture_command"))
        shown = run.status()
        self.assertEqual("approve_plan", shown["needs"]["kind"], shown)
        self.assertEqual("fixture-claude", shown["routes"]["sol"]["model"])
        probes = self.operations("probe")
        self.assertTrue(any(row["tool"] == "fixture_command" and "fixture-claude" in row["argv"] for row in probes))
        self.assertFalse(any(row["tool"] == "opencode" and "fixture-claude" in row["argv"] for row in probes))

    def test_explicit_selection_without_checker_refuses_before_spend(self):
        self.write_tool(models=[MODELS[0]], auth="● Z.AI Coding Plan api")
        with self.assertRaises(taskrun.TaskRunError) as caught:
            self.start(("--auto-select",))
        self.assertIn("connect a second model family", str(caught.exception).lower())
        self.assertFalse(self.operations("launch"))
        self.assertFalse(self.operations("probe"))

    def test_missing_accounts_refuse_before_spend(self):
        self.write_tool(auth="")
        with self.assertRaises(taskrun.TaskRunError):
            self.start(("--auto-select",))
        self.assertFalse(self.operations("launch"))
        self.assertFalse(self.operations("probe"))
