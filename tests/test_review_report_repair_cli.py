"""A source-bound Tester edit completes through the CLI without recopying checks."""

import json
import os
import sys
import unittest
from pathlib import Path

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
array_case = os.environ.get("REVIEW_REPAIR_ARRAY") == "1"
completion_case = os.environ.get("REVIEW_REPAIR_COMPLETION") == "1"
stage = data["original"]["stage"] if repair else data["stage"]
target_stage = "astra_review" if completion_case else "sol"
output = Path(sys.argv[sys.argv.index("-o") + 1])
if repair and stage == target_stage:
    source = data["rejected_report"]
    schema = json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
    offered = "repair_report" in schema.get("properties", {})
    target = ["checks", len(source["content"]["checks"]) - 1] if array_case else ["extraneous_field"]
    report = {"repair_report": {"sha256": source["sha256"], "edits": [
        {"op": "remove", "path": target, "value_json": "null"}
    ]}} if offered else source["content"]
    if completion_case:
        report = dict(source["content"])
        report.pop("extraneous_field", None)
        if offered:
            report = {"repair_report": report}
    output.write_text(json.dumps(report))
    with Path(os.environ["REVIEW_REPAIR_TRACE"]).open("a") as stream:
        stream.write(json.dumps({"offered": offered, "patch": report, "output": str(output),
                                 "checks": source["content"]["checks"][:-1] if array_case else source["content"].get("checks", [])}) + "\n")
    print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}))
    print(json.dumps({"type": "turn.completed"}))
else:
    sys.stdin = io.StringIO(prompt)
    try:
        runpy.run_path(str(delegate), run_name="__main__")
    except SystemExit as error:
        if error.code not in (None, 0):
            raise
    if stage == target_stage and not repair:
        report = json.loads(output.read_text())
        if array_case:
            report["checks"].append({"unexpected": "Only this extra item needs removal."})
        else:
            report["extraneous_field"] = "Only this schema defect needs correction."
        output.write_text(json.dumps(report))
"""


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised provider requires supported Linux")
class ReviewReportRepairCLI(unittest.TestCase):
    def test_tester_patch_completes_with_original_checks_and_preserved_raw_response(self):
        self.assert_review_patch_completes()

    def test_tester_removes_one_extra_array_item_without_recopying_authenticated_checks(self):
        row = self.assert_review_patch_completes(array_case=True)
        self.assertEqual(["checks", len(row["checks"])], row["patch"]["repair_report"]["edits"][0]["path"])

    def test_full_completion_repair_keeps_exact_approved_criterion_text(self):
        row = self.assert_review_patch_completes(completion_case=True)
        self.assertTrue(all(item["criterion"] for item in row["patch"]["repair_report"]["acceptance_criteria"]))

    def assert_review_patch_completes(self, *, array_case=False, completion_case=False):
        fixture = cli_support.SubprocessFlow()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        executable = fixture.root / "fixture-bin/codex"
        executable.rename(executable.with_name("ordinary_fake_codex.py"))
        executable.write_text(f"#!{sys.executable}\n" + WRAPPER)
        executable.chmod(0o755)
        trace = fixture.root / "review-repair-trace.jsonl"
        fixture.env.update(AUTOCODE_FIXTURE_MODE="no-human", REVIEW_REPAIR_TRACE=str(trace))
        if array_case:
            fixture.env["REVIEW_REPAIR_ARRAY"] = "1"
        if completion_case:
            fixture.env["REVIEW_REPAIR_COMPLETION"] = "1"
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
        row = rows[0]
        self.assertTrue(row["offered"])
        report = Path(row["output"])
        repaired = json.loads(report.read_text())
        self.assertEqual(row["checks"], repaired.get("checks", []))
        self.assertNotIn("extraneous_field", repaired)
        self.assertEqual(row["patch"], json.loads(report.with_suffix(".reported.json").read_text()))
        return row
