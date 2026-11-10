"""Public coordinated questions preserve peer work; changes enter normal review."""

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import autocode_coordination_budget as budget
import autocode_coordination_conversation as conversation
import autocode_coordination_store as store
import autocode_taskrun as taskrun

from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class CoordinatedConversationCliTests(unittest.TestCase):
    def test_held_peer_allows_requirements_reply_without_consuming_pause_or_replanning(self):
        workspace, env = fixture_workspace(self)
        peer_workspace, _ = fixture_workspace(self)
        self.enterContext(patch.dict(os.environ, env))
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        calls = provider.parent / "held-conversation-calls"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        amendment = textwrap.dedent(f"""
            with Path({str(calls)!r}).open('a') as log:
                log.write(json.dumps({{'stage': stage, 'message': data.get('message')}}) + '\\n')
            if stage == 'requirements_gather' and data.get('message'):
                message = data['message']
                changing = message['text'].startswith('Please change')
                report = {{'message_id': message['id'], 'contract_token': message['contract_token'],
                          'intent': 'change' if changing else 'question',
                          'assistant_reply': 'Peer plan needs review.' if changing else 'The request may stay empty.',
                          'affected_targets': ['peer'] if changing else []}}
        """)
        self.assertIn(marker, provider.read_text())
        provider.write_text(provider.read_text().replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        source = taskrun.TaskRun.start(
            workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"), env=env, timeout=240
        )
        source_before = source.advance_until_input()
        peer = taskrun.TaskRun.start(
            peer_workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--no-adaptive-planning"), env=env, timeout=240
        )
        peer.advance_until_input()
        response = subprocess.run(
            [
                *taskrun.AUTOCODE,
                "intervention",
                "submit",
                "--workspace",
                str(peer.workspace),
                "--run-dir",
                str(peer.run_dir),
                "--request-id",
                "independent-pause",
                "--kind",
                "pause",
                "--json",
            ],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, response.returncode, response.stdout + response.stderr)
        self.assertTrue(json.loads(response.stdout)["accepted"])
        peer_before = peer.intervention_status()
        self.assertIn("independent-pause", peer_before["interventions"]["pending_ids"])
        before_calls = calls.read_text().splitlines()
        store.save(
            {
                "version": 1,
                "id": "held-conversation-fixture",
                "anchor": str(source.run_dir),
                "phase": "fixing",
                "task": BRIEF,
                "request_sources": [],
                "choices": {},
                "initial_scope": [],
                "joint_attempts": [],
                "approved_contracts": {"token": "retained"},
                "members": [
                    {
                        "target": "peer",
                        "workspace": str(peer.workspace),
                        "run_dir": str(peer.run_dir),
                        "base": "fixture",
                        "started": True,
                        "brief": BRIEF,
                    }
                ],
                "budget": {**budget.initial(source_before), "cap_usd": 0},
            }
        )

        def invoke(operation, *args):
            response = subprocess.run(
                [*taskrun.AUTOCODE, "coordinate", operation, "--run-dir", str(source.run_dir), *args, "--json"],
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                timeout=240,
            )
            self.assertEqual(0, response.returncode, response.stdout + response.stderr)
            return json.loads(response.stdout)

        for index, text in enumerate(("Can the request stay empty?", "Please change the result code only.")):
            identifier = "held-message-" + str(index)
            receipt = invoke("message", "--request-id", identifier, "--text", text)
            self.assertTrue(receipt["accepted"])
            for _ in range(12):
                result = invoke("advance")
                answers = result["conversation"]["results"]
                if any(row["request_id"] == identifier for row in answers):
                    break
                self.assertIn(result["needs"]["kind"], ("conversation", "watch"), result["needs"])
            else:
                self.fail("Requirements did not answer the held task message")
            answer = next(row for row in answers if row["request_id"] == identifier)
            self.assertEqual("change" if index else "question", answer["reply"]["conversation"]["intent"])
            self.assertTrue(answer["answered_at"])
            after_capture = calls.read_text().splitlines()
            settled = invoke("advance")
            self.assertEqual("blocked", settled["needs"]["kind"])
            self.assertEqual(answers, settled["conversation"]["results"])
            self.assertEqual(answers, invoke("status")["conversation"]["results"])
            retry = invoke("message", "--request-id", identifier, "--text", text)
            self.assertTrue(retry["idempotent"])
            self.assertEqual(receipt["receipt"], retry["receipt"])
            self.assertEqual("blocked", invoke("advance")["needs"]["kind"])
            self.assertEqual(after_capture, calls.read_text().splitlines())
            peer_now = peer.intervention_status()
            self.assertEqual(peer_before["interventions"], peer_now["interventions"])
            for field in ("needs", "displayed_plan", "approved_contract", "usage", "request_sources"):
                self.assertEqual(peer_before["view"].get(field), peer_now["view"].get(field), field)
            self.assertEqual({"token": "retained"}, store.read(source.run_dir)["approved_contracts"])
        new_calls = [json.loads(row) for row in calls.read_text().splitlines()[len(before_calls) :]]
        self.assertEqual(2, len(new_calls), new_calls)
        self.assertTrue(all(row["stage"] == "requirements_gather" and row["message"] for row in new_calls))
        self.assertEqual(source_before["request_sources"], source.status()["request_sources"])
        self.assertFalse((peer_workspace / "greet.py").exists())

    def test_message_during_owned_planner_pauses_answers_and_resumes_without_a_second_draft(self):
        workspace, env = fixture_workspace(self)
        peer_workspace, _ = fixture_workspace(self)
        self.enterContext(patch.dict(os.environ, env))
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        gate, ready, calls = (provider.parent / name for name in ("planner-gate", "planner-ready", "planner-calls"))
        os.mkfifo(gate)
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        amendment = textwrap.dedent(f"""
            with Path({str(calls)!r}).open('a') as log:
                log.write(stage + (' conversation' if data.get('message') else '') + '\\n')
            if stage == 'draft_plan' and not Path({str(ready)!r}).exists():
                Path({str(ready)!r}).write_text('ready')
                with Path({str(gate)!r}).open() as signal:
                    signal.read()
            if stage == 'requirements_gather' and data.get('message'):
                message = data['message']
                report = {{'message_id': message['id'], 'contract_token': message['contract_token'],
                           'intent': 'question', 'assistant_reply': 'We should assess conversion fidelity.', 'affected_targets': []}}
        """)
        self.assertIn(marker, provider.read_text())
        provider.write_text(provider.read_text().replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        source = taskrun.TaskRun.start(
            workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"), env=env, timeout=240
        )
        source_before = source.advance_until_input()
        peer = taskrun.TaskRun.start(
            peer_workspace,
            BRIEF,
            options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"),
            env=env,
            timeout=240,
        )
        gathered = peer.advance_until_input()
        question = gathered["needs"]["questions"][-1]
        peer.answer(question["id"], "plan", resolver_token=gathered["needs"]["resolver_token"])
        store.save(
            {
                "version": 1,
                "id": "live-question-fixture",
                "anchor": str(source.run_dir),
                "phase": "fixing",
                "task": BRIEF,
                "request_sources": [],
                "choices": {},
                "initial_scope": [],
                "joint_attempts": [],
                "members": [
                    {
                        "target": "peer",
                        "workspace": str(peer.workspace),
                        "run_dir": str(peer.run_dir),
                        "base": "fixture",
                        "started": True,
                        "brief": BRIEF,
                    }
                ],
                "budget": {**budget.initial(source_before), "cap_usd": 0},
            }
        )

        def invoke(operation, *args):
            response = subprocess.run(
                [*taskrun.AUTOCODE, "coordinate", operation, "--run-dir", str(source.run_dir), *args, "--json"],
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                timeout=240,
            )
            self.assertEqual(0, response.returncode, response.stdout + response.stderr)
            return json.loads(response.stdout)

        with ThreadPoolExecutor(max_workers=1) as executor:
            advancing = executor.submit(invoke, "advance")
            try:
                deadline = time.monotonic() + 45
                while not ready.exists() and not advancing.done() and time.monotonic() < deadline:
                    peer.status()  # Observe the real owned process; no arbitrary delay.
                self.assertTrue(ready.exists(), advancing.result() if advancing.done() else "Planner did not start")
                saved = invoke("message", "--request-id", "live-question", "--text", "Could we reuse the conversion?")
                self.assertTrue(saved["accepted"])
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    public = peer.intervention_status()
                    if public["interventions"]["pending_ids"]:
                        break
                    self.assertFalse(advancing.done(), "The held Planner must still own its result")
                else:
                    self.fail("No public pause receipt arrived during the active Planner")
            finally:
                if ready.exists():
                    try:
                        descriptor = os.open(gate, os.O_WRONLY | os.O_NONBLOCK)
                    except OSError:
                        pass  # The provider may already have exited after a failed assertion.
                    else:
                        with os.fdopen(descriptor, "w") as release:
                            release.write("continue")
            advancing.result(timeout=180)
        self.assertEqual("PAUSED_INTERVENTION", peer.status()["status"])
        for _ in range(18):
            result = invoke("advance")
            if result["conversation"]["results"] and peer.status()["needs"]["kind"] == "approve_plan":
                break
            self.assertIn(result["needs"]["kind"], ("conversation", "watch"), result["needs"])
        else:
            self.fail(str(result["needs"]))
        events = calls.read_text().splitlines()
        self.assertEqual(1, events.count("draft_plan"), events)
        self.assertEqual(1, events.count("requirements_gather conversation"), events)
        self.assertNotIn("builder", events)
        approved = peer.status()["needs"]["token"]
        retry = invoke("message", "--request-id", "live-question", "--text", "Could we reuse the conversion?")
        self.assertTrue(retry["idempotent"])
        self.assertEqual(1, len(invoke("status")["conversation"]["results"]))
        self.assertEqual(approved, peer.status()["needs"]["token"])
        self.assertEqual(events, calls.read_text().splitlines())

    def test_owned_pause_restores_published_scope_without_repeating_planner_or_answering(self):
        workspace, env = fixture_workspace(self)
        peer_workspace, _ = fixture_workspace(self)
        self.enterContext(patch.dict(os.environ, env))
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        calls = provider.parent / "scope-conversation-calls"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        amendment = textwrap.dedent(f"""
            with Path({str(calls)!r}).open('a') as log:
                log.write(stage + (' conversation' if data.get('message') else '') + '\\n')
            if stage == 'draft_plan':
                report['workspace_scope'] = {{'spans_repositories': True, 'external_parts': [{{
                    'target': 'history', 'work': 'Preserve the history result',
                    'source_quote': 'history', 'evidence': ''}}]}}
            if stage == 'requirements_gather' and data.get('message'):
                message = data['message']
                report = {{'message_id': message['id'], 'contract_token': message['contract_token'],
                           'intent': 'question', 'assistant_reply': 'Evidence is needed.', 'affected_targets': []}}
        """)
        self.assertIn(marker, provider.read_text())
        provider.write_text(provider.read_text().replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        source = taskrun.TaskRun.start(
            workspace,
            BRIEF,
            options=(*FIXTURE_OPTIONS, "--max-cost-usd", "0", "--spec-first", "--no-adaptive-planning"),
            env=env,
            timeout=240,
        )
        source_before = source.advance_until_input()
        peer = taskrun.TaskRun.start(
            peer_workspace,
            BRIEF + " Coordinate with history.",
            options=(*FIXTURE_OPTIONS, "--max-cost-usd", "0", "--spec-first", "--no-adaptive-planning"),
            env=env,
            timeout=240,
        )
        gathered = peer.advance_until_input()
        question = gathered["needs"]["questions"][-1]
        peer.answer(question["id"], "plan", resolver_token=gathered["needs"]["resolver_token"])
        scoped = peer.advance_until_input()
        self.assertEqual("WAITING_FOR_USER", scoped["status"])
        self.assertTrue(scoped["needs"]["questions"][0]["id"].startswith("workspace-scope-"))
        document = {
            "version": 1,
            "id": "scope-conversation-fixture",
            "anchor": str(source.run_dir),
            "phase": "fixing",
            "task": BRIEF,
            "request_sources": [],
            "choices": {},
            "initial_scope": [],
            "joint_attempts": [],
            "members": [
                {
                    "target": "peer",
                    "workspace": str(peer.workspace),
                    "run_dir": str(peer.run_dir),
                    "base": "fixture",
                    "started": True,
                    "brief": BRIEF,
                }
            ],
            "budget": {**budget.initial(source_before), "cap_usd": 0},
        }
        store.save(document)

        def invoke(operation, *args):
            response = subprocess.run(
                [*taskrun.AUTOCODE, "coordinate", operation, "--run-dir", str(source.run_dir), *args, "--json"],
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                timeout=240,
            )
            self.assertEqual(0, response.returncode, response.stdout + response.stderr)
            return json.loads(response.stdout)

        identifier, text = "scope-question", "What evidence is still needed?"
        receipt = invoke("message", "--request-id", identifier, "--text", text)
        pause_id = conversation.request_id(document, {"id": identifier}, "peer", "pause")
        paused = subprocess.run(
            [
                *taskrun.AUTOCODE,
                "intervention",
                "submit",
                "--workspace",
                str(peer.workspace),
                "--run-dir",
                str(peer.run_dir),
                "--request-id",
                pause_id,
                "--kind",
                "pause",
                "--json",
            ],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, paused.returncode, paused.stdout + paused.stderr)
        self.assertTrue(json.loads(paused.stdout)["accepted"])
        for _ in range(18):
            result = invoke("advance")
            current = peer.status()
            if result["conversation"]["results"] and current["status"] == "WAITING_FOR_USER":
                break
            self.assertIn(result["needs"]["kind"], ("conversation", "watch"), result["needs"])
        else:
            self.fail(str(result["needs"]))
        self.assertEqual(scoped["needs"], current["needs"])
        self.assertEqual(scoped["usage"], current["usage"])
        self.assertIsNone(current.get("approved_contract"))
        retry = invoke("message", "--request-id", identifier, "--text", text)
        self.assertEqual(receipt["receipt"], retry["receipt"])
        self.assertTrue(retry["idempotent"])
        self.assertEqual(result["conversation"]["results"], invoke("status")["conversation"]["results"])
        events = calls.read_text().splitlines()
        self.assertEqual(1, events.count("draft_plan"), events)
        self.assertEqual(1, events.count("requirements_gather conversation"), events)
        self.assertNotIn("builder", events)

    def test_question_preserves_plans_and_change_delivers_only_original_text_with_fresh_review(self):
        workspace, env = fixture_workspace(self)
        peer_origin, _ = fixture_workspace(self)
        peer_workspace = peer_origin.parent / "peer-worktree"
        subprocess.run(
            ["git", "-C", str(peer_origin), "worktree", "add", "--detach", str(peer_workspace), "HEAD"],
            check=True,
            capture_output=True,
        )
        self.enterContext(patch.dict(os.environ, env))
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        calls = provider.parent / "conversation-calls"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        amendment = textwrap.dedent(f"""
            with Path({str(calls)!r}).open('a') as log:
                log.write(json.dumps({{'stage': stage, 'message': data.get('message')}}) + '\\n')
            if stage == 'requirements_gather' and data.get('message'):
                message = data['message']
                assert message['context']['participants'][0]['target'] == 'peer'
                changing = message['text'].startswith('Please change')
                report = {{'message_id': message['id'], 'contract_token': message['contract_token'],
                          'intent': 'change' if changing else 'question',
                          'assistant_reply': 'Peer plan needs review.' if changing else 'Conversion needs a fidelity check.',
                          'affected_targets': ['peer'] if changing else []}}
        """)
        self.assertIn(marker, provider.read_text())
        provider.write_text(provider.read_text().replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        source = taskrun.TaskRun.start(
            workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"), env=env, timeout=240
        )
        source_before = source.advance_until_input()
        peer = taskrun.TaskRun.start(
            peer_workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--no-adaptive-planning"), env=env, timeout=240
        )
        planned = peer.advance_until_input()
        self.assertEqual("approve_plan", planned["needs"]["kind"])
        profile = workspace.parent / "related-profile.json"
        profile.write_text(json.dumps(source_before["related_profile"]))
        saved = {
            "version": 1,
            "id": "conversation-fixture",
            "anchor": str(source.run_dir),
            "phase": "fixing",
            "task": BRIEF,
            "profile": str(profile),
            "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
            "initial_scope": [],
            "choices": {},
            "joint_attempts": [],
            "request_sources": [],
            "approved_contracts": {"token": "retained-evidence"},
            "members": [
                {
                    "target": "peer",
                    "slug": "peer",
                    "origin": str(peer_origin),
                    "git_dir": str(peer_origin / ".git"),
                    "workspace": str(peer.workspace),
                    "run_dir": str(peer.run_dir),
                    "base": subprocess.check_output(
                        ["git", "-C", str(peer_workspace), "rev-parse", "HEAD"], text=True
                    ).strip(),
                    "started": True,
                    "brief": BRIEF,
                }
            ],
            "budget": {**budget.initial(source_before), "cap_usd": 0},
        }
        store.save(saved)
        before_calls = calls.read_text().splitlines()

        def invoke(operation, *args):
            response = subprocess.run(
                [*taskrun.AUTOCODE, "coordinate", operation, "--run-dir", str(source.run_dir), *args, "--json"],
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                timeout=180,
            )
            if response.returncode:
                active = (store.read(source.run_dir).get("conversation") or {}).get("active") or {}
                print(
                    json.dumps(
                        {
                            "operation": operation,
                            "active_message": active.get("id"),
                            "reply_captured": bool(active.get("reply")),
                            "source": source.intervention_status(),
                            "peer": peer.intervention_status(),
                        }
                    )
                )
            self.assertEqual(0, response.returncode, response.stdout + response.stderr)
            return json.loads(response.stdout)

        for index, text in enumerate(
            ("Could we reuse the conversion?", "Please change the peer to preserve the result.")
        ):
            identifier = "message-" + str(index)
            calls_before_submit = calls.read_text().splitlines()
            with store.locked(source.run_dir):
                receipt = invoke("message", "--request-id", identifier, "--text", text)
            self.assertTrue(receipt["accepted"])
            self.assertEqual(calls_before_submit, calls.read_text().splitlines())
            for _ in range(18):
                result = invoke("advance")
                answers = result["conversation"]["results"]
                routing = result["needs"]
                if any(row["request_id"] == identifier for row in answers) and (
                    routing["kind"] not in ("conversation", "watch") or routing.get("operation") == "resume"
                ):
                    break
                self.assertIn(result["needs"]["kind"], ("conversation", "watch"), result["needs"])
            else:
                self.fail(str(result["needs"]))
            answer = next(row for row in answers if row["request_id"] == identifier)
            self.assertEqual(text, answer["text"])
            self.assertEqual("change" if index else "question", answer["reply"]["conversation"]["intent"])
            retry = invoke("message", "--request-id", identifier, "--text", text)
            self.assertEqual(receipt["receipt"], retry["receipt"])
            self.assertTrue(retry["idempotent"])
            reopened = invoke("status")
            self.assertEqual(result["conversation"]["results"], reopened["conversation"]["results"])
            peer_now = peer.status()
            if not index:
                self.assertEqual(planned["needs"], peer_now["needs"])
                self.assertEqual(planned["usage"]["accounting"], peer_now["usage"]["accounting"])
                self.assertEqual({"token": "retained-evidence"}, store.read(source.run_dir)["approved_contracts"])
            else:
                self.assertIsNone(peer_now.get("approved_contract"))
                self.assertEqual("PAUSED_INTERVENTION", peer_now["status"])
                feedback = [
                    row for row in peer_now["chat"]["messages"] if row.get("role") == "user" and row.get("text") == text
                ]
                self.assertEqual(1, len(feedback))
                self.assertNotIn("approved_contracts", store.read(source.run_dir))
        stages = [json.loads(row) for row in calls.read_text().splitlines()]
        self.assertEqual(2, len([row for row in stages if row.get("message")]))
        self.assertEqual([json.loads(row) for row in before_calls], [row for row in stages if not row.get("message")])
        self.assertEqual(source_before["request_sources"], source.status()["request_sources"])
        self.assertFalse((peer_workspace / "greet.py").exists())
        self.assertEqual("approve_plan", peer.resume_paused()["needs"]["kind"])
        member_token = peer.status()["needs"]["token"]
        started = invoke("advance")
        integration = started["integration"]
        self.assertIsNotNone(integration, started["needs"])
        joint = taskrun.TaskRun(Path(integration["workspace"]), Path(integration["run_dir"]), env=env)
        joint_task = joint.status()["task"]
        self.assertIn("Applied task message for peer:\nPlease change the peer to preserve the result.", joint_task)
        self.assertEqual(1, joint_task.count("Please change the peer to preserve the result."))
        self.assertNotIn("Could we reuse the conversion?", joint_task)
        self.assertNotIn("Peer plan needs review.", joint_task)
        self.assertEqual(integration, invoke("status")["integration"])
        self.assertEqual(joint_task, joint.status()["task"])
        self.assertEqual(member_token, peer.status()["needs"]["token"])
        self.assertIsNone(joint.status().get("approved_contract"))
        self.assertFalse((peer_workspace / "greet.py").exists())
