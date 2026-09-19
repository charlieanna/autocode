"""Offline contract fixtures used by regression and subprocess smoke tests."""
import copy


def body(*, questions=False, human=False):
    return {
        "intended_outcome": "Provide a deterministic greeting CLI",
        "intended_user": "A local developer",
        "end_to_end_flow": ["Run the CLI with a name", "Read the greeting or an invalid-input error"],
        "technical_approach": ["A standard-library Python CLI using sys.argv"],
        "milestones": [{"id": "M1", "objective": "Deliver and verify the greeting flow", "acceptance_criteria": ["C1"]}],
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
            "proposed_default": "CLI: local use without a server"}] if questions else [],
    }


def envelope(state):
    contract = state["goal_contract"]
    return {"contract_revision": contract["revision"], "contract_hash": contract["hash"],
            "task_id": state.get("current_task", {}).get("id", ""),
            "deferred_backlog": [], "user_request": {"kind": "none", "discovered": "", "impact": "",
                "decision_needed": "", "options": [], "proposed_delta": ""}}


def approve_fixture(state, goals):
    goals.migrate(state)
    goals.install_draft(state, body(), origin="fixture")
    goals.present(state)
    goals.approve(state, goals.token(state["goal_contract"]))
    state.update(next_stage="terra", phase="EXECUTING")
