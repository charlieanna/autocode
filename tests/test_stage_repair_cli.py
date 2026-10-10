"""Revision-31 report faults through the real CLI and an explicitly offline provider.

Fixture completion is evidence for the runtime, not proof of live model recovery.
"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_stuck_job as stuck
import autocode_verify as verify
from goal_fixtures import body
from units import autoplanner

from . import stage_repair_provider as provider
from . import test_report_repair as repair_support
from . import test_subprocess as cli_support

support = repair_support.support


class StageRepairProviderTests(unittest.TestCase):
    def invoke(self, exit_code, *, crash=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            delegate, report, trace = root / "delegate.py", root / "report.json", root / "trace.jsonl"
            prompt = 'CURRENT HANDOFF DATA\n{"stage": "astra_discovery"}'
            delegate.write_text(
                "import json, os, sys\nfrom pathlib import Path\n"
                "Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps({\n"
                "    'pid': os.getpid(), 'prompt': sys.stdin.read(), 'args': sys.argv[1:]}))\n"
                "print(json.dumps({'type': 'turn.completed'}))\n"
                "print('delegate diagnostic', file=sys.stderr)\n"
                + ("raise RuntimeError('delegate crashed')\n" if crash else f"raise SystemExit({exit_code!r})\n")
            )
            args = ["codex", "exec", "-o", str(report)]
            stdin, stdout, stderr = io.StringIO(prompt), io.StringIO(), io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    STAGE_REPAIR_DELEGATE=str(delegate),
                    STAGE_REPAIR_TRACE=str(trace),
                    STAGE_REPAIR_CASE="finalizer",
                ),
                patch.object(sys, "argv", args),
                patch.object(sys, "stdin", stdin),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                code = provider.main()
                self.assertIs(sys.stdin, stdin)
                self.assertEqual("codex", sys.argv[0])
            payload = json.loads(report.read_text())
            self.assertEqual(prompt, payload["prompt"])
            self.assertEqual(args[1:], payload["args"])
            self.assertEqual([{"type": "turn.completed"}], [json.loads(row) for row in stdout.getvalue().splitlines()])
            self.assertEqual(code == 0, trace.exists())
            return code, payload, stderr.getvalue()

    def test_delegate_reuses_provider_process_without_changing_transport(self):
        code, payload, stderr = self.invoke(0)
        self.assertEqual(0, code)
        self.assertEqual(os.getpid(), payload["pid"])
        self.assertEqual("", stderr)

    def test_delegate_exit_status_and_failure_diagnostics_are_preserved(self):
        for exit_code, expected in ((None, 0), (7, 7), ("fixture failed", 1)):
            with self.subTest(exit_code=exit_code):
                code, _, stderr = self.invoke(exit_code)
                self.assertEqual(expected, code)
                self.assertEqual(
                    "delegate diagnostic\n" + ("fixture failed\n" if expected == 1 else "") if expected else "", stderr
                )

    def test_delegate_exception_keeps_transport_output_and_traceback(self):
        code, _, stderr = self.invoke(None, crash=True)
        self.assertEqual(1, code)
        self.assertIn("delegate diagnostic\n", stderr)
        self.assertIn("Traceback (most recent call last)", stderr)
        self.assertIn("RuntimeError: delegate crashed", stderr)


class StageRepairPromptContracts(unittest.TestCase):
    def request_for(self, stage, value, *, source=None):
        fixture = repair_support.RepairTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.state["next_stage"] = stage
        if source is not None:
            saved = fixture.run / "archived-diagnostic" / "plan-finalize-01.json"
            saved.parent.mkdir(parents=True)
            saved.write_text(json.dumps(source))
            value = provider.investigator_report(saved)
            fixture.state["stuck_investigation"] = {
                "identity": "astra_finalize:PAUSED_INVALID_OUTPUT",
                "stage": "astra_finalize",
                "status": "PAUSED_INVALID_OUTPUT",
                "reason": "Missing concern decisions",
            }
            fixture.state["stages"].append(
                {
                    "stage": "astra_finalize",
                    "output": str(saved),
                    "rejected": True,
                    "rejection_reason": "Missing concern decisions",
                }
            )
        fixture.queue(stage=stage, role="astra", report=json.dumps(value))
        request = fixture.repair_request()
        data = json.loads(request["prompt"].split("CURRENT HANDOFF DATA\n", 1)[1])
        return fixture, request, data

    def test_finalizer_initial_task_is_required_inside_closed_contract(self):
        valid = provider.finalizer_report(body())
        missing = copy.deepcopy(valid)
        task = missing["contract"].pop("initial_task")
        schema = autoplanner.SCHEMAS["astra_finalize"]
        support.validate_schema(valid, schema)
        with self.assertRaisesRegex(ValueError, "missing initial_task"):
            support.validate_schema(missing, schema)
        missing["initial_task"] = task
        with self.assertRaises(ValueError):
            support.validate_schema(missing, schema)

    def test_finalizer_repair_prompt_corrects_observed_root_task_misplacement(self):
        malformed = provider.finalizer_report(body())
        malformed["contract"].pop("initial_task")
        _, request, data = self.request_for("astra_finalize", malformed)
        repaired = provider.repair_finalizer(request["prompt"], data["rejected_report"]["content"])
        self.assertNotIn("initial_task", repaired, "The actual repair prompt leaves the observed root field mistake")
        support.validate_schema(repaired, autoplanner.SCHEMAS["astra_finalize"])

    def test_investigator_repair_uses_existing_files_and_real_scratch_probe(self):
        fixture, request, data = self.request_for("investigate_stuck", {}, source={"decisions": []})
        original = data["rejected_report"]["content"]
        self.assertEqual(1, len(original["evidence_refs"]))
        initial_files = stuck.cited_files(original, fixture.root, fixture.run)
        initial = verify.scratch_run(
            fixture.root, fixture.run / "initial-diagnosis-probe", command=original["probe"], files=initial_files
        )
        self.assertNotEqual(0, initial["exit_code"], "The observed archive-prefixed name must fail in scratch")
        repaired = provider.repair_investigator(request["prompt"], data, original)
        support.validate_schema(repaired, stuck.SCHEMA)
        copied = stuck.cited_files(repaired, fixture.root, fixture.run)
        self.assertEqual(["run/plan-finalize-01.json"], list(copied))
        self.assertNotIn(str(fixture.run), repaired["probe"], "A probe must use the supplied scratch path")
        shown = verify.scratch_run(
            fixture.root, fixture.run / "repaired-diagnosis-probe", command=repaired["probe"], files=copied
        )
        self.assertEqual(0, shown["exit_code"], shown)
        self.assertIn("diagnosed missing concern decisions", shown["tail"])


class StageRepairCLI(unittest.TestCase):
    def fixture_for(self, case):
        fixture = cli_support.SubprocessFlow()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source = Path(__file__).with_name("stage_repair_provider.py")
        executable = fixture.root / "fixture-bin" / "codex"
        delegate = executable.with_name("ordinary_fake_codex.py")
        executable.rename(delegate)
        executable.write_text(source.read_text().replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1))
        executable.chmod(0o755)
        fixture.env.update(
            STAGE_REPAIR_CASE=case,
            STAGE_REPAIR_DELEGATE=str(delegate),
            STAGE_REPAIR_TRACE=str(fixture.root / "stage-repair-trace.jsonl"),
            AUTOCODE_FIXTURE_MODE="no-human",
        )
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "OPENCODE_CONFIG_CONTENT"):
            fixture.env.pop(key, None)
        fixture.new_run_engine_args = (
            "--engine",
            "codex",
            "--joint-planning",
            "--astra-model",
            "gpt-5.6-sol",
            "--terra-model",
            "gpt-5.6-terra",
            "--sol-model",
            "gpt-5.6-sol",
            "--completion-model",
            "gpt-5.6-sol",
            "--plan-reviewer-model",
            "gpt-6-astra",
            "--plan-reviewer-reasoning-effort",
            "high",
        )
        return fixture

    def plan(self, fixture):
        started = fixture.launch(["Build a greeting tool", "--no-chat"], 2)
        run, waiting = fixture.saved()
        self.assertEqual("WAITING_FOR_USER", waiting["status"], started.stdout + started.stderr)
        fixture.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        fixture.launch(["--run-dir", str(run), "--no-chat"], 2)
        run, state = fixture.saved()
        view = json.loads(fixture.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        trace = [json.loads(line) for line in (fixture.root / "stage-repair-trace.jsonl").read_text().splitlines()]
        evidence_directory = os.environ.get("STAGE_REPAIR_EVIDENCE_DIR")
        if evidence_directory:
            destination = Path(evidence_directory)
            destination.mkdir(parents=True, exist_ok=True)
            retained = []
            for row in state["stages"]:
                if row["stage"].startswith(("astra_finalize", "investigate_stuck")):
                    record = {
                        key: row.get(key)
                        for key in (
                            "stage",
                            "original_stage",
                            "iteration",
                            "output",
                            "events",
                            "rejected",
                            "rejection_reason",
                            "applied_original_events",
                        )
                    }
                    record["report"] = json.loads(Path(row["output"]).read_text())
                    record["command_events"] = [
                        json.loads(line)
                        for line in Path(row["events"]).read_text().splitlines()
                        if line.strip() and json.loads(line).get("type") == "item.completed"
                    ]
                    retained.append(record)
            (destination / (fixture.env["STAGE_REPAIR_CASE"] + ".json")).write_text(
                json.dumps(
                    {
                        "offline_fixture_only": True,
                        "view": view,
                        "trace": trace,
                        "stages": retained,
                        "investigations": state.get("stuck_investigations", []),
                        "review_calls_used": state["planning"]["astra_calls"],
                        "review_call_limit": autoplanner.review_call_limit(state),
                        "report_repair_history": state.get("report_repair_history", []),
                    },
                    indent=2,
                )
                + "\n"
            )
        return run, state, view, trace

    def assert_approval_boundary(self, fixture, state, view, trace):
        self.assertEqual("AWAITING_GOAL_APPROVAL", view["status"], view.get("stop_reason"))
        self.assertFalse(view["done"])
        self.assertEqual("approve_plan", view["needs"]["kind"])
        self.assertNotEqual("approved", state["goal_contract"]["approval_status"])
        self.assertFalse((fixture.project / "greet.py").exists())
        self.assertFalse(any(row["stage"] == "terra" for row in trace))
        final = state["planning"]["reports"]["astra_finalize"]["report"]
        self.assertNotIn("initial_task", final)
        support.validate_schema(final, autoplanner.SCHEMAS["astra_finalize"])
        concerns = state["planning"]["reports"]["astra_challenge"]["report"]["concerns"]
        self.assertEqual([row["id"] for row in concerns], [row["concern_id"] for row in final["decisions"]])

    def test_exhausted_report_repair_allowance_buys_no_third_provider_call(self):
        fixture = self.fixture_for("repair-limit")
        run, _, view, trace = self.plan(fixture)
        self.assertFalse(view["done"])
        self.assertEqual("PAUSED_INVALID_OUTPUT", view["status"], view.get("stop_reason"))
        repairs = [row for row in trace if row["stage"] == "astra_finalize" and row["repair"]]
        self.assertEqual(2, len(repairs), trace)
        self.assertEqual(2, len({row["error"] for row in repairs}), repairs)
        self.assertFalse(any(row["stage"] == "terra" for row in trace))
        calls = (fixture.root / "stage-repair-trace.jsonl").read_bytes()
        fixture.launch(["--run-dir", str(run), "--no-chat"], 2)
        self.assertEqual(calls, (fixture.root / "stage-repair-trace.jsonl").read_bytes())

    def test_closed_finalizer_repair_archives_missing_task_then_awaits_approval(self):
        fixture = self.fixture_for("finalizer")
        _, state, view, trace = self.plan(fixture)
        self.assert_approval_boundary(fixture, state, view, trace)
        originals = [row for row in state["stages"] if row["stage"] == "astra_finalize" and row.get("rejected")]
        self.assertEqual(1, len(originals))
        original = originals[0]
        self.assertIn("missing initial_task", original["rejection_reason"])
        self.assertTrue(Path(original["output"]).parent.name.startswith("archived-"))
        self.assertNotIn("initial_task", json.loads(Path(original["output"]).read_text())["contract"])
        finalizer_calls = [row for row in trace if row["stage"] == "astra_finalize"]
        self.assertEqual([False, True], [row["repair"] for row in finalizer_calls])
        self.assertEqual(["missing", "contract.initial_task"], [row["initial_task_path"] for row in finalizer_calls])
        accepted = [
            row for row in state["report_repair_history"] if row["repair"]["original_stage"] == "astra_finalize"
        ]
        self.assertEqual(1, len(accepted))
        self.assertEqual(original["events"], accepted[0]["repair"]["applied_original_events"])

    def test_oversized_finalizer_repair_resumes_as_a_fresh_review_then_awaits_approval(self):
        # 2026-10-05 self-build: the rejected final review's repair handoff exceeded 256 KiB, and every
        # resume retried the same unlaunchable repair. An explicit resume now starts a fresh review.
        fixture = self.fixture_for("oversized")
        run, state, view, trace = self.plan(fixture)
        self.assertEqual("PAUSED_REPORT_REPAIR_INPUT", state["status"], state.get("stop_reason"))
        self.assertIn("Complete report-repair handoff exceeds", state["stop_reason"])
        self.assertIn("autocode resume to archive this repair", state["stop_reason"])
        pending = state["pending_report_repair"]
        self.assertEqual(("astra_finalize", 0), (pending["original"]["stage"], pending["attempts"]))
        self.assertLess(Path(pending["original"]["output"]).stat().st_size, repair_support.runner.REPAIR_REPORT_BYTES)
        self.assertEqual([False], [row["repair"] for row in trace if row["stage"] == "astra_finalize"])
        self.assertEqual(2, state["planning"]["astra_calls"])

        # The fresh review is a third plan-review call: it stops at the allowance as any review does,
        # and AutoResolver asks for the operator's decision.
        fixture.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        run, state = fixture.saved()
        self.assertEqual(
            ("WAITING_FOR_USER", "astra_finalize"), (state["status"], state["next_stage"]), state.get("stop_reason")
        )
        self.assertIn("2/2 plan-review calls used", state["stop_reason"])
        trace = [json.loads(line) for line in (fixture.root / "stage-repair-trace.jsonl").read_text().splitlines()]
        self.assertEqual([False], [row["repair"] for row in trace if row["stage"] == "astra_finalize"])
        self.assertNotIn("pending_report_repair", state)
        archived = state["report_repair_archive"][-1]
        self.assertEqual(pending, archived["repair"])
        self.assertIn("cannot launch from its saved inputs", archived["reason"])
        self.assertTrue(all(Path(path).is_file() for path in pending["pins"]))

        fixture.launch(["--run-dir", str(run), "--planning-review-call-limit", "3"], 0)
        fixture.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        run, state = fixture.saved()
        view = json.loads(fixture.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        trace = [json.loads(line) for line in (fixture.root / "stage-repair-trace.jsonl").read_text().splitlines()]
        self.assert_approval_boundary(fixture, state, view, trace)
        self.assertEqual([False, False], [row["repair"] for row in trace if row["stage"] == "astra_finalize"])
        self.assertEqual(3, state["planning"]["astra_calls"])

    def test_investigator_repairs_archive_name_and_event_citation_then_runs_scratch(self):
        fixture = self.fixture_for("investigator")
        _, state, view, trace = self.plan(fixture)
        self.assert_approval_boundary(fixture, state, view, trace)
        # The first diagnosis retries the malformed report while retaining the
        # two spent review calls. A second, distinct budget diagnosis grants
        # exactly the one final review still needed. Neither diagnosis approves.
        identities = ["astra_finalize:PAUSED_REPEATED_FAILURE", "astra_finalize:PAUSED_PLANNING_BUDGET"]
        diagnoses = state["stuck_investigations"]
        observed = json.dumps({"investigations": diagnoses, "trace": trace}, indent=2)
        self.assertEqual(identities, [row["identity"] for row in diagnoses], observed)
        originals = [row for row in state["stages"] if row["stage"] == "investigate_stuck" and row.get("rejected")]
        self.assertEqual(2, len(originals), observed)
        for original in originals:
            self.assertIn("did not exit 0", original["rejection_reason"])
            self.assertTrue(Path(original["output"]).parent.name.startswith("archived-"))
            rejected = json.loads(Path(original["output"]).read_text())
            support.validate_schema(rejected, stuck.SCHEMA)
            self.assertIn("run/archived-", rejected["probe"])
            self.assertTrue(stuck.cited_files(rejected, fixture.project, fixture.saved()[0]))
            self.assertFalse(any(ref.startswith("event:") for ref in rejected["evidence_refs"]))
        investigation_calls = [row for row in trace if row["stage"] == "investigate_stuck"]
        self.assertEqual([False, True, False, True], [row["repair"] for row in investigation_calls], observed)
        self.assertEqual(
            [identities[0], identities[0], identities[1], identities[1]],
            [row["stuck_identity"] for row in investigation_calls],
            observed,
        )
        repairs = [row for row in investigation_calls if row["repair"]]
        for repair, diagnosis in zip(repairs, diagnoses, strict=False):
            self.assertTrue(repair["has_scratch_map"])
            self.assertFalse(any(ref.startswith("event:") for ref in repair["evidence_refs"]))
            self.assertEqual("retried", diagnosis["outcome"])
            self.assertEqual(0, diagnosis["probe_result"]["exit_code"])
            self.assertIn("diagnosed missing concern decisions", diagnosis["probe_result"]["tail"])
            self.assertEqual(repair["probe"], diagnosis["probe"])
        self.assertEqual(3, state["planning"]["astra_calls"])
        self.assertEqual(3, autoplanner.review_call_limit(state))


if __name__ == "__main__":
    unittest.main()
