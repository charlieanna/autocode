"""A rejected report preserves the level chosen through the CLI."""

import json
import sys
import unittest

from . import test_subprocess as cli_support

WRAPPER = r"""
import io, json, os, runpy, sys, uuid
from pathlib import Path
delegate = Path(__file__).with_name("ordinary_fake_codex.py")
if sys.argv[1:] == ["login", "status"]:
    os.execv(sys.executable, [sys.executable, str(delegate), *sys.argv[1:]])
prompt = sys.stdin.read()
data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
repair = bool(data.get("report_repair"))
stage = data["original"]["stage"] if repair else data["stage"]
output = Path(sys.argv[sys.argv.index("-o") + 1])
if repair and stage == "tester":
    session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
    # A real rejected report remains rejected after both bounded format repairs.
    output.write_text(json.dumps({"repair_report": data["rejected_report"]["content"]}))
    print(json.dumps({"type": "thread.started", "thread_id": session}))
    print(json.dumps({"type": "turn.completed"}))
else:
    sys.stdin = io.StringIO(prompt)
    try:
        runpy.run_path(str(delegate), run_name="__main__")
    except SystemExit as error:
        if error.code not in (None, 0):
            raise
    if stage == "tester":
        report = json.loads(output.read_text())
        report["extraneous_field"] = "Deliberately invalid scripted report"
        output.write_text(json.dumps(report))
if stage == "tester":
    with Path(os.environ["EXPLICIT_EFFORT_TRACE"]).open("a") as stream:
        stream.write(json.dumps({"stage": stage, "report_only": repair}) + "\n")
"""


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised provider requires supported Linux")
class ExplicitRetryEffortCLI(unittest.TestCase):
    def test_exhausted_report_repair_keeps_the_explicit_tester_level_and_all_attempts(self):
        fixture = cli_support.SubprocessFlow()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        executable = fixture.root / "fixture-bin/codex"
        executable.rename(executable.with_name("ordinary_fake_codex.py"))
        executable.write_text(f"#!{sys.executable}\n" + WRAPPER)
        executable.chmod(0o755)
        trace = fixture.root / "tester-attempts.jsonl"
        fixture.env.update(AUTOCODE_FIXTURE_MODE="no-human", EXPLICIT_EFFORT_TRACE=str(trace))
        fixture.launch(
            ["Build a greeting tool", "--chat", "--tester-model", "gpt-6-sol", "--tester-reasoning-effort", "high"],
            2,
            answers="CLI\nyes\n",
        )
        run = next((fixture.project / ".autocode/runs").iterdir())
        status = json.loads(fixture.launch(["--run-dir", str(run), "--status"], 0).stdout)
        self.assertEqual("PAUSED_INVALID_OUTPUT", status["status"])
        self.assertEqual("high", status["view"]["routes"]["tester"]["reasoning_effort"])
        self.assertEqual("user_explicit", status["settings"]["effort_origins"]["tester"])
        self.assertEqual([], [row for row in status.get("reasoning_escalations", []) if row["role"] == "tester"])
        self.assertEqual(
            [{"stage": "tester", "report_only": value} for value in (False, True, True)],
            [json.loads(line) for line in trace.read_text().splitlines()],
        )
        self.assertFalse(status["completion_current"])
