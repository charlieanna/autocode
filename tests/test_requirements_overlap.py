"""The Requirements reading-time overlap: the full pass runs while the person reads (#3233 part 1).

Every case is a concrete example of the approved plan. The chat cases drive the real
``autocode --chat`` conversation with the scripted provider adapter in
``requirements_overlap_provider`` (only model output is scripted); the no-chat guard drives
the ordinary ``--no-chat`` task-run flow; the unit cases pin the pure overlap decisions and
the handoff's answer binding. A pass that runs while the person is still reading shows the
offered defaults as its saved answers and no recorded answer from the person in the
provider call log.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

import autocode_requirements_handoff as handoff
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
JOINT = ("--joint-planning", "--no-adaptive-planning")

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


class QuestionsPauseChatTests(CliFixture):
    """AC1, AC2, AC4: the handoff's questions-only pause (the triage pass asks them on a
    medium brief; a small brief's ordinary pass asks them itself)."""

    mode = "questions"

    def test_ac1_overlap_keeps_background_report(self):
        self.chat(MEDIUM_BRIEF, fixture.QUESTION_DEFAULT + "\n")
        asked = self.asked(fixture.QUESTION_ID)
        self.assertEqual(fixture.QUESTION_ID, asked.get("id"))
        self.assertEqual(fixture.QUESTION_DEFAULT, asked.get("proposed_default"))
        self.assertEqual("medium", (self.state().get("workflow") or {}).get("brief_size"))
        calls = self.requirements_calls()
        self.assertEqual(2, len(calls), calls)
        background = calls[-1]
        self.assertEqual({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}, background["saved"])
        self.assertNotIn(
            fixture.QUESTION_ID,
            background["answered"],
            "the full pass must run before the person's answer is recorded",
        )
        report = self.state()["requirements_handoff"]["report"]
        self.assertIn(stamped({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}), report["summary"])
        import autocode_requirements_overlap as overlap

        self.assertEqual(
            overlap.answers_digest({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}),
            report.get("assumed_answers_digest"),
            "the kept report binds the exact answers it assumed",
        )

    def test_ac2_differing_answer_runs_ordinary_pass(self):
        self.chat(MEDIUM_BRIEF, fixture.DIFFERENT_ANSWER + "\n", after_calls=2)
        calls = self.requirements_calls()
        self.assertEqual(3, len(calls), calls)
        background, ordinary = calls[-2], calls[-1]
        self.assertEqual({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}, background["saved"])
        self.assertNotIn(fixture.QUESTION_ID, background["answered"])
        self.assertEqual({fixture.QUESTION_ID: fixture.DIFFERENT_ANSWER}, ordinary["saved"])
        report = self.state()["requirements_handoff"]["report"]
        self.assertIn(stamped({fixture.QUESTION_ID: fixture.DIFFERENT_ANSWER}), report["summary"])
        self.assertNotIn(
            stamped({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}),
            report["summary"],
            "the background report is never adopted",
        )

    def test_ac4_small_brief_overlap(self):
        self.chat(self.scenario.brief, fixture.QUESTION_DEFAULT + "\n")
        self.assertEqual("small", (self.state().get("workflow") or {}).get("brief_size"))
        calls = self.requirements_calls()
        self.assertEqual(2, len(calls), calls)
        background = calls[-1]
        self.assertEqual({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}, background["saved"])
        self.assertNotIn(fixture.QUESTION_ID, background["answered"])
        report = self.state()["requirements_handoff"]["report"]
        self.assertIn(stamped({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}), report["summary"])
        import autocode_requirements_overlap as overlap

        self.assertEqual(
            overlap.answers_digest({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}),
            report.get("assumed_answers_digest"),
            "the kept report binds the exact answers it assumed",
        )

    def test_overlap_offered_choice_exact_and_whitespace_answers(self):
        # Canonicalized answers match the offered default (#3651): surrounding whitespace and /default
        # keep the background call, while case-distinct free text on an optionless question discards it.
        for typed, expected_saved, keeps in (
            (fixture.QUESTION_DEFAULT, fixture.QUESTION_DEFAULT, True),
            (" " + fixture.QUESTION_DEFAULT + " ", fixture.QUESTION_DEFAULT, True),
            (fixture.QUESTION_DEFAULT.lower(), fixture.QUESTION_DEFAULT.lower(), False),
            ("/default", fixture.QUESTION_DEFAULT, True),
        ):
            with self.subTest(typed=typed):
                self.setUp()
                self.chat(self.scenario.brief, typed + "\n", after_calls=2)
                calls = self.requirements_calls()
                self.assertEqual(2 if keeps else 3, len(calls), calls)
                background = calls[1]
                self.assertEqual({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}, background["saved"])
                self.assertNotIn(fixture.QUESTION_ID, background["answered"], "dispatch precedes the answer")
                if keeps:
                    self.assertIn(
                        stamped({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}),
                        self.state()["requirements_handoff"]["report"]["summary"],
                    )
                else:
                    self.assertEqual({fixture.QUESTION_ID: expected_saved}, calls[-1]["saved"])
                    self.assertIn(fixture.QUESTION_ID, calls[-1]["answered"])
                self.assertEqual(
                    "kept" if keeps else "discarded",
                    self.state()["requirements_overlap"]["status"],
                )
                if typed == "/default":
                    events = [
                        event
                        for event in self.state().get("user_events") or []
                        if event.get("question_id") == fixture.QUESTION_ID
                    ]
                    self.assertEqual(1, len(events), events)
                    self.assertEqual(fixture.QUESTION_DEFAULT, events[0]["text"])

    def test_ac16_background_uses_saved_route_and_budgets(self):
        model = "gpt-5.6-sol"
        effort = "low"
        self.chat(
            self.scenario.brief,
            fixture.QUESTION_DEFAULT + "\n",
            "--requirements-model",
            model,
            "--requirements-reasoning-effort",
            effort,
            "--max-cost-usd",
            "0",
            "--max-seconds",
            "3600",
            "--max-stage-seconds",
            "180",
        )
        state = self.state()
        calls = self.requirements_calls()
        self.assertEqual(2, len(calls), calls)
        self.assertNotIn(fixture.QUESTION_ID, calls[-1]["answered"], "the background call ran before the answer")
        self.assertEqual("kept", state["requirements_overlap"]["status"])
        rows = [row for row in state["stages"] if row.get("stage") == "requirements_gather"]
        speculative = [row for row in rows if row.get("speculative")]
        self.assertEqual(1, len(speculative), "the kept background attempt is bound to its own stage row")
        self.assertEqual("kept", speculative[0]["outcome"])
        self.assertEqual("requirements", speculative[0]["role"])
        self.assertEqual("codex", speculative[0]["engine"])
        self.assertEqual(model, speculative[0]["launch_route"]["model"])
        self.assertEqual(effort, speculative[0]["launch_route"]["reasoning_effort"])
        self.assertEqual(model, state["settings"]["roles"]["requirements"]["model"])
        self.assertEqual(effort, state["settings"]["roles"]["requirements"]["reasoning_effort"])
        self.assertEqual(0, state["settings"]["limits"]["max_cost_usd"])
        self.assertEqual(3600, state["settings"]["limits"]["max_seconds"])
        self.assertEqual(180, state["settings"]["limits"]["stage_timeout_seconds"])


class SpecFirstOpenQuestionsChatTests(CliFixture):
    """AC13: a --spec-first run's complete handoff still pauses on its unanswered open questions."""

    mode = "open-questions"

    def test_ac13_spec_first_open_questions_overlap(self):
        self.chat(self.scenario.brief, fixture.QUESTION_DEFAULT + "\n", "--spec-first")
        asked = self.asked(fixture.QUESTION_ID)
        self.assertEqual(fixture.QUESTION_DEFAULT, asked.get("proposed_default"))
        calls = self.requirements_calls()
        self.assertEqual(2, len(calls), calls)
        background = calls[-1]
        self.assertEqual({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}, background["saved"])
        self.assertNotIn(fixture.QUESTION_ID, background["answered"])
        report = self.state()["requirements_handoff"]["report"]
        self.assertIn(stamped({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}), report["summary"])
        import autocode_requirements_overlap as overlap

        self.assertEqual(
            overlap.answers_digest({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}),
            report.get("assumed_answers_digest"),
            "the kept report binds the exact answers it assumed",
        )


class NoChatGuardTests(CliFixture):
    """AC3: without a live chat process today's pause-then-run behavior is kept."""

    mode = "questions"

    def test_ac3_no_chat_keeps_today_behavior(self):
        run = taskrun.TaskRun.start(
            self.project,
            self.scenario.brief,
            options=(*self.flags, *JOINT),
            env=self.environment,
            timeout=180,
            cwd=self.root,
        )
        self.run_dir = run.run_dir
        view = run.advance_until_input()
        need = view.get("needs") or {}
        self.assertEqual("answer", need.get("kind"), need)
        questions = need.get("questions") or []
        self.assertEqual([fixture.QUESTION_ID], [row["id"] for row in questions], questions)
        self.assertEqual(fixture.QUESTION_DEFAULT, questions[0]["proposed_default"])
        self.assertEqual(1, len(self.requirements_calls()), "no background pass starts without a chat process")
        run.answer(fixture.QUESTION_ID, fixture.DIFFERENT_ANSWER, resolver_token=need["resolver_token"])
        view = run.advance_until_input()
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), view.get("needs"))
        calls = self.requirements_calls()
        self.assertEqual(2, len(calls), calls)
        self.assertEqual(
            {fixture.QUESTION_ID: fixture.DIFFERENT_ANSWER},
            calls[-1]["saved"],
            "the full Requirements pass runs only after the person's answers, and with them",
        )

    def test_ac16_saved_requirements_route_and_budgets_unchanged(self):
        model = "gpt-5.6-sol"
        effort = "low"
        limits = ("--max-cost-usd", "0", "--max-seconds", "3600", "--max-stage-seconds", "180")
        run = taskrun.TaskRun.start(
            self.project,
            self.scenario.brief,
            options=(
                *self.flags,
                *JOINT,
                "--requirements-model",
                model,
                "--requirements-reasoning-effort",
                effort,
                *limits,
            ),
            env=self.environment,
            timeout=180,
            cwd=self.root,
        )
        self.run_dir = run.run_dir
        need = run.advance_until_input().get("needs") or {}
        self.assertEqual("answer", need.get("kind"), need)
        self.assertEqual(1, len(self.requirements_calls()), "observe an ordinary Requirements call on base too")
        run.answer(fixture.QUESTION_ID, fixture.QUESTION_DEFAULT, resolver_token=need["resolver_token"])
        run.advance_until_input()
        state = self.state()
        self.assertEqual(model, state["settings"]["roles"]["requirements"]["model"])
        self.assertEqual(effort, state["settings"]["roles"]["requirements"]["reasoning_effort"])
        self.assertEqual(0, state["settings"]["limits"]["max_cost_usd"])
        self.assertEqual(3600, state["settings"]["limits"]["max_seconds"])
        self.assertEqual(180, state["settings"]["limits"]["stage_timeout_seconds"])
        self.assertEqual(2, len(self.requirements_calls()))
        full = [row for row in state["stages"] if row.get("stage") == "requirements_gather"]
        self.assertTrue(full)
        for row in full:
            self.assertEqual("requirements", row.get("route_role", row["role"]))
            self.assertEqual("codex", row["engine"])
            self.assertEqual(effort, row["reasoning_effort"])
            self.assertEqual(model, row["launch_route"]["model"])
            self.assertEqual(effort, row["launch_route"]["reasoning_effort"])


class CapChatTests(CliFixture):
    mode = "questions"

    def test_ac6_cap_skips_background_call(self):
        # A finite cost cap cannot authorize an unbounded speculative call even if the estimate
        # looks affordable. The stage deadline also cannot fit inside the active-time cap.
        for flags, limits in (
            (("--max-cost-usd", "1000"), {"max_cost_usd": 1000}),
            (
                ("--max-cost-usd", "0", "--max-seconds", "120", "--max-stage-seconds", "180"),
                {"max_cost_usd": 0, "max_seconds": 120, "stage_timeout_seconds": 180},
            ),
        ):
            with self.subTest(cap=flags):
                self.setUp()  # a fresh isolated project and run for each cap
                self.chat(self.scenario.brief, fixture.QUESTION_DEFAULT + "\n", *flags)
                calls = self.requirements_calls()
                self.assertEqual(1, len(calls), "denied cap preserves the original one-call flow")
                self.assertEqual({}, calls[0]["saved"])
                self.assertNotIn(fixture.QUESTION_ID, calls[0]["answered"])
                asked = self.asked(fixture.QUESTION_ID)
                self.assertEqual(fixture.QUESTION_DEFAULT, asked["proposed_default"])
                state = self.state()
                answers = [
                    event["text"]
                    for event in state.get("user_events") or []
                    if event.get("question_id") == fixture.QUESTION_ID
                ]
                self.assertEqual([fixture.QUESTION_DEFAULT], answers)
                self.assertIn(stamped({}), state["requirements_handoff"]["report"]["summary"])
                self.assertFalse(
                    any(row.get("speculative") for row in state.get("stages") or []),
                    "a denied cap cannot dispatch a background attempt",
                )
                self.assertNotEqual("kept", (state.get("requirements_overlap") or {}).get("status"))
                for key, value in limits.items():
                    self.assertEqual(value, state["settings"]["limits"][key])
        # Admission is not a blanket ban: with no finite cost cap and enough bounded active
        # time, the same pause must dispatch before the answer (the behavioral fail-first).
        self.setUp()
        self.chat(
            self.scenario.brief,
            fixture.QUESTION_DEFAULT + "\n",
            "--max-cost-usd",
            "0",
            "--max-seconds",
            "3600",
            "--max-stage-seconds",
            "180",
        )
        calls = self.requirements_calls()
        self.assertEqual(2, len(calls), calls)
        self.assertNotIn(fixture.QUESTION_ID, calls[-1]["answered"])
        self.assertEqual("kept", self.state()["requirements_overlap"]["status"])


class TwoQuestionsChatTests(CliFixture):
    mode = "two-questions"

    def test_ac18_two_defaults_require_every_answer_to_match(self):
        for second in (fixture.SECOND_QUESTION["proposed_default"], "JSON"):
            with self.subTest(second_answer=second):
                self.setUp()
                self.chat(self.scenario.brief, fixture.QUESTION_DEFAULT + "\n" + second + "\n", after_calls=2)
                calls = self.requirements_calls()
                self.assertEqual(2 if second == fixture.SECOND_QUESTION["proposed_default"] else 3, len(calls), calls)
                background = calls[1]
                defaults = {
                    fixture.QUESTION_ID: fixture.QUESTION_DEFAULT,
                    fixture.SECOND_QUESTION["id"]: fixture.SECOND_QUESTION["proposed_default"],
                }
                self.assertEqual(defaults, background["saved"])
                self.assertFalse(set(defaults) & set(background["answered"]), "dispatch precedes both answers")
                actual = {**defaults, fixture.SECOND_QUESTION["id"]: second}
                if second != defaults[fixture.SECOND_QUESTION["id"]]:
                    self.assertEqual(actual, calls[-1]["saved"])
                    self.assertTrue(set(actual) <= set(calls[-1]["answered"]))
                report = self.state()["requirements_handoff"]["report"]
                self.assertIn(stamped(actual), report["summary"])
                self.assertEqual(
                    "kept" if second == defaults[fixture.SECOND_QUESTION["id"]] else "discarded",
                    self.state()["requirements_overlap"]["status"],
                )


class CancellationChatTests(CliFixture):
    mode = "questions"

    def test_ac8_stop_cancels_background_pass(self):
        import autocode_interventions as inbox

        for kind in ("stop", "pause"):
            with self.subTest(intervention=kind):
                self.setUp()
                self.environment["AUTOCODE_OVERLAP_HOLD_FULL"] = "1"
                command = [
                    *taskrun.AUTOCODE,
                    "--workspace",
                    str(self.project),
                    self.scenario.brief,
                    *self.flags,
                    "--in-place",
                    "--chat",
                ]
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=self.root,
                    env={**os.environ, **self.environment},
                )
                try:
                    # Never supply an answer: the second Requirements invocation must have started
                    # while the person is still reading the question.
                    deadline = time.monotonic() + 120  # watchdog, not a speed assertion
                    while len(self.requirements_calls()) < 2 and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertEqual(2, len(self.requirements_calls()), self.calls())
                    run_dir = next((self.project / ".autocode/runs").iterdir())
                    self.run_dir = run_dir
                    inbox.submit(self.project, run_dir, request_id=f"overlap-{kind}", kind=kind, text="")
                    process.wait(timeout=120)  # watchdog: stdin is deliberately unanswered
                    state = self.state()
                    self.assertEqual("discarded", state["requirements_overlap"]["status"])
                    self.assertNotIn(
                        "assumed_answers_digest", (state.get("requirements_handoff") or {}).get("report") or {}
                    )
                    self.assertEqual(2, len(self.requirements_calls()), "no result may be adopted after cancellation")
                    records = [row for row in state.get("stages", []) if row.get("stage") == "requirements_gather"]
                    self.assertEqual(2, len(records), "the in-flight attempt's observed spend is accounted once")
                    self.assertEqual(1, len([row for row in records if row.get("speculative")]))
                    self.assertEqual("discarded", [row for row in records if row.get("speculative")][0]["outcome"])
                    self.assertIsNotNone([row for row in records if row.get("speculative")][0].get("metrics"))
                    seen = [row["kind"] for row in inbox.pending(run_dir)] + [
                        row["kind"] for row in state.get("applied_interventions", [])
                    ]
                    self.assertIn(kind, seen, "the requested intervention remains effective")
                finally:
                    if process.poll() is None:
                        process.terminate()
                    process.communicate(timeout=10)


class ResumeTests(CliFixture):
    mode = "questions"

    def test_ac9_resume_discards_unfinished(self):
        self.environment["AUTOCODE_OVERLAP_HOLD_FULL"] = "1"
        process = subprocess.Popen(
            [
                *taskrun.AUTOCODE,
                "--workspace",
                str(self.project),
                self.scenario.brief,
                *self.flags,
                "--in-place",
                "--chat",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.root,
            env={**os.environ, **self.environment},
        )
        try:
            # A real launched provider, its saved attempt and emitted usage are the fixture.
            # No unowned PID or invented stage record is inserted into state.json.
            deadline = time.monotonic() + 120
            while len(self.requirements_calls()) < 2 and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(2, len(self.requirements_calls()), self.calls())
            self.run_dir = next((self.project / ".autocode/runs").iterdir())
            sidecar = self.run_dir / "requirements-overlap-state.json"
            observed = None
            while process.poll() is None and time.monotonic() < deadline:
                if sidecar.is_file():
                    active = json.loads(sidecar.read_text()).get("active_stage") or {}
                    events = active.get("events")
                    if active.get("supervision") and events and Path(events).is_file():
                        lines = Path(events).read_text()
                        if '"input_tokens": 17' in lines or '"input_tokens":17' in lines:
                            observed = active
                            break
                time.sleep(0.05)
            self.assertIsNotNone(observed, "the real supervised attempt emitted its usage before the crash")
            process.kill()  # simulate controller crash, leaving its authenticated attempt for recovery
            process.communicate(timeout=30)
            self.environment.pop("AUTOCODE_OVERLAP_HOLD_FULL")
            run = taskrun.TaskRun(self.project, self.run_dir, env=self.environment, timeout=180, cwd=self.root)
            need = run.status().get("needs") or {}
            self.assertEqual("answer", need.get("kind"), need)
            run.answer(fixture.QUESTION_ID, fixture.DIFFERENT_ANSWER, resolver_token=need["resolver_token"])
            run.advance_until_input()
            state = self.state()
            self.assertEqual("discarded", state["requirements_overlap"]["status"])
            self.assertEqual(3, len(self.requirements_calls()), "one original, one interrupted, one ordinary pass")
            self.assertEqual({fixture.QUESTION_ID: fixture.DIFFERENT_ANSWER}, self.requirements_calls()[-1]["saved"])
            self.assertIn(
                stamped({fixture.QUESTION_ID: fixture.DIFFERENT_ANSWER}),
                state["requirements_handoff"]["report"]["summary"],
            )
            records = [row for row in state["stages"] if row.get("stage") == "requirements_gather"]
            recovered = [row for row in records if row.get("speculative")]
            self.assertEqual(1, len(recovered), "the crashed attempt is accounted exactly once")
            self.assertEqual(17, recovered[0]["metrics"]["provider_tokens"]["input_tokens"])
            run.advance_until_input()
            repeated = [row for row in self.state()["stages"] if row.get("speculative")]
            self.assertEqual(recovered, repeated, "resume cannot duplicate observed spend")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=30)


class EligibilityIntegrationGuards(CliFixture):
    def test_ac12_defaultless_question_keeps_original_cli_flow(self):
        for extra in ((), ("--spec-first",)):
            with self.subTest(extra=extra):
                self.setUp()
                self.environment["AUTOCODE_OVERLAP_MODE"] = "defaultless"
                self.chat(self.scenario.brief, fixture.DIFFERENT_ANSWER + "\n", *extra)
                asked = self.asked(fixture.QUESTION_ID)
                self.assertEqual(fixture.QUESTION_TEXT, asked["question"])
                self.assertEqual([], asked["options"])
                self.assertEqual("", asked["proposed_default"])
                # Preserve the independently accepted guard's exact lines when formatting additions.
                # fmt: off
                events = [
                    event for event in self.state().get("user_events") or []
                    if event.get("question_id") == fixture.QUESTION_ID
                ]
                # fmt: on
                self.assertEqual(1, len(events), events)
                self.assertEqual(fixture.DIFFERENT_ANSWER, events[0]["text"])
                calls = self.requirements_calls()
                self.assertEqual(1, len(calls), calls)
                self.assertEqual({}, calls[0]["saved"])
                self.assertFalse(
                    any(row.get("speculative") for row in self.state().get("stages") or []),
                    "a question without a default cannot launch a speculative pass",
                )
                self.assertNotEqual("kept", (self.state().get("requirements_overlap") or {}).get("status"))

    def test_integration_review_confirmation_starts_no_pass(self):
        self.environment["AUTOCODE_OVERLAP_MODE"] = "review-only"
        self.chat(self.scenario.brief, "plan\n", "--spec-first")
        calls = self.requirements_calls()
        self.assertEqual(1, len(calls), calls)
        self.assertEqual({}, calls[0]["saved"])
        self.assertIn("draft_plan", [row.get("stage") for row in self.calls()])


class BusinessQuestionsChatTests(CliFixture):
    """AC14: the default flow overlaps even when its old answer path did not refresh."""

    mode = "open-questions"

    def build_project(self):
        import autocode_coordination_store as coordination

        anchor = str(self.root / "intake/.autocode/runs/fix")
        with patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "registry")}):
            project = without_maintenance(materialize(self.scenario.seed, coordination.root(anchor) / "joint-1"))
            run_dir = project / ".autocode/runs/joint-regression"
            coordination.save(
                {
                    "version": 1,
                    "anchor": anchor,
                    "members": [],
                    "joint": {"workspace": str(project), "run_dir": str(run_dir), "started": False},
                    "pending": {"operation": "joint_plan_start"},
                }
            )
        self.run_dir = run_dir
        return project

    def test_ac14_business_questions_start_background(self):
        for answer in (fixture.QUESTION_DEFAULT, fixture.DIFFERENT_ANSWER):
            with self.subTest(answer=answer):
                self.setUp()
                self.chat(
                    self.scenario.brief,
                    answer + "\n",
                    "--workflow",
                    "build",
                    "--no-requirements-triage",
                    "--new-run-dir",
                    str(self.run_dir.absolute()),
                    after_calls=2,
                )
                asked = self.asked(fixture.QUESTION_ID)
                self.assertEqual(fixture.QUESTION_TEXT, asked.get("question"))
                self.assertEqual(fixture.QUESTION_DEFAULT, asked.get("proposed_default"))
                calls = self.requirements_calls()
                self.assertEqual(2 if answer == fixture.QUESTION_DEFAULT else 3, len(calls), calls)
                self.assertEqual({}, calls[0]["saved"])
                background = calls[1]
                self.assertEqual({fixture.QUESTION_ID: fixture.QUESTION_DEFAULT}, background["saved"])
                self.assertNotIn(fixture.QUESTION_ID, background["answered"], "dispatch precedes the answer")
                if answer != fixture.QUESTION_DEFAULT:
                    self.assertEqual({fixture.QUESTION_ID: answer}, calls[-1]["saved"])
                    self.assertIn(fixture.QUESTION_ID, calls[-1]["answered"])
                report = self.state()["requirements_handoff"]["report"]
                self.assertIn(stamped({fixture.QUESTION_ID: answer}), report["summary"])
                self.assertEqual(
                    "kept" if answer == fixture.QUESTION_DEFAULT else "discarded",
                    self.state()["requirements_overlap"]["status"],
                )


class ControlsGuardTests(CliFixture):
    """AC10: the overlap moves when the model works, never what the person is shown or offered."""

    mode = "questions"

    def test_ac10_the_pause_shows_the_same_questions_and_offers_no_new_control(self):
        transcript = self.chat(MEDIUM_BRIEF, fixture.DIFFERENT_ANSWER + "\n").stdout
        for shown in (
            f"Resolver: {fixture.QUESTION_TEXT}",
            f"Why: {fixture.QUESTION['why']}",
            f"Suggested default: {fixture.QUESTION_DEFAULT}",
        ):
            self.assertIn(shown, transcript, "the same question, reason and offered default as before this change")
        self.assertEqual(
            ["You (/default, /feedback TEXT, or /pause): "],
            sorted(set(re.findall(r"You \([^)]*\): ", transcript))),
            "the chat offers no new control alongside the ordinary answers",
        )
        self.assertNotRegex(
            transcript,
            r"(?i)speculative|background (pass|call)|while you (read|are reading)",
            "the pause narrates AutoCode's own machinery to the person",
        )
        help_text = subprocess.run(
            [*taskrun.AUTOCODE, "--help"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=self.root,
            env={**os.environ, **self.environment},
        ).stdout
        self.assertNotRegex(help_text, r"(?i)overlap|speculative", "the overlap added a CLI flag")


class OverlapUnitTests(unittest.TestCase):
    """Pure decisions: the assumed-answers binding, and the eligibility exclusions."""

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

    def question(self, question_id: str, default: str) -> dict:
        return {
            "id": question_id,
            "question": "How should this behave?",
            "why": "The request leaves it open.",
            "options": [],
            "proposed_default": default,
            "kind": "decision",
            "category": "requested_outcome",
            "delegable": False,
        }

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

    def test_ac5_bound_answers_refuse_mismatch(self):
        assumed = {"Q1": "CLI: local use without a server"}
        digest_of = lambda answers: util.digest(sorted((qid, text) for qid, text in answers.items()))
        self.assertEqual(
            digest_of({"Q1": "a", "Q2": "b"}),
            digest_of({"Q2": "b", "Q1": "a"}),
            "the digest of question ids and answer values does not depend on the mapping's order",
        )
        self.assertNotEqual(digest_of({"Q1": "a"}), digest_of({"Q1": "b"}))
        digest = digest_of(assumed)
        state = self.state(answers={"Q1": {"text": assumed["Q1"]}})
        value = self.report(assumed_answers_digest=digest)
        handoff.accept(state, value, {"output": None}, check_code_refs=mock.Mock(), queue=mock.Mock())
        self.assertEqual(digest, state["requirements_handoff"]["report"]["assumed_answers_digest"])
        with self.assertRaises(ValueError):
            handoff.accept(
                self.state(answers={"Q1": {"text": assumed["Q1"]}}),
                self.report(assumed_answers_digest=digest_of({"Q1": "A web endpoint"})),
                {"output": None},
                check_code_refs=mock.Mock(),
                queue=mock.Mock(),
            )
        handoff.accept(
            self.state(answers={}),
            self.report(),
            {"output": None},
            check_code_refs=mock.Mock(),
            queue=mock.Mock(),
        )

    def test_canonical_answer_matching_and_digest_normalize_options_and_whitespace(self):
        import autocode_requirements_overlap as overlap

        question = {
            "id": "Q1",
            "question": "How should callers invoke it?",
            "why": "Selects the public surface.",
            "options": ["Command line", "A web’s endpoint"],
            "proposed_default": "  `command   line`  ",
        }
        self.assertEqual({"Q1": "Command line"}, overlap.assumed_defaults([question]))
        expected_digest = overlap.answers_digest({"Q1": "Command line"}, questions=[question])
        for variant in ("Command line", "command line", "COMMAND LINE", "  'Command   line'  ", "`Command line`"):
            with self.subTest(variant=variant):
                matched, reason = overlap.answers_match([question], {"Q1": {"text": variant, "question": question}})
                self.assertTrue(matched, reason)
                self.assertEqual(
                    expected_digest,
                    overlap.answers_digest({"Q1": {"text": variant, "question": question}}),
                )
                state = self.state(
                    answers={"Q1": {"text": variant, "question": question}},
                    report=self.report(open_questions=[question]),
                )
                handoff.accept(
                    state,
                    self.report(assumed_answers_digest=expected_digest),
                    {"output": None},
                    check_code_refs=mock.Mock(),
                    queue=mock.Mock(),
                )
        matched, _ = overlap.answers_match([question], {"Q1": {"text": "a web's endpoint", "question": question}})
        self.assertFalse(matched)
        free_text = {
            "id": "Q2",
            "question": "What prefix should log lines use?",
            "why": "Shown to operators.",
            "options": [],
            "proposed_default": "AutoCode",
        }
        self.assertTrue(overlap.answers_match([free_text], {"Q2": "  AutoCode  "})[0])
        self.assertFalse(overlap.answers_match([free_text], {"Q2": "autocode"})[0])

    def test_ac12_defaultless_question_keeps_today_behavior(self):
        questions = [self.question("Q_A", "Command line"), self.question("Q_B", "")]
        state = self.state(report=self.report(open_questions=list(questions)))
        self.assertEqual(questions, review.questions(state))
        self.assertEqual({}, state["answers"])
        state["answers"] = {"Q_A": {"text": "Command line"}, "Q_B": {"text": "A web endpoint"}}
        review.after_answers(state)
        self.assertEqual(review.STAGE, state["next_stage"], "today's behavior: the full pass runs after the answers")

    def test_ac15_review_confirmation_no_background(self):
        state = self.state()
        shown = review.questions(state)
        self.assertEqual(1, len(shown), shown)
        confirmation = shown[0]
        self.assertTrue(str(confirmation["id"]).startswith(review.PREFIX), confirmation)
        self.assertEqual(review.APPROVE, confirmation["proposed_default"])
        state["answers"] = {confirmation["id"]: {"text": review.APPROVE}}
        review.after_answers(state)
        self.assertEqual(review.NEXT, state["next_stage"], "a 'plan' answer keeps today's confirm-and-plan flow")

    def test_ac7_status_usage_marks_each_background_attempt_with_its_own_outcome(self):
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

    def test_follow_up_turn_retaining_earlier_contract_admits_current_handoff_questions_only(self):
        import autocode_requirements_overlap as overlap

        q_turn1 = self.question("Q_TURN1", "Old default")
        q_turn2 = self.question("Q_TURN2", "Command line")
        turn1_handoff = {"output": "iter/001/req-01.json", "report": self.report(open_questions=[q_turn1])}
        state = self.state()
        state.update(
            goal_contract={"revision": 1, "hash": "turn1", "body": {}},
            requirements_handoff=turn1_handoff,
            turns=[
                {
                    "event_id": "fb-turn2",
                    "say": "Also support CSV",
                    "previous": {"requirements_output": "iter/001/req-01.json"},
                }
            ],
            user_events=[{"id": "fb-turn2", "kind": "brief_feedback", "contract_token": "r1:turn1"}],
        )
        # Turn 1's unarchived handoff is not eligible on Turn 2.
        self.assertFalse(overlap.eligible(state, [q_turn1])["eligible"])

        state["requirements_history"] = [turn1_handoff]
        state["requirements_handoff"] = {
            "output": "iter/002/req-01.json",
            "report": self.report(open_questions=[q_turn2]),
        }
        self.assertTrue(overlap.eligible(state, [q_turn2])["eligible"])

        state["goal_contract"] = {"revision": 2, "hash": "turn2", "body": {}}
        self.assertFalse(overlap.eligible(state, [q_turn2])["eligible"])


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
    """The named overlap criteria as fast in-process proofs of the controller wiring.

    Each case drives the real ``autocode.chat_checkpoint`` conversation — the existing
    controller, importable unchanged on the original base — with the launch path injected
    (``run_role``, request preparation, the commit and the accounting are recorded fakes) and a
    real stdin pipe, so the reading-time overlap is observed deterministically: dispatch timing,
    exact full-call counts, keep/discard against the literal typed answers, cap admission, owned
    cancellation while stdin is unanswered, resume disposal and the background call's saved route
    choices. On the original base every case fails at its missing-overlap behavioral assertion,
    never at an import; these IDs prove the behavior, and the separately named CLI receipts in
    this module remain the mandatory integration evidence.
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

    def joint_workspace(self):
        """A coordination-authenticated joint workspace, as the AC14 default flow pauses within."""
        import autocode_coordination_store as coordination

        anchor = str(self.root / "intake/.autocode/runs/fix")
        environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "registry")})
        environment.__enter__()
        self.addCleanup(environment.__exit__, None, None, None)
        workspace = coordination.root(anchor) / "joint-1"
        run_dir = workspace / ".autocode/runs/joint-fast"
        run_dir.mkdir(parents=True, exist_ok=True)
        coordination.save(
            {
                "version": 1,
                "anchor": anchor,
                "members": [],
                "joint": {"workspace": str(workspace), "run_dir": str(run_dir), "started": False},
                "pending": {"operation": "joint_plan_start"},
            }
        )
        self.workspace, self.run_dir = workspace, run_dir

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

    def paused(
        self, *, questions, spec_first=False, complete=False, limits=None, answers=None, brief="small", joint=False
    ):
        """A live-chat run paused on the handoff's own blocking questions, exactly as the flow pauses it."""
        import autocode_resolver_human as resolver_human

        if joint:
            self.joint_workspace()
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

    def watch_dispatch(self, stack, dispatch, started):
        """Observe the real start and fail promptly if no provider dispatch can follow."""
        import autocode_requirements_overlap as overlap

        background = []
        original_start = overlap.Controller.start

        def start(controller, *args, **kwargs):
            handle = original_start(controller, *args, **kwargs)
            if handle is not None:
                # Check before chat_checkpoint receives the handle: otherwise its
                # cleanup would try to join a thread that was never started.
                self.assertIsNotNone(handle.thread.ident, "the background worker was never started")
                background.append(handle)
                if not handle.thread.is_alive() and not started.is_set():
                    self.fail(f"the background worker exited before provider dispatch: {handle.error!r}")
            return handle

        stack.enter_context(patch.object(overlap.Controller, "start", start))

        def wait_for_dispatch():
            self.assertTrue(background, "no background worker was created before waiting for provider dispatch")
            deadline = time.monotonic() + 120  # hang watchdog only for a live worker
            while not started.is_set():
                # Dispatch may start and the worker may exit between the event
                # check above and this liveness observation.
                if background and not background[0].thread.is_alive() and not started.is_set():
                    self.fail(f"the background worker exited before provider dispatch: {background[0].error!r}")
                if time.monotonic() >= deadline:
                    self.fail("the live background worker never reached provider dispatch")
                started.wait(0.05)

        return wait_for_dispatch

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

        helper = getattr(runner, "overlap_input", None)
        read = helper.read if helper is not None else None

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
            if helper is not None and hasattr(dispatch, "started"):
                wait_for_dispatch = self.watch_dispatch(stack, dispatch, dispatch.started)

                def observed_input(prompt, directory=None, waiting=False, **kwargs):
                    nonlocal lookups
                    lookups = 0
                    if waiting:
                        wait_for_dispatch()
                    return read(prompt, directory, waiting, **kwargs)

                stack.enter_context(patch.object(helper, "read", observed_input))
            result = runner.chat_checkpoint(state, self.run_dir)
        return SimpleNamespace(result=result, dispatch=dispatch, commit=commit, accounted=accounted, saved=saved)

    def chat_unanswered(self, state, dispatch):
        """Run the chat conversation in a worker thread whose stdin is never answered."""
        import autocode as runner

        commit = []
        accounted, saved, account_stage, save_record = self._wiring()

        def commit_stage_result(current, stage, value, record, workspace, run_dir):
            commit.append((stage, value, record))
            current.setdefault("stages", []).append(record)
            current["requirements_handoff"] = {"report": value}

        read_fd, write_fd = os.pipe()
        stdin = os.fdopen(read_fd, "r")
        self.addCleanup(stdin.close)
        holder = {"result": None, "error": None, "commit": commit, "accounted": accounted, "saved": saved}

        def worker():
            try:
                with contextlib.ExitStack() as stack:
                    for context in (
                        patch.object(runner, "run_role", dispatch),
                        patch.object(runner, "commit_stage_result", commit_stage_result),
                        patch.object(runner, "account_stage", account_stage),
                        patch.object(runner, "save_record", save_record),
                        patch.object(runner.autopilot, "prepare_request", self.prepare_request),
                        patch.object(runner.launch_inputs, "guard", lambda *args, **kwargs: None),
                        patch("sys.stdin", stdin),
                    ):
                        stack.enter_context(context)
                    holder["result"] = runner.chat_checkpoint(state, self.run_dir)
            except BaseException as error:  # surfaced by the test's own assertions
                holder["error"] = error

        thread = threading.Thread(target=worker, name="fast-overlap-chat", daemon=True)
        thread.start()

        def close_input():
            with contextlib.suppress(OSError):
                os.close(write_fd)  # never leave the conversation blocked on stdin
            thread.join(timeout=30)

        self.addCleanup(close_input)
        return holder

    def saved_answers(self, state):
        return {key: row["text"] for key, row in (state.get("answers") or {}).items()}

    def speculative(self, state):
        return [row for row in state.get("stages", []) if row.get("speculative")]

    def test_ac1_overlap_keeps_background_report(self):
        question = self.question(options=(self.DEFAULT, self.DIFFERENT))
        state = self.paused(questions=[question])
        outcome = self.chat(state, self.DEFAULT + "\n")
        self.assertEqual(1, len(outcome.dispatch.calls), "exactly one background full Requirements call runs")
        call = outcome.dispatch.calls[0]
        self.assertEqual(
            {question["id"]: self.DEFAULT},
            {key: row["text"] for key, row in call["state"]["answers"].items()},
            "the background call assumed the offered defaults",
        )
        self.assertEqual(
            [],
            [row for row in call["state"].get("user_events") or [] if row.get("question_id") == question["id"]],
            "the dispatch precedes the person's answer",
        )
        self.assertEqual(self.MODEL, call["model"], "the background call uses the saved stage model")
        self.assertEqual("low", call["state"]["settings"]["roles"]["requirements"]["reasoning_effort"])
        self.assertEqual({"stage_timeout_seconds": 180}, state["settings"]["limits"], "no cap or model choice changed")
        self.assertEqual(1, len(outcome.commit), "matching answers keep the report; no second Requirements call")
        self.assertEqual(outcome.dispatch.report, outcome.commit[0][1])
        self.assertEqual("kept", (state.get("requirements_overlap") or {}).get("status"))
        self.assertEqual(1, len(self.speculative(state)))
        self.assertEqual("kept", self.speculative(state)[0]["outcome"])
        for options, default, typed, expected_saved, keeps in (
            ((self.DEFAULT, self.DIFFERENT), self.DEFAULT, self.DEFAULT, self.DEFAULT, True),
            ((self.DEFAULT, self.DIFFERENT), self.DEFAULT, self.DEFAULT.lower(), self.DEFAULT, True),
            ((self.DEFAULT, self.DIFFERENT), self.DEFAULT, " " + self.DEFAULT + " ", self.DEFAULT, True),
            ((self.DEFAULT, self.DIFFERENT), self.DEFAULT, f"`{self.DEFAULT}`", self.DEFAULT, True),
            ((self.DEFAULT, self.DIFFERENT), self.DEFAULT.lower(), self.DEFAULT, self.DEFAULT, True),
            ((self.DEFAULT, self.DIFFERENT), self.DEFAULT.lower(), "/default", self.DEFAULT.lower(), True),
            ((self.DEFAULT, self.DIFFERENT), self.DEFAULT, "/default", self.DEFAULT, True),
            ((self.DEFAULT, self.DIFFERENT), self.DEFAULT, self.DIFFERENT.lower(), self.DIFFERENT, False),
            ((), self.DEFAULT, self.DEFAULT.lower(), self.DEFAULT.lower(), False),
        ):
            with self.subTest(options=options, default=default, offered_choice=typed):
                self.setUp()
                question = self.question(default=default, options=options)
                state = self.paused(questions=[question])
                outcome = self.chat(state, typed + "\n")
                self.assertEqual(1, len(outcome.dispatch.calls), "the background call dispatched before the answer")
                if keeps:
                    self.assertEqual(1, len(outcome.commit), "canonicalized default choice keeps the background report")
                    self.assertEqual("kept", state["requirements_overlap"]["status"])
                else:
                    self.assertEqual([], outcome.commit, "a different choice or case-distinct free text discards it")
                    self.assertEqual("discarded", state["requirements_overlap"]["status"])
                    self.assertEqual(
                        expected_saved,
                        self.saved_answers(state)[question["id"]],
                        "the accepted answer keeps its canonical value",
                    )
                    self.assertEqual(
                        "requirements_gather", state["next_stage"], "the ordinary pass runs after the answer"
                    )

        self.assert_input_boundaries()
        state = self.paused(questions=[self.question()])
        outcome = self.chat(state, self.DEFAULT + "\n", commit_error=ValueError("report refused"))
        self.assertEqual("discarded", state["requirements_overlap"]["status"])
        self.assertEqual(1, len(outcome.accounted), "a refused adoption charges the parent only once")
        self.assertEqual(1, len(self.speculative(state)), "the refused attempt is retained once")
        self.assertEqual("discarded", self.speculative(state)[0]["outcome"])
        self.assertEqual("requirements_gather", state["next_stage"])
        self.assert_dispatch_watch_failures()

    def assert_dispatch_watch_failures(self):
        import autocode_requirements_overlap as overlap

        with patch.object(overlap.Controller, "start", return_value=None):
            with contextlib.ExitStack() as stack:
                wait_for_dispatch = self.watch_dispatch(stack, None, threading.Event())
                self.assertIsNone(overlap.Controller.start(object()))
                with self.assertRaisesRegex(AssertionError, "no background worker was created"):
                    wait_for_dispatch()

        original_start = threading.Thread.start

        def skip_background_start(thread):
            if thread.name != "requirements-overlap":
                return original_start(thread)

        state = self.paused(questions=[self.question()])
        with patch.object(threading.Thread, "start", skip_background_start):
            with self.assertRaisesRegex(AssertionError, "background worker was never started"):
                self.chat(state, self.DEFAULT + "\n")

        def exit_before_provider(background):
            background.error = RuntimeError("exited before fake-provider dispatch")
            background.outcome = "failed"

        state = self.paused(questions=[self.question()])
        with patch.object(overlap.RequirementsOverlapPass, "_run", exit_before_provider):
            with self.assertRaisesRegex(AssertionError, "background worker exited before provider dispatch"):
                self.chat(state, self.DEFAULT + "\n")

    def test_dispatch_watch_accepts_dispatch_at_worker_exit(self):
        """An exit observed after dispatch starts must not look like pre-dispatch failure."""
        import autocode_requirements_overlap as overlap

        started = threading.Event()
        observations = []

        def alive():
            observations.append(True)
            if len(observations) == 1:
                return True  # the start wrapper sees the live worker
            started.set()  # dispatch starts between the wait's event and exit checks
            return False

        thread = SimpleNamespace(ident=1, is_alive=alive)
        handle = SimpleNamespace(thread=thread, error=None)
        with patch.object(overlap.Controller, "start", return_value=handle):
            with contextlib.ExitStack() as stack:
                wait_for_dispatch = self.watch_dispatch(stack, None, started)
                self.assertIs(overlap.Controller.start(object()), handle)
                wait_for_dispatch()
        self.assertEqual(2, len(observations), "the waiter observed dispatch during worker exit")
        self.assertTrue(started.is_set())

    def test_ac2_differing_answer_runs_ordinary_pass(self):
        question = self.question()
        state = self.paused(questions=[question])
        outcome = self.chat(state, self.DIFFERENT + "\n")
        self.assertEqual(1, len(outcome.dispatch.calls), "the background call ran while the person read")
        self.assertEqual(self.MODEL, outcome.dispatch.calls[0]["model"], "the background call used the saved route")
        self.assertEqual([], outcome.commit, "the background report is never adopted")
        self.assertEqual("discarded", (state.get("requirements_overlap") or {}).get("status"))
        self.assertEqual({question["id"]: self.DIFFERENT}, self.saved_answers(state))
        self.assertEqual("requirements_gather", state["next_stage"], "the ordinary pass runs with the real answers")
        self.assertEqual(1, len(self.speculative(state)))
        self.assertEqual("discarded", self.speculative(state)[0]["outcome"])
        self.assertEqual({"stage_timeout_seconds": 180}, state["settings"]["limits"], "no cap or model choice changed")

        # A fast differing answer can arrive before the background thread is
        # scheduled. Cancellation in that window must not launch a provider or
        # claim an unknown process needs cleanup.
        class DeferredThread:
            def __init__(self, *, target, **kwargs):
                self.target = target

            def start(self):
                pass

            def join(self, timeout=None):
                self.target()

            def is_alive(self):
                return False

        state = self.paused(questions=[self.question()])
        dispatched = mock.Mock(side_effect=AssertionError("cancelled before dispatch"))
        # A plain function has no fake-provider started event: the answer is
        # intentionally available before this background task is scheduled.
        launch = lambda **kwargs: dispatched(**kwargs)
        with patch("threading.Thread", DeferredThread):
            outcome = self.chat(state, self.DIFFERENT + "\n", dispatch=launch)
        dispatched.assert_not_called()
        self.assertEqual([], outcome.accounted)
        self.assertEqual("discarded", state["requirements_overlap"]["status"])
        self.assertNotIn("uncertainty", state["requirements_overlap"])
        self.assertEqual("requirements_gather", state["next_stage"])

    def test_ac3_no_chat_keeps_today_behavior(self):
        import autocode as runner
        import autocode_run_actions as actions

        state = self.paused(questions=[self.question()])
        with (
            patch.object(runner, "chat_checkpoint") as chat,
            patch.object(runner, "run_role") as launch,
            patch.object(actions.skip_requirements, "settle", return_value=False),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = actions.admit(
                runner,
                SimpleNamespace(chat=False, json=True),
                None,
                state,
                self.run_dir / "state.json",
                self.run_dir,
                self.workspace,
                False,
            )
        self.assertEqual(2, result)
        chat.assert_not_called()
        launch.assert_not_called()
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.assertEqual({}, state["answers"])
        self.assertFalse(state.get("requirements_overlap"))

    def test_ac5_bound_answers_refuse_mismatch(self):
        question = self.question()
        state = self.paused(questions=[question])
        state["answers"] = {question["id"]: {"text": self.DEFAULT}}
        value = self.report(questions=[], complete=True)
        value["source_refs"] = ["task"]
        value["assumed_answers_digest"] = util.digest([(question["id"], self.DIFFERENT)])
        with self.assertRaisesRegex(ValueError, "answers"):
            handoff.accept(
                copy.deepcopy(state), value, {"output": None}, check_code_refs=mock.Mock(), queue=mock.Mock()
            )
        value["assumed_answers_digest"] = util.digest([(question["id"], self.DEFAULT)])
        handoff.accept(state, value, {"output": None}, check_code_refs=mock.Mock(), queue=mock.Mock())
        self.assertEqual(
            value["assumed_answers_digest"], state["requirements_handoff"]["report"]["assumed_answers_digest"]
        )

    def test_ac4_small_brief_overlap(self):
        for brief in ("small", "medium"):
            with self.subTest(brief_size=brief):
                self.setUp()
                question = self.question()
                state = self.paused(questions=[question], brief=brief)
                outcome = self.chat(state, self.DEFAULT + "\n")
                self.assertEqual(brief, (state.get("workflow") or {}).get("brief_size"))
                self.assertEqual(1, len(outcome.dispatch.calls), "small and medium briefs overlap alike")
                self.assertEqual(1, len(outcome.commit))
                self.assertEqual("kept", (state.get("requirements_overlap") or {}).get("status"))

    def test_ac6_cap_skips_background_call(self):
        question = self.question()
        for limits, why in (
            ({"max_cost_usd": 1000}, "a finite cost cap cannot authorize a speculative call"),
            (
                {"max_cost_usd": 0, "max_seconds": 120, "stage_timeout_seconds": 180},
                "the stage deadline exceeds the active time left",
            ),
            ({"max_cost_usd": 0, "max_seconds": 3600}, "no enforced stage deadline bounds the call"),
        ):
            with self.subTest(cap=why):
                state = self.paused(questions=[question], limits=limits)
                if "max_seconds" in limits:
                    state["active_seconds"] = 0
                outcome = self.chat(state, self.DEFAULT + "\n")
                self.assertEqual([], outcome.dispatch.calls, why)
                self.assertEqual([], outcome.commit)
                self.assertEqual("skipped", (state.get("requirements_overlap") or {}).get("status"), why)
        state = self.paused(
            questions=[question], limits={"max_cost_usd": 0, "max_seconds": 3600, "stage_timeout_seconds": 180}
        )
        state["active_seconds"] = 0
        outcome = self.chat(state, self.DEFAULT + "\n")
        self.assertEqual(1, len(outcome.dispatch.calls), "an eligible bounded call with headroom still dispatches")
        self.assertEqual("kept", (state.get("requirements_overlap") or {}).get("status"))

        # A finite cap is not headroom unless the recorded usage is a valid measurement.
        limits = {"max_cost_usd": 0, "max_seconds": 3600, "stage_timeout_seconds": 180}
        missing = object()
        for usage in (missing, None, "unknown", float("nan"), float("inf"), -float("inf"), -1, True, False):
            with self.subTest(active_seconds=usage):
                state = self.paused(questions=[question], limits=limits)
                if usage is missing:
                    state.pop("active_seconds", None)
                    self.assertNotIn("active_seconds", state)
                else:
                    state["active_seconds"] = usage
                outcome = self.chat(state, self.DEFAULT + "\n")
                self.assertEqual(
                    0, len(outcome.dispatch.calls), "unknown or invalid usage cannot admit a background call"
                )
                self.assertEqual([], outcome.commit)
                self.assertEqual("skipped", state["requirements_overlap"]["status"])
                self.assertTrue(state["requirements_overlap"]["reason"], "the denial has a recorded reason")
                self.assertEqual(limits, state["settings"]["limits"])

        import autocode_requirements_overlap as overlap

        boundary = overlap.TERMINATION_ALLOWANCE_SECONDS + 180
        for remaining, expected in ((boundary, 1), (boundary - 1, 0)):
            with self.subTest(remaining_active_seconds=remaining):
                state = self.paused(questions=[question], limits={"max_seconds": 3600, "stage_timeout_seconds": 180})
                state["active_seconds"] = 3600 - remaining
                outcome = self.chat(state, self.DEFAULT + "\n")
                self.assertEqual(expected, len(outcome.dispatch.calls), "the exact stage-plus-termination boundary")
                self.assertEqual(3600, state["settings"]["limits"]["max_seconds"])

        for limits in ({}, {"max_seconds": 0}, {"max_seconds": 0, "stage_timeout_seconds": 0}):
            with self.subTest(unbounded=limits):
                state = self.paused(questions=[question], limits=limits)
                outcome = self.chat(state, self.DEFAULT + "\n")
                self.assertEqual([], outcome.dispatch.calls, "an unlimited active allowance is not a stage deadline")
                self.assertEqual("skipped", state["requirements_overlap"]["status"])
                self.assertEqual(limits, state["settings"]["limits"])
        limits = {"max_seconds": 250, "stage_timeout_seconds": 180}
        for authorized, calls in ((True, 0), (False, 1)):
            with self.subTest(authorized_retry=authorized):
                state = self.paused(questions=[question], limits=limits)
                state["active_seconds"] = 0
                state["job_failure"] = {
                    "stage": "requirements_gather",
                    "retry_stage_timeout_seconds": 360,
                    "job_retry_token": "retry-1",
                }
                state["job_retry_authorization"] = {
                    "stage": "requirements_gather",
                    "consumed": False,
                    "token": "retry-1" if authorized else "stale-retry",
                }
                outcome = self.chat(state, self.DEFAULT + "\n")
                self.assertEqual(calls, len(outcome.dispatch.calls), "admission uses only the actual launch deadline")
                self.assertEqual("skipped" if authorized else "kept", state["requirements_overlap"]["status"])
                self.assertEqual(limits, state["settings"]["limits"])

    def test_ac8_stop_cancels_background_pass(self):
        import autocode as runner
        import autocode_interventions as inbox
        import autocode_process as processes

        for kind in ("stop", "pause"):
            with self.subTest(intervention=kind):
                self.setUp()
                state = self.paused(questions=[self.question()])
                dispatch = _FakeDispatch(self)
                started, stopped = threading.Event(), threading.Event()
                identity = {"pid": 43210, "birth_identity": 12345.0}

                def held(**kwargs):
                    dispatch.calls.append(kwargs)
                    kwargs["state"]["active_stage"] = {**dispatch.record, "supervision": {"provider": identity}}
                    kwargs["persist"](self.run_dir / "state.json", kwargs["state"])
                    started.set()
                    if not stopped.wait(120):
                        raise AssertionError("cancellation did not reach the owned provider")
                    raise ValueError("provider cancelled before reporting")

                def stop(group, sig):
                    self.assertEqual(identity["pid"], group)
                    stopped.set()

                with contextlib.ExitStack() as stack:
                    # Original source has no overlap input helper. Its ordinary input
                    # returns /pause and fails the missing-dispatch assertion below.
                    helper = getattr(runner, "overlap_input", None)
                    if helper is not None:
                        original_read = helper.read
                        wait_for_dispatch = self.watch_dispatch(stack, held, started)

                        def read(*args, **kwargs):
                            wait_for_dispatch()
                            inbox.submit(self.workspace, self.run_dir, request_id=f"fast-{kind}", kind=kind, text="")
                            return original_read(*args, **kwargs)

                        stack.enter_context(patch.object(helper, "read", read))
                    stack.enter_context(patch("builtins.input", return_value="/pause"))
                    stack.enter_context(
                        patch.object(processes, "live_processes", lambda rows: [] if stopped.is_set() else rows)
                    )
                    stack.enter_context(patch("os.getpgid", return_value=identity["pid"]))
                    stack.enter_context(patch("os.killpg", stop))
                    stack.enter_context(patch("os.kill", stop))
                    outcome = self.chat(state, "", dispatch=held)
                self.assertEqual(1, len(dispatch.calls), "one owned call starts while the person reads")
                self.assertEqual("discarded", (state.get("requirements_overlap") or {}).get("status"))
                self.assertTrue(stopped.is_set(), "the verified provider was stopped")
                self.assertEqual([], outcome.commit)
                self.assertEqual(1, len(outcome.accounted))
                self.assertEqual(1, len(self.speculative(state)))
                self.assertEqual(dispatch.record["metrics"], self.speculative(state)[0]["metrics"])
                self.assertIn(kind, [row.get("kind") for row in inbox.pending(self.run_dir)])
        self.assertEqual(1, len(dispatch.calls), "the owned call was dispatched before cancellation")
        self.assert_stop_boundaries()

    def test_ac9_resume_discards_unfinished(self):
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

    def test_ac12_defaultless_question_keeps_original_cli_flow(self):
        for spec_first in (False, True):
            with self.subTest(spec_first=spec_first):
                self.setUp()
                question = self.question(default="")
                state = self.paused(questions=[question], spec_first=spec_first, complete=True, joint=not spec_first)
                outcome = self.chat(state, self.DIFFERENT + "\n")
                self.assertEqual([], outcome.dispatch.calls)
                self.assertEqual([], outcome.commit)
                self.assertEqual({question["id"]: self.DIFFERENT}, self.saved_answers(state))
                self.assertFalse(state.get("requirements_overlap"))

    def test_ac15_review_confirmation_no_background(self):
        state = self.paused(questions=[], complete=True, spec_first=True)
        question = state["pending_questions"][0]
        outcome = self.chat(state, "plan\n")
        self.assertEqual([], outcome.dispatch.calls)
        self.assertEqual([], outcome.commit)
        self.assertEqual("draft_plan", state["next_stage"])
        self.assertEqual({question["id"]: "plan"}, self.saved_answers(state))
        self.assertFalse(state.get("requirements_overlap"))

    def test_ac16_saved_requirements_route_and_budgets_unchanged(self):
        import autocode as runner

        # Observe the ordinary dispatcher at its provider launch boundary, on both
        # base and candidate. This guard is not just a comparison of saved settings.
        state = {
            "version": 3,
            "iteration": 1,
            "next_stage": "requirements_gather",
            "status": "RUNNING",
            "workspace": str(self.workspace),
            "run_dir": str(self.run_dir),
            "settings": {
                "joint_planning": True,
                "roles": {"requirements": {"engine": "codex", "model": self.MODEL, "reasoning_effort": "low"}},
                "limits": {"max_cost_usd": 0, "max_seconds": 3600, "stage_timeout_seconds": 180},
            },
        }
        configured = copy.deepcopy(state["settings"])
        observed = []

        class ProviderObserved(Exception):
            pass

        def launch(command, **kwargs):
            observed.append((command, kwargs, copy.deepcopy(state["active_stage"])))
            raise ProviderObserved()

        schema = self.run_dir / "fixture-schema.json"
        util.atomic_json(schema, {"type": "object"})
        with (
            patch.object(runner.processes, "preflight"),
            patch.object(runner.provider_launch, "prepare", return_value=(["fixture-provider"], {}, {}, {})) as prepare,
            patch.object(runner.readonly_events, "launch_excludes", return_value=None),
            patch.object(runner.readonly_events, "prepare_opencode_snapshots"),
            patch.object(runner.task_preflight, "guard"),
            patch.object(runner.role_launch, "admit_launch"),
            patch.object(runner.supervision, "launch", launch),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(ProviderObserved):
                runner.run_role(
                    role="requirements",
                    prompt="Gather requirements",
                    sandbox="read-only",
                    workspace=self.workspace,
                    run_dir=self.run_dir,
                    state=state,
                    schema=schema,
                    model=self.MODEL,
                    allow_write=False,
                    dry_run=False,
                )
        self.assertEqual(1, len(observed))
        self.assertEqual(self.MODEL, prepare.call_args.kwargs["model"])
        self.assertEqual("low", prepare.call_args.kwargs["effort"])
        command, options, record = observed[0]
        self.assertEqual(["fixture-provider"], command)
        self.assertEqual(180, options["timeout"])
        self.assertEqual("requirements_gather", record["stage"])
        self.assertEqual("codex", record["engine"])
        self.assertEqual(self.MODEL, record["launch_route"]["model"])
        self.assertEqual("low", record["reasoning_effort"])
        self.assertEqual(configured["roles"], state["settings"]["roles"])
        self.assertEqual(configured["limits"], state["settings"]["limits"])

    def test_ac13_spec_first_open_questions_overlap(self):
        question = self.question()
        state = self.paused(questions=[question], complete=True, spec_first=True)
        outcome = self.chat(state, self.DEFAULT + "\n")
        self.assertEqual(
            1, len(outcome.dispatch.calls), "one additional full call for the pause, zero after matching answers"
        )
        self.assertEqual(1, len(outcome.commit))
        self.assertEqual("kept", (state.get("requirements_overlap") or {}).get("status"))
        self.assertEqual({question["id"]: self.DEFAULT}, self.saved_answers(state))

    def test_ac14_business_questions_start_background(self):
        question = self.question()
        for typed, keeps in ((self.DEFAULT, True), (self.DIFFERENT, False)):
            with self.subTest(answer=typed):
                self.setUp()
                state = self.paused(questions=[question], complete=True, spec_first=False, joint=True)
                outcome = self.chat(state, typed + "\n")
                self.assertEqual(1, len(outcome.dispatch.calls), "the default flow overlaps its business questions too")
                if keeps:
                    self.assertEqual(1, len(outcome.commit), "matching answers adopt the bound report")
                    self.assertEqual("kept", state["requirements_overlap"]["status"])
                else:
                    self.assertEqual([], outcome.commit, "a differing answer never adopts it")
                    self.assertEqual("discarded", state["requirements_overlap"]["status"])
                    self.assertEqual({question["id"]: typed}, self.saved_answers(state))
                    self.assertEqual(
                        "requirements_gather",
                        state["next_stage"],
                        "a differing answer explicitly refreshes Requirements before planning",
                    )

    def test_ac18_two_defaults_require_every_answer_to_match(self):
        first = self.question()
        second = self.question("OVERLAP_FORMAT", "Plain text")
        for typed_second, keeps in ((second["proposed_default"], True), ("JSON", False)):
            with self.subTest(second_answer=typed_second):
                self.setUp()
                state = self.paused(questions=[first, second])
                outcome = self.chat(state, first["proposed_default"] + "\n" + typed_second + "\n")
                self.assertEqual(1, len(outcome.dispatch.calls), "one background full pass assumed both defaults")
                self.assertEqual(
                    {first["id"]: first["proposed_default"], second["id"]: second["proposed_default"]},
                    {key: row["text"] for key, row in outcome.dispatch.calls[0]["state"]["answers"].items()},
                )
                self.assertEqual(
                    [],
                    [
                        row
                        for row in outcome.dispatch.calls[0]["state"].get("user_events") or []
                        if row.get("question_id") in {first["id"], second["id"]}
                    ],
                    "dispatch precedes both answers",
                )
                if keeps:
                    self.assertEqual(1, len(outcome.commit), "both matching defaults keep the one background pass")
                    self.assertEqual("kept", state["requirements_overlap"]["status"])
                else:
                    self.assertEqual([], outcome.commit, "one differing answer discards the report")
                    self.assertEqual("discarded", state["requirements_overlap"]["status"])
                    self.assertEqual(
                        {first["id"]: first["proposed_default"], second["id"]: typed_second},
                        self.saved_answers(state),
                        "the ordinary pass receives both actual answers",
                    )
                    self.assertEqual("requirements_gather", state["next_stage"])

    def test_ac7_a_settled_background_pass_shows_one_speculative_usage_row(self):
        """AC7 through the real controller: the attempt the chat adopted is the row the status view
        names speculative, carrying the outcome and reason its own record settled under, while the
        full pass that published the questions keeps an ordinary usage row."""
        import autocode_run_view as run_view

        question = self.question()
        state = self.paused(questions=[question])
        state["stages"].append(
            {
                "stage": "requirements_gather",
                "role": "requirements",
                "engine": "codex",
                "iteration": 1,
                "attempt": 1,
                "output": str(self.run_dir / "requirements-01.json"),
                "metrics": {"provider_tokens": {"input_tokens": 400, "output_tokens": 70}},
            }
        )
        self.chat(state, self.DEFAULT + "\n")
        rows = run_view.view(state)["usage"]["accounting"]["attempts"]
        self.assertEqual(2, len(rows), [row.get("output") for row in rows])
        marked = [row for row in rows if row.get("speculative")]
        self.assertEqual(1, len(marked), "only the background pass is speculative")
        self.assertEqual(str(self.run_dir / "requirements-02.json"), marked[0]["output"])
        self.assertEqual("kept", marked[0]["outcome"])
        self.assertEqual(state["requirements_overlap"]["reason"], marked[0]["reason"])
        self.assertEqual(400, rows[0]["tokens"]["input_tokens"])
        self.assertFalse({"speculative", "outcome", "reason"} & set(rows[0]), "the ordinary pass stays unmarked")

    def test_settled_attempts_carry_their_final_reason(self):
        """The durable attempt/outcome/reason binding: every settled speculative attempt carries
        its overlap entry's final reason — kept, discarded on a differing answer, a refused
        adoption, a kept decision whose call produced no report, and cancellation — and a later
        overlap never overwrites the earlier entry's settled status and reason."""
        import autocode as runner
        import autocode_requirements_overlap as overlap

        question = self.question()
        state = self.paused(questions=[question])
        self.chat(state, self.DEFAULT + "\n")
        entry = state["requirements_overlap"]
        self.assertEqual("kept", entry["status"])
        self.assertEqual(1, len(self.speculative(state)))
        row = self.speculative(state)[0]
        self.assertEqual("kept", row["outcome"])
        self.assertTrue(row["reason"])
        self.assertEqual(entry["reason"], row["reason"], "the kept attempt carries its entry's final reason")

        state = self.paused(questions=[question])
        self.chat(state, self.DIFFERENT + "\n")
        entry = state["requirements_overlap"]
        row = self.speculative(state)[0]
        self.assertEqual("discarded", entry["status"])
        self.assertEqual("discarded", row["outcome"])
        self.assertEqual(entry["reason"], row["reason"])
        self.assertIn("differently", row["reason"], "the discard reason names the differing answer")

        state = self.paused(questions=[question])
        outcome = self.chat(state, self.DEFAULT + "\n", commit_error=ValueError("report refused"))
        entry = state["requirements_overlap"]
        row = self.speculative(state)[0]
        self.assertEqual("discarded", row["outcome"], "a refused adoption replaces the provisional kept annotation")
        self.assertIn("refused", row["reason"])
        self.assertEqual(entry["reason"], row["reason"])
        self.assertEqual(1, len(outcome.accounted), "re-annotating an accounted attempt does not charge again")

        accounted, saved, account_stage, save_record = self._wiring()
        dispatch = _FakeDispatch(self)
        persisted = {}

        def persist(path, value):
            persisted[str(path)] = value

        def failing(**kwargs):
            kwargs["state"]["active_stage"] = copy.deepcopy(dispatch.record)
            raise ValueError("the owned provider failed before a report")

        with (
            patch.object(runner, "run_role", failing),
            patch.object(runner, "account_stage", account_stage),
            patch.object(runner, "save_record", save_record),
            patch.object(runner.autopilot, "prepare_request", self.prepare_request),
            patch.object(runner.launch_inputs, "guard", lambda *args, **kwargs: None),
            patch.object(runner, "commit_stage_result", lambda *args, **kwargs: self.fail("no report to adopt")),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            controller = overlap.Controller.wired(runner)
            state = self.paused(questions=[question])
            background = controller.start(state, self.run_dir, persist)
            self.assertIsNotNone(background)
            background.wait()
            controller.settle(background, state, self.run_dir, persist, literal={question["id"]: self.DEFAULT})
            after_failure = copy.deepcopy(persisted[str(self.run_dir / "state.json")])
            state = self.paused(questions=[question])
            background = controller.start(state, self.run_dir, persist)
            self.assertIsNotNone(background)
            background.wait()
            controller.cancel(background, state, self.run_dir, persist)
        self.assertEqual("discarded", after_failure["requirements_overlap"]["status"])
        rows = [row for row in after_failure.get("stages", []) if row.get("speculative")]
        self.assertEqual(1, len(rows))
        self.assertEqual("discarded", rows[0]["outcome"])
        self.assertEqual(after_failure["requirements_overlap"]["reason"], rows[0]["reason"])
        self.assertEqual(
            "the background call produced no report; the ordinary pass runs with the real answers",
            rows[0]["reason"],
            "a kept decision whose call failed discards with the no-report reason",
        )
        self.assertEqual("discarded", state["requirements_overlap"]["status"])
        rows = [row for row in state.get("stages", []) if row.get("speculative")]
        self.assertEqual(1, len(rows))
        self.assertEqual("discarded", rows[0]["outcome"])
        self.assertEqual(state["requirements_overlap"]["reason"], rows[0]["reason"])
        self.assertEqual(
            "the chat stopped or paused before the answers were taken",
            state["requirements_overlap"]["reason"],
            "cancellation settles its own final reason",
        )
        self.assertEqual(2, len(accounted), "each attempt charged exactly once")

        # A later overlap preserves the earlier entry's settled status and reason in history.
        settled_entry = copy.deepcopy(state["requirements_overlap"])
        controller.record(state, {"at": 0, "status": "running", "reason": "a later pass runs now"})
        self.assertEqual(settled_entry, state["requirements_overlap_history"][-1])
        self.assertEqual("running", state["requirements_overlap"]["status"])

    def test_recovery_keeps_usage_uncertainty_explicit(self):
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

    def assert_stop_boundaries(self):
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

    def assert_input_boundaries(self):
        import autocode_requirements_overlap_input as overlap_input

        with patch("builtins.input", return_value="Command line") as plain:
            self.assertEqual("Command line", overlap_input.read("You: ", None, False))
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
                    self.assertEqual("first", overlap_input.read("", waiting=False))
                    self.assertEqual("雪 café", overlap_input.read("", waiting=True))
                    self.assertEqual("last", overlap_input.read("", waiting=False))
                    with self.assertRaises(EOFError):
                        overlap_input.read("", waiting=True)
        # A pending Stop wins even when stdin is ready and when a prior chunk
        # contained a second line. Changing streams cannot inherit that line.
        descriptor = os.open(os.devnull, os.O_RDONLY)
        self.addCleanup(os.close, descriptor)
        stream = SimpleNamespace(fileno=lambda: descriptor, encoding="utf-8")
        with patch("sys.stdin", stream):
            self.assertEqual(
                "first", overlap_input.read("", waiting=True, ready=lambda: True, chunk=lambda: "first\nsecond\n")
            )
            self.assertIs(
                overlap_input.INTERVENED,
                overlap_input.read(
                    "",
                    "run",
                    True,
                    pending=lambda run: True,
                    ready=lambda: True,
                    chunk=lambda: self.fail("Stop must be observed before reading"),
                ),
            )
            self.assertEqual(
                "second",
                overlap_input.read("", waiting=True, ready=lambda: True, chunk=lambda: self.fail("buffered answer")),
            )
        replacement = SimpleNamespace(fileno=lambda: descriptor, encoding="utf-8")
        with patch("sys.stdin", replacement):
            chunks = iter(("part", "ial", ""))
            self.assertEqual(
                "partial", overlap_input.read("", waiting=True, ready=lambda: True, chunk=lambda: next(chunks))
            )
            with self.assertRaises(EOFError):
                overlap_input.read("", waiting=True)
        with patch("sys.stdin", io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "no file descriptor"):
                overlap_input.read("", waiting=True)

        # A tty uses input()'s C readline unless the proxy keeps it on our reader.
        # Wait for the interrupted reader to hand off before feeding the ordinary prompt.
        import pty

        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        script = """import sys
sys.path.insert(0, "tools")
import autocode_requirements_overlap_input as reader
count = [0]
def pending(run):
    count[0] += 1
    if count[0] == 1:
        print("READY", flush=True)
    return count[0] == 2
assert reader.read("", waiting=True, pending=pending) is reader.INTERVENED
print("INPUT_READY", flush=True)
print("RESULT=" + reader.read("", waiting=False), flush=True)
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
            self.assertIn(b"READY", output)
            os.write(master, "雪 café\n".encode())
            while b"RESULT=" not in output:
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, "the tty child did not complete its input transition")
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
        self.assertIn(
            "RESULT=雪 café", output.decode(errors="replace"), "an interrupted character survives the next prompt"
        )


if __name__ == "__main__":
    unittest.main()
