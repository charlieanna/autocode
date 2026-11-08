"""A provider config moved with its checkout keeps the run going and its charged access evidence.

The same bytes at a new path are the same transport (#2773): the run continues with no pause and
no person, and spends no new access check. Different bytes are a transport change: the run pauses,
the change is accepted publicly, and access is checked again.
"""

import hashlib
import json
import shutil
import sys
import unittest
from pathlib import Path

import autocode_transport_relocation as relocation
from autocode_taskrun import TaskRun

from tests import test_taskrun as fixtures

HERE = Path(__file__).resolve().parents[1]

WRAPPER = """import os,sys
from pathlib import Path
sys.path.insert(0,os.environ['RELOCATION_RUNTIME']+'/tools')
import autocode
from providers import command
original=command.bundled_config_path
def bundled(name):
 if name=='fixture_command':return Path(Path(os.environ['RELOCATION_CONFIG_POINTER']).read_text())
 return original(name)
command.bundled_config_path=bundled
raise SystemExit(autocode.main())
"""

PROBE = """if 'CURRENT HANDOFF DATA\\n' not in prompt:
    import hashlib
    model = sys.argv[sys.argv.index('--model') + 1]
    effort_flag = '--variant' if '--variant' in sys.argv else '--effort'
    config = Path(Path(os.environ['RELOCATION_CONFIG_POINTER']).read_text())
    record({'operation': 'preflight', 'model': model,
            'effort': sys.argv[sys.argv.index(effort_flag) + 1],
            'config_sha256': hashlib.sha256(config.read_bytes()).hexdigest()})
    session = 'access_' + uuid.uuid4().hex
    for row in [
        {'type': 'step_start', 'sessionID': session,
         'part': {'id': 'start', 'messageID': 'probe'}},
        {'type': 'text', 'sessionID': session,
         'part': {'id': 'report', 'messageID': 'probe', 'text': '{"ok": true}'}},
        {'type': 'step_finish', 'sessionID': session,
         'part': {'id': 'finish', 'messageID': 'probe', 'reason': 'stop', 'cost': 0.02,
                  'tokens': {'input': 17, 'output': 3, 'reasoning': 0,
                             'cache': {'read': 0, 'write': 0}}}},
    ]:
        print(json.dumps(row), flush=True)
    raise SystemExit(0)
"""


class PreflightRelocationCLI(unittest.TestCase):
    operations = fixtures.MixedRoleTransportTaskRunTests.operations

    def setUp(self):
        fixtures.MixedRoleTransportTaskRunTests.setUp(self)
        self.start_options = ()
        options = [value for value in self.options if value != "--allow-uncontained-tools"]
        options[options.index("--provider") + 1] = "fixture_command"
        self.options = (*options, "--max-cost-usd", "0")
        self.env["RELOCATION_RUNTIME"] = str(HERE)
        self.pointer = self.root / "config-location"
        self.env["RELOCATION_CONFIG_POINTER"] = str(self.pointer)
        self.wrapper = self.root / "cli.py"
        self.wrapper.write_text(WRAPPER)
        self.old_config = self.root / "runtime-one" / "fixture_command.toml"
        self.old_config.parent.mkdir()
        shutil.move(self.config, self.old_config)
        with self.old_config.open("a") as stream:
            stream.write('\n[auth]\ncommand = ["fixture_command", "auth", "list"]\n')
            for prefix in ("fixture-", "zai-coding-plan/", "xiaomi-token-plan-sgp/"):
                stream.write(
                    "\n[[auth.routes]]\n"
                    f'models = "{prefix}"\nname = "Fixture"\nbilling = "subscription"\n'
                    'pattern = "Anthropic (api)"\nexpect = "api"\n'
                )
        self.pointer.write_text(str(self.old_config))
        executable = self.root / "bin" / "fixture_command"
        script = executable.read_text()
        start = script.index("models = (")
        end = script.index("\ndef record(", start)
        script = (
            script[:start]
            + (
                "models = ['fixture-claude', 'zai-coding-plan/glm-5.3-flash', "
                "'zai-coding-plan/glm-5.3', 'xiaomi-token-plan-sgp/mimo-v2.6-pro']"
            )
            + script[end:]
        )
        script = script.replace("prompt = sys.stdin.read()\n", "prompt = sys.stdin.read()\n" + PROBE, 1)
        executable.write_text(script)

    @staticmethod
    def attempts(view):
        return [row for row in view["usage"]["accounting"]["attempts"] if row["stage"] == "model_preflight"]

    def exercise(self, *, changed=False):
        run = TaskRun.start(
            self.workspace,
            fixtures.BRIEF,
            command=(sys.executable, "-B", str(self.wrapper)),
            options=self.options,
            env=self.env,
            timeout=300,
        )
        before = run.status()
        self.assertEqual("approve_plan", before["needs"]["kind"], before)
        self.assertIn("Greet", run.show_goal())
        run.approve_plan(before["needs"]["token"])
        approved = run.status()
        # Resume one ordinary Planner stage after acceptance, then stop before Builder.
        run.options = (*run.options, "--pause-after-stage")
        probes = self.operations("preflight")
        initial_models = {"fixture-claude", "zai-coding-plan/glm-5.3", "xiaomi-token-plan-sgp/mimo-v2.6-pro"}
        self.assertCountEqual(initial_models, [row["model"] for row in probes])
        self.assertEqual({("fixture_command", "high")}, {(row["tool"], row["effort"]) for row in probes})
        self.assertEqual(
            {hashlib.sha256(self.old_config.read_bytes()).hexdigest()}, {row["config_sha256"] for row in probes}
        )
        attempts = self.attempts(before)
        self.assertEqual(len(probes), len(attempts))
        self.assertTrue(all(row["tokens"]["input_tokens"] == 17 for row in attempts), attempts)
        evidence = {row["events"]: Path(row["events"]).read_bytes() for row in attempts}
        usage = before["usage"]["by_role"]["model_preflight"]
        self.assertEqual(len(probes), usage["stages"])
        self.assertGreater(usage["input_tokens"], 0)
        new_config = self.root / "runtime-two" / "fixture_command.toml"
        new_config.parent.mkdir()
        shutil.copyfile(self.old_config, new_config)
        if changed:
            with new_config.open("a") as stream:
                stream.write("\n# Deliberately different bytes require a new access check.\n")
        else:
            self.assertEqual(self.old_config.read_bytes(), new_config.read_bytes())
        self.pointer.write_text(str(new_config))
        if changed:
            held = run.advance()
            self.assertEqual("PAUSED_TRANSPORT_CHANGED", held["status"], held)
            self.assertIn("--accept-transport-change", held["next_command"])
            self.assertEqual(probes, self.operations("preflight"))
            self.assertEqual("PAUSED_TRANSPORT_CHANGED", run.resume_paused()["status"])
            self.assertEqual(probes, self.operations("preflight"))
            after = run.accept_transport_change()
        else:
            after = run.advance()
            self.assertNotIn("--accept-transport-change", after.get("next_command") or "")
            # The decision is recorded once, as metadata; the saved identity keeps its bound path.
            state = json.loads((Path(after["run_dir"]) / "state.json").read_text())
            moves = [row for row in state["configuration_changes"] if row.get("reason") == relocation.REASON]
            self.assertEqual(
                [(str(self.old_config), str(new_config))],
                [(row["saved_config_path"], row["config_path"]) for row in moves],
            )
            self.assertEqual(str(self.old_config), state["settings"]["transport_identity"]["config_path"])
            self.assertEqual(
                state["settings"]["transport_identity"], state["settings"]["transport_identities"]["opencode"]
            )
        self.assertEqual("PAUSED_REQUESTED", after["status"], after)
        self.assertEqual(approved["approved_contract"], after["approved_contract"])
        self.assertEqual(before["routes"], after["routes"])
        self.assertEqual(before["cost"]["cap_usd"], after["cost"]["cap_usd"])
        self.assertFalse(any(row["stage"] == "terra" for row in self.operations("launch")))
        current = self.attempts(after)
        self.assertEqual(attempts, current[: len(attempts)])
        self.assertEqual(evidence, {path: Path(path).read_bytes() for path in evidence})
        if changed:
            fresh = self.operations("preflight")[len(probes) :]
            # Requirements replaced its own fresh ping. After a byte change its
            # completed call proves only the old transport, so all four pairs
            # need a new probe on the accepted connection.
            fresh_models = initial_models | {"zai-coding-plan/glm-5.3-flash"}
            self.assertCountEqual(fresh_models, [row["model"] for row in fresh])
            self.assertEqual({("fixture_command", "high")}, {(row["tool"], row["effort"]) for row in fresh})
            self.assertEqual(
                {hashlib.sha256(new_config.read_bytes()).hexdigest()}, {row["config_sha256"] for row in fresh}
            )
            self.assertTrue({row["config_sha256"] for row in probes}.isdisjoint(row["config_sha256"] for row in fresh))
            fresh_attempts = current[len(attempts) :]
            self.assertCountEqual(fresh_models, [row["model"] for row in fresh_attempts])
            self.assertTrue(all(row["launch_route"]["effort"] == "high" for row in fresh_attempts))
            self.assertTrue(all(row["launch_route"]["provider"] == "fixture_command" for row in fresh_attempts))
            self.assertEqual(len(fresh), len(fresh_attempts))
            self.assertTrue(all(row["tokens"]["input_tokens"] == 17 for row in fresh_attempts))
            self.assertEqual(
                usage["input_tokens"] + 17 * len(fresh_attempts),
                after["usage"]["by_role"]["model_preflight"]["input_tokens"],
            )
        else:
            self.assertEqual(probes, self.operations("preflight"))
            self.assertEqual(attempts, current)
            self.assertEqual(usage, after["usage"]["by_role"]["model_preflight"])

    def test_relocation_continues_without_a_pause_and_reuses_charged_access_attempts(self):
        self.exercise()

    def test_changed_toml_bytes_require_fresh_access_attempts(self):
        self.exercise(changed=True)
