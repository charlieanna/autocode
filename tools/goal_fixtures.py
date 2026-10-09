"""Offline contract fixtures used by regression and subprocess smoke tests."""
from pathlib import Path


def write_greeting_source(workspace, *, revision=None):
    """A real fixture artifact; revision markers vary source without breaking behavior."""
    marker = f"# Fixture revision: {revision}\n" if revision is not None else ""
    (Path(workspace) / "greet.py").write_text(marker + '''import sys

def main():
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("A nonempty name is required", file=sys.stderr)
        return 2
    print("Hello, " + sys.argv[1])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
''')


def seed_greeting_workspace(workspace):
    """Give check-replay a discoverable suite that exercises the fixture contract."""
    root = Path(workspace)
    write_greeting_source(root)
    (root / "test_greeting.py").write_text('''from pathlib import Path
import subprocess
import sys
import unittest

class GreetingTests(unittest.TestCase):
    def invoke(self, name):
        return subprocess.run([sys.executable, str(Path(__file__).with_name("greet.py")), name],
                              capture_output=True, text=True)

    def test_greets_a_valid_name(self):
        result = self.invoke("Ada")
        self.assertEqual(0, result.returncode)
        self.assertEqual("Hello, Ada\\n", result.stdout)
        self.assertEqual("", result.stderr)

    def test_rejects_empty_and_whitespace_names(self):
        for name in ("", "   "):
            with self.subTest(name=name):
                result = self.invoke(name)
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertIn("nonempty name", result.stderr)
''')


def assert_operational_wait(test, state, pause_status):
    from tools import autocode_resolver_human as human
    test.assertEqual('WAITING_FOR_USER', state['status'])
    test.assertEqual('WAITING_FOR_USER', state['phase'])
    public = human.current(state)
    test.assertIsNotNone(public)
    test.assertEqual('operational_exhaustion', public['scope'])
    proposal = state['resolver']['human_escalations'][public['request_id']]['identity']['proposal']
    test.assertEqual(pause_status, proposal['origin']['pause_status'])
    return public


def body(*, questions=False, human=False, task_kind="build"):
    return {
        "task_kind": task_kind,
        "intended_outcome": "Provide a deterministic greeting CLI",
        "intended_user": "A local developer",
        "end_to_end_flow": ["Run the CLI with a name", "Read the greeting or an invalid-input error"],
        "technical_approach": ["A standard-library Python CLI using sys.argv"],
        "milestones": [{"id": "M1", "objective": "Deliver and verify the greeting flow",
                        "acceptance_criteria": ["C1"], "depends_on": [], "affected_paths": ["greet.py"]}],
        "deliverables": ["greet.py", "CLI regression tests"],
        "required_behaviors": ["Print Hello, NAME for a nonempty name"],
        "important_failure_cases": ["Reject an empty name with nonzero exit status"],
        "scope_exclusions": ["Web service", "Deployment"],
        "constraints": ["Python standard library only"],
        "permission_boundaries": ["Read and edit only this fixture Git workspace; no external writes"],
        "accepted_assumptions": [{"text": "CLI invocation is sufficient", "basis": "agent_proposed", "answer_id": ""}],
        "delegated_decisions": [],
        "acceptance_criteria": [{"id": "C1", "criterion": "Contract holds",
                                  "verification_method": "Execute greeting and invalid-input regression checks", "human_review": human}],
        "open_blocking_questions": [{"id": "Q1", "question": "Should the greeting be a CLI or web endpoint?",
            "why": "This determines the delivered interface", "options": ["CLI: local use", "Web: requires a server"],
            "proposed_default": "CLI: local use without a server",
            "kind": "decision", "category": "behavior", "delegable": True}] if questions else [],
    }


def envelope(state):
    contract = state["goal_contract"]
    return {"contract_revision": contract["revision"], "contract_hash": contract["hash"],
            "task_id": state.get("current_task", {}).get("id", ""),
            "deferred_backlog": [], "user_request": {"kind": "none", "discovered": "", "impact": "",
                "decision_needed": "", "options": [], "proposed_delta": ""}}


def approve_fixture(state, goals):
    # The lifecycle and Resolver modules are imported the way `goals` was (plain, tools. or
    # autocode_cli.), so the fixture acts on the same module instances as the caller.
    import importlib
    prefix = goals.__name__[:-len("autocode_goals")]
    lifecycle = importlib.import_module(prefix + "autocode_goal_lifecycle")
    human = importlib.import_module(prefix + "autocode_resolver_human")
    lifecycle.migrate(state)
    lifecycle.install_draft(state, body(), origin="fixture")
    # The fixture simulates the writer boundary, never a presentation-side grant.
    human.evaluate(state)
    lifecycle.present(state)
    lifecycle.approve(state, goals.token(state["goal_contract"]))
    state.update(next_stage="terra", phase="EXECUTING")
