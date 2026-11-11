"""A bug-fix diagnosis as an earlier runtime's Investigator saved it in a run's state.

AutoCode no longer runs that Investigator (``investigate_bug``), but a run saved past it still carries its
report under ``state["investigation"]``, and the planning readers (autocode_test_cases,
autocode_current_behavior, the Builder's and validators' packets) still read it. These helpers write
that saved record directly, in the shape the retired stage left it, so the readers can be tested
without the stage.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path

CASE = {
    "id": "T1",
    "given": "the registry times out once after applying a renew",
    "when": "renew('example.com') runs",
    "then": "the registry records exactly 1 renew mutation",
    "kind": "restore",
}


def state_for(workspace="/nowhere", task="Occasionally we renew the same domain twice after a timeout. Fix it."):
    return {
        "version": 3,
        "task": task,
        "workspace": workspace,
        "status": "RUNNING",
        "stages": [],
        "workflow": {"kind": "bugfix", "then": "requirements_gather"},
        "settings": {
            "joint_planning": True,
            "roles": {
                "requirements": {"model": "r"},
                "planner": {"model": "g"},
                "plan_reviewer": {"model": "p"},
                "task_planner": {"model": "a", "engine": "codex"},
                "builder": {"model": "t"},
                "tester": {"model": "s"},
            },
        },
    }


def diagnosis(outcome="reproduced", **overrides):
    """The Investigator's report, as the retired stage's schema had it."""
    value = {
        "outcome": outcome,
        "note_path": "docs/bugs/duplicate-renew.json",
        "observed": "Renewed twice after a timeout",
        "reproduction": "fail_next('timeout-after'); renew() -> 2 mutations",
        "root_cause": "retries after an uncertain timeout with a fresh cl_trid",
        "affected_paths": ["epp/client.py"],
        "test_paths": ["tests/test_client.py"],
        "invariant": "one logical renew, at most one mutation",
        "test_cases": [CASE],
        "probe": "",
        "untestable": "The fixture has no registry to replay against",
        "conclusion": "Reconcile before resending.",
        "fix_size": "small",
        "fix_plan": ["keep one cl_trid", "poll before resending"],
        "questions": [],
        "tests_run": ["python3 -m unittest"],
        "plan_approval_requested": False,
        "workspace_scope": {"spans_repositories": False, "external_parts": []},
        "counterexample": {"file": "", "find": "", "replace": ""},
        "project_notes": [],
    }
    if outcome == "not_reproduced":
        value.update(
            root_cause="",
            affected_paths=[],
            test_paths=[],
            invariant="",
            fix_size="none",
            fix_plan=[],
            test_cases=[],
            note_path="docs/bugs/none-cells.json",
            conclusion="export() already writes None as empty.",
            questions=["Which version is the reporter running?"],
        )
    value.update(overrides)
    return value


def bind(state, value, output=None, *, source_revision="rev-1", probe_result=None, stage=True) -> dict:
    """Save ``value`` as the run's bound diagnosis, as the retired stage left it: the report, the hash of its
    output file, the source revision and the runner's probe result (None when no probe showed the bug).
    With ``stage``, the Investigator's attempt is recorded too, so this turn's readers take it as current."""
    path = Path(output) if output else None
    state["investigation"] = {
        **value,
        "output": str(path) if path else None,
        "output_hash": hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else None,
        "source_revision": source_revision,
        "probe_result": probe_result,
    }
    if stage and path:
        state.setdefault("stages", []).append(
            {"stage": "investigate_bug", "output": str(path), "source_revision": source_revision}
        )
    return state["investigation"]


def workspace() -> Path:
    """A Git repository holding epp/client.py, the file the diagnosis names."""
    root = Path(tempfile.mkdtemp())
    (root / "epp").mkdir()
    (root / "epp" / "client.py").write_text("x = 1\n")
    for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *command], cwd=root, check=True)
    return root


def investigated(**overrides) -> dict:
    """A run saved past the Investigator with its diagnosis bound, on its way to the Planner's draft."""
    root = workspace()
    state = {
        **state_for(str(root)),
        "task_id": "task-under-test",
        "iteration": 1,
        "answers": {},
        "user_events": [],
        "history": [],
        "sessions": {},
        "phase": "PLANNING",
        "next_stage": "draft_plan",
    }
    output = root.parent / f"{root.name}-investigation.json"
    output.write_text("{}")
    bind(state, diagnosis(**overrides), output, stage=False)
    return state


def approved_fix(*, bound=True, **overrides) -> dict:
    """A bug-fix run whose one-task plan the person approved and the runner assigned to the Builder; with
    ``bound``, it carries the saved diagnosis it was planned from."""
    import autocode_goal_lifecycle as lifecycle
    import autocode_goals as goals
    import autocode_source_scope as source_scope
    from goal_fixtures import body

    state = investigated(**overrides)
    if not bound:
        state.pop("investigation")
    found = diagnosis(**overrides)
    paths = list(dict.fromkeys([*found["affected_paths"], *found["test_paths"]]))
    draft = {**body(), "task_kind": "bugfix", "deliverables": list(found["affected_paths"])}
    draft["milestones"][0]["affected_paths"] = paths
    draft["initial_task"] = {
        "objective": "Fix the root cause: " + found["root_cause"],
        "affected_paths": paths,
        "kind": "implement",
        "milestone_id": "M1",
        "requirements": ["Renew a domain once even when the registry times out"],
        "acceptance_criteria": ["C1"],
        "validation_plan": ["python3 -m unittest -v"],
    }
    lifecycle.install_draft(state, draft, origin="fixture", queue_human=False)
    contract = state["goal_contract"]
    event = {"kind": "goal_approval", "actor": "user_cli", "at": "2026-11-10T00:00:00+00:00"}
    event["token"] = goals.token(contract)
    state["user_events"].append(event)
    contract.update(approval_status="approved", approval_event=event)
    lifecycle.assign_task(state, goals.initial_decision(draft), source_scope.snapshot(Path(state["workspace"]), state))
    state.update({"status": "RUNNING", "phase": "EXECUTING", "next_stage": "builder"})
    return state
