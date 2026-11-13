"""An indented but content-bounded Tester report repairs through the public CLI."""

import json
import os
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
    source = data["rejected_report"]
    report = source["content"]
    with Path(os.environ["FORMATTING_REPAIR_TRACE"]).open("a") as stream:
        stream.write(json.dumps({"source": source, "output": str(output)}) + "\n")
    report.pop("extraneous_field")
    output.write_text(json.dumps(report))
    session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
    print(json.dumps({"type": "thread.started", "thread_id": session}))
    print(json.dumps({"type": "turn.completed"}))
else:
    sys.stdin = io.StringIO(prompt)
    try:
        runpy.run_path(str(delegate), run_name="__main__")
    except SystemExit as error:
        if error.code not in (None, 0):
            raise
    if stage == "tester" and not repair:
        report = json.loads(output.read_text())
        report["extraneous_field"] = [{"line": i, "reason": "retained"} for i in range(2200)]
        output.write_text(json.dumps(report, indent=2))
"""


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised provider requires supported Linux")
class ReportFormattingRepairCLI(unittest.TestCase):
    def test_indented_report_repairs_without_losing_checks_or_changing_saved_source(self):
        from hashlib import sha256
        from pathlib import Path

        fixture = cli_support.SubprocessFlow()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        executable = fixture.root / "fixture-bin/codex"
        executable.rename(executable.with_name("ordinary_fake_codex.py"))
        executable.write_text(f"#!{sys.executable}\n" + WRAPPER)
        executable.chmod(0o755)
        trace = fixture.root / "formatting-repair-trace.jsonl"
        fixture.env.update(AUTOCODE_FIXTURE_MODE="no-human", FORMATTING_REPAIR_TRACE=str(trace))
        fixture.env["PATH"] = (
            str(executable.parent) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + fixture.env["PATH"]
        )
        fixture.launch(["Build a greeting tool", "--chat"], 0, answers="CLI\nyes\n")
        run = next((fixture.project / ".autocode/runs").iterdir())
        status = json.loads(fixture.launch(["--run-dir", str(run), "--status"], 0).stdout)
        self.assertEqual("TASK_COMPLETE", status["status"])
        self.assertTrue(status["completion_current"])
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual(1, len(rows))
        source = rows[0]["source"]
        raw = Path(source["path"]).read_bytes()
        self.assertGreater(len(raw), 128 * 1024)
        self.assertLess(len(json.dumps(source["content"], separators=(",", ":")).encode()), 128 * 1024)
        self.assertEqual(source["bytes"], len(raw))
        self.assertEqual(source["sha256"], sha256(raw).hexdigest())
        self.assertEqual(source["content"], json.loads(raw))
        repaired = json.loads(Path(rows[0]["output"]).read_text())
        self.assertEqual(source["content"]["checks"], repaired["checks"])
        self.assertNotIn("extraneous_field", repaired)
