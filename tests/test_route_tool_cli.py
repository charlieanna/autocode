"""A job whose quota ran out moves to another connected tool when the person names a model there.

Real CLI processes, two configured command tools and the offline fixture provider. The run starts on
``tool_a``; its Plan Reviewer's quota runs out there. Before, the only answer was a model on the same
tool, so a run whose API credit ran out stopped although the same models were reachable through
another sign-in (2026-11-16). Owner decision 2026-11-16: at a quota stop only.
"""

import json
import sys
import unittest
from pathlib import Path

import autocode_quota_route as quota_route
from autocode_taskrun import TaskRun

from tests import test_subprocess as subprocess_test_support

PROVIDER = r"""
import json
import subprocess
import sys
from pathlib import Path

tool, report, delegate, log, model = sys.argv[1:6]
prompt = sys.stdin.read()
data = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
stage = data['original']['stage'] if data.get('report_repair') else data['stage']
with Path(log).open('a') as out:
    out.write(json.dumps({'tool': tool, 'stage': stage, 'model': model}) + '\n')
if tool == 'tool_a' and stage == 'review_plan':
    print(json.dumps({'type': 'error', 'error': {'message': 'Your API quota is exhausted', 'statusCode': 429}}))
    raise SystemExit(1)
result = subprocess.run(
    [sys.executable, delegate, 'exec', '-', '--json', '-o', report], input=prompt, capture_output=True, text=True
)
sys.stdout.write(result.stdout)
raise SystemExit(result.returncode)
"""

ROLES = ("task_planner", "builder", "tester", "completion", "planner", "plan_reviewer")


@unittest.skipUnless(sys.platform == "linux", "Requires the supported Linux provider boundary")
class RouteToolCliTests(unittest.TestCase):
    setUp = subprocess_test_support.SubprocessFlow.setUp

    def tool(self, name, models):
        source = Path(__file__).resolve().parents[1] / "tools"
        config = self.config_root / f"autocode/providers/{name}.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            str(self.root / "provider.py"),
            name,
            "{report}",
            str(source / "live_fixture_provider.py"),
            str(self.log),
            "{model}",
        ]
        config.write_text(
            f'name = "{name}"\noutput = "report_file"\nprompt = "stdin"\ncommand = '
            + json.dumps(command)
            + "\nmodels = "
            + json.dumps(sorted(models.values()))
            + "\n[roles]\n"
            + "\n".join(f'{role} = {{ model = "{model}", effort = "medium" }}' for role, model in models.items())
            + "\n"
        )

    def start(self):
        (self.root / "provider.py").write_text(PROVIDER)
        self.log = self.root / "calls.jsonl"
        self.config_root = self.root / "provider-config"
        self.tool("tool_a", {role: f"fixture/{role}" for role in ROLES})
        self.tool("tool_b", {role: f"other/{role}" for role in ROLES})
        from .test_taskrun import BRIEF

        source = Path(__file__).resolve().parents[1] / "tools"
        return TaskRun.start(
            self.project,
            BRIEF,
            command=[sys.executable, str(source / "autocode.py")],
            options=(
                "--provider",
                "tool_a",
                "--no-auto-select",
                "--joint-planning",
                "--evidence-provenance",
                "fake",
            ),
            env={**self.env, "XDG_CONFIG_HOME": str(self.config_root)},
            timeout=180,
            cwd=self.root,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def stopped(self):
        run = self.start()
        view = run.status()
        self.assertEqual("plan_reviewer", ((view["needs"] or {}).get("route") or {}).get("role"), view["stop_reason"])
        self.assertIn("TOOL:MODEL", view["needs"]["questions"][0]["why"])
        return run, view

    def saved(self, run):
        return json.loads((Path(run.run_dir) / "state.json").read_text())

    def test_a_quota_stop_moves_the_job_to_the_tool_the_person_names(self):
        run, _ = self.stopped()
        view = run.assign_model("plan_reviewer", "tool_b:other/plan_reviewer")
        self.assertEqual("other/plan_reviewer", view["routes"]["plan_reviewer"]["model"])
        view = run.resume_paused()
        self.assertEqual("approve_plan", (view["needs"] or {}).get("kind"), view["stop_reason"])
        run.approve_plan(view["needs"]["token"])
        run.advance_until_input()
        self.assertEqual(
            [{"from": "tool_a", "to": "tool_b"}], [row.get("tool") for row in run.status()["route_assignments"]]
        )
        calls = self.calls()
        stopped = calls.index({"tool": "tool_a", "stage": "review_plan", "model": "fixture/plan_reviewer"})
        reviews = [call for call in calls[stopped + 1 :] if call["stage"] in ("review_plan", "finalize_plan")]
        self.assertTrue(reviews)
        self.assertEqual({("tool_b", "other/plan_reviewer")}, {(call["tool"], call["model"]) for call in reviews})
        others = [call for call in calls[stopped + 1 :] if call["stage"] in ("builder", "tester")]
        self.assertEqual(["builder", "tester"], sorted({call["stage"] for call in others}))
        self.assertEqual({"tool_a"}, {call["tool"] for call in others}, "every other job stays where it was")
        state = self.saved(run)
        settings = state["settings"]
        self.assertEqual("tool_b", settings["roles"]["plan_reviewer"]["provider"])
        self.assertEqual("tool_b", settings["transport_identities"]["role:plan_reviewer"]["provider"])
        self.assertEqual("tool_a", settings["provider"])
        # A binding taken before the answer still holds after it: undoing the assignment puts the tool back.
        before = quota_route.unassigned(state, settings)
        self.assertEqual("fixture/plan_reviewer", before["roles"]["plan_reviewer"]["model"])
        self.assertNotEqual("tool_b", before["roles"]["plan_reviewer"].get("provider"))
        self.assertNotIn("role:plan_reviewer", before.get("transport_identities") or {})

    def test_a_tool_is_named_only_when_connected_and_offering_the_model(self):
        run, view = self.stopped()
        token = view["needs"]["resolver_token"]
        for answer, refusal in (
            ("missing_tool:other/plan_reviewer", "missing_tool"),
            ("tool_b:other/not_offered", "not_offered"),
        ):
            with self.subTest(answer=answer):
                with self.assertRaisesRegex(Exception, refusal):
                    run.answer("route-plan_reviewer", answer, resolver_token=token)
                self.assertEqual(token, run.status()["needs"]["resolver_token"], "the request stays open")
        self.assertNotIn("role:plan_reviewer", self.saved(run)["settings"].get("transport_identities") or {})


if __name__ == "__main__":
    unittest.main()
