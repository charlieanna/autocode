"""Public core conversations retain human inputs and one unapproved task run."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from autocode_conversation_policy import request_path
from autocode_taskrun import TaskRun, TaskRunError

ROOT = Path(__file__).resolve().parents[1]
BRIEF = (
    "Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
    "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line to "
    "stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
    "Python standard library only."
)
OPTIONS = (
    "--evidence-provenance",
    "fake",
    "--engine",
    "opencode",
    "--provider",
    "opencode",
    "--joint-planning",
    "--requirements-reasoning-effort",
    "medium",
    "--glm-reasoning-effort",
    "medium",
    "--plan-reviewer-reasoning-effort",
    "medium",
)


class ConversationSessionCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="autocode-conversation-public-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.env = dict(os.environ)
        self.git_env = {key: value for key, value in self.env.items() if not key.startswith("GIT_")}
        self.git("init", "-q")
        self.git("config", "maintenance.auto", "false")
        self.git("config", "gc.auto", "0")
        (self.workspace / ".gitignore").write_text(".autocode/\n")
        (self.workspace / "README.md").write_text("# Greeting CLI\nPython standard library only.\n")
        self.git("add", ".gitignore", "README.md")
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-qm",
            "seed",
        )
        self.command = (
            tuple(shlex.split(os.environ["AUTOCODE_TEST_CLI"]))
            if os.environ.get("AUTOCODE_TEST_CLI")
            else (sys.executable, str(ROOT / "tools" / "autocode.py"))
        )
        config = self.root / "fake-config.json"
        reference = self.root / "reference"
        reference.mkdir()
        config.write_text(
            json.dumps(
                {
                    "title": "Greeting CLI",
                    "brief": BRIEF,
                    "reference": str(reference),
                    "check": "python3 -m unittest test_greet.py",
                    "paths": ["greet.py", "test_greet.py", "README.md"],
                }
            )
        )
        self.capture = self.root / "provider-inputs.jsonl"
        self.bindir = self.root / "bin"
        self.bindir.mkdir()
        self.provider_delegate = self.bindir / "provider.py"
        (self.bindir / "opencode").write_bytes((ROOT / "tools" / "fake_opencode.py").read_bytes())
        (self.bindir / "opencode").chmod(0o755)
        # Keep the checked-in event adapter and its honest 1.18.31 version.
        # Only its sibling scripted report producer differs from the older
        # tools/fake_codex fixture used by the shared offline bootstrap.
        (self.bindir / "codex").write_text(
            f"#!{sys.executable}\n"
            + textwrap.dedent("""\
            import json
            import os
            import subprocess
            import sys
            import tempfile
            from pathlib import Path

            prompt = sys.stdin.read() if '-o' in sys.argv else None
            arguments = list(sys.argv[1:])
            if prompt is None or 'CURRENT HANDOFF DATA\\n' not in prompt:
                raise SystemExit('Offline conversation fixture requires a complete handoff')
            prefix, raw = prompt.split('CURRENT HANDOFF DATA\\n', 1)
            packet = json.loads(raw)
            with Path(os.environ['CONVERSATION_PROVIDER_INPUTS']).open('a') as stream:
                stream.write(json.dumps({'stage': packet.get('stage'), 'task': packet.get('task')}) + '\\n')
            # OpenCode carries the compact runner schema immediately before
            # the handoff. Pass the same schema to the existing v2 emitter.
            if 'OPENCODE OUTPUT CONTRACT' not in prefix:
                raise SystemExit('Offline fixture requires the OpenCode schema contract')
            schema = json.loads(prefix.rstrip().rsplit('\\n', 1)[-1])
            if not isinstance(schema, dict):
                raise SystemExit('Offline fixture requires an object schema')
            with tempfile.TemporaryDirectory(prefix='conversation-provider-schema-') as temp:
                schema_path = Path(temp) / 'schema.json'
                schema_path.write_text(json.dumps(schema))
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).with_name('provider.py')),
                     *arguments, '--output-schema', str(schema_path)], input=prompt, text=True)
            raise SystemExit(result.returncode)
        """)
        )
        (self.bindir / "codex").chmod(0o755)
        self.bundle = self.bindir / "bundle.json"
        self.set_provider_delegate((ROOT / "scenarios" / "harness" / "fake_codex.py").read_text())
        bootstrap = self.root / "conversation-opencode-fixture-cli.py"
        bootstrap_source = (ROOT / "tests" / "opencode_fixture_cli.py").read_text()
        checked = textwrap.dedent("""\
        def checked_fixture(executable="opencode", *, env=None):
            # TEST-ONLY: authenticate this exact copied event adapter and
            # its owned scripted delegate before metadata/auth preflight.
            import hashlib
            import json
            import stat
            env = os.environ if env is None else env
            selected = shutil.which(str(executable), path=env.get("PATH", ""))
            if not selected:
                raise RuntimeError("Offline conversation fixture missing")
            selected = Path(selected).absolute()
            bundle_path = selected.with_name("bundle.json")
            if selected.name != "opencode" or str(bundle_path) != env.get("CONVERSATION_FIXTURE_BUNDLE"):
                raise RuntimeError("Offline bootstrap refuses a foreign client")
            bundle_info = bundle_path.lstat()
            if not stat.S_ISREG(bundle_info.st_mode) or bundle_info.st_nlink != 1 or bundle_info.st_size > 4096:
                raise RuntimeError("Offline bundle authority is not a bounded regular leaf")
            bundle_raw = bundle_path.read_bytes()
            if hashlib.sha256(bundle_raw).hexdigest() != env.get("CONVERSATION_FIXTURE_BUNDLE_SHA256"):
                raise RuntimeError("Offline bundle authority bytes differ")
            expected = json.loads(bundle_raw)
            known = FIXED_OPEN_CODE_AND_WRAPPER_DIGESTS
            if set(expected) != {"opencode", "codex", "provider.py"}:
                raise RuntimeError("Offline fixture bundle differs")
            if any(expected[name] != digest for name, digest in known.items()):
                raise RuntimeError("Offline bootstrap refuses an unknown adapter or wrapper")
            for name, wanted in expected.items():
                path = selected.with_name(name)
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 2 * 1024 * 1024:
                    raise RuntimeError("Offline fixture refuses an aliased or unbounded leaf")
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != wanted:
                    raise RuntimeError("Offline fixture bytes differ: " + name)
            return selected
        """)
        checked = checked.replace(
            "FIXED_OPEN_CODE_AND_WRAPPER_DIGESTS",
            repr(
                {name: hashlib.sha256((self.bindir / name).read_bytes()).hexdigest() for name in ("opencode", "codex")}
            ),
        )
        before = bootstrap_source.index("def checked_fixture(")
        after = bootstrap_source.index("\ndef main():", before)
        bootstrap_source = bootstrap_source[:before] + checked + "\n" + bootstrap_source[after:]
        bootstrap_source = bootstrap_source.replace(
            'TOOLS = Path(__file__).resolve().parents[1] / "tools"',
            f"TOOLS = Path({str(ROOT / 'tools')!r})",
            1,
        )
        # A public conversation command creates another ordinary TaskRun CLI
        # child. Keep that child on this same authenticated test transport;
        # production command selection and containment stay untouched.
        simulated_tail = """        for module in boundaries:
            stack.enter_context(patch.object(module, "unavailable", lambda *args, **kwargs: None))
        runpy.run_path(target, run_name="__main__")"""
        nested_tail = """        for module in boundaries:
            stack.enter_context(patch.object(module, "unavailable", lambda *args, **kwargs: None))
        interfaces = [importlib.import_module("autocode_conversation_cli")]
        if Path(target).resolve() != (TOOLS / "autocode.py").resolve():
            interfaces.append(importlib.import_module("autocode_cli.autocode_conversation_cli"))
        for interface in interfaces:
            stack.enter_context(patch.object(
                interface, "command",
                lambda: (sys.executable, str(Path(__file__).resolve()), target)))
        runpy.run_path(target, run_name="__main__")"""
        self.assertEqual(1, bootstrap_source.count(simulated_tail))
        bootstrap_source = bootstrap_source.replace(simulated_tail, nested_tail, 1)
        bootstrap.write_text(bootstrap_source)
        target = self.command[1:] if self.command[0] == sys.executable else self.command
        self.assertEqual(1, len(target), "Fixture bootstrap requires one real CLI script")
        self.command = (sys.executable, str(bootstrap), *target)
        self.env.update(
            {
                "PATH": str(self.bindir) + os.pathsep + self.env["PATH"],
                "AUTOCODE_HOME": str(self.root / "registry"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "SCENARIO_FAKE_CONFIG": str(config),
                "CONVERSATION_PROVIDER_INPUTS": str(self.capture),
                "CONVERSATION_FIXTURE_BUNDLE": str(self.bundle),
            }
        )
        self.before = self.footprint()

    def set_provider_delegate(self, source):
        self.provider_delegate.write_text(source)
        self.bundle.write_text(
            json.dumps(
                {
                    name: hashlib.sha256((self.bindir / name).read_bytes()).hexdigest()
                    for name in ("opencode", "codex", "provider.py")
                }
            )
        )

        self.env["CONVERSATION_FIXTURE_BUNDLE_SHA256"] = hashlib.sha256(self.bundle.read_bytes()).hexdigest()

    def git(self, *arguments):
        return subprocess.run(
            ["git", "-C", str(self.workspace), *arguments],
            env=self.git_env,
            check=True,
            capture_output=True,
        ).stdout

    def footprint(self):
        names = self.git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
        files = {
            os.fsdecode(name): hashlib.sha256((self.workspace / os.fsdecode(name)).read_bytes()).hexdigest()
            for name in names
            if name
        }
        return (
            self.git("rev-parse", "HEAD"),
            self.git("ls-files", "--stage", "-z"),
            files,
        )

    def calls(self):
        return [json.loads(line) for line in self.capture.read_text().splitlines()] if self.capture.exists() else []

    def attempts(self, run):
        return copy.deepcopy(
            [row for row in run.status()["usage"]["accounting"]["attempts"] if not row["runner_owned"]]
        )

    def cli(self, action, *arguments, expected=0):
        result = subprocess.run(
            [
                *self.command,
                "conversation",
                action,
                "--workspace",
                str(self.workspace),
                "--timeout",
                "300",
                *map(str, arguments),
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout) if expected == 0 else result

    def assert_reviewed_frontier(self, run, conversation):
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertNotIn("approved_contract", view)
        self.assertFalse(view["done"])
        self.assertTrue(conversation["review"]["current"], conversation)
        self.assertTrue(conversation["handoff_ready"], conversation)
        self.assertEqual("current", conversation["current_draft"]["freshness"])
        records = {row["stage"]: row for row in conversation["planning_records"]}
        for stage, role, model in (
            ("astra_discovery", "Planner", "openai/gpt-6-sol"),
            ("astra_challenge", "Plan Reviewer", "zai-coding-plan/glm-5.3"),
        ):
            with self.subTest(stage=stage):
                self.assertIn(stage, records)
                self.assertEqual(role, records[stage]["role"])
                self.assertEqual(model, records[stage]["model"])
                self.assertEqual("medium", records[stage]["reasoning_effort"])
                self.assertEqual("saved_accepted", records[stage]["availability"])
        self.assertFalse(any(row["stage"] in ("terra", "builder") for row in self.attempts(run)))
        self.assertEqual(self.before, self.footprint())

    def test_same_run_queue_saved_action_restart_refresh_and_unapproved_handoff(self):
        run = TaskRun.start_conversation(
            self.workspace,
            BRIEF,
            title="Greeting CLI",
            request_id="start-1",
            start_options=OPTIONS,
            command=self.command,
            env=self.env,
            timeout=300,
        )
        initial = run.read_conversation()
        self.assert_reviewed_frontier(run, initial)
        self.assertEqual(1, initial["revision"])
        first_packet = self.calls()[0]
        task = json.loads(first_packet["task"])
        self.assertEqual("autocode.conversation-task", task["kind"])
        self.assertEqual(["user"], [row["role"] for row in task["handoff"]["messages"]])
        self.assertEqual([BRIEF], [row["text"] for row in task["handoff"]["messages"]])
        attempts = self.attempts(run)
        calls = self.calls()
        restarted = self.cli(
            "start",
            "--text",
            BRIEF,
            "--title",
            "Greeting CLI",
            "--request-id",
            "start-1",
            "--",
            *OPTIONS,
        )
        self.assertEqual(str(run.run_dir), restarted["run_dir"])
        self.assertEqual(initial["id"], restarted["id"])
        self.assertEqual(attempts, self.attempts(run))
        self.assertEqual(calls, self.calls())
        attached = TaskRun.attach_conversation(self.workspace, command=self.command, env=self.env, timeout=300)
        self.assertIsNotNone(attached)
        self.assertEqual(run.run_dir, attached.run_dir)
        self.assertEqual(initial, attached.read_conversation())
        self.assertEqual(initial, self.cli("attach", "--run-dir", run.run_dir))
        self.assertEqual(initial, self.cli("read", "--run-dir", run.run_dir))

        queued = self.cli(
            "send",
            "--run-dir",
            run.run_dir,
            "--text",
            "Python standard library only.",
            "--request-id",
            "message-1",
            "--expected-revision",
            1,
        )
        self.assertEqual(2, queued["revision"])
        self.assertEqual(1, len(queued["pending_message_ids"]))
        self.assertEqual("stale", queued["current_draft"]["freshness"])
        self.assertFalse(queued["handoff_ready"])
        self.assertEqual(attempts, self.attempts(run))
        self.assertEqual(calls, self.calls())
        self.assertEqual(
            queued,
            attached.send_conversation(
                "Python standard library only.",
                request_id="message-1",
                expected_revision=1,
            ),
        )
        self.cli(
            "send",
            "--run-dir",
            run.run_dir,
            "--text",
            "A stale correction",
            "--request-id",
            "stale-message",
            "--expected-revision",
            1,
            expected=2,
        )
        self.assertEqual(queued, run.read_conversation())

        crash = self.root / "crash-after-saved-action.py"
        marker = self.root / "saved-action-return.json"
        crash.write_text(
            textwrap.dedent(f"""\
            import json
            import subprocess
            import sys
            from pathlib import Path

            result = subprocess.run({list(self.command)!r} + sys.argv[1:])
            marker = Path({str(marker)!r})
            if '--conversation-request' in sys.argv and result.returncode == 0 and not marker.exists():
                marker.write_text(json.dumps({{'actual_child_exit': result.returncode}}))
                raise SystemExit(71)
            raise SystemExit(result.returncode)
        """)
        )
        interrupted = TaskRun(
            self.workspace,
            run.run_dir,
            (sys.executable, str(crash)),
            env=self.env,
            timeout=300,
        )
        with self.assertRaises(TaskRunError) as failure:
            interrupted.refresh_conversation(request_id="refresh-1", expected_revision=2)
        self.assertEqual(71, failure.exception.process.returncode)
        self.assertEqual({"actual_child_exit": 0}, json.loads(marker.read_text()))
        saved = attached.read_conversation()
        self.assertEqual([], saved["pending_message_ids"])
        self.assertEqual(2, saved["applied_revision"])
        self.assertEqual(["refresh-1"], [row["request_id"] for row in saved["actions"]])
        self.assertEqual(1, len(saved["saved_human_actions"]))
        self.assertEqual("Python standard library only.", saved["saved_human_actions"][0]["text"])
        self.assertEqual(attempts, self.attempts(run))
        self.assertEqual(calls, self.calls())
        self.assertEqual(self.before, self.footprint())

        refreshed = self.cli(
            "refresh",
            "--run-dir",
            run.run_dir,
            "--request-id",
            "refresh-1",
            "--expected-revision",
            2,
        )
        self.assert_reviewed_frontier(run, refreshed)
        self.assertEqual([], refreshed["pending_message_ids"])
        self.assertEqual(saved["saved_human_actions"], refreshed["saved_human_actions"])
        self.assertEqual(saved["actions"], refreshed["actions"])
        self.assertNotEqual(initial["current_draft"]["token"], refreshed["current_draft"]["token"])
        self.assertGreater(len(self.attempts(run)), len(attempts))
        new_calls = self.calls()[len(calls) :]
        self.assertTrue(any(row["stage"] == "astra_discovery" for row in new_calls), new_calls)
        self.assertTrue(any(row["stage"] == "astra_challenge" for row in new_calls), new_calls)
        attempts, calls = self.attempts(run), self.calls()
        replayed = attached.refresh_conversation(request_id="refresh-1", expected_revision=2)
        self.assertTrue(replayed["refresh_noop"])
        noop = attached.refresh_conversation(request_id="empty-refresh", expected_revision=2)
        self.assertTrue(noop["refresh_noop"])
        with self.assertRaisesRegex(ValueError, "changed"):
            attached.refresh_conversation(request_id="stale-refresh", expected_revision=1)
        self.cli(
            "handoff",
            "--run-dir",
            run.run_dir,
            "--expected-revision",
            2,
            "--expected-plan-token",
            initial["current_draft"]["token"],
            expected=2,
        )
        self.assertEqual(refreshed, run.read_conversation())
        self.assertEqual(attempts, self.attempts(run))
        self.assertEqual(calls, self.calls())
        handed = self.cli(
            "handoff",
            "--run-dir",
            run.run_dir,
            "--expected-revision",
            2,
            "--expected-plan-token",
            refreshed["current_draft"]["token"],
        )
        self.assertEqual(str(run.run_dir), handed["handoff"]["run_dir"])
        self.assertIs(handed["handoff"]["approved"], False)
        self.assertEqual(attempts, self.attempts(run))
        self.assertEqual(calls, self.calls())
        self.assert_reviewed_frontier(run, handed)
        self.assertEqual([run.run_dir], TaskRun.runs_in(self.workspace))

    def test_concurrent_ordinary_answer_rejection_can_be_corrected_without_replaying_old_input(
        self,
    ):
        # Use the existing legacy fixture with two genuine unresolved questions.
        # Only the provider substitutes I/O; answers and refusals use the real CLI.
        source = (ROOT / "tools" / "live_fixture_provider.py").read_text()
        amendment = textwrap.dedent("""
            if stage == "astra_discovery":
                questions = [
                    {"id": "readme-audience", "question": "Should the README target beginners or experienced Python users?",
                     "why": "The audience is not specified.", "options": ["Beginners", "Experienced Python users"],
                     "proposed_default": "", "kind": "decision", "category": "requested_outcome", "delegable": False},
                    {"id": "greeting-punctuation", "question": "Should the greeting end with an exclamation mark?",
                     "why": "The punctuation is not specified.", "options": ["No punctuation", "Exclamation mark"],
                     "proposed_default": "", "kind": "decision", "category": "requested_outcome", "delegable": False},
                ]
                questions = [q for q in questions if q['id'] not in data.get('saved_answers', {})]
                report['contract']['open_blocking_questions'] = questions
                report['contract'].setdefault('initial_task', _planning_contract()['initial_task'])
                if questions:
                    report['contract'].update(technical_approach=[], milestones=[])
                    report['contract']['initial_task'] = {
                        'objective': '', 'affected_paths': [], 'kind': 'none', 'milestone_id': '',
                        'requirements': [], 'acceptance_criteria': [], 'validation_plan': []}
        """)
        anchor = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        self.assertEqual(1, source.count(anchor))
        self.set_provider_delegate(source.replace(anchor, textwrap.indent(amendment, "    ") + "\n" + anchor))
        options = OPTIONS
        run = TaskRun.start_conversation(
            self.workspace,
            BRIEF,
            request_id="question-start",
            start_options=options,
            command=self.command,
            env=self.env,
            timeout=300,
        )
        need = run.status()["needs"]
        self.assertEqual("answer", need["kind"])
        self.assertEqual(
            ["readme-audience", "greeting-punctuation"],
            [q["id"] for q in need["questions"]],
        )
        attempts = self.attempts(run)
        run.send_conversation("Experienced Python users", request_id="old-message", expected_revision=1)
        before = run.read_conversation()
        with self.assertRaisesRegex(ValueError, "stale"):
            run.refresh_conversation(
                request_id="stale-before-seal",
                expected_revision=2,
                question_id="readme-audience",
                resolver_token="old-token",
            )
        self.assertEqual(before, run.read_conversation())
        self.assertNotIn("stale-before-seal", before["refreshes"])

        marker = self.root / "ordinary-answer-race.json"
        wrapper = self.root / "ordinary-answer-race.py"
        wrapper.write_text(
            textwrap.dedent(f"""\
            import json
            import subprocess
            import sys
            from pathlib import Path
            arguments = sys.argv[1:]
            marker = Path({str(marker)!r})
            if '--conversation-request' in arguments and not marker.exists():
                direct = list(arguments)
                index = direct.index('--conversation-request')
                sealed_path = direct[index + 1]
                del direct[index:index + 2]
                direct[direct.index('--answer') + 1] = 'readme-audience=Beginners'
                prior = subprocess.run({list(self.command)!r} + direct)
                marker.write_text(json.dumps({{'actual_ordinary_exit': prior.returncode, 'sealed_path': sealed_path}}))
                if prior.returncode != 0:
                    raise SystemExit(prior.returncode)
            result = subprocess.run({list(self.command)!r} + arguments)
            raise SystemExit(result.returncode)
        """)
        )
        other_client = TaskRun(
            self.workspace,
            run.run_dir,
            (sys.executable, str(wrapper)),
            env=self.env,
            timeout=300,
        )
        with self.assertRaises(TaskRunError) as rejected:
            other_client.refresh_conversation(
                request_id="rejected-answer",
                expected_revision=2,
                question_id="readme-audience",
                resolver_token=need["resolver_token"],
            )
        self.assertEqual(2, rejected.exception.process.returncode)
        self.assertIn("Input rejected:", rejected.exception.process.stderr)
        witness = json.loads(marker.read_text())
        self.assertEqual(0, witness["actual_ordinary_exit"])
        rejected_view = run.read_conversation()
        self.assertEqual("rejected", rejected_view["refreshes"]["rejected-answer"]["status"])
        self.assertEqual(
            "runner_rejected_input",
            rejected_view["rejections"]["rejected-answer"]["kind"],
        )
        self.assertEqual([], rejected_view["actions"])
        self.assertEqual(attempts, self.attempts(run))
        corrected = self.cli(
            "send",
            "--run-dir",
            run.run_dir,
            "--text",
            "No punctuation",
            "--request-id",
            "corrected-message",
            "--expected-revision",
            2,
            "--supersedes-request-id",
            "old-message",
        )
        self.assertEqual(3, corrected["revision"])
        self.assertEqual(1, len(corrected["pending_message_ids"]))
        self.assertEqual("corrected-message", corrected["messages"][1]["superseded_by"])
        self.assertEqual(rejected_view["rejections"], corrected["rejections"])
        fresh = run.status()["needs"]
        self.assertEqual(["greeting-punctuation"], [q["id"] for q in fresh["questions"]])
        finished = run.refresh_conversation(
            request_id="corrected-answer",
            expected_revision=3,
            question_id="greeting-punctuation",
            resolver_token=fresh["resolver_token"],
        )
        self.assertEqual([], finished["pending_message_ids"])
        self.assertEqual(["corrected-answer"], [row["request_id"] for row in finished["actions"]])
        self.assertEqual("approve_plan", run.status()["needs"]["kind"])
        attempts = self.attempts(run)
        with self.assertRaises(TaskRunError):
            run.apply_conversation_request(
                Path(witness["sealed_path"]),
                "Experienced Python users",
                question_id="readme-audience",
                resolver_token=need["resolver_token"],
            )
        self.assertEqual(finished, run.read_conversation())
        self.assertEqual(attempts, self.attempts(run))
        self.assertEqual(self.before, self.footprint())

    def test_empty_workspace_bootstraps_before_staging_and_retries_the_same_run(self):
        missing = self.root / "missing-project"
        with self.assertRaisesRegex(ValueError, "exist|directory"):
            TaskRun.start_conversation(
                missing,
                BRIEF,
                request_id="missing-start",
                start_options=OPTIONS,
                command=self.command,
                env=self.env,
                timeout=300,
            )
        self.assertFalse(missing.exists())
        empty = self.root / "empty-project"
        empty.mkdir()
        arguments = dict(
            request_id="empty-start",
            start_options=OPTIONS,
            command=self.command,
            env=self.env,
            timeout=300,
        )
        run = TaskRun.start_conversation(empty, BRIEF, **arguments)
        self.assertEqual(empty, run.workspace)
        self.assertEqual(empty / ".autocode" / "runs", run.run_dir.parent)
        self.assertFalse((empty / "autocode-projects").exists())
        initial = run.read_conversation()
        self.assertTrue(initial["handoff_ready"], initial)
        self.assertEqual("approve_plan", run.status()["needs"]["kind"])
        calls = self.calls()
        restarted = TaskRun.start_conversation(empty, BRIEF, **arguments)
        self.assertEqual(run.run_dir, restarted.run_dir)
        attached = TaskRun.attach_conversation(
            empty,
            run_dir=run.run_dir,
            command=self.command,
            env=self.env,
            timeout=300,
        )
        self.assertEqual(initial, attached.read_conversation())
        self.assertEqual([run.run_dir], TaskRun.runs_in(empty))
        self.assertEqual(calls, self.calls())
        self.assertFalse(run.status().get("approved_contract"))

    def test_model_route_rejection_can_be_corrected_and_acknowledged_without_requirements_feedback(self):
        self.env["AUTOCODE_FIXTURE_CONTENT_FILTER_STAGE"] = "astra_discovery"
        self.env["AUTOCODE_FIXTURE_CONTENT_FILTER_MODEL"] = "openai/gpt-6-sol"
        run = TaskRun.start_conversation(
            self.workspace,
            BRIEF,
            request_id="route-start",
            # Exercise the fixed-route human question, not automatic recovery.
            start_options=(*OPTIONS, "--no-auto-select"),
            command=self.command,
            env=self.env,
            timeout=300,
        )
        need = run.status()["needs"]
        self.assertEqual("route-glm", need["questions"][0]["id"])
        calls = self.calls()
        run.send_conversation("not-a-model", request_id="bad-model", expected_revision=1)
        with self.assertRaises(TaskRunError):
            run.refresh_conversation(
                request_id="rejected-route",
                expected_revision=2,
                question_id="route-glm",
                resolver_token=need["resolver_token"],
            )
        rejected = run.read_conversation()
        self.assertEqual("rejected", rejected["refreshes"]["rejected-route"]["status"])
        self.assertEqual("runner_rejected_input", rejected["rejections"]["rejected-route"]["kind"])
        self.assertEqual([], rejected["actions"])
        run.send_conversation(
            "openai/gpt-6-luna",
            request_id="corrected-model",
            expected_revision=2,
            supersedes_request_id="bad-model",
        )
        current = run.status()["needs"]
        refreshed = run.refresh_conversation(
            request_id="accepted-route",
            expected_revision=3,
            question_id="route-glm",
            resolver_token=current["resolver_token"],
        )
        self.assertEqual(3, refreshed["applied_revision"])
        self.assertEqual([], refreshed["pending_message_ids"])
        self.assertEqual(["accepted-route"], [row["request_id"] for row in refreshed["actions"]])
        sources = refreshed["actions"][0]["source_events"]
        self.assertEqual(["route_assignment"], [row["kind"] for row in sources])
        self.assertEqual("openai/gpt-6-luna", sources[0]["to"])
        self.assertEqual([], refreshed["saved_human_actions"])
        self.assertEqual("openai/gpt-6-luna", run.status()["routes"]["glm"]["model"])
        self.assertEqual("resume", run.status()["needs"]["kind"])
        self.assertEqual(calls, self.calls())
        token = current["resolver_token"]
        replay = run.refresh_conversation(
            request_id="accepted-route",
            expected_revision=3,
            question_id="route-glm",
            resolver_token=token,
        )
        self.assertTrue(replay["refresh_noop"])
        # Retry the actual retained receipt after its operational token was consumed.
        request = request_path(run.run_dir, "accepted-route")
        run.apply_conversation_request(request, "openai/gpt-6-luna", question_id="route-glm", resolver_token=token)
        self.assertEqual(refreshed["actions"], run.read_conversation()["actions"])
        self.assertEqual(calls, self.calls())
        self.assertEqual(self.before, self.footprint())

    def test_model_route_receipt_survives_exit_after_abandonment_before_final_commit(self):
        bootstrap = Path(self.command[1])
        source = bootstrap.read_text()
        anchor = (
            "                lambda: (sys.executable, str(Path(__file__).resolve()), target)))\n"
            '        runpy.run_path(target, run_name="__main__")'
        )
        self.assertEqual(1, source.count(anchor))
        fault = textwrap.dedent("""\
        import autocode_action_resolver
        original_route_answer = autocode_action_resolver.answer_quota_question
        def route_answer_with_fault(runner, args, *other):
            if args.conversation_request and os.environ.get("CONVERSATION_ROUTE_COMMIT_FAULT") == "1":
                original_commit = runner.commit_user_action
                def interrupted_commit(*values, **keywords):
                    raise SystemExit(71)
                runner.commit_user_action = interrupted_commit
                try:
                    return original_route_answer(runner, args, *other)
                finally:
                    runner.commit_user_action = original_commit
            return original_route_answer(runner, args, *other)
        stack.enter_context(patch.object(
            autocode_action_resolver, "answer_quota_question", route_answer_with_fault))
        """)
        bootstrap.write_text(
            source.replace(
                anchor,
                anchor.splitlines(keepends=True)[0]
                + textwrap.indent(fault, "        ")
                + '        runpy.run_path(target, run_name="__main__")',
            )
        )
        self.env["AUTOCODE_FIXTURE_CONTENT_FILTER_STAGE"] = "astra_discovery"
        self.env["AUTOCODE_FIXTURE_CONTENT_FILTER_MODEL"] = "openai/gpt-6-sol"
        run = TaskRun.start_conversation(
            self.workspace,
            BRIEF,
            request_id="crash-route-start",
            # Exercise the fixed-route human question, not automatic recovery.
            start_options=(*OPTIONS, "--no-auto-select"),
            command=self.command,
            env=self.env,
            timeout=300,
        )
        need = run.status()["needs"]
        calls = self.calls()
        run.send_conversation("openai/gpt-6-luna", request_id="crash-model", expected_revision=1)
        self.env["CONVERSATION_ROUTE_COMMIT_FAULT"] = "1"
        with self.assertRaises(TaskRunError) as stopped:
            run.refresh_conversation(
                request_id="crash-route",
                expected_revision=2,
                question_id="route-glm",
                resolver_token=need["resolver_token"],
            )
        self.assertEqual(71, stopped.exception.process.returncode)
        retained = run.read_conversation()
        self.assertEqual([], retained["pending_message_ids"])
        self.assertEqual(2, retained["applied_revision"])
        self.assertEqual(["crash-route"], [row["request_id"] for row in retained["actions"]])
        self.assertEqual("openai/gpt-6-luna", run.status()["routes"]["glm"]["model"])
        self.env.pop("CONVERSATION_ROUTE_COMMIT_FAULT")
        recovered = run.refresh_conversation(
            request_id="crash-route",
            expected_revision=2,
            question_id="route-glm",
            resolver_token=need["resolver_token"],
        )
        self.assertEqual(retained["actions"], recovered["actions"])
        self.assertEqual("finished", recovered["refreshes"]["crash-route"]["status"])
        self.assertEqual([], recovered["pending_message_ids"])
        self.assertEqual(calls, self.calls())

    def test_cli_refuses_execution_and_approval_creation_flags_before_provider_dispatch(
        self,
    ):
        for arguments in (
            ("--unit", "autocode"),
            ("--auto-approve",),
            ("--approve-goal", "invented-token"),
        ):
            with self.subTest(arguments=arguments):
                result = self.cli(
                    "start",
                    "--text",
                    BRIEF,
                    "--request-id",
                    "forbidden-start",
                    "--",
                    *OPTIONS,
                    *arguments,
                    expected=2,
                )
                self.assertIn("Unsupported conversation creation option", result.stderr)
                self.assertEqual([], TaskRun.runs_in(self.workspace))
                self.assertEqual([], self.calls())
                self.assertEqual(self.before, self.footprint())
