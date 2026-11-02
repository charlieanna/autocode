"""A coordinated member owns the foreground while anchor polling continues."""

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_chat_client as chat
import autocode_chat_coordination as coordination


class ApprovalPacketTests(unittest.TestCase):
    def test_identical_joint_summary_is_displayed_once_without_hiding_distinct_plans(self):
        for role, included, count in (
            ("joint", {"token": "r6:joint", "body": "Joint plan text"}, 1),
            ("joint", {"token": "r5:joint", "body": "Earlier joint text"}, 1),
            ("member", {"token": "r6:joint", "body": "Joint plan text"}, 2),
        ):
            with self.subTest(role=role, token=included["token"]):
                summary = {
                    "members": [],
                    "needs": {
                        "kind": "approval",
                        "summary": {"runs": [{"target": "Joint regression", "role": role, "plan": included}]},
                        "joint_plan": {"token": "r6:joint", "body": "Joint plan text"},
                    },
                    "approval": {"publication": "No automatic merge."},
                }
                text = coordination.approval_text(summary, lambda plan: plan["body"])
                self.assertEqual(count, text.count("Joint plan text"))
                self.assertIn(included["body"], text, "a different or non-joint plan must remain visible")

    def test_packet_preserves_all_plans_provenance_and_publication_in_order(self):
        summary = {
            "source_catalog": {"catalog": "local", "catalog_identity": "catalog-id", "source_snapshot": "snapshot"},
            "members": [
                {
                    "target": "api",
                    "workspace": "/api",
                    "base": "base-api",
                    "supporting_evidence": {
                        "source_quote": "original symptom",
                        "evidence": "entry.go:12",
                        "evidence_repository": "/caller",
                        "evidence_revision": "caller-base",
                    },
                },
                {"target": "history", "location": "/history", "role": "reader", "base": "base-history"},
            ],
            "needs": {
                "kind": "approval",
                "summary": {
                    "runs": [
                        {"target": "api", "plan": "API COMPLETE PLAN"},
                        {"target": "history", "plan": "HISTORY COMPLETE PLAN"},
                    ]
                },
                "joint_plan": "JOINT COMPLETE PLAN",
            },
            "approval": {"publication": "Publish only after independent proof. No automatic merge."},
        }
        text = coordination.approval_text(summary, lambda plan: plan)
        for expected in (
            "local",
            "catalog-id",
            "snapshot",
            "Repository: api · role member",
            "/api",
            "base-api",
            "original symptom",
            "/caller@caller-base:entry.go:12",
            "Repository: history · role reader",
            "/history",
            "base-history",
            "API COMPLETE PLAN",
            "HISTORY COMPLETE PLAN",
            "JOINT COMPLETE PLAN",
            "Publish only after independent proof. No automatic merge.",
        ):
            self.assertIn(expected, text)
        self.assertLess(text.index("API COMPLETE PLAN"), text.index("HISTORY COMPLETE PLAN"))
        self.assertLess(text.index("HISTORY COMPLETE PLAN"), text.index("JOINT COMPLETE PLAN"))
        self.assertTrue(text.endswith(summary["approval"]["publication"]))

    def test_joint_only_approval_displays_its_plan_and_publication(self):
        summary = {
            "needs": {"kind": "joint_approval", "plan": "Complete revised joint plan"},
            "approval": {"publication": "No automatic merge."},
        }
        self.assertEqual(
            "\nJoint regression\nComplete revised joint plan\nNo automatic merge.",
            coordination.approval_text(summary, lambda plan: plan),
        )


class CoordinatedLiveScreenTests(unittest.TestCase):
    def test_approval_opens_at_the_complete_packets_start_not_the_last_repository(self):
        from tests import ROOT
        from tests.terminal_screen import TerminalScreen, require_tmux

        script = """
import sys
sys.path.insert(0, 'tools')
from types import SimpleNamespace
from unittest.mock import patch
import autocode_chat_coordination as coordination
from autocode_chat_tui import ChatTUI
summary = {
    'done': False,
    'members': [{'target': name, 'workspace': '/' + name, 'status': 'AWAITING_GOAL_APPROVAL'}
                for name in ('api', 'history')],
    'needs': {'kind': 'approval', 'summary': {'runs': [
        {'target': name, 'plan': '\\n'.join(name + ' plan row ' + str(n) for n in range(30))}
        for name in ('api', 'history')]},
        'joint_plan': '\\n'.join('joint plan row ' + str(n) for n in range(30))},
    'approval': {'publication': 'Only publish after independent proof.'},
}
with ChatTUI() as ui:
    client = SimpleNamespace(say=ui.say, ask=ui.ask, said=set())
    with patch.object(coordination, 'read', return_value=summary):
        try:
            coordination.step(client, {}, lambda plan: plan)
        except EOFError:
            ui.say('No approval given.')
"""
        with TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=110, rows=25, tmux=require_tmux(self)
        ) as screen:
            screen.wait_for(
                lambda shown: "Approve these coordinated plans?" in shown and "Repository: api" in shown,
                timeout=5,
                what="the first repository at the top of the complete approval packet",
            )
            for _ in range(12):
                screen.keys("NPage")
            screen.wait_for(
                lambda shown: "joint plan row 29" in shown and "Only publish after independent proof." in shown,
                what="the final plan and publication restriction remain reachable",
            )
            screen.keys("C-d")
            self.assertEqual(0, screen.exit_status())

    def test_real_terminal_shows_member_log_during_one_gated_coordinator_action(self):
        self.assert_live_terminal()

    def test_real_terminal_follows_joint_identity_published_during_start(self):
        self.assert_live_terminal(joint_start=True)

    def assert_live_terminal(self, joint_start=False):
        from tests import ROOT
        from tests.terminal_screen import TerminalScreen, require_tmux

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            gate = root / "gate"
            os.mkfifo(gate)
            backend = root / "backend.py"
            backend.write_text("""
import json, os, sys
from pathlib import Path
root = Path(sys.argv[1])
anchor, member = str(root / 'anchor'), str(root / 'member')
joint_start = sys.argv[2] == 'joint'
args = sys.argv[3:]
command = ['autocode', 'coordinate', 'advance', '--run-dir', anchor, '--json']
def ledger(run, spend, tokens, complete=True):
    return {'run_dir': run, 'cost': {'spent_usd': spend, 'complete': complete},
            'usage': {'accounting': {'tokens': {'input_tokens': tokens, 'output_tokens': 0}}}}
accounting = {'version': 1, 'anchor': anchor, 'runs': [ledger(anchor, 1, 100),
              ledger(str(root / 'previous'), 2, 200), ledger(member, 0, 0, False)]}
if joint_start:
    accounting['runs'].pop()
if args[:2] == ['coordinate', 'status']:
    print(json.dumps({'version': 1, 'anchor': anchor, 'done': False, 'accounting': accounting,
        'members': [] if joint_start else [{'target': 'api', 'workspace': str(root), 'run_dir': member, 'status': 'RUNNING'}],
        'needs': {'kind': 'joint_plan_start'} if joint_start else {'kind': 'start', 'target': 'api'},
        'next_command': command}))
elif args[:2] == ['coordinate', 'advance']:
    with (root / 'advances').open('a') as stream:
        stream.write('anchor\\n')
    with (root / 'gate').open() as stream:
        stream.read()
elif '--status' in args:
    run = args[args.index('--run-dir') + 1]
    if run == anchor and (root / 'fail-anchor-status').exists():
        raise SystemExit(1)
    view = {**ledger(run, 0 if run == member else 1, 0 if run == member else 100, run != member), 'workspace': str(root), 'run_dir': run, 'status': 'RUNNING' if run == member else 'WAITING_FOR_USER',
            'needs': {'kind': 'continue'}, 'chat': {'log': []}}
    if run == member:
        view['chat']['log'] = [{'id': 'member-message', 'agent': 'Requirements', 'kind': 'message',
                               'text': 'Inspecting the actual request path'}]
    else:
        view['coordination'] = {'status_command': ['autocode', 'coordinate', 'status', '--run-dir', anchor, '--json']}
        if joint_start and (root / 'advances').exists():
            view['coordination']['integration'] = {'workspace': str(root), 'run_dir': member}
    print(json.dumps({'view': view}))
else:
    raise SystemExit('Unexpected command: ' + repr(args))
""")
            script = f"""
import sys
sys.path.insert(0, {str(ROOT / "tools")!r})
from pathlib import Path
from autocode_chat_client import ChatClient
from autocode_chat_tui import ChatTUI
import autocode_chat_coordination as coordination
root = Path({str(root)!r})
said = []
with ChatTUI() as ui:
    def say(text):
        said.append(text)
        ui.say(text)
    meter_options = dict(observe_coordination=ui.observe_coordination) if hasattr(ui, "observe_coordination") else dict()
    client = ChatClient(root, root / 'anchor', command=(sys.executable, {str(backend)!r}, str(root), {"joint" if joint_start else "member"!r}),
                        observe=ui.observe, **meter_options, unavailable=ui.status_unavailable, say=say,
                        ask=ui.ask, wait=ui.wait, poll_seconds=0.1)
    ui.bind(client.slash, run=True)
    ui.idle = client.poll
    client.tabs_changed = ui.tabs
    try:
        coordination.step(client, client.status(), lambda value: str(value))
        client.show(client.status())
        ui.say('Coordinator action finished')
        ui.ask('Close this test viewer?')
    finally:
        client.close()
        (root / 'transcript').write_text('\\n'.join(said))
"""
            screen = TerminalScreen(
                [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=120, rows=30, tmux=require_tmux(self)
            )
            self.addCleanup(screen.close)
            screen.wait_for(
                lambda text: (
                    "▌ Requirements" in text
                    and "LIVE · all agents" not in text
                    and "Inspecting the actual request path" in text
                    and "task $3.00+" in text
                    and "300 tokens" in text
                    and "TASK USAGE" in text
                ),
                what="active coordinated member live log and stable task total",
                timeout=10,
            )
            screen.type("/help")
            screen.keys("Enter")
            screen.wait_for("List these commands", what="responsive help during the coordinated action")
            self.assertEqual("anchor\n", (root / "advances").read_text())
            # Help shares the conversation now; earlier progress remains reachable
            # by paging while the persistent status indicator continues updating.
            for _ in range(8):
                screen.keys("PPage")
            screen.wait_for("Inspecting the actual request path", what="member progress retained above help")
            outage = root / "fail-anchor-status"
            outage.touch()
            screen.wait_for(
                lambda text: "unavailable" in text and "Inspecting the actual request path" in text,
                what="anchor outage remains visible without hiding the active member",
            )
            outage.unlink()
            screen.wait_for(
                lambda text: "unavailable" not in text and "Inspecting the actual request path" in text,
                timeout=10,
                what="recovered anchor indicator without a stale outage in the transcript",
            )
            for _ in range(8):
                screen.keys("NPage")
            with gate.open("w") as release:
                release.write("done")
            screen.wait_for(
                lambda text: "Coordinator action finished" in text and "task $3.00+" in text and "300 tokens" in text,
                what="task total after returning to the coordinator",
            )
            screen.keys("Enter")
            self.assertEqual(0, screen.exit_status())
            self.assertNotIn("Status unavailable (anchor):", (root / "transcript").read_text())
            self.assertNotIn("Status available again (anchor).", (root / "transcript").read_text())


class UncappedRecoveryScreenTests(unittest.TestCase):
    def test_real_terminal_renders_uncapped_recovery_and_accepts_exact_resume(self):
        from tests import ROOT
        from tests.terminal_screen import TerminalScreen, require_tmux

        script = """
import sys
sys.path.insert(0, 'tools')
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import autocode_chat_coordination as coordination
from autocode_chat_tui import ChatTUI
run = Path('/tmp/uncapped-screen/.autocode/runs/task')
need = {'kind': 'coordination_resume', 'cap_usd': 0, 'known_spent_usd': 0.845216,
        'complete': True, 'minimum_cap_usd': 0.845217, 'current_cap_usd': 0,
        'allows_no_cap': True, 'token': 'screen-token', 'effect': 'Resume saved work.',
        'next_command': ['autocode', 'coordinate', 'resume', '--run-dir', str(run),
                         '--token', 'screen-token', '--max-cost-usd', 'N', '--json']}
calls = []
with ChatTUI() as ui:
    client = SimpleNamespace(run_dir=run, say=ui.say, ask=ui.ask,
                             advance=lambda *args, **kwargs: calls.append((args, kwargs)))
    with patch.object(coordination, 'read', return_value={'needs': need}):
        coordination.budget_reply(client, {}, need)
assert calls == [((['coordinate', 'resume', '--run-dir', str(run), '--token', '-',
                    '--max-cost-usd', '0', '--json'],),
                  {'env': {'AUTOCODE_COORDINATION_TOKEN': 'screen-token'}})], calls
"""
        screen = TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=120, rows=30, tmux=require_tmux(self)
        )
        self.addCleanup(screen.close)
        screen.wait_for(
            lambda text: "Shared accounting cap: no cap" in text and "New whole-set accounting cap" in text,
            what="uncapped recovery prompt",
        )
        screen.type("0")
        screen.keys("Enter")
        screen.wait_for("Remove spending limits and recover this saved coordination?")
        screen.type("yes")
        screen.keys("Enter")
        self.assertEqual(0, screen.exit_status())


class CoordinatedDisplayTests(unittest.TestCase):
    def test_fullscreen_progress_does_not_append_internal_repository_statuses_to_chat(self):
        summary = {
            "done": False,
            "members": [{"target": "api", "status": "WAITING_FOR_USER"}],
            "needs": {"kind": "watch"},
        }
        for fullscreen in (True, False):
            with self.subTest(fullscreen=fullscreen):
                client = Mock(said=set(), _observe=Mock() if fullscreen else None)
                with patch.object(coordination, "read", return_value=summary):
                    coordination.step(client, {}, str)
                if fullscreen:
                    client.say.assert_not_called()
                else:
                    client.say.assert_called_once_with("api: WAITING_FOR_USER")
                client.pace.assert_called_once_with(client.poll_seconds)

    def test_fullscreen_completion_still_shows_each_published_result(self):
        summary = {
            "done": True,
            "members": [{"target": "api", "status": "TASK_COMPLETE", "pr_url": "https://example.test/pr/1"}],
        }
        client = Mock(said=set(), _observe=Mock())
        with patch.object(coordination, "read", return_value=summary):
            self.assertEqual(0, coordination.step(client, {}, str))
        self.assertEqual(
            ["Joint regression passed. Linked pull requests are ready:", "api: https://example.test/pr/1"],
            [call.args[0] for call in client.say.call_args_list],
        )

    def test_anchor_outage_uses_the_existing_status_indicator_during_member_display(self):
        for fullscreen in (True, False):
            with self.subTest(fullscreen=fullscreen):
                workspace = Path("/tmp/coordinated-status-indicator").resolve()
                anchor = {
                    "workspace": str(workspace),
                    "run_dir": str(workspace / "anchor"),
                    "status": "RUNNING",
                    "needs": {"kind": "continue"},
                }
                observed, said, tabs = [], [], []
                parent = chat.ChatClient(
                    workspace,
                    Path(anchor["run_dir"]),
                    say=said.append,
                    observe=observed.append if fullscreen else None,
                    unavailable=Mock() if fullscreen else None,
                )
                self.addCleanup(parent.close)
                parent.tabs_changed = tabs.append
                parent.accept_status(anchor)
                observed.clear()
                key = (str(workspace), anchor["run_dir"])
                summary = {
                    "needs": {"target": "api"},
                    "members": [{"target": "api", "workspace": str(workspace), "run_dir": str(workspace / "member")}],
                }

                def advance(argv):
                    with patch.object(parent._poller, "tick") as tick:
                        tick.return_value = [(key, "", "status read timed out after 5s")]
                        parent.poll()
                        self.assertEqual("unavailable", tabs[-1][0]["state"])
                        tick.return_value = [(key, json.dumps({"view": anchor}), "")]
                        parent.poll()
                        self.assertNotEqual("unavailable", tabs[-1][0]["state"])

                with patch.object(parent, "advance", side_effect=advance):
                    coordination.advance_with_progress(parent, summary, ["named-action"])
                if fullscreen:
                    self.assertEqual(
                        [], said, "temporary callback detachment must not create a permanent outage transcript"
                    )
                    self.assertEqual([], observed, "anchor reads must not replace the foreground member")
                    parent.accept_status(anchor)
                    self.assertEqual([anchor], observed, "the foreground observer is restored after the action")
                else:
                    self.assertEqual(2, len(said), "the line client still needs outage and recovery messages")
                    self.assertIn("Status unavailable", said[0])
                    self.assertIn("Status available again", said[1])

    def test_automatic_member_and_joint_actions_show_live_status_without_transferring_control(self):
        for target in ("api", "Joint regression", "joint_plan_advance", "joint_advance", "joint_plan_start"):
            for fail in (False, True):
                with self.subTest(target=target, action_fails=fail):
                    workspace = Path("/tmp/coordinated-live-display").resolve()
                    anchor = {"workspace": str(workspace), "run_dir": str(workspace / "anchor")}
                    member = {
                        "workspace": str(workspace / "member"),
                        "run_dir": str(workspace / "member/run"),
                        "status": "RUNNING",
                        "needs": {"kind": "continue"},
                        "chat": {"log": [{"text": "Testing the actual request path"}]},
                    }
                    seen, watchers, closed = [], [], []
                    observed = lambda view: seen.append(view)
                    unavailable = Mock()
                    wait = Mock(return_value="pause")
                    parent = chat.ChatClient(
                        workspace,
                        Path(anchor["run_dir"]),
                        say=Mock(),
                        observe=observed,
                        unavailable=unavailable,
                        wait=wait,
                    )
                    self.addCleanup(parent.close)
                    action = ["autocode", "coordinate", "advance", "--run-dir", anchor["run_dir"], "--json"]
                    summary = {
                        "members": [{**member, "target": "api"}],
                        "done": False,
                        "needs": {"kind": target}
                        if target.startswith("joint_")
                        else {"kind": "start", "target": target},
                        "next_command": action,
                        "integration": member if target != "api" else None,
                    }

                    def poll(child):
                        watchers.append(child)
                        self.assertEqual(Path(member["run_dir"]), child.run_dir)
                        child.accept_status(member)

                    def advance(argv):
                        self.assertEqual(action[1:], argv)
                        parent.accept_status(anchor)
                        self.assertEqual("pause", parent._wait(0.05))
                        if fail:
                            raise RuntimeError("coordinator refused")

                    original_close = chat.ChatClient.close

                    def close(child):
                        closed.append(child)
                        original_close(child)

                    with (
                        patch.object(coordination, "read", return_value=summary),
                        patch.object(parent, "advance", side_effect=advance) as dispatched,
                        patch.object(chat.ChatClient, "poll", new=poll),
                        patch.object(chat.ChatClient, "close", new=close),
                        patch.object(chat.ChatClient, "respond") as respond,
                    ):
                        if fail:
                            with self.assertRaisesRegex(RuntimeError, "coordinator refused"):
                                coordination.step(parent, anchor, Mock())
                        else:
                            coordination.step(parent, anchor, Mock())
                    self.assertEqual([member], seen)
                    self.assertEqual(watchers, closed)
                    self.assertEqual(1, len(watchers))
                    dispatched.assert_called_once()
                    respond.assert_not_called()
                    wait.assert_called_once_with(0.05)
                    self.assertEqual(Path(anchor["run_dir"]), parent.run_dir)
                    self.assertIs(parent._observe, observed)
                    self.assertIs(parent._unavailable, unavailable)
                    self.assertIs(parent._wait, wait)

    def test_joint_start_follows_later_public_identity_without_guessing_a_run_path(self):
        for kind in ("joint_plan_start", "reconcile"):
            for fail in (False, True):
                with self.subTest(kind=kind, fail=fail):
                    workspace = Path("/tmp/joint-start-progress").resolve()
                    anchor = {"workspace": str(workspace), "run_dir": str(workspace / "anchor")}
                    member = {
                        "workspace": str(workspace / "published-joint"),
                        "run_dir": str(workspace / "published-joint/run"),
                        "status": "RUNNING",
                        "needs": {"kind": "continue"},
                    }
                    seen, watched, closed = [], [], []
                    observe, unavailable, wait = seen.append, Mock(), Mock(return_value="pause")
                    parent = chat.ChatClient(
                        workspace,
                        Path(anchor["run_dir"]),
                        say=Mock(),
                        observe=observe,
                        unavailable=unavailable,
                        wait=wait,
                    )
                    self.addCleanup(parent.close)
                    summary = {"members": [], "needs": {"kind": kind, "operation": "joint_plan_start"}}

                    def poll(child):
                        watched.append(child.run_dir)
                        child.accept_status(member)

                    def advance(argv):
                        self.assertEqual(["named-action"], argv)
                        parent._status_view = anchor
                        self.assertEqual("pause", parent._wait(0.05))
                        self.assertEqual([], watched, "No unpublished path may be guessed")
                        parent._status_view = {**anchor, "coordination": {"integration": member}}
                        self.assertEqual("pause", parent._wait(0.05))
                        parent.accept_status(anchor)
                        parent._wait(0.05)
                        if fail:
                            raise RuntimeError("coordinator refused")

                    original_close = chat.ChatClient.close

                    def close(child):
                        closed.append(child.run_dir)
                        original_close(child)

                    with (
                        patch.object(parent, "advance", side_effect=advance) as dispatched,
                        patch.object(chat.ChatClient, "poll", new=poll),
                        patch.object(chat.ChatClient, "close", new=close),
                        patch.object(chat.ChatClient, "respond") as respond,
                    ):
                        if fail:
                            with self.assertRaisesRegex(RuntimeError, "coordinator refused"):
                                coordination.advance_with_progress(parent, summary, ["named-action"])
                        else:
                            coordination.advance_with_progress(parent, summary, ["named-action"])
                    self.assertEqual([Path(member["run_dir"])] * 2, watched)
                    self.assertEqual([member, member], seen)
                    self.assertEqual([Path(member["run_dir"])], closed)
                    dispatched.assert_called_once()
                    respond.assert_not_called()
                    self.assertIs(parent._observe, observe)
                    self.assertIs(parent._unavailable, unavailable)
                    self.assertIs(parent._wait, wait)

    def test_automatic_action_without_published_participant_keeps_anchor_display(self):
        parent = Mock(run_dir=Path("/tmp/unknown-participant/run").resolve(), said=set())
        original = parent._observe
        summary = {
            "members": [],
            "done": False,
            "needs": {"kind": "resolve", "target": "unknown"},
            "next_command": ["autocode", "coordinate", "advance", "--run-dir", str(parent.run_dir), "--json"],
        }
        with patch.object(coordination, "read", return_value=summary):
            coordination.step(parent, {}, Mock())
        parent.advance.assert_called_once_with(summary["next_command"][1:])
        self.assertIs(original, parent._observe)

    def test_missing_repository_asks_for_catalog_setup_without_bug_specific_repos_command(self):
        run_dir = Path("/tmp/coordinated-missing/.autocode/runs/chat").resolve()
        client = Mock(run_dir=run_dir, said=set())
        summary = {
            "version": 1,
            "anchor": str(run_dir),
            "done": False,
            "members": [],
            "needs": {
                "kind": "repository",
                "reason": "No matching repository was found",
                "target": "fulfillment history",
                "candidates": [],
            },
        }
        with patch.object(coordination, "read", return_value=summary):
            result = coordination.step(client, {}, Mock())
        self.assertEqual(2, result)
        client.ask.assert_not_called()
        shown = "\n".join(call.args[0] for call in client.say.call_args_list)
        self.assertIn("autocode registry roots add PATH", shown)
        self.assertIn("autocode registry repositories add PATH", shown)
        self.assertNotIn("/repos PATH", shown)

    def test_environment_uses_stable_user_catalog_and_preserves_explicit_autocode_home(self):
        with patch.dict(os.environ, {"HOME": "/tmp/fixture-user-home"}, clear=True):
            first = coordination.environment(Path("/tmp/project-one"))
            second = coordination.environment(Path("/tmp/project-two"))
        self.assertEqual(first["AUTOCODE_SOURCE_CATALOG_HOME"], second["AUTOCODE_SOURCE_CATALOG_HOME"])
        self.assertEqual("/tmp/fixture-user-home/.autocode", first["AUTOCODE_SOURCE_CATALOG_HOME"])
        self.assertEqual(str(Path("/tmp/project-one/.autocode/registry").resolve()), first["AUTOCODE_HOME"])
        with patch.dict(os.environ, {"AUTOCODE_HOME": "/shared/autocode"}, clear=True):
            explicit = coordination.environment(Path("/tmp/project-one"))
        self.assertEqual("/shared/autocode", explicit["AUTOCODE_SOURCE_CATALOG_HOME"])
        self.assertEqual("/shared/autocode", explicit["AUTOCODE_HOME"])

    def test_repositories_command_reads_the_shared_catalog_without_registering_a_bug_specific_path(self):
        history = Mock()
        history.read.side_effect = [
            {
                "catalog": "/home/user/.autocode/source-catalog.json",
                "roots": [{"path": "/work/services"}],
                "repositories": [{"path": "/work/standalone/history"}],
            },
            {
                "complete": True,
                "diagnostics": [],
                "repositories": [
                    {
                        "path": "/work/services/api",
                        "candidates": [{"path": "/work/services/api", "head": "abc123"}],
                    }
                ],
            },
            {"github": {"configured": True}},
        ]
        text = coordination.repositories(history, "/tmp/one-off-repository")
        self.assertEqual(
            [
                ["registry", "roots", "list", "--json"],
                ["registry", "roots", "discover", "--json"],
                ["coordinate", "access", "--json"],
            ],
            [call.args[0] for call in history.read.call_args_list],
        )
        self.assertIn("/work/services/api", text)
        self.assertIn("/work/standalone/history", text)
        self.assertIn("configure", text.lower())

    def test_approval_display_includes_each_member_location_revision_role_and_grounding(self):
        root = Path("/work/history")
        run_dir = root / ".autocode/runs/fix"
        client = Mock(run_dir=run_dir)
        client.said = set()
        client.ask.return_value = "n"
        member = {
            "target": "history",
            "location": str(root),
            "base": "a" * 40,
            "role": "member",
            "status": "WAITING_FOR_APPROVAL",
            "supporting_evidence": {
                "source_quote": "keep its history in fulfillment-rg-epp-history",
                "evidence": "client.py:4",
                "evidence_repository": "/work/api",
                "evidence_revision": "b" * 40,
            },
        }
        summary = {
            "version": 1,
            "anchor": str(run_dir),
            "done": False,
            "members": [member],
            "needs": {
                "kind": "approval",
                "summary": {"runs": [{"target": "history", "plan": {"body": "Plan body"}}]},
                "joint_plan": {"body": "Combined regression plan body"},
            },
            "approval": {"token": "approval-token", "publication": "No automatic merge."},
        }
        with patch.object(coordination, "read", return_value=summary):
            coordination.step(client, {}, lambda plan: plan["body"])
        packets = [call.args[0] for call in client.say.call_args_list if "Plan body" in call.args[0]]
        self.assertEqual(1, len(packets))
        self.assertIn("Repository: history", packets[0], "one display update contains the complete packet")
        self.assertIn("Combined regression plan body", packets[0])
        self.assertIn("No automatic merge.", packets[0])
        shown = "\n".join(call.args[0] for call in client.say.call_args_list)
        for value in (
            str(root),
            "a" * 40,
            "member",
            "keep its history",
            "/work/api",
            "client.py:4",
            "b" * 40,
            "Combined regression plan body",
        ):
            self.assertIn(value, shown)

    def test_a_carried_approval_is_announced_once_and_taken_as_the_named_advance(self):
        run_dir = Path("/tmp/coordinated-carry/.autocode/runs/chat").resolve()
        client = Mock(run_dir=run_dir, said=set())
        summary = {
            "version": 1,
            "anchor": str(run_dir),
            "done": False,
            "members": [],
            "needs": {
                "kind": "carry_approval",
                "carried": {"revision": 2, "token": "first", "unchanged": ["api", "history"]},
            },
            "next_command": ["autocode", "coordinate", "advance", "--run-dir", str(run_dir), "--json"],
        }
        with patch.object(coordination, "read", return_value=summary):
            self.assertIsNone(coordination.step(client, {}, Mock()))
            self.assertIsNone(coordination.step(client, {}, Mock()))
        client.advance.assert_called_with(["coordinate", "advance", "--run-dir", str(run_dir), "--json"])
        self.assertEqual(2, client.advance.call_count)
        said = [call.args[0] for call in client.say.call_args_list]
        self.assertEqual(
            ["The revised plans keep what you approved (api, history); continuing under that approval."], said
        )
        client.ask.assert_not_called()

    def test_candidate_binding_takes_the_named_core_action(self):
        run_dir = Path("/tmp/coordinated-bind/.autocode/runs/chat").resolve()
        client = Mock(run_dir=run_dir, said=set())
        summary = {
            "version": 1,
            "anchor": str(run_dir),
            "done": False,
            "members": [],
            "needs": {"kind": "bind_candidates"},
            "next_command": [
                "autocode",
                "coordinate",
                "bind",
                "--run-dir",
                str(run_dir),
                "--json",
            ],
        }
        with patch.object(coordination, "read", return_value=summary):
            self.assertIsNone(coordination.step(client, {}, Mock()))
        client.advance.assert_called_once_with(["coordinate", "bind", "--run-dir", str(run_dir), "--json"])

    def test_anchor_reads_cannot_replace_the_active_member_and_callbacks_are_restored(self):
        for fail in (False, True):
            with self.subTest(child_fails=fail):
                events = []
                observed = lambda view: events.append(("view", view["run_dir"]))
                unavailable = lambda reason: events.append(("unavailable", reason))
                workspace = Path("/tmp/coordinated-display")
                anchor = {
                    "workspace": str(workspace),
                    "run_dir": str(workspace / "anchor"),
                    "status": "WAITING_FOR_USER",
                    "needs": {"kind": "answer"},
                }
                member = {
                    **anchor,
                    "workspace": str(workspace / "member"),
                    "run_dir": str(workspace / "member/run"),
                    "status": "RUNNING",
                    "needs": {"kind": "continue"},
                }
                parent = chat.ChatClient(
                    workspace, Path(anchor["run_dir"]), say=Mock(), observe=observed, unavailable=unavailable
                )
                self.addCleanup(parent.close)

                def respond(child, view):
                    # ChatTUI.wait still pumps the anchor's idle poll while this
                    # child advances. Its data stays fresh but must not steal focus.
                    parent.accept_status(anchor)
                    if parent._unavailable:
                        parent._unavailable("anchor-only outage")
                    child.accept_status(member)
                    if child._unavailable:
                        child._unavailable("member outage")
                    if fail:
                        raise RuntimeError("child refused")
                    return True

                summary = {"members": [], "done": False, "needs": {"kind": "input", "view": member}}
                with (
                    patch.object(coordination, "read", return_value=summary),
                    patch.object(chat.ChatClient, "respond", new=respond),
                ):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "child refused"):
                            coordination.step(parent, anchor, Mock())
                    else:
                        self.assertIsNone(coordination.step(parent, anchor, Mock()))
                self.assertEqual(
                    [("view", member["run_dir"]), ("view", member["run_dir"]), ("unavailable", "member outage")],
                    events,
                )
                parent.accept_status(anchor)
                self.assertEqual(("view", anchor["run_dir"]), events[-1])
                self.assertIs(parent._observe, observed)
                self.assertIs(parent._unavailable, unavailable)


class SharedBudgetDisplayTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("/tmp/coordinated-budget-display").resolve()
        self.client = Mock(run_dir=self.root / ".autocode/runs/chat")
        self.need = {
            "kind": "budget",
            "cap_usd": 10,
            "known_spent_usd": 9.5,
            "complete": False,
            "minimum_cap_usd": 10.5,
            "token": "secret-budget-token",
            "effect": "Retain prior spend, routes and all other limits; approve no plan.",
            "next_command": [
                "autocode",
                "coordinate",
                "budget",
                "--run-dir",
                str(self.client.run_dir),
                "--token",
                "secret-budget-token",
                "--max-cost-usd",
                "N",
                "--json",
            ],
        }
        self.summary = {"members": [], "done": False, "needs": self.need}

    def test_explicit_finite_cap_uses_only_fresh_named_action_and_private_token(self):
        self.client.ask.side_effect = ["15.25", "yes"]
        with patch.object(coordination, "read", return_value=self.summary):
            self.assertIsNone(coordination.step(self.client, {}, Mock()))
        self.client.advance.assert_called_once_with(
            [
                "coordinate",
                "budget",
                "--run-dir",
                str(self.client.run_dir),
                "--token",
                "-",
                "--max-cost-usd",
                "15.25",
                "--json",
            ],
            env={"AUTOCODE_COORDINATION_TOKEN": "secret-budget-token"},
        )
        shown = " ".join(str(call.args) for call in self.client.say.call_args_list)
        self.assertIn("incomplete", shown)
        self.assertNotIn("secret-budget-token", shown)

    def test_cancel_recovery_can_explicitly_remove_caps_without_approving_plans(self):
        self.need.update(kind="coordination_resume", allows_no_cap=True, current_cap_usd=10)
        self.need["next_command"][2] = "resume"
        self.client.ask.side_effect = ["0", "yes"]
        with patch.object(coordination, "read", return_value=self.summary):
            self.assertIsNone(coordination.step(self.client, {}, Mock()))
        argv = self.client.advance.call_args.args[0]
        self.assertEqual("resume", argv[1])
        self.assertEqual("0", argv[argv.index("--max-cost-usd") + 1])
        prompts = " ".join(c.args[0] for c in self.client.ask.call_args_list)
        self.assertIn("remove", prompts)
        self.assertIn("spending", prompts)
        self.assertNotIn("Approve", prompts)

    def test_cancel_recovery_displays_existing_uncapped_run_and_keeps_named_resume(self):
        self.need.update(kind="coordination_resume", cap_usd=None, allows_no_cap=True, current_cap_usd=0)
        self.need["next_command"][2] = "resume"
        self.client.ask.side_effect = ["0", "yes"]
        with patch.object(coordination, "read", return_value=self.summary):
            self.assertIsNone(coordination.step(self.client, {}, Mock()))
        shown = " ".join(call.args[0] for call in self.client.say.call_args_list)
        self.assertIn("Shared accounting cap: no cap", shown)
        self.assertIn("known spend: $9.5", shown)
        self.assertIn("incomplete; unknown costs remain unknown", shown)
        argv = self.client.advance.call_args.args[0]
        self.assertEqual("resume", argv[1])
        self.assertEqual("0", argv[argv.index("--max-cost-usd") + 1])

    def test_uncapped_cancel_recovery_can_remain_paused_without_dispatch(self):
        self.need.update(kind="coordination_resume", cap_usd=None, allows_no_cap=True, current_cap_usd=0)
        self.need["next_command"][2] = "resume"
        self.client.ask.return_value = ""
        with patch.object(coordination, "read", return_value=self.summary):
            self.assertEqual(2, coordination.step(self.client, {}, Mock()))
        self.client.advance.assert_not_called()

    def test_explicit_zero_cap_is_displayed_as_uncapped_after_recovery(self):
        self.need.update(kind="coordination_resume", cap_usd=0, allows_no_cap=True, current_cap_usd=0)
        self.need["next_command"][2] = "resume"
        self.client.ask.return_value = ""
        with patch.object(coordination, "read", return_value=self.summary):
            coordination.step(self.client, {}, Mock())
        shown = " ".join(call.args[0] for call in self.client.say.call_args_list)
        self.assertIn("Shared accounting cap: no cap", shown)
        self.assertNotIn("Shared accounting cap: $0", shown)
        self.client.advance.assert_not_called()

    def test_cancel_recovery_rechecks_its_exact_kind_after_confirmation(self):
        self.need.update(kind="coordination_resume", allows_no_cap=True, current_cap_usd=10)
        self.need["next_command"][2] = "resume"
        self.client.ask.side_effect = ["0", "yes"]
        changed = copy.deepcopy(self.summary)
        changed["needs"]["kind"] = "budget"
        with patch.object(coordination, "read", side_effect=[self.summary, changed]):
            coordination.step(self.client, {}, Mock())
        self.client.advance.assert_not_called()

    def test_decline_blank_and_invalid_amounts_never_change_a_cap(self):
        for answers in ([""], ["15", "no"], ["0"], ["nan"], ["inf"], ["9"], ["1e30"]):
            with self.subTest(answers=answers):
                self.client.reset_mock()
                self.client.ask.side_effect = answers
                with patch.object(coordination, "read", return_value=self.summary):
                    coordination.step(self.client, {}, Mock())
                self.client.advance.assert_not_called()

    def test_changed_checkpoint_after_confirmation_is_shown_again_without_dispatch(self):
        self.client.ask.side_effect = ["15", "yes"]
        fresh = copy.deepcopy(self.summary)
        fresh["needs"]["token"] = "new-checkpoint"
        with patch.object(coordination, "read", side_effect=[self.summary, fresh]):
            self.assertIsNone(coordination.step(self.client, {}, Mock()))
        self.client.advance.assert_not_called()

    def test_budget_amount_cannot_be_inserted_into_another_named_operation(self):
        self.need["next_command"][2] = "approve"
        with patch.object(coordination, "read", return_value=self.summary):
            with self.assertRaises(coordination.actions.ClientGap):
                coordination.step(self.client, {}, Mock())
        self.client.ask.assert_not_called()
        self.client.advance.assert_not_called()

    def test_planning_reallocation_uses_its_own_fresh_bounded_named_action(self):
        self.need.update(
            kind="planning_budget",
            planning_cap_usd=3,
            planning_spent_usd=4,
            minimum_cap_usd=4.000001,
            maximum_cap_usd=10,
        )
        self.need["next_command"][2] = "planning-budget"
        self.need["next_command"][7] = "--max-planning-cost-usd"
        self.client.ask.side_effect = ["9.25", "yes"]
        with patch.object(coordination, "read", return_value=self.summary):
            self.assertIsNone(coordination.step(self.client, {}, Mock()))
        argv = self.client.advance.call_args.args[0]
        self.assertEqual("planning-budget", argv[1])
        self.assertEqual(["--max-planning-cost-usd", "9.25", "--json"], argv[-3:])
        self.assertEqual("-", argv[argv.index("--token") + 1])
        self.assertNotIn("--max-cost-usd", argv)

    def test_planning_reallocation_rejects_above_cap_and_changed_kind(self):
        self.need.update(
            kind="planning_budget",
            planning_cap_usd=3,
            planning_spent_usd=4,
            minimum_cap_usd=4.000001,
            maximum_cap_usd=10,
        )
        self.need["next_command"][2] = "planning-budget"
        self.need["next_command"][7] = "--max-planning-cost-usd"
        self.client.ask.side_effect = ["10.01"]
        with patch.object(coordination, "read", return_value=self.summary):
            self.assertIsNone(coordination.step(self.client, {}, Mock()))
        self.client.advance.assert_not_called()
        self.client.ask.side_effect = ["9", "yes"]
        changed = copy.deepcopy(self.summary)
        changed["needs"]["kind"] = "budget"
        with patch.object(coordination, "read", side_effect=[self.summary, changed]):
            self.assertIsNone(coordination.step(self.client, {}, Mock()))
        self.client.advance.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class TaskUsageCallbackTests(unittest.TestCase):
    def test_public_coordination_read_delivers_complete_task_snapshot_to_renderer(self):
        import json

        from tests.test_coordination_usage import ledger

        run = Path("/source")
        received = []
        snapshot = {"version": 1, "anchor": str(run), "runs": [ledger(str(run), 1, 100)]}
        client = chat.ChatClient(Path("/workspace"), run, observe_coordination=received.append)
        self.addCleanup(client.close)
        summary = {"version": 1, "anchor": str(run), "accounting": snapshot}
        client.cli = Mock(return_value=Mock(returncode=0, stdout=json.dumps(summary)))
        view = {
            "coordination": {"status_command": ["autocode", "coordinate", "status", "--run-dir", str(run), "--json"]}
        }
        self.assertEqual(summary, coordination.read(client, view))
        self.assertEqual([snapshot], received)
