"""Unit and integration tests for `autocode report-check` (#3344 step 3)."""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import autocode as runner
import autocode_report_check as report_check
import autocode_tool_containment as containment
import autocode_tool_handoff as tool_handoff

from . import test_autocode as base


def _schema_obj(required: list[str], properties: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _planning_contract(*, milestones: list[dict], criteria: list[dict]) -> dict:
    return {
        "version": 3,
        "goal_summary": "Add report preflight checking",
        "acceptance_criteria": criteria,
        "milestones": milestones,
        "open_blocking_questions": False,
    }


class ReportCheckUnitTests(unittest.TestCase):
    def test_invalid_json_and_schema_violation_are_reported(self):
        schema = _schema_obj(["summary"], {"summary": {"type": "string"}})
        spec = report_check.build_spec(stage="builder", workspace=Path("/tmp"), schema=schema)
        self.assertRegex(report_check.check_report("{not json", spec=spec)[0], "not valid JSON")
        self.assertTrue(report_check.check_report(json.dumps({}), spec=spec))
        self.assertEqual([], report_check.check_report(json.dumps({"summary": "ok"}), spec=spec))

    def test_requirements_gather_checks_quotes_checklist_and_coverage(self):
        schema = _schema_obj(
            ["requirements", "ignored_statements"],
            {
                "requirements": {
                    "type": "array",
                    "items": _schema_obj(
                        ["id", "source_quote"],
                        {"id": {"type": "string"}, "source_quote": {"type": "string"}},
                    ),
                },
                "ignored_statements": {"type": "array", "items": {"type": "string"}},
            },
        )
        state = {
            "task": "The CLI must emit JSON output.\nExit codes must stay backwards compatible.",
            "user_events": [],
            "brief_feedback": [],
        }
        spec = report_check.build_spec(stage="requirements_gather", workspace=Path("/tmp"), schema=schema, state=state)
        self.assertEqual(2, len(spec["coverage_checklist"]))

        bad_id = {"requirements": [{"id": "R1", "source_quote": "c99"}], "ignored_statements": []}
        self.assertTrue(any("c99" in err for err in report_check.check_report(json.dumps(bad_id), spec=spec)))

        ungrounded = {
            "requirements": [{"id": "R1", "source_quote": "Support XML format."}],
            "ignored_statements": [],
        }
        self.assertTrue(report_check.check_report(json.dumps(ungrounded), spec=spec))

        missing_cov = {
            "requirements": [{"id": "R1", "source_quote": "c1"}],
            "ignored_statements": [],
        }
        self.assertTrue(
            any("unaddressed" in err.lower() for err in report_check.check_report(json.dumps(missing_cov), spec=spec))
        )

        valid = {
            "requirements": [
                {"id": "R1", "source_quote": "c1"},
                {"id": "R2", "source_quote": "c2"},
            ],
            "ignored_statements": [],
        }
        self.assertEqual([], report_check.check_report(json.dumps(valid), spec=spec))

    def test_planning_checks_source_citations_dag_and_revise_concerns(self):
        schema = _schema_obj(
            ["contract", "responses"],
            {
                "contract": {"type": "object"},
                "responses": {"type": "array"},
            },
        )
        state = {
            "task": "Ship parser change.",
            "user_events": [],
            "brief_feedback": [],
            "planning": {
                "reports": {
                    "review_plan": {
                        "report": {
                            "concerns": [
                                {"id": "con-1", "concern": "Missing test plan"},
                            ]
                        }
                    }
                }
            },
        }
        spec = report_check.build_spec(stage="revise_plan", workspace=Path("/tmp"), schema=schema, state=state)
        src_id = spec["sources"][0]["id"]

        bad_cite = {
            "contract": _planning_contract(
                milestones=[{"id": "M1", "depends_on": [], "acceptance_criteria": ["C1"]}],
                criteria=[{"id": "C1", "sources": [{"source_id": src_id, "quote": f"{src_id}#99"}]}],
            ),
            "responses": [
                {
                    "concern_id": "con-1",
                    "response": "Added test",
                    "change": "Added M1",
                    "acceptance_test": "pytest",
                    "evidence_refs": ["tests/test_parser.py"],
                }
            ],
        }
        self.assertTrue(report_check.check_report(json.dumps(bad_cite), spec=spec))

        cyclic_dag = {
            "contract": _planning_contract(
                milestones=[
                    {"id": "M1", "depends_on": ["M2"], "acceptance_criteria": ["C1"]},
                    {"id": "M2", "depends_on": ["M1"], "acceptance_criteria": []},
                ],
                criteria=[{"id": "C1", "sources": [{"source_id": src_id, "quote": f"{src_id}#1"}]}],
            ),
            "responses": bad_cite["responses"],
        }
        self.assertTrue(
            any("cycle" in err.lower() for err in report_check.check_report(json.dumps(cyclic_dag), spec=spec))
        )

        missing_concern_and_evidence = {
            "contract": _planning_contract(
                milestones=[{"id": "M1", "depends_on": [], "acceptance_criteria": ["C1"]}],
                criteria=[{"id": "C1", "sources": [{"source_id": src_id, "quote": f"{src_id}#1"}]}],
            ),
            "responses": [
                {
                    "concern_id": "con-wrong",
                    "response": "Added test",
                    "change": "Added M1",
                    "acceptance_test": "pytest",
                    "evidence_refs": [],
                }
            ],
        }
        errs = report_check.check_report(json.dumps(missing_concern_and_evidence), spec=spec)
        self.assertTrue(any("evidence_refs" in err for err in errs))
        self.assertTrue(any("con-1" in err for err in errs))

        valid = {
            "contract": _planning_contract(
                milestones=[{"id": "M1", "depends_on": [], "acceptance_criteria": ["C1"]}],
                criteria=[{"id": "C1", "sources": [{"source_id": src_id, "quote": f"{src_id}#1"}]}],
            ),
            "responses": bad_cite["responses"],
        }
        self.assertEqual([], report_check.check_report(json.dumps(valid), spec=spec))

    def test_validator_checks_check_refs_and_missing_evidence_files(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            schema = _schema_obj(
                ["status", "checks", "evidence_refs"],
                {
                    "status": {"type": "string"},
                    "checks": {"type": "array"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
            )
            spec = report_check.build_spec(stage="tester", workspace=workspace, schema=schema)

            fail_only = {
                "status": "PASS",
                "checks": [{"command": "pytest", "exit_code": 1}],
                "evidence_refs": ["check:1"],
            }
            self.assertTrue(report_check.check_report(json.dumps(fail_only), spec=spec))

            out_of_range = {
                "status": "PASS",
                "checks": [{"command": "pytest", "exit_code": 0}],
                "evidence_refs": ["check:9"],
            }
            self.assertTrue(
                any("check:9" in err for err in report_check.check_report(json.dumps(out_of_range), spec=spec))
            )

            missing_receipt = {
                "status": "PASS",
                "checks": [{"command": "pytest", "exit_code": 0}],
                "evidence_refs": [".autocode/evidence/missing.json", "check:1"],
            }
            self.assertTrue(
                any("missing.json" in err for err in report_check.check_report(json.dumps(missing_receipt), spec=spec))
            )

            receipt = workspace / ".autocode/evidence/pass.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text("{}")
            valid = {
                "status": "PASS",
                "checks": [{"command": "pytest", "exit_code": 0}],
                "evidence_refs": [".autocode/evidence/pass.json", "check:1"],
            }
            self.assertEqual([], report_check.check_report(json.dumps(valid), spec=spec))

    def test_investigate_stuck_retry_rules(self):
        schema = _schema_obj(
            ["recommendation", "diagnosis"],
            {
                "recommendation": {"type": "string"},
                "diagnosis": {"type": "string"},
                "guidance": {"type": "string"},
                "example": {"type": "string"},
                "probe": {"type": "string"},
                "untestable": {"type": "string"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
            },
        )
        spec = report_check.build_spec(stage="investigate_stuck", workspace=Path("/tmp"), schema=schema)

        missing_example = {
            "recommendation": "retry",
            "diagnosis": "Timeout on lock",
            "guidance": "Use shorter lock wait",
            "example": "",
            "probe": "test -f lock",
            "untestable": "",
            "evidence_refs": ["a.py"],
        }
        self.assertTrue(
            any("example" in err.lower() for err in report_check.check_report(json.dumps(missing_example), spec=spec))
        )

        both_probe_and_untestable = {
            **missing_example,
            "example": "Lock file stayed present",
            "untestable": "Race condition",
        }
        self.assertTrue(
            any(
                "exactly one of probe" in err
                for err in report_check.check_report(json.dumps(both_probe_and_untestable), spec=spec)
            )
        )

        valid = {**missing_example, "example": "Lock file stayed present"}
        self.assertEqual([], report_check.check_report(json.dumps(valid), spec=spec))

    def test_cli_stdin_json_writes_nothing_to_workspace_and_respects_private_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "tracked.txt").write_text("clean\n")
            run_dir = workspace / ".autocode/runs/own"
            spec_path = run_dir / "planning/report-check/tester-001-01.json"
            spec_path.parent.mkdir(parents=True)
            (run_dir / "state.json").write_text('{"secret": true}')
            schema = _schema_obj(["summary"], {"summary": {"type": "string"}})
            spec = report_check.build_spec(stage="builder", workspace=workspace, schema=schema)
            spec_path.write_text(json.dumps(spec))

            reads = containment.private_run_reads(workspace, run_dir)
            self.assertEqual(str(run_dir / "planning"), reads["readable"][0])
            self.assertTrue(str(spec_path).startswith(reads["readable"][0]))

            before = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                old_stdin = sys.stdin
                try:
                    sys.stdin = io.StringIO(json.dumps({}))
                    code = report_check.cli(["--spec", str(spec_path), "--json", "-"])
                finally:
                    sys.stdin = old_stdin
            self.assertEqual(1, code)
            payload = json.loads(out.getvalue())
            self.assertFalse(payload["ok"])
            self.assertTrue(payload["errors"])
            after = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_handoff_preserves_compact_or_indented_packet(self):
        compact = "Instr\nCURRENT HANDOFF DATA\n" + json.dumps({"stage": "builder"}, separators=(",", ":"))
        out_compact = tool_handoff.with_report_check_command(compact, "/tmp/spec.json")
        _, _, packet_compact = out_compact.partition(tool_handoff.MARKER)
        self.assertNotIn("\n", packet_compact)
        self.assertIn("report-check --spec /tmp/spec.json -", json.loads(packet_compact)["report_check_command"])


class ReportCheckStageIntegrationTests(unittest.TestCase):
    setUp = base.RetrofitTest.setUp

    def test_fake_stage_uses_report_check_command_and_avoids_report_repair(self):
        fake_codex = self.root / "bin/codex"
        fake_codex.parent.mkdir(parents=True, exist_ok=True)
        fake_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, shlex, subprocess, sys\n"
            "prompt = sys.stdin.read()\n"
            "data = json.loads(prompt.split('CURRENT HANDOFF DATA\\n', 1)[1])\n"
            "cmd = shlex.split(data['report_check_command'])\n"
            "bad = json.dumps({'wrong': True})\n"
            "res_bad = subprocess.run(cmd, input=bad, text=True, capture_output=True)\n"
            "assert res_bad.returncode == 1, (res_bad.returncode, res_bad.stdout, res_bad.stderr)\n"
            "good = json.dumps({'summary': 'Verified via report-check'})\n"
            "res_good = subprocess.run(cmd, input=good, text=True, capture_output=True)\n"
            "assert res_good.returncode == 0, (res_good.returncode, res_good.stdout, res_good.stderr)\n"
            "out = sys.argv[sys.argv.index('-o') + 1]\n"
            "open(out, 'w').write(good)\n"
            "print(json.dumps({'type': 'thread.started', 'thread_id': 't-precheck'}))\n"
            "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}}))\n"
        )
        fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IEXEC)

        schema = self.run / "builder.schema.json"
        schema.write_text(json.dumps(_schema_obj(["summary"], {"summary": {"type": "string"}})))
        self.state["next_stage"] = "builder"
        self.state["sessions"] = {}
        self.state["settings"]["engine"] = "codex"
        self.state["settings"]["roles"] = {
            "builder": {"engine": "codex", "provider": "openai", "model": "gpt-5-codex", "reasoning_effort": "medium"}
        }
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{fake_codex.parent}:{old_path}"
        try:
            prompt = "Build it.\nCURRENT HANDOFF DATA\n" + json.dumps({"stage": "builder"}, indent=2)
            value, record = runner.run_role(
                role="builder",
                prompt=prompt,
                sandbox="workspace-write",
                workspace=self.root,
                run_dir=self.run,
                state=self.state,
                schema=schema,
                model="gpt-5-codex",
                allow_write=True,
                dry_run=False,
            )
        finally:
            os.environ["PATH"] = old_path

        self.assertEqual({"summary": "Verified via report-check"}, value)
        self.assertNotIn("pending_report_repair", self.state)
        self.assertFalse(any(s.get("report_only") for s in self.state.get("stages", [])))
        self.assertEqual(0, len([s for s in self.state.get("stages", []) if s.get("report_only")]))
        self.assertTrue((self.run / "planning/report-check/builder-005-builder-01.json").is_file())
