"""The public CLI admits every selected tool before the first model request.

Only the Tester uses Kilo. Its native default agent can read files but leaves
bash at ask/deny, so an ordinary Requirements provider must not launch first.
The fixture implements Kilo's model-free native inspection; it grants no host
admission exemption and never contacts a model service.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests import test_subprocess as subprocess_test_support

KILO = r"""import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
trace = Path(os.environ["AUTOCODE_KILO_READINESS_TRACE"])
operation = " ".join(args[:3]) if args[:1] == ["debug"] else " ".join(args[:2])
with trace.open("a") as stream:
    stream.write(json.dumps({"operation": operation, "model_request": args[:1] == ["run"]}) + "\n")
if args == ["--version"]:
    print("7.8.3")
elif args[:1] == ["models"]:
    print("fixture/checker")
    if "--verbose" in args:
        print(json.dumps({"variants": {name: {} for name in ("low", "medium", "high", "max")}}))
elif args == ["debug", "config"]:
    print(json.dumps({"default_agent": "verification"}))
elif args[:2] == ["debug", "agent"]:
    rules = [{"permission": "*", "pattern": "*", "action": "allow"}]
    if args[2] == "verification":
        rules.append({"permission": "bash", "pattern": "*", "action": os.environ["AUTOCODE_KILO_BASH_ACTION"]})
    print(json.dumps({"permission": rules, "prompt": "Independently test the approved change."}))
else:
    raise SystemExit("A Kilo model request must never start before tool readiness is established")
"""


@unittest.skipUnless(sys.platform == "linux", "Requires the supported Linux provider boundary")
class KiloToolReadinessCliTests(unittest.TestCase):
    setUp = subprocess_test_support.SubprocessFlow.setUp

    def check_refusal(self, action):
        trace = self.root / "kilo-operations.jsonl"
        primary_requests = self.root / "primary-model-requests.jsonl"
        self.env.update(
            AUTOCODE_KILO_READINESS_TRACE=str(trace),
            AUTOCODE_KILO_BASH_ACTION=action,
            AUTOCODE_PRIMARY_READINESS_TRACE=str(primary_requests),
        )
        # A configured Codex-shaped provider keeps the mixed-role public interface:
        # --engine codex itself deliberately cannot bind an external role transport.
        codex = self.root / "fixture-bin" / "codex"
        source = codex.read_text()
        marker = "prompt = sys.stdin.read()"
        self.assertEqual(1, source.count(marker))
        source = source.replace(
            marker,
            'with Path(os.environ["AUTOCODE_PRIMARY_READINESS_TRACE"]).open("a") as stream:\n'
            '    stream.write(json.dumps({"model_request": True}) + "\\n")\n' + marker,
        )
        # The copied small provider predates conversational Requirements. Supply
        # its actual blocking question in the chat field the selected schema asks for.
        next_stage = 'elif stage == "draft_plan":'
        self.assertEqual(1, source.count(next_stage))
        source = source.replace(
            next_stage,
            '    if "assistant_reply" in schema.get("properties", {}):\n'
            '        result["assistant_reply"] = result["open_questions"][0]["question"]\n' + next_stage,
        )
        codex.write_text("#!" + sys.executable + "\n" + source.split("\n", 1)[1])
        kilo = self.root / "fixture-bin" / "kilo"
        kilo.write_text("#!" + sys.executable + "\n" + KILO)
        kilo.chmod(0o755)

        providers = Path(self.env["XDG_CONFIG_HOME"]) / "autocode/providers"
        providers.mkdir(parents=True)
        roles = ("task_planner", "builder", "tester", "completion", "planner", "plan_reviewer", "requirements")
        command = [str(codex), "--output-schema", "{schema}", "-o", "{report}", "--model", "{model}", "-"]
        (providers / "fixture_codex.toml").write_text(
            'name = "fixture_codex"\nprompt = "stdin"\noutput = "report_file"\ncommand = '
            + json.dumps(command)
            + "\nmodels = "
            + json.dumps(["fixture/" + role for role in roles])
            + "\n[roles]\n"
            + "\n".join(role + ' = {model="fixture/' + role + '", effort="medium"}' for role in roles)
            + "\n"
        )
        (providers / "kilocode.toml").write_text(
            'name = "kilocode"\nprompt = "stdin"\noutput = "opencode_events"\n'
            'resume = ["--session", "{session}"]\ncommand = '
            + json.dumps(
                [str(kilo), "run", "--auto", "--model", "{model}", "--variant", "{effort}", "--format", "json"]
            )
            + "\nversion_command = "
            + json.dumps([str(kilo), "--version"])
            + '\nmodels = ["fixture/checker"]\n[roles]\n'
            + "\n".join(role + ' = {model="fixture/checker", effort="medium"}' for role in roles)
            + "\n"
        )
        run = self.project / ".autocode/runs/tool-readiness"
        command = [
            *self.entry,
            "Build a greeting CLI",
            "--provider",
            "fixture_codex",
            "--role-transport",
            "tester=kilocode",
            "--tester-model",
            "fixture/checker",
            "--tester-reasoning-effort",
            "medium",
            "--no-auto-select",
            "--spec-first",
            "--joint-planning",
            "--in-place",
            "--no-chat",
            "--max-seconds",
            "120",
            "--max-stage-seconds",
            "30",
            "--max-idle-seconds",
            "15",
            "--max-cost-usd",
            "0.25",
            "--max-planning-calls",
            "7",
            "--workspace",
            str(self.project),
            "--new-run-dir",
            str(run),
            "--evidence-provenance",
            "fake",
        ]
        # A task-stage keeper may retain inherited output after the CLI exits.
        # Files preserve that evidence without making EOF a second completion gate.
        out = self.root / "start.stdout"
        err = self.root / "start.stderr"
        with out.open("w") as stdout, err.open("w") as stderr:
            result = subprocess.run(command, cwd=self.root, env=self.env, stdout=stdout, stderr=stderr, timeout=120)
        self.assertEqual(2, result.returncode, out.read_text() + err.read_text())
        status = subprocess.run(
            [*self.entry, "--status", "--workspace", str(self.project), "--run-dir", str(run)],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        envelope = json.loads(status.stdout)
        (self.root / "public-status.json").write_text(status.stdout)
        view = envelope["view"]
        self.assertEqual("PAUSED_TOOL_CONTAINMENT", view["status"], view.get("stop_reason"))
        self.assertFalse(primary_requests.exists(), "Requirements must not spend before a later tool is admitted")
        operations = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertFalse(any(row["model_request"] for row in operations), operations)
        self.assertIn("debug agent verification", [row["operation"] for row in operations])
        self.assertEqual([], [row for row in view["usage"]["accounting"]["attempts"] if not row["runner_owned"]])
        self.assertFalse(envelope.get("pending_questions"), "A shell-less Tester is not a permission question")
        self.assertNotEqual("answer", (view.get("needs") or {}).get("kind"))
        self.assertFalse(view["done"])
        self.assertIn("kilo", view["stop_reason"].lower())

        settings = envelope["settings"]
        self.assertEqual("fixture_codex", settings["provider"])
        self.assertEqual("fixture/requirements", settings["roles"]["requirements"]["model"])
        self.assertEqual("fixture/planner", settings["roles"]["planner"]["model"])
        self.assertEqual("fixture/builder", settings["roles"]["builder"]["model"])
        tester = settings["roles"]["tester"]
        self.assertEqual(
            ("kilocode", "fixture/checker", "medium"), (tester["provider"], tester["model"], tester["reasoning_effort"])
        )
        limits = settings["limits"]
        self.assertEqual(
            (120, 30, 15, 0.25),
            tuple(
                limits[key] for key in ("max_seconds", "stage_timeout_seconds", "idle_timeout_seconds", "max_cost_usd")
            ),
        )
        self.assertEqual(7, limits["planning_calls"])

    def test_a_tester_whose_native_shell_asks_stops_before_any_model_request(self):
        self.check_refusal("ask")

    def test_a_tester_whose_native_shell_is_denied_stops_before_any_model_request(self):
        self.check_refusal("deny")


if __name__ == "__main__":
    unittest.main()
