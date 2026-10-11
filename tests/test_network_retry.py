"""Authenticated no-effect network recovery through policy and public task runs."""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_command_supervision as commands
import autocode_network_retry as retry
import autocode_source_scope as scope
import autocode_util as util
from autocode_taskrun import TaskRun

from . import test_subprocess


def failure(**data):
    return {
        "type": "error",
        "sessionID": "ses_fresh",
        "error": {
            "name": "APIError",
            "data": {
                "message": "Connection reset by server",
                "isRetryable": True,
                "metadata": {"code": "ECONNRESET", "syscall": "read", "message": "Connection reset by server"},
                **data,
            },
        },
    }


class NetworkDiagnosticTests(unittest.TestCase):
    def test_explicit_network_code_or_http_status(self):
        for code in retry.NETWORK_CODES:
            with self.subTest(code=code):
                self.assertEqual(code, retry.retryable_error(json.dumps(failure(metadata={"code": code}))))
        for status in retry.HTTP_CODES:
            with self.subTest(status=status):
                self.assertEqual(
                    str(status), retry.retryable_error(json.dumps(failure(metadata={}, statusCode=status)))
                )
        self.assertEqual("ECONNRESET", retry.retryable_error(json.dumps({**failure(), "timestamp": 1})))
        self.assertEqual(
            "ECONNRESET",
            retry.retryable_error(json.dumps(failure(metadata={"code": "ECONNRESET", "syscall": "", "message": ""}))),
        )
        self.assertEqual(
            "503",
            retry.retryable_error(
                json.dumps(
                    failure(
                        metadata={"url": "https://provider.example.test/generate"},
                        statusCode=503,
                        responseHeaders={"retry-after": "1"},
                        responseBody="Service unavailable",
                    )
                )
            ),
        )

    def test_retryable_boolean_and_network_identity_are_required(self):
        controls = [failure(isRetryable=value) for value in (False, None, 1, "true")]
        controls += [
            failure(metadata={}),
            failure(metadata={"code": "ENOTFOUND"}),
            failure(statusCode=429),
            failure(statusCode=True),
            failure(message="Quota exhausted"),
            failure(message="Authentication failed"),
            failure(message="output token limit"),
            failure(metadata={"code": "ECONNRESET", "message": "output token limit"}),
            failure(metadata={"code": "ECONNRESET", "message": "Quota exhausted"}),
            failure(metadata={"code": "ECONNRESET", "message": "Authentication failed"}),
            failure(responseBody="Quota exhausted"),
            failure(metadata={"code": "ECONNRESET", "errno": True}),
            failure(metadata={"code": "ECONNRESET", "syscall": {}}),
            failure(metadata={"code": "ECONNRESET", "address": []}),
            failure(metadata={"code": "ECONNRESET", "port": True}),
            failure(metadata={"code": "ECONNRESET", "extra": 1}),
            failure(statusCode=None),
            failure(responseBody={}),
            failure(responseHeaders={"retry-after": 1}),
            failure(url=[]),
            {**failure(), "sessionID": None},
            {**failure(), "timestamp": True},
        ]
        for row in controls:
            with self.subTest(row=row):
                self.assertIsNone(retry.retryable_error(json.dumps(row)))

    def test_partial_turn_tools_text_unknown_rows_and_duplicate_keys_refuse(self):
        raw = json.dumps(failure())
        for kind in ("step_start", "step_finish", "text", "tool_use", "turn.completed", "usage", "unknown"):
            for stream in (json.dumps({"type": kind}) + "\n" + raw, raw + "\n" + json.dumps({"type": kind})):
                with self.subTest(kind=kind, stream=stream):
                    self.assertIsNone(retry.retryable_error(stream))
        for stream in (
            "diagnostic\n" + raw,
            raw + "\n{}",
            raw[:-1],
            raw.replace('"type": "error"', '"type":"text","type":"error"'),
            json.dumps({**failure(), "usage": {"output_tokens": 0}}),
            json.dumps({**failure(), "unexpected": True}),
            json.dumps(failure()).replace("true", "NaN"),
            json.dumps(failure(metadata={"code": "ECONNRESET", "port": 1})).replace('"port": 1', '"port": 1e309'),
        ):
            with self.subTest(stream=stream):
                self.assertIsNone(retry.retryable_error(stream))


class CommandProviderShapeTests(unittest.TestCase):
    """#1001: command providers (kilocode) log the bare name/data error row —
    the same APIError/isRetryable facts, a different nesting than OpenCode's
    type/error row. Both must classify; unknown fields keep refusing."""

    def kilo(self, name, **data):
        return json.dumps({"name": name, "data": data})

    def test_a_retryable_command_provider_api_error_classifies(self):
        # Live shape from the #979 flap (requirements-revise-03/04 jsonl tails).
        raw = self.kilo("APIError", message="Network connection failed", isRetryable=True)
        self.assertIsNotNone(retry.retryable_error(raw), raw)

    def test_isretryable_false_and_conflict_messages_still_refuse(self):
        self.assertIsNone(
            retry.retryable_error(self.kilo("APIError", message="Network connection failed", isRetryable=False))
        )
        self.assertIsNone(
            retry.retryable_error(self.kilo("APIError", message="429 rate limit exceeded", isRetryable=True))
        )

    def test_unknown_fields_in_the_bare_row_refuse(self):
        raw = json.dumps({"name": "APIError", "data": {"message": "x", "isRetryable": True}, "surprise": 1})
        self.assertIsNone(retry.retryable_error(raw))
        raw = json.dumps({"name": "APIError", "data": {"message": "x", "isRetryable": True, "statusCode": 503}})
        self.assertIsNone(retry.retryable_error(raw))

    def test_a_tls_certificate_unknown_error_classifies_for_one_spawn(self):
        # Live shape from the 0.4s death (requirements-revise-05 jsonl tail).
        raw = self.kilo("UnknownError", message="unable to get local issuer certificate")
        self.assertEqual("certificate", retry.retryable_error(raw))

    def test_other_unknown_errors_still_refuse(self):
        self.assertIsNone(retry.retryable_error(self.kilo("UnknownError", message="model exploded")))
        self.assertIsNone(retry.retryable_error(self.kilo("APIError")))


class ChainInheritanceTests(unittest.TestCase):
    def setUp(self):
        # Explicit saved-history policy inputs; authority production/archival is
        # covered separately by the owned-process and public CLI controls.
        self.chain = {
            "root_attempt": "001/requirements-gather-01",
            "binding": "failed-launch-binding",
            "consumed": True,
            "cause": "ECONNRESET",
        }
        self.saved = {"stages": [{"stage": "requirements_gather", "rejected": True, "network_retry_chain": self.chain}]}
        self.fresh = {"stage": "requirements_gather", "engine": "opencode", "expected_session": None}
        for name, value in (("_binding", "synthetic-launch-binding"), ("_index", "synthetic-index-pin")):
            authority = patch.object(retry, name, return_value=value)
            authority.start()
            self.addCleanup(authority.stop)

    def bind(self, state, record):
        retry.bind_attempt(state, record, {}, Path("/synthetic-owned-workspace"))

    def test_unbound_rejected_attempts_cannot_renew_a_consumed_chain(self):
        controls = (
            ({"report_only": True}, None),
            ({"expected_session": "ses_prior"}, None),
            ({}, "_index"),
            ({}, "_binding"),
        )
        for fields, unavailable in controls:
            with self.subTest(fields=fields, unavailable=unavailable):
                state = copy.deepcopy(self.saved)
                intermediary = {**self.fresh, **fields}
                if unavailable:
                    with patch.object(retry, unavailable, side_effect=OSError("authority unavailable")):
                        self.bind(state, intermediary)
                else:
                    self.bind(state, intermediary)
                self.assertEqual(self.chain, intermediary.get("network_retry_chain"))
                self.assertNotIn("network_retry_binding", intermediary)
                state["stages"].append({**intermediary, "rejected": True})
                restored = dict(self.fresh)
                state = json.loads(json.dumps(state))
                self.bind(state, restored)
                self.assertEqual(self.chain, restored.get("network_retry_chain"))
                self.assertIn("network_retry_binding", restored)

    def test_command_provider_attempts_are_bound_like_the_native_engine(self):
        """#1001: a codex-interfaced attempt carries retry authority too."""
        state = copy.deepcopy(self.saved)
        intermediary = {**self.fresh, "engine": "codex"}
        self.bind(state, intermediary)
        self.assertIn("network_retry_binding", intermediary)
        self.assertEqual(self.chain, intermediary.get("network_retry_chain"))

    def test_only_accepted_ordinary_work_resets_a_saved_failed_chain(self):
        for intermediary in (
            {"stage": "resolver", "runner_owned": True},
            {"stage": "requirements_gather_report_repair", "report_only": True},
            {"stage": "requirements_gather", "rejected": True},
        ):
            with self.subTest(intermediary=intermediary):
                state = copy.deepcopy(self.saved)
                state["stages"].append(intermediary)
                restored = dict(self.fresh)
                self.bind(state, restored)
                self.assertEqual(self.chain, restored.get("network_retry_chain"))
        state = copy.deepcopy(self.saved)
        state["stages"].append({"stage": "requirements_gather", "exit_code": 0})
        accepted = dict(self.fresh)
        self.bind(state, accepted)
        self.assertIn("network_retry_binding", accepted)
        self.assertNotIn("network_retry_chain", accepted)
        no_chain = dict(self.fresh)
        self.bind({"stages": []}, no_chain)
        self.assertEqual(accepted, no_chain)


class RecoveryAdmissionTests(unittest.TestCase):
    def setUp(self):
        sleeper = patch.object(util, "sleep")  # the #704 seam: backoff never costs suite time
        sleeper.start()
        self.addCleanup(sleeper.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve()
        self.run = self.workspace / ".autocode/runs/retry"
        self.run.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", self.workspace], check=True)
        (self.workspace / "seed.txt").write_text("unchanged source\n")
        subprocess.run(["git", "-C", self.workspace, "add", "seed.txt"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                self.workspace,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=f@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "seed",
            ],
            check=True,
        )
        self.state = {
            "status": "RUNNING",
            "iteration": 1,
            "next_stage": "requirements_gather",
            "settings": {"roles": {}, "limits": {}},
            "stages": [],
        }
        self.base = self.run / "iterations/001/requirements-gather-01"
        self.base.parent.mkdir(parents=True)
        self.record = {
            "stage": "requirements_gather",
            "role": "glm",
            "engine": "opencode",
            "iteration": 1,
            "expected_session": None,
            "launch_route": {
                "engine": "opencode",
                "provider": "fixture",
                "model": "fixture-model",
                "reasoning_effort": "medium",
            },
            "events": str(self.base.with_suffix(".jsonl")),
            "output": str(self.base.with_suffix(".json")),
            "before_ref": str(self.base.with_suffix(".before.json")),
            "started_at": util.now(),
            "finished_at": util.now(),
            "duration_seconds": 1,
            "exit_code": 3,
            "processes": [],
            "metrics": {},
            "supports_sessions": True,
        }
        before = scope.snapshot(self.workspace, self.state)
        util.atomic_json(Path(self.record["before_ref"]), before)
        retry.bind_attempt(self.state, self.record, before, self.workspace)
        Path(self.record["events"]).write_text(json.dumps(failure()) + "\n")
        Path(self.record["events"]).chmod(0o400)
        result = commands.run(
            f'{sys.executable} -c "raise SystemExit(3)"',
            self.workspace,
            self.run / "owned/output.log",
            timeout=10,
            env=None,
        )
        self.assertEqual(3, result["exit_code"])
        self.record["supervision"] = result["supervision"]
        self.owned_metadata = copy.deepcopy(result["supervision"])
        self.state["active_stage"] = self.record
        self.error = util.Paused("PAUSED_PROVIDER_UNCERTAIN", "fixture network failure")

    def recover(self):
        return retry.recover(runner, self.state, self.run, self.workspace, self.error)

    def test_normal_owned_failure_archives_raw_charges_usage_and_reserves_one_retry(self):
        raw = Path(self.record["events"]).read_bytes()
        self.assertTrue(self.recover())
        archived = self.state["stages"][-1]
        self.assertEqual(raw, Path(archived["events"]).read_bytes())
        self.assertEqual(self.state["next_stage"], archived["stage"])
        self.assertTrue(archived["accounted"])
        child = {**self.record, "output": str(self.base.with_name("requirements-gather-02").with_suffix(".json"))}
        child.pop("network_retry_chain")
        retry.bind_attempt(self.state, child, scope.snapshot(self.workspace, self.state), self.workspace)
        self.assertTrue(child["network_retry_chain"]["consumed"])
        # Restarting from a saved JSON checkpoint cannot create a new allowance.
        loaded = json.loads((self.run / "state.json").read_text())
        retry.bind_attempt(loaded, child, scope.snapshot(self.workspace, loaded), self.workspace)
        self.assertTrue(child["network_retry_chain"]["consumed"])
        for marker in ({"consumed": False}, None, [], "invalid"):
            with self.subTest(marker=marker):
                loaded["stages"][-1]["network_retry_chain"] = marker
                loaded["settings"]["roles"] = {"glm": "operator-changed-model"}
                child.pop("network_retry_chain", None)
                retry.bind_attempt(loaded, child, scope.snapshot(self.workspace, loaded), self.workspace)
                self.assertIn("network_retry_chain", child)
                self.state = loaded
                self.state["active_stage"] = child
                self.assertFalse(self.recover())

    def test_missing_dirty_live_or_uncertain_ownership_refuses_without_mutation(self):
        controls = [{}, {**self.record["supervision"], "nonce": "0" * 32}]
        for metadata in controls:
            with self.subTest(metadata=metadata):
                self.record["supervision"] = metadata
                before = copy.deepcopy(self.state)
                self.assertFalse(self.recover())
                self.assertEqual(before, self.state)
        metadata = self.record["supervision"] = self.owned_metadata
        receipt_path = Path(metadata["receipt"])
        raw = receipt_path.read_bytes()
        value = json.loads(raw)
        for changed in (
            {"cleanup_error": "inspection unavailable"},
            {"cause": "controller_interrupted"},
            {"phase": "armed", "cause": None},
        ):
            with self.subTest(changed=changed):
                util.atomic_json(receipt_path, {**value, **changed})
                before = copy.deepcopy(self.state)
                self.assertFalse(self.recover())
                self.assertEqual(before, self.state)
        receipt_path.write_bytes(raw)

    def test_source_index_task_contract_settings_and_route_drift_refuse(self):
        for change in (
            lambda: self.state.update(task="changed"),
            lambda: self.state.update(current_task={"id": "new"}),
            lambda: self.state.update(goal_contract={"hash": "new"}),
            lambda: self.state["settings"].update(permission="new"),
            lambda: self.record["launch_route"].update(model="new"),
        ):
            with self.subTest(change=change):
                state = copy.deepcopy(self.state)
                change()
                before = copy.deepcopy(self.state)
                self.assertFalse(self.recover())
                self.assertEqual(before, self.state)
                self.state = state
                self.record = self.state["active_stage"]
        (self.workspace / "effect.txt").write_text("partial source effect")
        self.assertFalse(self.recover())
        (self.workspace / "effect.txt").unlink()
        original = scope.snapshot(self.workspace, self.state)
        subprocess.run(["git", "-C", self.workspace, "update-index", "--assume-unchanged", "seed.txt"], check=True)
        self.assertEqual(original, scope.snapshot(self.workspace, self.state))
        before = copy.deepcopy(self.state)
        self.assertFalse(self.recover())
        self.assertEqual(before, self.state)

    def test_timeout_interruption_session_report_human_and_partial_tools_refuse(self):
        for key, value in (
            ("timed_out", True),
            ("interrupted", True),
            ("exit_code", None),
            ("exit_code", False),
            ("exit_code", -15),
            ("expected_session", "ses_prior"),
            ("report_only", True),
            ("activity", {"completed_tool_count": 1}),
            ("activity", ["unknown"]),
            ("output", str(self.workspace / "foreign.json")),
            ("events", str(self.base.parent / "foreign.jsonl")),
            ("network_retry_chain", {"consumed": True}),
        ):
            with self.subTest(key=key, value=value):
                previous = copy.deepcopy(self.record)
                self.record[key] = value
                before = copy.deepcopy(self.state)
                self.assertFalse(self.recover())
                self.assertEqual(before, self.state)
                self.record.clear()
                self.record.update(previous)
        self.state["pending_questions"] = [{"id": "human"}]
        self.assertFalse(self.recover())
        self.state.pop("pending_questions")
        raw = (json.dumps(failure()) + "\n").encode()
        events = Path(self.record["events"])
        events.chmod(0o600)
        events.write_bytes(raw + b'{"type":"tool_use"}\n')
        events.chmod(0o400)
        before = copy.deepcopy(self.state)
        with patch.object(retry.os, "read", return_value=raw):
            self.assertFalse(self.recover())
        self.assertEqual(before, self.state)
        self.assertEqual(raw + b'{"type":"tool_use"}\n', events.read_bytes())
        Path(self.record["output"]).write_text("partial report")
        self.assertFalse(self.recover())

    def test_live_actor_or_denied_process_inspection_never_archives(self):
        for result in ([{"pid": 1}], retry.processes.ProcessError("inspection denied")):
            with (
                self.subTest(result=result),
                patch.object(
                    retry.processes,
                    "live_processes",
                    **({"side_effect": result} if isinstance(result, Exception) else {"return_value": result}),
                ),
            ):
                before = copy.deepcopy(self.state)
                self.assertFalse(self.recover())
                self.assertEqual(before, self.state)


FAULT = r"""
import json,os,subprocess,sys
from pathlib import Path
prompt=sys.stdin.read(); data=json.loads(prompt.split("CURRENT HANDOFF DATA\n",1)[1]);stage=data['stage']
root=Path(os.environ['NETWORK_FIXTURE_ROOT']); calls=root/'calls.jsonl'
rows=[json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
row={'stage':stage,'model':sys.argv[sys.argv.index('--model')+1] if '--model' in sys.argv else None}
with calls.open('a') as stream:stream.write(json.dumps(row)+'\n')
if (stage=='requirements_gather' or os.environ['NETWORK_FIXTURE_MODE']=='abandon') and (os.environ['NETWORK_FIXTURE_MODE']!='once' or not any(r['stage']==stage for r in rows)):
    if os.environ['NETWORK_FIXTURE_MODE']=='effect':
        Path('effect.txt').write_text('retained partial source')
        print(json.dumps({'type':'tool_use','sessionID':'ses_fresh','part':{'tool':'bash'}}),flush=True)
    print(json.dumps({'type':'error','sessionID':'ses_fresh','error':{'name':'APIError','data':{'message':'Connection reset by server','isRetryable':True,'metadata':{'code':'ECONNRESET','syscall':'read','message':'Connection reset by server'}}}}),flush=True)
    raise SystemExit(3)
result=subprocess.run(sys.argv[1:],input=prompt,text=True)
raise SystemExit(result.returncode)
"""

BOOTSTRAP = r"""
import contextlib,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,ROOT)
from tests import opencode_fixture_cli as fixture
import autocode_supervision as supervision
original=supervision.launch
@contextlib.contextmanager
def launch(args,*positional,**kwargs):
    if Path(str(args[0])).name=='opencode':args=[sys.executable,FAULT,*map(str,args)]
    with original(args,*positional,**kwargs) as child:yield child
with patch.object(supervision,'launch',launch):fixture.main()
"""


class CommandProviderRecoveryTests(unittest.TestCase):
    """#1001: command providers (kilocode, engine codex) recover under the same
    admission claims as the native engine — the attempt record is shared and the
    classifier decides. A command chain renews once more; certificate and native
    lineages never renew."""

    def setUp(self):
        sleeper = patch.object(util, "sleep")  # renewal backoff never costs suite time
        sleeper.start()
        self.addCleanup(sleeper.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve()
        self.run = self.workspace / ".autocode/runs/retry"
        self.run.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", self.workspace], check=True)
        (self.workspace / "seed.txt").write_text("unchanged source\n")
        subprocess.run(["git", "-C", self.workspace, "add", "seed.txt"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                self.workspace,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=f@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "seed",
            ],
            check=True,
        )
        self.state = {
            "status": "RUNNING",
            "iteration": 1,
            "next_stage": "requirements_gather",
            "settings": {"roles": {}, "limits": {}},
            "stages": [],
        }
        self.base = self.run / "iterations/001/requirements-gather-01"
        self.base.parent.mkdir(parents=True)
        self.record = {
            "stage": "requirements_gather",
            "role": "glm",
            "engine": "codex",
            "iteration": 1,
            "expected_session": None,
            "launch_route": {
                "engine": "codex",
                "provider": "kilocode",
                "model": "xiaomi-sgp/mimo-v2.6-pro",
                "reasoning_effort": "high",
            },
            "events": str(self.base.with_suffix(".jsonl")),
            "output": str(self.base.with_suffix(".json")),
            "before_ref": str(self.base.with_suffix(".before.json")),
            "started_at": util.now(),
            "finished_at": util.now(),
            "duration_seconds": 1,
            "exit_code": 3,
            "processes": [],
            "metrics": {},
            "supports_sessions": False,
        }
        before = scope.snapshot(self.workspace, self.state)
        util.atomic_json(Path(self.record["before_ref"]), before)
        retry.bind_attempt(self.state, self.record, before, self.workspace)
        # The live kilo terminal row from the #979 flap (requirements-revise-03).
        Path(self.record["events"]).write_text(
            json.dumps({"name": "APIError", "data": {"message": "Network connection failed", "isRetryable": True}})
            + "\n"
        )
        Path(self.record["events"]).chmod(0o400)
        result = commands.run(
            f'{sys.executable} -c "raise SystemExit(3)"',
            self.workspace,
            self.run / "owned/output.log",
            timeout=10,
            env=None,
        )
        self.record["supervision"] = result["supervision"]
        self.template = copy.deepcopy(self.record)  # pre-recovery shape for re-dispatched children
        self.state["active_stage"] = self.record
        self.error = util.Paused("PAUSED_PROVIDER_UNCERTAIN", "fixture network failure")

    def recover(self):
        return retry.recover(runner, self.state, self.run, self.workspace, self.error)

    def test_a_command_provider_retryable_failure_recovers(self):
        self.assertTrue(self.recover())
        chain = self.state["stages"][-1]["network_retry_chain"]
        self.assertEqual("isRetryable", chain["cause"])
        self.assertEqual(1, chain["retry_number"])

    def fresh_attempt(self, name):
        """A production-shaped re-dispatch: a clean record (an archived parent
        carries rejection markers no new attempt inherits), new paths, the chain
        inherited through bind_attempt."""
        base = self.base.with_name(name)
        child = {
            **self.template,
            "events": str(base.with_suffix(".jsonl")),
            "output": str(base.with_suffix(".json")),
            "before_ref": str(base.with_suffix(".before.json")),
            "started_at": util.now(),
            "finished_at": util.now(),
        }
        child.pop("network_retry_binding")
        child.pop("network_retry_chain", None)
        before = scope.snapshot(self.workspace, self.state)
        util.atomic_json(Path(child["before_ref"]), before)
        retry.bind_attempt(self.state, child, before, self.workspace)
        Path(child["events"]).write_text(
            json.dumps({"name": "APIError", "data": {"message": "Network connection failed", "isRetryable": True}})
            + "\n"
        )
        Path(child["events"]).chmod(0o400)
        result = commands.run(
            f'{sys.executable} -c "raise SystemExit(3)"',
            self.workspace,
            self.run / f"owned/{name}.log",
            timeout=10,
            env=None,
        )
        child["supervision"] = result["supervision"]
        return child

    def test_a_command_chain_renews_once_more_then_refuses(self):
        self.assertTrue(self.recover())
        second = self.fresh_attempt("requirements-gather-02")
        self.assertEqual(1, second["network_retry_chain"]["retry_number"])
        self.state["active_stage"] = second
        self.assertTrue(self.recover())
        self.assertEqual(2, self.state["stages"][-1]["network_retry_chain"]["retry_number"])
        third = self.fresh_attempt("requirements-gather-03")
        self.assertEqual(2, third["network_retry_chain"]["retry_number"])
        self.state["active_stage"] = third
        self.assertFalse(self.recover())

    def test_a_certificate_failure_recovers_once_only(self):
        Path(self.record["events"]).chmod(0o600)
        Path(self.record["events"]).write_text(
            json.dumps({"name": "UnknownError", "data": {"message": "unable to get local issuer certificate"}}) + "\n"
        )
        Path(self.record["events"]).chmod(0o400)
        self.assertTrue(self.recover())
        second = self.fresh_attempt("requirements-gather-02")
        self.assertEqual("certificate", second["network_retry_chain"]["cause"])
        self.state["active_stage"] = second
        self.assertFalse(self.recover())

    def test_native_and_malformed_chains_never_renew(self):
        for chain in (
            {"root_attempt": "001/x-01", "binding": "b", "consumed": True, "cause": "ECONNRESET", "retry_number": 1},
            {"root_attempt": "001/x-01", "binding": "b", "consumed": False, "cause": "isRetryable", "retry_number": 1},
            {"root_attempt": "001/x-01", "binding": "b", "consumed": True, "cause": "isRetryable", "retry_number": 2},
            {"root_attempt": "001/x-01", "binding": "b", "consumed": True, "cause": "isRetryable", "surprise": 1},
        ):
            with self.subTest(chain=chain):
                self.record["network_retry_chain"] = chain
                self.assertFalse(self.recover())


class NetworkRetryCliTests(unittest.TestCase):
    def setUp(self):
        test_subprocess.SubprocessFlow.setUp(self)
        source = Path(__file__).resolve().parents[1]
        opencode = self.root / "fixture-bin/opencode"
        shutil.copy2(source / "tools/fake_opencode.py", opencode)
        opencode.chmod(0o755)
        fault = self.root / "network-provider.py"
        fault.write_text(FAULT)
        cli = self.root / "network-cli.py"
        cli.write_text("ROOT=" + repr(str(source)) + "\nFAULT=" + repr(str(fault)) + "\n" + BOOTSTRAP)
        self.command = (sys.executable, str(cli), str(source / "tools/autocode.py"))
        self.env.update(NETWORK_FIXTURE_ROOT=str(self.root), NETWORK_FIXTURE_MODE="once")
        self.options = ("--engine", "opencode", "--workflow", "build", "--unit", "autoplanner")

    def start(self):
        return TaskRun.start(
            self.project,
            "Build a greeting CLI",
            command=self.command,
            options=self.options,
            env=self.env,
            cwd=self.root,
            timeout=60,
        )

    def calls(self):
        return [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]

    def test_one_retry_reaches_real_question_frontier_and_keeps_both_attempts(self):
        run = self.start()
        view = run.status()
        self.assertEqual("answer", view["needs"]["kind"])
        self.assertFalse(view["done"])
        calls = self.calls()
        self.assertEqual(
            ["requirements_gather", "requirements_gather", "astra_discovery"], [row["stage"] for row in calls]
        )
        self.assertEqual(calls[0], calls[1])
        attempts = [row for row in view["usage"]["accounting"]["attempts"] if not row["runner_owned"]]
        self.assertEqual(3, len(attempts))
        self.assertEqual([3, 0, 0], [row["exit_code"] for row in attempts])
        self.assertEqual([row["stage"] for row in calls], [row["stage"] for row in attempts])
        raw = Path(attempts[0]["events"]).read_bytes()
        self.assertEqual("ECONNRESET", retry.retryable_error(raw.decode()))
        self.assertNotEqual(attempts[0]["events"], attempts[1]["events"])
        self.assertGreaterEqual(sum(row["duration_seconds"] for row in attempts), 0)
        self.assertEqual(raw, Path(attempts[0]["events"]).read_bytes())

    def test_second_failure_and_fresh_reattachment_cannot_launch_a_third_attempt(self):
        self.env["NETWORK_FIXTURE_MODE"] = "abandon"
        run = self.start()
        view = run.status()
        self.assertEqual("WAITING_FOR_USER", view["status"])
        self.assertEqual(2, len(self.calls()))
        attempts = [row for row in view["usage"]["accounting"]["attempts"] if not row["runner_owned"]]
        self.assertEqual([3, 3], [row["exit_code"] for row in attempts])
        before = [Path(row["events"]).read_bytes() for row in attempts]
        attached = TaskRun.attach(
            self.project, command=self.command, options=self.options, env=self.env, cwd=self.root, timeout=60
        )
        view = attached.resume_paused()
        self.assertEqual("WAITING_FOR_USER", view["status"])
        self.assertEqual(2, len(self.calls()))
        self.assertFalse(view["done"])
        self.assertEqual(
            before,
            [
                Path(row["events"]).read_bytes()
                for row in view["usage"]["accounting"]["attempts"]
                if not row["runner_owned"]
            ],
        )
        # Abandonment preserves the human gate and cannot renew the chain's
        # automatic allowance; a plain resume still launches no new provider.
        attached.abandon_stage(attempts[-1]["attempt_id"])
        view = attached.resume_paused()
        self.assertEqual("PAUSED_STAGE_ABANDONED", view["status"])
        self.assertEqual(2, len(self.calls()))
        retained = [row for row in view["usage"]["accounting"]["attempts"] if not row["runner_owned"]]
        self.assertEqual(before, [Path(row["events"]).read_bytes() for row in retained])
        attached.resume_paused()
        self.assertEqual(2, len(self.calls()))

    def test_observed_tool_and_source_effect_hold_with_one_call(self):
        self.env["NETWORK_FIXTURE_MODE"] = "effect"
        run = self.start()
        view = run.status()
        self.assertEqual("WAITING_FOR_USER", view["status"])
        self.assertEqual(1, len(self.calls()))
        self.assertFalse(view["done"])
        self.assertEqual("retained partial source", (self.project / "effect.txt").read_text())
        run.resume_paused()
        self.assertEqual(1, len(self.calls()))
