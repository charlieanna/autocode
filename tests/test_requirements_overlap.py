"""The chat waits for the person's answer; AutoCode assumes none (#3233, owner decision 2026-11-19).

AutoCode used to run the full Requirements pass while the person read its blocking questions,
assuming each question's offered default (#3580). The owner removed that: AutoCode makes no
assumption about an answer. These cases show the chat starting no model call until the person
answers, and a run saved while such a pass ran settling it on resume without adopting its report.

The CLI cases drive the real ``autocode --chat`` conversation with the scripted provider adapter
in ``requirements_overlap_provider`` (only model output is scripted). The fast cases drive the
real ``autocode.chat_checkpoint`` over a real stdin pipe with the launch path injected.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_requirements_review as review
import autocode_taskrun as taskrun
import autocode_util as util

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import FAKE_BILLING_ENV, fake_setup
from harness.project import materialize, without_maintenance

from . import requirements_overlap_provider as fixture

ROOT = Path(__file__).resolve().parents[1]

# The catalog's brief is small (workflows.brief_size); this one is long enough to be medium,
# so one case each covers the two sizes.
MEDIUM_BRIEF = (
    "Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty "
    "name argument and exits 0. Any other argument count (no arguments, or two or more) prints a "
    "usage line to stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a "
    "short README.md. Python standard library only. The tool must be deterministic and require no "
    "network access at runtime on every supported platform. Keep the implementation straightforward "
    "and easy to read for a newcomer to this repository. Document the usage examples in the README so "
    "a developer can try the tool immediately after cloning. The regression tests must cover the "
    "valid case and every invalid argument count described above, and they must run without any "
    "external service or fixture. Do not add third-party dependencies or optional features beyond "
    "the described behavior. The exit codes and the exact usage line must stay stable for callers "
    "that parse them."
)


def stamped(saved: dict) -> str:
    """The provider adapter's marker: the exact saved answers a call ran with."""
    return f"[overlap saved={json.dumps(saved, sort_keys=True)}]"


class CliFixture(unittest.TestCase):
    """One fresh run per case on the scripted provider; no product import at module level."""

    mode = ""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="requirements-overlap-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.scenario = catalog.load("greenfield-greeting-cli")
        self.log_path = self.root / "provider-calls.jsonl"
        self.run_dir = None
        self.project = self.build_project()
        flags, provider_env = fake_setup(self.scenario, self.root, self.scenario.reference)
        self.flags = flags
        self.environment = {
            **provider_env,
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "AUTOCODE_OVERLAP_MODE": self.mode,
            "AUTOCODE_OVERLAP_LOG": str(self.log_path),
        }
        paths = self.environment["PATH"].split(os.pathsep)
        self.environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        provider = self.root / "bin" / "codex"
        provider.write_text(
            "#!/bin/sh\nexec "
            + shlex.join([sys.executable, str(ROOT / "tests/requirements_overlap_provider.py")])
            + ' "$@"\n'
        )
        # Ambient API keys trip joint planning's billing-route gate although no real model is
        # called (harness.driver.FAKE_BILLING_ENV); every child starts from the scrubbed parent.
        for key in FAKE_BILLING_ENV:
            os.environ.pop(key, None)

    def build_project(self):
        return without_maintenance(materialize(self.scenario.seed, self.root / "project"))

    def chat(self, brief: str, answers: str, *extra, after_calls=None) -> subprocess.CompletedProcess:
        """Run the chat with ``answers`` on its input.

        ``after_calls`` holds the answers back until the provider has logged that many Requirements
        calls, as a person answers after reading. A case whose answer discards the background call
        and then counts it names the asking call and that call (2): answers piped at launch are read
        the moment the question is asked, and on a busy host that is before the background call is
        dispatched, so the pass is cancelled before it launches and no such call is logged.
        """
        command = [
            *taskrun.AUTOCODE,
            "--workspace",
            str(self.project),
            brief,
            *self.flags,
            *extra,
            "--in-place",
            "--chat",
        ]
        options = {"text": True, "cwd": self.root, "env": {**os.environ, **self.environment}}
        if after_calls is None:
            result = subprocess.run(command, input=answers, capture_output=True, timeout=240, **options)
        else:
            process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options
            )
            try:
                deadline = time.monotonic() + 120  # watchdog, not a speed assertion
                while (
                    len(self.requirements_calls()) < after_calls
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.05)
                self.assertEqual(after_calls, len(self.requirements_calls()), self.calls())
                stdout, stderr = process.communicate(answers, timeout=240)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=10)
            result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        self.assertIn(result.returncode, (0, 2), result.stdout + result.stderr)
        return result

    def calls(self) -> list:
        if not self.log_path.is_file():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines() if line.strip()]

    def requirements_calls(self) -> list:
        return [row for row in self.calls() if row.get("stage") == "requirements_gather"]

    def state(self) -> dict:
        run_dir = self.run_dir
        if run_dir is None:
            runs = sorted((self.project / ".autocode/runs").iterdir())
            self.assertEqual(1, len(runs), runs)
            run_dir = runs[0]
        return json.loads((Path(run_dir) / "state.json").read_text())

    def asked(self, question_id: str) -> dict:
        """The question row the run showed the person, from the saved answer's own record."""
        for event in self.state().get("user_events") or []:
            if isinstance(event, dict) and event.get("question_id") == question_id:
                return event.get("question") or {}
        self.fail(f"{question_id} was never answered; events: {self.state().get('user_events')}")


class ChatWaitsForTheAnswerTests(CliFixture):
    """A question offering a default: no Requirements call runs before the person answers it."""

    mode = "questions"

    def assert_every_later_call_ran_on_the_persons_answer(self, answer):
        calls = self.calls()
        self.assertEqual("requirements_gather", calls[0]["stage"], calls)
        self.assertEqual({}, calls[0]["saved"], "the first call asks the question")
        self.assertGreaterEqual(len(calls), 2, "the run continued after the answer")
        for call in calls[1:]:
            self.assertIn(
                fixture.QUESTION_ID,
                call["answered"],
                f"a model call ran before the person answered: {call}",
            )
            self.assertEqual({fixture.QUESTION_ID: answer}, call["saved"], call)
        state = self.state()
        self.assertNotIn("requirements_overlap", state, "no background pass was started")
        self.assertFalse([row for row in state.get("stages") or [] if row.get("speculative")])

    def test_a_small_brief_waits_for_the_answer_even_when_it_is_the_default(self):
        self.chat(self.scenario.brief, fixture.QUESTION_DEFAULT + "\n")
        self.assertEqual("small", (self.state().get("workflow") or {}).get("brief_size"))
        self.assert_every_later_call_ran_on_the_persons_answer(fixture.QUESTION_DEFAULT)

    def test_a_medium_brief_waits_for_the_answer_even_when_it_is_the_default(self):
        self.chat(MEDIUM_BRIEF, fixture.QUESTION_DEFAULT + "\n")
        self.assertEqual("medium", (self.state().get("workflow") or {}).get("brief_size"))
        self.assert_every_later_call_ran_on_the_persons_answer(fixture.QUESTION_DEFAULT)

    def test_a_different_answer_is_the_only_answer_any_later_call_sees(self):
        self.chat(self.scenario.brief, fixture.DIFFERENT_ANSWER + "\n")
        self.assert_every_later_call_ran_on_the_persons_answer(fixture.DIFFERENT_ANSWER)


class SavedUsageTests(unittest.TestCase):
    """A saved run's background attempts keep their own public usage rows."""

    TASK = "Fix tier selection: below 10 items use basic; above 10 use bulk. Keep the public JSON format."

    def report(self, **change):
        value = {
            "summary": "Requirements for the tier selection change",
            "intended_outcome": "Select the requested tier on either side of the transition",
            "required_behaviors": ["Below 10 items use basic; above 10 use bulk"],
            "constraints": ["Keep the public JSON format"],
            "acceptance_tests": ["Given 9 items, selection returns basic.", "Given 11 items, selection returns bulk."],
            "requirements": [{"id": "R1", "text": self.TASK, "source_quote": self.TASK}],
            "source_refs": ["task"],
            "proposed_assumptions": [],
            "open_questions": [],
            "ignored_statements": [],
            "conflicts": [],
            "proposed_reframes": [],
            "job": "build",
        }
        value.update(change)
        return value

    def state(self, answers=None, report=None, spec_first=True):
        temporary = tempfile.TemporaryDirectory(prefix="requirements-overlap-unit-")
        self.addCleanup(temporary.cleanup)
        return {
            "version": 3,
            "task_id": "bound-answers",
            "task": self.TASK,
            "workspace": str(Path(temporary.name)),
            "settings": {
                "joint_planning": True,
                "spec_first": spec_first,
                "roles": {"task_planner": {"engine": "opencode", "model": "fixture"}},
            },
            "stages": [],
            "answers": dict(answers or {}),
            "user_events": [],
            "requirements_history": [],
            "requirements_handoff": {"report": report if report is not None else self.report()},
            "next_stage": review.NEXT,
        }

    def test_a_saved_runs_background_attempts_keep_their_own_usage_rows(self):
        """AC7 on the public view: a background Requirements attempt is named speculative with the
        outcome and reason that settled it, beside the spend it observed. An ordinary pass's row
        carries none of the three, and two settled background passes never share an outcome."""
        import autocode_run_view as run_view

        state = self.state()
        root = Path(state["workspace"])

        def attempt(name, *, tokens, **annotation):
            return {
                "stage": "requirements_gather",
                "role": "requirements",
                "engine": "codex",
                "iteration": 1,
                "output": str(root / f"{name}.json"),
                "metrics": {"provider_tokens": tokens},
                **annotation,
            }

        state["stages"] = [
            attempt("requirements-01", tokens={"input_tokens": 500, "output_tokens": 90}),
            attempt(
                "requirements-02",
                tokens={"input_tokens": 900, "output_tokens": 180},
                speculative=True,
                outcome="kept",
                reason="answers matched offered defaults",
            ),
            attempt("requirements-03", tokens={"input_tokens": 620, "output_tokens": 110}),
            attempt(
                "requirements-04",
                tokens={"input_tokens": 880, "output_tokens": 160},
                speculative=True,
                outcome="discarded",
                reason="the person answered Q_A differently from the offered default",
            ),
        ]
        rows = {row["output"]: row for row in run_view.view(state)["usage"]["accounting"]["attempts"]}
        self.assertEqual(4, len(rows), "each attempt keeps its own public usage row")

        kept = rows[str(root / "requirements-02.json")]
        self.assertIs(True, kept["speculative"])
        self.assertEqual("kept", kept["outcome"])
        self.assertEqual("answers matched offered defaults", kept["reason"])
        self.assertEqual(900, kept["tokens"]["input_tokens"], "the mark sits beside the attempt's own spend")
        discarded = rows[str(root / "requirements-04.json")]
        self.assertEqual("discarded", discarded["outcome"])
        self.assertIn("differently", discarded["reason"])
        self.assertEqual(880, discarded["tokens"]["input_tokens"])
        for name in ("requirements-01", "requirements-03"):
            ordinary = rows[str(root / f"{name}.json")]
            self.assertFalse(
                {"speculative", "outcome", "reason"} & set(ordinary),
                f"an ordinary pass's usage row names no speculative outcome: {name}",
            )


class _FakeDispatch:
    """The injected ``run_role`` for the fast proving cases: one bounded, recorded Requirements call.

    With ``child`` it models a real held provider — a process it spawns and whose verified
    identity it checkpoints onto the attempt — so the ownership stop path observes a live
    process exactly as production does; the call ends when that process is stopped. Without it,
    the scripted report is returned at once.
    """

    def __init__(self, test, *, child=False):
        self.test = test
        self.child_mode = child
        self.child = None
        self.calls = []
        self.started = threading.Event()
        self.record = {
            "stage": "requirements_gather",
            "role": "requirements",
            "route_role": "requirements",
            "iteration": 1,
            "attempt": 2,
            "output": str(test.run_dir / "requirements-02.json"),
            "events": str(test.run_dir / "requirements-02.jsonl"),
            "engine": "codex",
            "reasoning_effort": "low",
            "launch_route": {"model": test.MODEL, "reasoning_effort": "low"},
            "metrics": {"provider_tokens": {"input_tokens": 17, "output_tokens": 3}},
            "duration_seconds": 4.0,
        }
        self.report = {
            "summary": "scripted requirements report",
            "intended_outcome": "A greeting CLI",
            "requirements": [{"id": "R1", "text": test.TASK, "source_quote": test.TASK}],
            "required_behaviors": ["greet prints Hello, NAME"],
            "acceptance_tests": ["Given NAME, greet prints Hello, NAME."],
            "constraints": [],
            "open_questions": [],
            "proposed_assumptions": [],
            "ignored_statements": [],
            "conflicts": [],
            "proposed_reframes": [],
            "job": "build",
        }

    def __call__(self, **kwargs):
        import autocode_process as processes

        self.calls.append(kwargs)
        record = copy.deepcopy(self.record)
        if self.child_mode:
            self.child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True
            )
            table = processes.process_table({self.child.pid})
            record.update(
                pid=self.child.pid,
                supervision={"schema": 1, "provider": processes.identity(table[self.child.pid])},
            )
        kwargs["state"]["active_stage"] = record
        kwargs["persist"](self.test.run_dir / "state.json", kwargs["state"])  # the launch checkpoint
        self.started.set()
        if self.child_mode:
            while self.child.poll() is None:
                time.sleep(0.02)
            raise ValueError("the owned provider was stopped before a report")
        return copy.deepcopy(self.report), record


class FastProofTests(unittest.TestCase):
    """The real chat conversation with the launch path injected, and a saved run's pass on resume.

    Each case drives ``autocode.chat_checkpoint`` or ``autocode.reconcile_active`` with the stage
    launch (``run_role``), request preparation, the commit and the accounting replaced by recorded
    fakes and a real stdin pipe, so whether any model call starts while the person reads is
    observed deterministically.
    """

    TASK = "Build a deterministic greeting CLI named greet.py."
    MODEL = "fixture-requirements-model"
    DEFAULT = "Command line"
    DIFFERENT = "A web endpoint"

    @classmethod
    def setUpClass(cls):
        import autocode_resolver_human as human

        # These fixture repositories never change product files. Compute their real
        # source identity once; run checkpoints stay under the ignored .autocode.
        # Likewise each proof/mutant starts a fresh interpreter with fixed runtime
        # bytes, so the cached hashes include that mutant rather than another run.
        file_hash = human.support.file_hash
        runtime = Path(human.__file__).resolve().parent
        files = sorted(runtime.rglob("*.py"))
        runtime_hashes = {str(path): file_hash(path) for path in files}
        names = {str(path): path.relative_to(runtime) for path in files}
        hash_patch = patch.object(
            human.support,
            "file_hash",
            lambda path: runtime_hashes[str(path)] if str(path) in runtime_hashes else file_hash(path),
        )
        hash_patch.start()
        cls.addClassCleanup(hash_patch.stop)

        class RuntimeFile(str):
            # These immutable filesystem observations retain the actual path, bytes
            # and relative name, without repeatedly walking or sorting Path objects.
            def relative_to(self, parent):
                assert parent == runtime
                return names[self]

        runtime_files = tuple(RuntimeFile(path) for path in runtime_hashes)

        class FixturePath(type(Path())):
            def rglob(self, pattern, *args, **kwargs):
                if self == runtime and pattern == "*.py":
                    return iter(runtime_files)
                return super().rglob(pattern, *args, **kwargs)

        path_patch = patch.object(human, "Path", FixturePath)
        path_patch.start()
        cls.addClassCleanup(path_patch.stop)
        cls.git_seed = tempfile.TemporaryDirectory(prefix="overlap-git-seed-")
        cls.addClassCleanup(cls.git_seed.cleanup)
        subprocess.run(["git", "init", "--template=", "-q", cls.git_seed.name], check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ],
            cwd=cls.git_seed.name,
            check=True,
        )
        snapshot = human.source_scope.snapshot
        frozen_source = snapshot(Path(cls.git_seed.name), {}, base_snapshot=human.support.snapshot)
        cls.fixture_workspaces = set()
        snapshots = {}

        def fixture_snapshot(workspace, state, **kwargs):
            key = str(workspace)
            if key in cls.fixture_workspaces:
                # Every registered fixture is a byte-for-byte copy of this immutable
                # committed source; only ignored run checkpoints change afterward.
                return copy.deepcopy(frozen_source)
            if key not in snapshots:
                try:
                    snapshots[key] = snapshot(workspace, state, **kwargs)
                except RuntimeError as error:
                    # These immutable unborn fixtures have the same honest unavailable
                    # identity each time; do not rerun failing Git queries per checkpoint.
                    snapshots[key] = error
            result = snapshots[key]
            if isinstance(result, Exception):
                raise result
            return copy.deepcopy(result)

        snapshot_patch = patch.object(human.source_scope, "snapshot", fixture_snapshot)
        snapshot_patch.start()
        cls.addClassCleanup(snapshot_patch.stop)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="requirements-overlap-fast-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.run_dir = self.workspace / ".autocode/runs/run-1"
        self.run_dir.mkdir(parents=True)
        import shutil

        shutil.copytree(Path(self.git_seed.name) / ".git", self.workspace / ".git")
        self.fixture_workspaces.add(str(self.workspace))

    def question(self, question_id="OVERLAP_FORM", default=DEFAULT, *, options=()):
        return {
            "id": question_id,
            "question": "How should the greeting be invoked?",
            "why": "The request does not say.",
            "options": list(options),
            "proposed_default": default,
            "kind": "decision",
            "category": "requested_outcome",
            "delegable": False,
        }

    def report(self, *, questions, complete):
        value = {
            "summary": "Requirements for the greeting CLI",
            "intended_outcome": "A greeting CLI",
            "requirements": [{"id": "R1", "text": self.TASK, "source_quote": self.TASK}],
            "constraints": [],
            "acceptance_tests": ["Given NAME, greet prints Hello, NAME."] if complete else [],
            "proposed_assumptions": [],
            "open_questions": list(questions),
            "ignored_statements": [],
            "conflicts": [],
            "proposed_reframes": [],
            "job": "build",
            "required_behaviors": ["greet prints Hello, NAME"] if complete else [],
        }
        return value

    def paused(self, *, questions, spec_first=False, complete=False, limits=None, answers=None, brief="small"):
        """A live-chat run paused on the handoff's own blocking questions, exactly as the flow pauses it."""
        import autocode_resolver_human as resolver_human

        state = {
            "version": 3,
            "task_id": "fast-overlap",
            "task": self.TASK,
            "workspace": str(self.workspace),
            "run_dir": str(self.run_dir),
            "iteration": 1,
            "status": "RUNNING",
            "phase": "DISCOVERING",
            "workflow": {"brief_size": brief},
            "settings": {
                "joint_planning": True,
                "spec_first": spec_first,
                "roles": {"requirements": {"engine": "codex", "model": self.MODEL, "reasoning_effort": "low"}},
                "limits": dict({"stage_timeout_seconds": 180} if limits is None else limits),
            },
            "stages": [],
            "history": [],
            "answers": dict(answers or {}),
            "user_events": [],
            "requirements_history": [],
            "requirements_handoff": {"report": self.report(questions=questions, complete=complete)},
            "next_stage": review.NEXT,
        }
        review.wait(state, {"output": "", "source_revision": "0" * 40, "task_id": "fast-overlap"}, resolver_human.queue)
        self.assertEqual("escalate", resolver_human.evaluate(state), "the pause publishes its questions")
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.assertEqual(
            [row["id"] for row in questions or review.questions(state)],
            [row["id"] for row in state["pending_questions"]],
        )
        return state

    @staticmethod
    def prepare_request(state, stage, state_path, schema_dir):
        return SimpleNamespace(
            role="requirements",
            route_role="requirements",
            prompt="requirements for the greeting CLI",
            schema={"type": "object"},
            allow_write=False,
        )

    @staticmethod
    def _wiring():
        """The injected accounting callables, each charged exactly once per attempt."""
        accounted, saved = [], []

        def account_stage(state, record):
            accounted.append(record)
            record["accounted"] = True

        def save_record(state, record):
            saved.append(record)
            state.setdefault("stages", []).append(record)

        return accounted, saved, account_stage, save_record

    def chat(self, state, typed, *, dispatch=None, commit_error=None):
        """Run the real chat conversation over a real stdin pipe with the launch path injected."""
        import autocode as runner

        dispatch = _FakeDispatch(self) if dispatch is None else dispatch
        commit = []
        accounted, saved, account_stage, save_record = self._wiring()
        current = runner.resolver_human.current
        lookups = 0

        def observed_current(value):
            nonlocal lookups
            lookups += 1
            self.assertLessEqual(lookups, 100, "question handling repeated without requesting input")
            return current(value)

        def commit_stage_result(current, stage, value, record, workspace, run_dir):
            if commit_error is not None:
                raise commit_error
            commit.append((stage, value, record))
            current.setdefault("stages", []).append(record)
            current["requirements_handoff"] = {"report": value}

        read_fd, write_fd = os.pipe()
        os.write(write_fd, typed.encode())
        os.close(write_fd)
        stdin = os.fdopen(read_fd, "r")
        self.addCleanup(stdin.close)
        with contextlib.ExitStack() as stack:
            for context in (
                patch.object(runner, "run_role", dispatch),
                patch.object(runner, "commit_stage_result", commit_stage_result),
                patch.object(runner, "account_stage", account_stage),
                patch.object(runner, "save_record", save_record),
                patch.object(runner.autopilot, "prepare_request", self.prepare_request),
                patch.object(runner.launch_inputs, "guard", lambda *args, **kwargs: None),
                patch.object(runner.resolver_human, "current", observed_current),
                patch("sys.stdin", stdin),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                stack.enter_context(context)
            result = runner.chat_checkpoint(state, self.run_dir)
        return SimpleNamespace(result=result, dispatch=dispatch, commit=commit, accounted=accounted, saved=saved)

    def saved_answers(self, state):
        return {key: row["text"] for key, row in (state.get("answers") or {}).items()}

    def speculative(self, state):
        return [row for row in state.get("stages", []) if row.get("speculative")]

    def test_the_chat_starts_no_model_call_while_the_person_reads(self):
        for typed, saved in (
            (self.DEFAULT + "\n", self.DEFAULT),
            ("/default\n", None),
            (self.DIFFERENT + "\n", self.DIFFERENT),
            ("", None),
        ):
            with self.subTest(typed=typed):
                question = self.question(options=(self.DEFAULT, self.DIFFERENT))
                state = self.paused(questions=[question])
                result = self.chat(state, typed)
                self.assertEqual([], result.dispatch.calls, "no Requirements call runs on an assumed answer")
                self.assertEqual([], result.accounted)
                self.assertNotIn("requirements_overlap", state)
                self.assertEqual([], self.speculative(state))
                if typed == "":
                    self.assertFalse(result.result, "an unanswered chat pauses with the question open")
                    self.assertEqual({}, self.saved_answers(state))
                elif saved is not None:
                    self.assertEqual({question["id"]: saved}, self.saved_answers(state))
                else:
                    self.assertIn(question["id"], self.saved_answers(state))

    def test_a_saved_unfinished_pass_is_discarded_on_resume(self):
        import autocode as runner
        import autocode_process as processes

        question = self.question()
        state = self.paused(questions=[question])
        # A crash has no surviving chat thread to rewrite the reconciled state.
        # Keep the provider boundary injected and drive the existing resume entry
        # point, including its verified-identity stop and durable accounting.
        state["requirements_overlap"] = {"status": "running", "attempt_nonce": "fixture-resume"}
        identity = {"pid": 43210, "birth_identity": 12345.0}
        record = {
            **_FakeDispatch(self).record,
            "supervision": {"provider": identity},
            "requirements_overlap_nonce": "fixture-resume",
        }
        sidecar = self.run_dir / "requirements-overlap-state.json"
        util.atomic_json(sidecar, {**copy.deepcopy(state), "active_stage": record})
        alive, signals = [True], []

        def live(rows):
            self.assertEqual([identity], rows)
            return rows if alive[0] else []

        def stop(group, sig):
            self.assertEqual(identity["pid"], group)
            signals.append(sig)
            alive[0] = False

        accounted, saved, account_stage, save_record = self._wiring()
        with (
            patch.object(runner, "account_stage", account_stage),
            patch.object(runner, "save_record", save_record),
            patch.object(processes, "live_processes", live),
            patch("os.getpgid", return_value=identity["pid"]),
            patch("os.killpg", stop),
            patch("os.kill", stop),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            runner.reconcile_active(state, self.run_dir, self.workspace)
        self.assertEqual(
            "discarded", (state.get("requirements_overlap") or {}).get("status"), "the unfinished pass is discarded"
        )
        self.assertEqual(
            1, len([row for row in state.get("stages", []) if row.get("speculative")]), "accounted exactly once"
        )
        self.assertFalse(alive[0], "the owned process was settled through the verified boundary")
        self.assertEqual(1, len(signals))
        self.assertEqual(1, len(accounted))
        self.assertEqual(record["metrics"], accounted[0]["metrics"])
        settled = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual("discarded", (settled.get("requirements_overlap") or {}).get("status"))
        with (
            patch.object(runner, "account_stage", account_stage),
            patch.object(runner, "save_record", save_record),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            runner.reconcile_active(settled, self.run_dir, self.workspace)
        self.assertEqual(
            1,
            len([row for row in settled.get("stages", []) if row.get("speculative")]),
            "resume cannot duplicate spend",
        )
        self.assertEqual(1, len(accounted))

        for change in ("nonce", "stage", "run_dir"):
            with self.subTest(stale_sidecar=change):
                resumed = copy.deepcopy(state)
                resumed["requirements_overlap"] = {"status": "running", "attempt_nonce": "new-attempt"}
                candidate = copy.deepcopy(resumed)
                candidate["active_stage"] = {**record, "requirements_overlap_nonce": "new-attempt"}
                if change == "nonce":
                    candidate["requirements_overlap"]["attempt_nonce"] = "old-attempt"
                elif change == "stage":
                    candidate["active_stage"]["stage"] = "builder"
                else:
                    candidate["run_dir"] = "another-run"
                util.atomic_json(sidecar, candidate)
                before = len(accounted)
                with (
                    patch.object(runner, "account_stage", account_stage),
                    patch.object(runner, "save_record", save_record),
                    patch.object(processes, "live_processes", side_effect=AssertionError("unbound process")),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    for _ in range(2):
                        with self.assertRaises(runner.support.Paused) as held:
                            runner.reconcile_active(resumed, self.run_dir, self.workspace)
                        self.assertEqual("PAUSED_PROCESS_CLEANUP", held.exception.status)
                self.assertEqual(before, len(accounted), "a stale sidecar cannot duplicate spend")
                self.assertIn("uncertainty", resumed["requirements_overlap"])
                self.assertEqual("discarded", resumed["requirements_overlap"]["status"])

        # An authenticated attempt whose exit is uncertain blocks replacement work
        # across resumes, then clears only after that same owned attempt settles.
        resumed = copy.deepcopy(state)
        resumed["requirements_overlap"] = {"status": "running", "attempt_nonce": "pending-cleanup"}
        candidate = copy.deepcopy(resumed)
        candidate["active_stage"] = {**record, "requirements_overlap_nonce": "pending-cleanup"}
        util.atomic_json(sidecar, candidate)
        before = len(accounted)
        import autocode_requirements_overlap as overlap

        with (
            patch.object(runner, "account_stage", account_stage),
            patch.object(runner, "save_record", save_record),
            patch.object(overlap, "stop_verified", return_value={"settled": False, "reason": "exit not confirmed"}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            for _ in range(2):
                with self.assertRaises(runner.support.Paused):
                    runner.reconcile_active(resumed, self.run_dir, self.workspace)
        self.assertEqual(before + 1, len(accounted), "a pending cleanup charges its observed attempt only once")
        with (
            patch.object(runner, "account_stage", account_stage),
            patch.object(runner, "save_record", save_record),
            patch.object(overlap, "stop_verified", return_value={"settled": True, "reason": "owned process exited"}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            runner.reconcile_active(resumed, self.run_dir, self.workspace)
        self.assertFalse(resumed["requirements_overlap"].get("cleanup_pending"))
        self.assertNotIn("uncertainty", resumed["requirements_overlap"])
        self.assertEqual(before + 1, len(accounted))
        self.assertTrue(any(row.get("cleanup_pending") for row in resumed["requirements_overlap_history"]))

    def test_a_saved_pass_whose_spend_is_unobserved_stays_explicit(self):
        """A resumed attempt whose spend cannot be observed stays explicit uncertainty: the entry
        and the accounted attempt both record the gap, no zero spend is invented, and repeated
        resume neither duplicates accounting nor drops the uncertainty."""
        import autocode as runner
        import autocode_process as processes

        question = self.question()
        state = self.paused(questions=[question])
        state["requirements_overlap"] = {"status": "running", "attempt_nonce": "fixture-usage-gap"}
        identity = {"pid": 43210, "birth_identity": 12345.0}
        record = {**_FakeDispatch(self).record}
        record.pop("metrics", None)  # no saved metrics...
        record["events"] = str(self.run_dir / "missing-events.jsonl")  # ...and no readable events
        record["supervision"] = {"provider": identity}
        record["requirements_overlap_nonce"] = "fixture-usage-gap"
        sidecar = self.run_dir / "requirements-overlap-state.json"
        util.atomic_json(sidecar, {**copy.deepcopy(state), "active_stage": record})
        accounted, saved, account_stage, save_record = self._wiring()
        with (
            patch.object(runner, "account_stage", account_stage),
            patch.object(runner, "save_record", save_record),
            patch.object(processes, "live_processes", lambda rows: []),  # the owned process verifiably exited
            contextlib.redirect_stdout(io.StringIO()),
        ):
            runner.reconcile_active(state, self.run_dir, self.workspace)
        entry = state["requirements_overlap"]
        self.assertEqual("discarded", entry["status"])
        self.assertIn("uncertainty", entry, "unobserved spend is not silently settled")
        rows = self.speculative(state)
        self.assertEqual(1, len(rows), "the unobserved attempt is still accounted exactly once")
        self.assertEqual("discarded", rows[0]["outcome"])
        self.assertEqual(entry["reason"], rows[0]["reason"], "the recovered attempt carries the resume reason")
        self.assertFalse(rows[0].get("metrics"), "no usage was invented")
        self.assertEqual(entry["uncertainty"], rows[0].get("uncertainty"), "the attempt itself names the gap")
        settled = json.loads((self.run_dir / "state.json").read_text())
        with (
            patch.object(runner, "account_stage", account_stage),
            patch.object(runner, "save_record", save_record),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            runner.reconcile_active(settled, self.run_dir, self.workspace)
        self.assertEqual(1, len(accounted), "repeated resume does not duplicate accounting")
        self.assertEqual(
            1, len([row for row in settled.get("stages", []) if row.get("speculative")]), "one attempt row"
        )
        self.assertIn("uncertainty", settled["requirements_overlap"], "the gap stays explicit across resumes")

    def test_a_saved_pass_is_stopped_only_through_its_verified_identity(self):
        import signal

        import autocode_process as processes
        import autocode_requirements_overlap as overlap

        identity = {"pid": 4242, "started": 1.0, "birth_time": 1.0, "birth_identity": 12345.5}
        signalled = []
        reused = overlap.stop_verified(
            {"supervision": {"provider": dict(identity)}},
            live=lambda rows: [],  # the PID now belongs to a different process
            kill=lambda pid, sig: signalled.append((pid, sig)),
            pause=lambda seconds: None,
        )
        self.assertTrue(reused["settled"])
        self.assertFalse(reused["signalled"])
        self.assertEqual([], signalled, "a reused PID is never signalled")
        unknown = overlap.stop_verified(
            {"pid": 4242},
            live=lambda rows: [],
            kill=lambda pid, sig: signalled.append((pid, sig)),
            pause=lambda seconds: None,
        )
        self.assertFalse(unknown["settled"], "a bare PID is not authority")
        self.assertEqual([], signalled)
        alive = iter(([{"pid": 4242}], [{"pid": 4242}], [], []))

        def live(rows):
            return next(alive)

        stopped = overlap.stop_verified(
            {"supervision": {"provider": dict(identity)}},
            live=live,
            kill=lambda pid, sig: signalled.append((pid, sig)),
            pause=lambda seconds: None,
        )
        self.assertTrue(stopped["settled"])
        self.assertEqual([(4242, signal.SIGTERM)], signalled[:1], "the verified owned process is stopped")
        denied = overlap.stop_verified(
            {"supervision": {"provider": dict(identity)}},
            live=lambda rows: (_ for _ in ()).throw(processes.ProcessError("identity unavailable")),
            kill=lambda pid, sig: signalled.append((pid, sig)),
            pause=lambda seconds: None,
        )
        self.assertFalse(denied["settled"], "an identity that cannot be verified is reported, never guessed")

        states = iter((True, None))
        with patch.object(overlap, "_alive", side_effect=lambda *args, **kwargs: next(states)):
            unknown_mid_stop = overlap.stop_verified(
                {"supervision": {"provider": identity}},
                kill=lambda pid, sig: signalled.append((pid, sig)),
                pause=lambda seconds: None,
            )
        self.assertFalse(unknown_mid_stop["settled"])
        with patch.object(overlap, "_await_exit", return_value=False):
            unconfirmed = overlap.stop_verified(
                {"supervision": {"provider": identity}},
                live=lambda rows: rows,
                kill=lambda pid, sig: False,
                pause=lambda seconds: None,
            )
        self.assertFalse(unconfirmed["settled"], "an unconfirmed exit never grants settlement")

    def test_answers_are_read_one_line_at_a_time_without_read_ahead(self):
        import autocode_requirements_overlap_input as overlap_input

        with patch("builtins.input", return_value="Command line") as plain:
            self.assertEqual("Command line", overlap_input.read("You: "))
            plain.assert_called_once_with("You: ")
        for regular in (False, True):
            with self.subTest(regular_file=regular):
                text = "first\n雪 café\nlast"
                if regular:
                    path = self.root / "answers.txt"
                    path.write_text(text)
                    stdin = path.open()
                else:
                    read_fd, write_fd = os.pipe()
                    os.write(write_fd, text.encode())
                    os.close(write_fd)
                    stdin = os.fdopen(read_fd)
                with stdin, patch("sys.stdin", stdin):
                    self.assertEqual("first", overlap_input.read(""))
                    self.assertEqual("雪 café", overlap_input.read(""))
                    self.assertEqual("last", overlap_input.read(""))
                    with self.assertRaises(EOFError):
                        overlap_input.read("")
        # A stream with no descriptor falls back to input() itself.
        with patch("sys.stdin", io.StringIO("plain\n")):
            self.assertEqual("plain", overlap_input.read(""))

        # A tty uses input()'s C readline unless the proxy keeps it on our reader.
        import pty

        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        script = """import sys
sys.path.insert(0, "tools")
import autocode_requirements_overlap_input as reader
print("INPUT_READY", flush=True)
print("RESULT=" + reader.read(""), flush=True)
"""
        child = subprocess.Popen([sys.executable, "-c", script], cwd=ROOT, stdin=slave, stdout=slave, stderr=slave)
        output = b""
        try:
            # Readiness events, not elapsed time, gate input and completion; the
            # deadline is only a watchdog for a hung child.
            import select

            deadline = time.monotonic() + 120
            while b"INPUT_READY" not in output:
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, "the tty child never became ready")
                if select.select([master], [], [], min(remaining, 0.1))[0]:
                    output += os.read(master, 65536)
                elif child.poll() is not None:
                    self.fail(f"the tty child exited before input readiness: {output!r}")
            os.write(master, "雪 café\n".encode())
            while b"RESULT=" not in output:
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, "the tty child did not complete its input")
                if select.select([master], [], [], min(remaining, 0.1))[0]:
                    output += os.read(master, 65536)
                elif child.poll() is not None:
                    self.fail(f"the tty child exited before completing input: {output!r}")
            child.wait(timeout=max(1, deadline - time.monotonic()))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
        os.set_blocking(master, False)
        while True:
            try:
                chunk = os.read(master, 65536)
            except (BlockingIOError, OSError):
                break
            if not chunk:
                break
            output += chunk
        self.assertEqual(0, child.returncode, output)
        self.assertIn("RESULT=雪 café", output.decode(errors="replace"), "a multibyte answer is read whole on a tty")


if __name__ == "__main__":
    unittest.main()
