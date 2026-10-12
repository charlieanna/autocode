"""Every needs() kind names an action the public CLI accepts (#1010).

The status view's next_command is built from needs via autocode_pause_command.
A new kind without a row here fails the suite the same way test_run_state fails
when tools/ invents an undocumentated state key.
"""

from __future__ import annotations

import copy
import shlex
import unittest
from pathlib import Path
from unittest import mock

import autocode_args
import autocode_configure
import autocode_pause_command as pause_command
import autocode_run_view as run_view

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
        "dependency_wait": {"producer_run": "/tmp/producer"},
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
    "dependency": lambda args: args is None,  # no recovery command
    "retry_job": lambda args: args.resume_paused and args.retry_failed_stage and args.job_retry_token == "job-token",
    "recover_source": lambda args: args.explain,
}


# Placeholders the view leaves for a person to fill (pause_command docstring).
_PERSON_INT = frozenset(
    {
        "--planning-review-call-limit",
        "--no-progress-limit",
        "--max-seconds",
        "--max-idle-seconds",
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
    """Parse a displayed next_command into args; returns (args, argv) or (None, None) when text is None."""
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
                if kind == "dependency":
                    self.assertIsNone(view.get("next_command"))
                    continue
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


class NeedsCommandAccepted(unittest.TestCase):
    """Shared helper the scenario driver uses: refuse a next_command the CLI rejects."""

    @staticmethod
    def assert_accepted(next_command: str | None) -> None:
        if next_command is None:
            return
        args, _ = parse_next_command(next_command)
        if args is None:
            raise AssertionError(f"next_command produced no args: {next_command!r}")


if __name__ == "__main__":
    unittest.main()
