"""Bounded recovery for token-truncated read-only review reports."""

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode as runner
import autocode_stage_recovery as recovery
import autocode_support as support

from tests import test_subprocess as subprocess_test_support


class TruncatedReviewRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        self.snapshot = {"revision": "source-revision", "head": "head", "files": {}}
        self.schema = self.run / "schemas/review.json"
        self.schema.parent.mkdir(parents=True)
        self.schema.write_text("{}")

    def attempt(self, stage="sol", ordinal=1, *, report_only=False):
        actual_stage = f"{stage}_report_repair" if report_only else stage
        base = self.run / "iterations/001" / f"{actual_stage}-{ordinal:02d}"
        base.parent.mkdir(parents=True, exist_ok=True)
        before = Path(str(base) + ".before.json")
        before.write_text(json.dumps(self.snapshot))
        events = Path(str(base) + ".jsonl")
        session, message = "ses_limit", f"msg_{ordinal}"
        events.write_text(
            "\n".join(
                json.dumps(row)
                for row in [
                    {"type": "step_start", "sessionID": session, "part": {"id": "p_start", "sessionID": session}},
                    {
                        "type": "text",
                        "sessionID": session,
                        "part": {
                            "id": "p_text",
                            "sessionID": session,
                            "messageID": message,
                            "text": '{"verdict":"PASS","checks":[...',
                        },
                    },
                    {
                        "type": "step_finish",
                        "sessionID": session,
                        "part": {
                            "id": "p_finish",
                            "sessionID": session,
                            "messageID": message,
                            "reason": "length",
                            "tokens": {"input": 12, "output": 7, "reasoning": 15, "cache": {"read": 2, "write": 0}},
                        },
                    },
                ]
            )
            + "\n"
        )
        record = {
            "role": "sol" if stage == "sol" else "completion",
            "stage": actual_stage,
            "iteration": 1,
            "started_at": "2026-10-01T00:00:00+00:00",
            "finished_at": "2026-10-01T00:00:02+00:00",
            "duration_seconds": 2,
            "exit_code": 0,
            "events": str(events),
            "output": str(base) + ".json",
            "schema": str(self.schema),
            "before_ref": str(before),
            "engine": "opencode",
            "processes": [],
            "supports_sessions": True,
            "source_revision": "source-revision",
            "contract_hash": "contract-hash",
        }
        if report_only:
            record.update(report_only=True, original_stage=stage)
        return record

    def state(self, record):
        return {
            "status": "RUNNING",
            "workspace": str(self.root),
            "next_stage": record.get("original_stage", record["stage"]),
            "iteration": 1,
            "active_seconds": 0,
            "active_stage": record,
            "settings": {"report_repair": {"max_attempts": 2}, "roles": {}},
            "goal_contract": {"hash": "contract-hash"},
            "stages": [],
            "history": [],
            "sessions": {},
        }

    def invoke_recovery(self, state, error=None):
        error = error or support.Paused("PAUSED_PROVIDER_UNCERTAIN", "Output token limit")

        def save(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))

        with (
            patch.object(support, "snapshot", return_value=self.snapshot) as source,
            patch.object(recovery.interventions, "pending", return_value=[]),
            patch.object(recovery.records, "write_json", side_effect=save),
        ):
            recovered = recovery.automatically_recover_truncated_review(state, self.run, self.root, error)
        if recovered:
            source.assert_called_once_with(self.root)
        return recovered

    def test_recovery_queues_report_only_repair_and_archives_usage_and_events(self):
        state = self.state(self.attempt())
        self.assertTrue(self.invoke_recovery(state))
        self.assertNotIn("active_stage", state)
        self.assertEqual(("RUNNING", "REPORT_REPAIR", "sol"), (state["status"], state["phase"], state["next_stage"]))
        pending = state["pending_report_repair"]
        original = pending["original"]
        self.assertTrue(original["truncated_output"])
        self.assertTrue(Path(original["events"]).is_file())
        self.assertEqual(22, original["metrics"]["provider_tokens"]["output_tokens"])
        self.assertEqual(0, original["metrics"]["completed_turns"])
        source = runner.repair_report_source(original)
        self.assertFalse(source["content"]["complete_report"])
        self.assertEqual('{"verdict":"PASS","checks":[...', source["content"]["partial_text"])

    def test_plan_reviewer_truncation_qualifies_for_report_only_repair(self):
        state = self.state(self.attempt("astra_challenge"))
        self.assertTrue(self.invoke_recovery(state))
        self.assertEqual(
            ("RUNNING", "REPORT_REPAIR", "astra_challenge"), (state["status"], state["phase"], state["next_stage"])
        )
        self.assertTrue(state["pending_report_repair"]["original"]["truncated_output"])
        self.assertTrue(Path(state["pending_report_repair"]["original"]["events"]).is_file())

    def test_report_only_retry_keeps_original_and_consumes_existing_bound(self):
        first = self.state(self.attempt())
        self.assertTrue(self.invoke_recovery(first))
        first["pending_report_repair"]["attempts"] = 1
        retry = self.attempt("sol", 1, report_only=True)
        first["active_stage"] = retry
        self.assertTrue(self.invoke_recovery(first))
        self.assertTrue(first["pending_report_repair"]["original"]["stage"] == "sol")
        self.assertTrue(first["pending_report_repair"]["latest_rejected"]["truncated_output"])
        self.assertEqual(1, first["pending_report_repair"]["attempts"])
        latest = runner.repair_report_source(first["pending_report_repair"]["latest_rejected"])
        self.assertEqual("truncated_provider_response", latest["format"])

    def test_exhausted_repair_limit_keeps_active_truncated_attempt_and_original(self):
        state = self.state(self.attempt())
        self.assertTrue(self.invoke_recovery(state))
        original_path = state["pending_report_repair"]["original"]["events"]
        state["pending_report_repair"]["attempts"] = 2
        state["active_stage"] = self.attempt("sol", 2, report_only=True)
        with self.assertRaisesRegex(support.Paused, "attempts exhausted"):
            self.invoke_recovery(state)
        self.assertEqual(original_path, state["pending_report_repair"]["original"]["events"])
        self.assertTrue(Path(original_path).is_file())
        self.assertEqual("sol_report_repair", state["active_stage"]["stage"])

    def test_changed_source_wrong_stage_and_non_truncation_do_not_recover(self):
        record = self.attempt("terra")
        state = self.state(record)
        self.assertFalse(self.invoke_recovery(state))
        self.assertIn("active_stage", state)
        record = self.attempt("sol", ordinal=2)
        state = self.state(record)
        changed = {**self.snapshot, "revision": "changed"}
        with patch.object(support, "snapshot", return_value=changed) as source:
            with patch.object(recovery.interventions, "pending", return_value=[]):
                self.assertFalse(
                    recovery.automatically_recover_truncated_review(
                        state, self.run, self.root, support.Paused("PAUSED_PROVIDER_UNCERTAIN", "Output token limit")
                    )
                )
        source.assert_called_once_with(self.root)
        record = self.attempt("sol", ordinal=3)
        Path(record["events"]).write_text("{}\n")
        state = self.state(record)
        self.assertFalse(self.invoke_recovery(state))

    def timed_out_repair(self):
        state = self.state(self.attempt("astra_challenge"))
        contract = {"task_id": "fixture", "revision": 1, "body": {}, "approval_status": "draft"}
        contract["hash"] = support.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        state["goal_contract"] = contract
        state["active_stage"]["contract_hash"] = contract["hash"]
        state["sessions"] = {"astra": "retained-session"}
        self.assertTrue(self.invoke_recovery(state))
        state["pending_report_repair"]["attempts"] = 1
        repair = self.attempt("astra_challenge", report_only=True)
        Path(repair["events"]).write_text(json.dumps({"type": "thread.started", "thread_id": "repair"}) + "\n")
        repair.update(
            timed_out=True,
            exit_code=-15,
            contract_hash=contract["hash"],
            processes=[{"pid": 99999999, "birth_time": 1}],
        )
        state["active_stage"] = repair
        return state

    def recover_timeout(self, state, *, snapshot=None):
        with (
            patch.object(support, "snapshot", return_value=snapshot or self.snapshot),
            patch.object(recovery.interventions, "pending", return_value=[]),
            patch.object(recovery.processes, "live_processes", return_value=[]),
        ):
            return recovery.automatically_recover_report_repair_timeout(
                state, self.run, self.root, support.Paused("PAUSED_PROVIDER_TIMEOUT", "Stopped repair stage deadline")
            )

    def test_truncated_original_survives_report_repair_timeout_without_replay_or_refund(self):
        state = self.timed_out_repair()
        retained = copy.deepcopy(
            {key: state[key] for key in ("pending_report_repair", "sessions", "settings", "goal_contract")}
        )
        self.assertTrue(self.recover_timeout(state))
        self.assertEqual(retained, {key: state[key] for key in retained})
        self.assertNotIn("active_stage", state)
        self.assertEqual(
            ("RUNNING", "REPORT_REPAIR", "astra_challenge"), (state["status"], state["phase"], state["next_stage"])
        )
        self.assertEqual(1, sum(row["stage"] == "astra_challenge" for row in state["stages"]))
        self.assertEqual(1, sum(row["stage"] == "astra_challenge_report_repair" for row in state["stages"]))
        receipt = next(row for row in state["stages"] if row["stage"] == "resolver")["receipt"]
        self.assertEqual(1, receipt["evidence"]["repair_attempts_used"])
        for path, digest in retained["pending_report_repair"]["pins"].items():
            self.assertEqual(digest, support.file_hash(path))

    def test_truncated_timeout_recovery_retains_all_existing_admission_guards(self):
        for case in (
            "source",
            "pin",
            "stage",
            "role",
            "contract",
            "criteria",
            "exhausted",
            "incomplete",
            "builder",
            "marker",
        ):
            with self.subTest(case=case):
                # Each owned fixture includes the real truncation queue and pins.
                # No production run or retained incident evidence is modified.
                state = self.timed_out_repair()
                original = state["pending_report_repair"]["original"]
                snapshot = self.snapshot
                if case == "source":
                    snapshot = {**snapshot, "revision": "changed"}
                elif case == "pin":
                    state["pending_report_repair"]["pins"][original["events"]] = "changed"
                elif case == "stage":
                    state["active_stage"]["original_stage"] = "sol"
                elif case == "role":
                    state["active_stage"]["role"] = "other"
                elif case == "contract":
                    state["active_stage"]["contract_hash"] = "changed"
                elif case == "criteria":
                    state["active_stage"]["criteria_revision"] = "changed"
                elif case == "exhausted":
                    state["pending_report_repair"]["attempts"] = 2
                elif case == "incomplete":
                    path = Path(original["events"])
                    path.write_text(
                        json.dumps(
                            {
                                "type": "turn.failed",
                                "error": {"code": "incomplete_turn", "message": "Output token limit"},
                            }
                        )
                    )
                    state["pending_report_repair"]["pins"][str(path)] = support.file_hash(path)
                elif case == "builder":
                    original.update(stage="terra", role="terra")
                    state["active_stage"].update(stage="terra_report_repair", original_stage="terra", role="terra")
                    state["next_stage"] = "terra"
                elif case == "marker":
                    original.pop("truncated_output")
                retained = copy.deepcopy(state)
                with self.assertRaises(support.Paused) as stopped:
                    self.recover_timeout(state, snapshot=snapshot)
                self.assertEqual("PAUSED_RESOLVER_OPERATIONAL", stopped.exception.status)
                self.assertEqual(retained, state)


class TruncatedReviewCliTests(unittest.TestCase):
    new_run_engine_args = ("--engine", "opencode")

    def setUp(self):
        subprocess_test_support.SubprocessFlow.setUp(self)
        source = Path(__file__).resolve().parents[1] / "tools" / "fake_opencode.py"
        provider = self.root / "fixture-bin" / "opencode"
        shutil.copy2(source, provider)
        provider.chmod(0o755)
        from .opencode_fixture_cli import entrypoint

        self.entry = entrypoint(self.entry)
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"

    launch = subprocess_test_support.SubprocessFlow.launch
    saved = subprocess_test_support.SubprocessFlow.saved

    def test_cli_repairs_truncated_validator_report_without_replaying_checks(self):
        self.env["AUTOCODE_FIXTURE_TRUNCATE_STAGE"] = "sol"
        self.launch(["Build a greeting tool", "--chat"], 0, answers="CLI\nyes\n")
        _, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual(1, sum(row["stage"] == "sol" for row in state["stages"]))
        self.assertEqual(1, sum(row["stage"] == "sol_report_repair" for row in state["stages"]))
        original = next(row for row in state["stages"] if row["stage"] == "sol")
        repair = next(row for row in state["stages"] if row["stage"] == "sol_report_repair")
        self.assertTrue(original["truncated_output"])
        self.assertEqual(0, original["metrics"]["completed_turns"])
        self.assertGreater(original["metrics"]["provider_tokens"]["output_tokens"], 0)
        self.assertEqual(original["events"], repair["applied_original_events"])
        self.assertFalse(any(event.get("type") == "tool_use" for event in support.events(repair["events"])))
        self.assertEqual("accepted", state["report_repair_history"][-1]["result"])

    def test_cli_repairs_truncated_plan_review_instead_of_asking_for_a_model(self):
        self.env["AUTOCODE_FIXTURE_TRUNCATE_STAGE"] = "astra_challenge"
        self.launch(["Build a greeting tool", "--chat", "--joint-planning"], 0, answers="CLI\nyes\n")
        _, state = self.saved()
        stages = [row["stage"] for row in state["stages"]]
        self.assertIn("astra_challenge_report_repair", stages, stages)
        original = next(row for row in state["stages"] if row["stage"] == "astra_challenge")
        self.assertTrue(original["truncated_output"])
        self.assertEqual("accepted", state["report_repair_history"][-1]["result"])
        self.assertFalse(any(str(q.get("id", "")).startswith("route-") for q in state.get("pending_questions", [])))


class TruncatedRepairTimeoutCliTests(unittest.TestCase):
    """Real Linux CLI/process cleanup; only provider output and a stopped deadline are injected."""

    setUp = subprocess_test_support.SubprocessFlow.setUp

    PROVIDER = r"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

report, runtime, delegate, probe, mode, sandbox = map(str, sys.argv[1:])
report, probe = Path(report), Path(probe)
prompt = sys.stdin.read()
data = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
repairing = bool(data.get('report_repair'))
stage = data['original']['stage'] if repairing else data['stage']
rows = [json.loads(line) for line in probe.read_text().splitlines()] if probe.exists() else []
prior_repairs = sum(row.get('stage') == stage and row.get('repairing') for row in rows)
row = {'stage': stage, 'repairing': repairing, 'sandbox': sandbox}
if repairing:
    run_dir = Path(data['state_file']).parent  # Public handoff path; never read state.json.
    response = subprocess.run(
        [sys.executable, runtime, '--status', '--workspace', str(Path.cwd()), '--run-dir', str(run_dir)],
        capture_output=True, text=True, check=True,
    )
    public = json.loads(response.stdout)
    row.update(
        settings_sha256=hashlib.sha256(json.dumps(public['settings'], sort_keys=True).encode()).hexdigest(),
        approved=public['view'].get('approved_contract') is not None,
        source_sha256=data['original']['source_revision'],
        original_events_sha256=data['rejected_report']['sha256'],
        attempts=[{key: attempt.get(key) for key in ('stage', 'timed_out', 'tokens', 'active')}
                  for attempt in public['view']['usage']['accounting']['attempts']],
    )
with probe.open('a') as output:
    output.write(json.dumps(row) + '\n')
if stage == 'astra_challenge' and repairing and (mode == 'all' or prior_repairs == 0):
    assert sandbox == 'read-only'
    print(json.dumps({'type': 'thread.started', 'thread_id': 'stopped-repair'}), flush=True)
    print(json.dumps({'type': 'usage.partial', 'usage': {'input_tokens': 7, 'output_tokens': 3}}), flush=True)
    print(json.dumps({'type': 'fixture.stopped_repair_deadline'}), flush=True)
    raise SystemExit(0)  # No report or terminal response; the bootstrap injects the stopped timeout.
if stage == 'astra_challenge' and repairing:
    report.write_bytes(probe.with_suffix('.original-report.json').read_bytes())
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 11, 'output_tokens': 5}}), flush=True)
    raise SystemExit(0)
result = subprocess.run(
    [sys.executable, delegate, 'exec', '-', '--json', '-o', str(report)],
    input=prompt, capture_output=True, text=True, check=True,
)
if stage == 'astra_challenge':
    probe.with_suffix('.original-report.json').write_bytes(report.read_bytes())
    report.unlink()
    print(json.dumps({'type': 'thread.started', 'thread_id': 'length-limited-review'}), flush=True)
    print(json.dumps({'type': 'turn.failed', 'error': {
        'code': 'output_token_limit', 'message': 'Fixture output token limit after 32000 output tokens'},
        'usage': {'input_tokens': 100, 'output_tokens': 32000}}), flush=True)
else:
    sys.stdout.write(result.stdout)
"""

    BOOTSTRAP = r"""
import json
import runpy
import sys
from pathlib import Path

runtime = Path(sys.argv.pop(1))
sys.path.insert(0, str(runtime.parent))
import autocode_process as processes
original = processes.wait_for_stage

def stopped_deadline(child, timeout, checkpoint, **kwargs):
    owned = []
    def retain(rows):
        owned[:] = rows
        checkpoint(rows)
    code, timed_out = original(child, timeout, retain, **kwargs)
    activity = kwargs.get('activity')
    rows = [json.loads(line) for line in activity.path.read_text().splitlines()] if activity else []
    if any(row.get('type') == 'fixture.stopped_repair_deadline' for row in rows):
        assert child.poll() is not None and not processes.live_processes(owned), 'Real cleanup must precede fault'
        assert not timed_out, 'Do not hide an unexpected real deadline'
        activity.timeout = {'kind': 'stage', 'reason': 'Injected stopped report-repair stage deadline'}
        return code, True
    return code, timed_out

processes.wait_for_stage = stopped_deadline
sys.argv[0] = str(runtime)
runpy.run_path(str(runtime), run_name='__main__')
"""

    def start_fixture(self, mode):
        from autocode_taskrun import TaskRun

        source = Path(__file__).resolve().parents[1] / "tools"
        provider, bootstrap = self.root / "provider.py", self.root / "stopped-deadline-cli.py"
        provider.write_text(self.PROVIDER)
        bootstrap.write_text(self.BOOTSTRAP)
        self.probe = self.root / "observations.jsonl"
        config_root = self.root / "provider-config"
        config = config_root / "autocode/providers/truncated_timeout_fixture.toml"
        config.parent.mkdir(parents=True)
        command = [
            sys.executable,
            str(provider),
            "{report}",
            str(source / "autocode.py"),
            str(source / "live_fixture_provider.py"),
            str(self.probe),
            mode,
            "{sandbox}",
        ]
        roles = ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")
        config.write_text(
            'name = "truncated_timeout_fixture"\noutput = "report_file"\nprompt = "stdin"\ncommand = '
            + json.dumps(command)
            + "\nmodels = "
            + json.dumps(["fixture/" + role for role in roles])
            + "\n[roles]\n"
            + "\n".join(role + ' = { model = "fixture/' + role + '", effort = "medium" }' for role in roles)
            + "\n"
        )
        from .test_taskrun import BRIEF

        return TaskRun.start(
            self.project,
            BRIEF,
            command=[sys.executable, str(bootstrap), str(source / "autocode.py")],
            options=(
                "--provider",
                "truncated_timeout_fixture",
                "--no-auto-select",
                "--joint-planning",
                "--evidence-provenance",
                "fake",
            ),
            env={**self.env, "XDG_CONFIG_HOME": str(config_root)},
            timeout=180,
            cwd=self.root,
        )

    def assert_bound_attempts(self, run):
        view = run.status()
        rows = [json.loads(line) for line in self.probe.read_text().splitlines()]
        originals = [row for row in rows if row["stage"] == "astra_challenge" and not row["repairing"]]
        repairs = [row for row in rows if row["stage"] == "astra_challenge" and row["repairing"]]
        self.assertEqual(1, len(originals), rows)
        self.assertEqual(2, len(repairs), (rows, view["status"], view["stop_reason"]))
        self.assertEqual(repairs[0]["settings_sha256"], repairs[1]["settings_sha256"])
        self.assertEqual(repairs[0]["source_sha256"], repairs[1]["source_sha256"])
        self.assertEqual(repairs[0]["original_events_sha256"], repairs[1]["original_events_sha256"])
        self.assertTrue(all(row["sandbox"] == "read-only" and not row["approved"] for row in repairs))
        retained = [
            row
            for row in repairs[1]["attempts"]
            if row["stage"] == "astra_challenge_report_repair" and row["timed_out"]
        ]
        self.assertEqual(1, len(retained), repairs[1])
        self.assertEqual((7, 3), (retained[0]["tokens"]["input_tokens"], retained[0]["tokens"]["output_tokens"]))
        self.assertFalse(retained[0]["active"])
        self.assertFalse(any(row["stage"] == "terra" for row in rows))
        self.assertFalse(view["done"])
        self.assertIsNone(view.get("approved_contract"))
        self.assertEqual([], view["approvals"])
        return view

    def test_cli_retries_only_remaining_repair_after_truncated_review_and_stopped_timeout(self):
        run = self.start_fixture("first")
        view = self.assert_bound_attempts(run)
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        attempts = view["usage"]["accounting"]["attempts"]
        self.assertEqual(2, sum(row["stage"] == "astra_challenge_report_repair" for row in attempts))
        self.assertEqual(1, sum(row["stage"] == "astra_challenge" for row in attempts))

    def test_cli_second_stopped_timeout_exhausts_bound_without_a_third_repair(self):
        run = self.start_fixture("all")
        view = self.assert_bound_attempts(run)
        self.assertEqual("WAITING_FOR_USER", view["status"], view)
        self.assertIn("allowance exhausted", view["stop_reason"])
        self.assertEqual("answer", view["needs"]["kind"])
        attempts = view["usage"]["accounting"]["attempts"]
        self.assertEqual(2, sum(row["stage"] == "astra_challenge_report_repair" for row in attempts))


if __name__ == "__main__":
    unittest.main()
