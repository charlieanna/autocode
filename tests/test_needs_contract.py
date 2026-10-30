"""Every needs() kind names an action the public CLI accepts (#1010).

The status view's next_command is built from needs via autocode_pause_command.
A new kind without a row here fails the suite the same way test_run_state fails
when tools/ invents an undocumentated state key.
"""

from __future__ import annotations

import copy
import importlib
import shlex
import unittest
from pathlib import Path
from unittest import mock

import autocode_args
import autocode_configure
import autocode_pause_command as pause_command
import autocode_run_view as run_view
import autocode_subcommands as subcommands

# Every kind needs() can return. Add a fixture below when this set grows.
KNOWN_KINDS = frozenset(
    {
        "resume",
        "retry_job",
        "recover_source",
        "dependency",
        "review",
        "answer",
        "approve_plan",
        "planning_budget",
        "continue",
    }
)

# Minimal state that produces each kind. Values are enough for needs() and for
# pause_command to render a command the parser accepts (or None, when no recovery).
KIND_STATES = {
    "approve_plan": {
        "status": "AWAITING_GOAL_APPROVAL",
        "displayed_goal": "plan-token",
        "settings": {},
    },
    "continue": {
        "status": "AWAITING_GOAL_APPROVAL",
        "settings": {},
    },
    "answer": {
        "status": "WAITING_FOR_USER",
        "pending_questions": [
            {
                "id": "Q1",
                "question": "Which option?",
                "why": "clarify",
                "options": ["a", "b"],
                "proposed_default": "a",
            }
        ],
        "user_request": {"kind": "clarification"},
        "resolver_human_request": {
            "request_id": "req-1",
            "request_token": "resolver-token",
            "scope": "clarification",
        },
        "resolver": {"human_escalations": {"req-1": {"status": "pending", "identity": {"proposal": {"origin": {}}}}}},
        "settings": {},
    },
    "review": {
        "status": "WAITING_FOR_USER",
        "pending_questions": [
            {
                "id": "R1",
                "question": "Accept AC1?",
                "review_criteria": ["AC1"],
                "review_token": "review-token",
            }
        ],
        "settings": {},
    },
    "planning_budget": {
        "status": "PAUSED_PLANNING_BUDGET",
        "stop_reason": "planning review call limit reached",
        "settings": {},
    },
    "resume": {
        "status": "PAUSED_PROVIDER_TIMEOUT",
        "stop_reason": "idle",
        "settings": {},
    },
    "dependency": {
        "status": "WAITING_FOR_DEPENDENCY",
        "stop_reason": "waiting on producer",
        "dependency_wait": {"producer_run": "/tmp/producer", "producer_workspace": "/tmp/producer-ws"},
        "settings": {},
    },
    "retry_job": {
        "status": "PAUSED_JOB_FAILURE",
        "job_failure": {
            "reason": "stage failed",
            "stage": "answer_question",
            "attempt_id": "001/answer_question-01",
            "job_retry_token": "job-token",
            "archive": "/tmp/archive",
            "source_identity": {"head": "abc"},
            "write_diagnosis": {},
            "unrestored": [],
        },
        "settings": {},
    },
    "recover_source": {
        "status": "PAUSED_JOB_FAILURE",
        "job_failure": {
            "reason": "stage failed without source",
            "stage": "answer_question",
            "attempt_id": "001/answer_question-01",
            "job_retry_token": "job-token",
            "archive": "/tmp/archive",
            "source_identity": None,
            "write_diagnosis": {},
            "unrestored": [],
        },
        "settings": {},
    },
}

# Attribute on the parsed args that proves the matching action family was selected.
KIND_ACCEPTANCE = {
    "approve_plan": lambda args: args.approve_goal == "plan-token",
    "continue": lambda args: args.chat is False,  # --no-chat relaunch
    "answer": lambda args: args.answer == ["Q1=ANSWER"] and args.resolver_token == "resolver-token",
    "review": lambda args: args.approve_review == ["AC1"] and args.review_token == "review-token",
    "planning_budget": lambda args: args.resume_paused and args.planning_review_call_limit == 10,
    "resume": lambda args: args.resume_paused,
    "dependency": lambda args: args.run_dir == Path("/tmp/producer") and args.workspace == Path("/tmp/producer-ws"),
    "retry_job": lambda args: args.resume_paused and args.retry_failed_stage and args.job_retry_token == "job-token",
    "recover_source": lambda args: args.explain,
}


# Placeholders the view leaves for a person to fill (pause_command docstring); "10" parses for each,
# --max-cost-usd's float included. scenarios/harness/needs_command.py keeps the same set.
_PERSON_INT = frozenset(
    {
        "--planning-review-call-limit",
        "--no-progress-limit",
        "--max-seconds",
        "--max-idle-seconds",
        "--max-cost-usd",
        "--max-iterations",
        "--max-milestone-seconds",
        "--max-milestone-replans",
        "--grant-recovery",
    }
)


def _fill_person_placeholders(words: list[str]) -> list[str]:
    """Replace N after int flags so the parser can accept the displayed shape."""
    filled = list(words)
    for index, word in enumerate(filled[:-1]):
        if word in _PERSON_INT and filled[index + 1] == "N":
            filled[index + 1] = "10"
    return filled


def parse_next_command(text: str):
    """Parse a displayed next_command into args; returns (args, argv) or (None, None) when text is None.

    A command whose first word is a registered subcommand parses with that subcommand's own
    parser — the same dispatch `autocode` itself performs — so a subcommand-shaped recovery
    is held to the parser that will actually run it, not the main parser's permissive task slot.
    """
    if text is None:
        return None, None
    words = shlex.split(text)
    environ = {}
    while words and words[0] != "autocode":
        name, value = words.pop(0).split("=", 1)
        environ[name] = value
    if not words or words[0] != "autocode":
        raise AssertionError(f"next_command does not start with autocode: {text!r}")
    words.pop(0)
    words = _fill_person_placeholders(words)
    with mock.patch.dict("os.environ", environ):
        if words and words[0] in subcommands.SUBCOMMANDS:
            module = importlib.import_module(subcommands.SUBCOMMANDS[words[0]])
            return module.parser().parse_args(words[1:]), words
        args, _ = autocode_args.parse(None, words, autocode_configure.DEFAULT_ROLE_MODELS)
    return args, words


class NeedsContractTests(unittest.TestCase):
    def test_every_known_kind_has_a_fixture_and_acceptance_rule(self):
        self.assertEqual(KNOWN_KINDS, frozenset(KIND_STATES))
        self.assertEqual(KNOWN_KINDS, frozenset(KIND_ACCEPTANCE))

    def test_each_kind_from_state_matches_and_its_command_parses(self):
        for kind, state in KIND_STATES.items():
            with self.subTest(kind=kind):
                need = run_view.needs(copy.deepcopy(state))
                self.assertIsNotNone(need, f"needs() returned None for {kind}")
                self.assertEqual(kind, need["kind"])
                self.assertIn(need["kind"], KNOWN_KINDS)
                text = pause_command.command(need, run_dir=Path("/tmp/run"), workspace=Path("/tmp/ws"))
                args, _ = parse_next_command(text)
                self.assertTrue(KIND_ACCEPTANCE[kind](args), f"kind={kind} command={text!r} args={args!r}")

    def test_view_next_command_matches_pause_command_for_each_kind(self):
        for kind, state in KIND_STATES.items():
            with self.subTest(kind=kind):
                view = run_view.view({**copy.deepcopy(state), "workspace": "/tmp/ws", "run_dir": "/tmp/run"})
                self.assertEqual(kind, (view.get("needs") or {}).get("kind"))
                # continue / approve_plan without a displayed path still offers a relaunch
                self.assertIsNotNone(view.get("next_command"), view)
                args, _ = parse_next_command(view["next_command"])
                self.assertIsNotNone(args)

    def test_unknown_kind_from_needs_is_not_silently_accepted(self):
        # Guard: if needs() starts returning a kind not in KNOWN_KINDS, this test
        # fails until KIND_STATES / KIND_ACCEPTANCE gain a row.
        observed = {run_view.needs(copy.deepcopy(state))["kind"] for state in KIND_STATES.values()}
        self.assertTrue(observed <= KNOWN_KINDS)
        self.assertEqual(observed, KNOWN_KINDS)


class CostPauseCommandTests(unittest.TestCase):
    """#721: a cost pause names --resume-paused --max-cost-usd N, and the cap a person fills in parses."""

    def test_the_cost_cap_a_person_fills_in_parses(self):
        state = {
            "status": "PAUSED_COST_LIMIT",
            "stop_reason": "Spend so far $0.18 reached the $0.10 cost cap at stage boundary",
            "settings": {"limits": {"max_cost_usd": 0.1}},
            "workspace": "/tmp/ws",
            "run_dir": "/tmp/run",
        }
        view = run_view.view(state)
        self.assertEqual("resume", view["needs"]["kind"])
        self.assertTrue(
            view["next_command"].endswith("--resume-paused --max-cost-usd N --expected-recovery-token -"),
            view["next_command"],
        )
        self.assertEqual("max_cost_usd", view["needs"]["command_inputs"][0]["id"])  # #2044: a client can fill it
        args, words = parse_next_command(view["next_command"])
        self.assertEqual("10", words[words.index("--max-cost-usd") + 1])
        self.assertTrue(args.resume_paused)
        self.assertEqual(10.0, args.max_cost_usd)


class NeedsCommandAccepted(unittest.TestCase):
    """Shared helper the scenario driver uses: refuse a next_command the CLI rejects."""

    @staticmethod
    def assert_accepted(next_command: str | None) -> None:
        if next_command is None:
            return
        args, _ = parse_next_command(next_command)
        if args is None:
            raise AssertionError(f"next_command produced no args: {next_command!r}")


import ast
import os
import re
import tempfile

import autocode_classification_recovery as classification_recovery
import autocode_progress_view as progress_view
import autocode_recovery_limits as recovery_limits
import autocode_taskrun as taskrun

DOCS = Path(__file__).resolve().parents[1] / "docs" / "task-run.md"

# The functions that build a needs record: the view's own, and the helpers it delegates to.
# autocode_component_plan.recovery and the other helpers that extend a resume record never set `kind`.
NEED_BUILDERS = ((run_view, "needs"), (classification_recovery, "offer"))

# What answers a kind when the view names no `action`: the TaskRun method docs/task-run.md documents.
# recover_source is answered by inspection, not a call; continue by advancing.
TASKRUN_METHODS = {
    "approve_plan": ("approve_plan",),
    "answer": ("answer", "respond_operational", "retry_failed_stage"),
    "review": ("approve_review",),
    "planning_budget": ("feedback",),
    "recover_source": (),
    "retry_job": ("retry_job",),
    "resume": ("resume_paused", "abandon_stage", "retry_report", "accept_source_edit", "grant_recovery"),
    "dependency": ("bind_dependency", "receive_dependency"),
    "continue": ("advance",),
}

# Values an `action` leaves for a person: a file, a count, a milestone, a token, a model, a request.
ACTION_PLACEHOLDERS = {"FILE": "__FILE__", "N": "1", "M": "M1", "TOKEN": "tok", "MODEL": "m", "ID": "r1", "TEXT": "t"}

# Shapes of a kind the fixtures above do not cover, each a run a test or a live run captured:
# a Resolver blocker and an operational request (#675, where the view said `answer` and the CLI
# refused --answer), a completion-time human review, and the resumes that name an exact command.
_RESOLVER = {
    "status": "WAITING_FOR_USER",
    "user_request": {"kind": "blocker"},
    "pending_questions": [{"id": "q1", "question": "Approve the correction?"}],
    "resolver_human_request": {"request_id": "r1", "request_token": "t1", "scope": "blocker"},
    "resolver": {"human_escalations": {"r1": {"status": "pending"}}},
    "settings": {},
}
KIND_SHAPES = {
    "continue": [
        {
            "status": "PAUSED_BUDGET",
            "stop_reason": "quota restored; set the attempt aside",
            "active_stage": {"iteration": 1, "output": "/run/terra-01.json", "stage": "terra"},
            "settings": {},
        }
    ],
    "answer": [
        _RESOLVER,
        {
            **_RESOLVER,
            "resolver_human_request": {**_RESOLVER["resolver_human_request"], "scope": "operational_exhaustion"},
        },
    ],
    "review": [
        {
            "status": "WAITING_FOR_USER",
            "pending_questions": [],
            "displayed_review": "r5:abc@def:ghi",
            "user_request": {"kind": "human_review", "criteria": ["AC11"], "decision_needed": "Review AC11"},
            "settings": {},
        }
    ],
    "resume": [
        {
            # A cleanly finished attempt keeps the result its hold stands on; archiving it is not routine.
            "status": "PAUSED_VERIFICATION_UNCERTAIN",
            "stop_reason": "verification cleanup was not confirmed",
            "active_stage": {
                "iteration": 1,
                "output": "/run/review-change-01.json",
                "stage": "review_change",
                "exit_code": 0,
                "finished_at": "2026-10-30T00:00:00Z",
            },
            "settings": {},
        },
        {"status": "PAUSED_COMPONENT_PLAN", "stop_reason": "component plan", "task": "Build a CLI", "settings": {}},
        {
            "status": "PAUSED_REPEATED_FAILURE",
            "stop_reason": "report rejected",
            "settings": {"report_repair": {"max_attempts": 2}},
            "pending_report_repair": {
                "error": "Check is not supported by an exact executed Validator event",
                "original": {"stage": "sol"},
                "attempts": 2,
                "latest_rejected": {"iteration": 1, "output": "/run/sol_report_repair-02.json"},
            },
        },
    ],
}


def documented_kinds() -> set[str]:
    """The `kind` column of the needs table in docs/task-run.md."""
    lines = DOCS.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| `kind` | Waiting for |"))
    kinds = set()
    for line in lines[start + 2 :]:
        if not line.startswith("| `"):
            break
        kinds.add(line.split("`")[1])
    return kinds


def _literal_kinds(value: ast.expr) -> set[str]:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return {value.value}
    if isinstance(value, ast.IfExp):
        return _literal_kinds(value.body) | _literal_kinds(value.orelse)
    return set()


def emitted_kinds(module, function: str) -> set[str]:
    """Every `kind` a dict literal inside `function` of `module` can carry."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    found = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == function)
    kinds = set()
    for node in ast.walk(found):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "kind":
                    kinds |= _literal_kinds(value)
    return kinds


class NeedsContractAgainstDocsTests(unittest.TestCase):
    """The needs table in docs/task-run.md, the kinds the code can emit, and KNOWN_KINDS are one set;
    the view's own `action` strings parse; a kind without an action has a documented TaskRun method."""

    @classmethod
    def setUpClass(cls):
        cls.options = set(
            autocode_args.build_parser(None, autocode_configure.DEFAULT_ROLE_MODELS)._option_string_actions
        )
        cls.temporary = tempfile.TemporaryDirectory(prefix="needs-contract-")
        cls.run_dir = Path(cls.temporary.name) / "run"
        cls.run_dir.mkdir()
        cls.file = Path(cls.temporary.name) / "body.json"
        cls.file.write_text("{}")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_the_docs_table_the_code_and_this_test_name_the_same_kinds(self):
        emitted = set()
        for module, function in NEED_BUILDERS:
            found = emitted_kinds(module, function)
            self.assertTrue(found, f"{module.__name__}.{function} builds no needs record; is the scan wrong?")
            emitted |= found
        self.assertEqual(documented_kinds(), emitted, "docs/task-run.md's needs table and the view disagree")
        self.assertEqual(documented_kinds(), set(KNOWN_KINDS), "KNOWN_KINDS and the docs table disagree")
        self.assertEqual(documented_kinds(), set(TASKRUN_METHODS), "say what answers the new kind")
        for kind, methods in TASKRUN_METHODS.items():
            for method in methods:
                self.assertTrue(callable(getattr(taskrun.TaskRun, method, None)), f"{kind}: TaskRun.{method}")

    def test_every_shape_names_an_accepted_action_and_asks_for_it(self):
        shapes = {kind: [state] for kind, state in KIND_STATES.items()}
        for kind, extra in KIND_SHAPES.items():
            shapes[kind].extend(extra)
        for kind, states in shapes.items():
            for state in states:
                with self.subTest(kind=kind, status=state["status"]):
                    need = run_view.needs(copy.deepcopy(state))
                    self.assertEqual(kind, need["kind"], need)
                    actions = [need[key] for key in ("action", "feedback_action", "retry_action") if need.get(key)]
                    for action in actions:
                        self.assert_action_accepted(action)
                    if not actions:
                        self.assertTrue(
                            TASKRUN_METHODS[kind] or need.get("recovery_hint"),
                            f"{kind} names no action, no TaskRun method and no hint",
                        )
                    NeedsCommandAccepted.assert_accepted(
                        pause_command.command(need, run_dir=self.run_dir, workspace=self.run_dir.parent)
                    )
                    if kind != "continue":
                        progress = progress_view.progress(state, accepted=(), stage=None, needs=need)
                        self.assertTrue(progress["needs_you"], f"{kind}: needs_you is empty")

    def test_the_shared_response_commands_parse(self):
        for action in (recovery_limits.RESPONSE_COMMAND, recovery_limits.SCOPED_RETRY, classification_recovery.ACTION):
            with self.subTest(action=action):
                self.assert_action_accepted(action)

    def assert_action_accepted(self, action: str) -> None:
        """Every option in `action` is one the parser knows, and each command in it parses with its
        placeholders filled; `(reads ENV)` names the variable a `-` token reads."""
        for flag in re.findall(r"--[a-z][a-z-]*", action):
            self.assertIn(flag, self.options, f"{flag!r} in {action!r} is not a CLI option")
        environment = {name: "tok" for name in re.findall(r"\(reads ([A-Z_]+)\)", action)}
        bare = re.sub(r"\s*\(reads [A-Z_]+\)", "", action)
        for command in bare.split(" then "):
            argv = []
            for token in shlex.split(command):
                token = ACTION_PLACEHOLDERS.get(token, token).replace("__FILE__", str(self.file))
                argv.append(re.sub(r"ROLE=MODEL$", "terra=m", token))
            argv += ["--workspace", str(self.run_dir.parent), "--run-dir", str(self.run_dir)]
            with (
                mock.patch.dict(os.environ, environment),
                open(os.devnull) as stdin,
                mock.patch.object(autocode_args.sys, "stdin", stdin),
            ):
                try:
                    autocode_args.parse(None, argv, autocode_configure.DEFAULT_ROLE_MODELS)
                except SystemExit as error:
                    self.fail(
                        f"the CLI refuses {command!r} (exit {error.code}); the view must name an accepted command"
                    )


if __name__ == "__main__":
    unittest.main()
