"""Black-box completion postcondition: who approved, as the public status view shows it (#722).

AutoCode records who made each plan and review approval, completion acceptance, goal
edit and pause-releasing resume, and `--status --json` lists them under ``approvals``
(docs/task-run.md, "Status view"). For every completed task run the harness drove,
including a program's workstream runs and a components run's architecture and component
runs, and a program's plan run once its plan is approved (the program is derived from it and
never relaunches it, so it does not complete), each row a person made (``actor``
``user_cli``) must carry the provenance of the person at the terminal: the Git identity every
scenario project configures for them (``project.PERSON_NAME``, ``project.PERSON_EMAIL``),
this machine's host name, and the command as ``autocode`` with option names only (and the
``resume`` command word), never their values. A row approved under the workflow policy names
no person and must carry no provenance. Each plan approval (by token) and review approval
(by criterion) the harness gave a run (``Driver.approvals``, which the conformance client
records too), and each budget pause the scenario's person acknowledged (``[fake]
acknowledge``, by the pause status its ``resume`` row names), must be listed among that run's
own rows, so a view that lists nothing cannot pass for a run that was approved or resumed. A
program or components scenario keeps one list for all its task runs, and each gate the harness
gave one of them names that run's ``run_dir`` (``driver.attribute``): it is matched to that
run's rows, never to another's.

Only the status view the harness already read is used; nothing from tools/ is imported.
"""

from __future__ import annotations

import socket

from .evidence_reports import complete
from .oracle import Check
from .project import PERSON_EMAIL, PERSON_NAME

PERSON = "user_cli"  # a row a person made: an approval, acceptance, edit or resume they typed
POLICY = "workflow_policy"  # an approval under the workflow policy the person agreed to; no one typed it
COMMAND_WORDS = ("resume",)  # the one command word AutoCode records beside option names
MATCHED = ("approve_plan", "review", "acknowledge")  # the gates matched to a row; problems() says how


def views(run):
    """(identity, status view, gates the harness gave that run) for each completed run in ``run``.

    In a program or components scenario the gates are those that name the run's ``run_dir``."""
    gates = run.get("approvals") or ()

    def given(run_dir):
        return [gate for gate in gates if run_dir and gate.get("run_dir") == str(run_dir)]

    if "program" in run:
        plan = run.get("plan") or {}
        view = plan.get("view") or {}
        if complete(view) or view.get("approved_contract"):  # the program is derived from it, never relaunched
            yield "program plan", view, given(plan.get("run_dir"))
        for identity, children in (run.get("children") or {}).items():
            for child in children:  # each child is its status view merged with where it ran
                if complete(child):
                    yield identity, child, given(child.get("run_dir"))
    elif "components" in run:
        view = run.get("architecture_view") or {}
        if complete(view):
            yield "architecture", view, given(run.get("architecture_run_dir"))
        for identity, child in ((run["components"] or {}).get("components") or {}).items():
            view = child.get("view") or {}
            if complete(view):
                yield identity, view, given(child.get("run_dir"))
    elif complete(run.get("view")):
        yield "task", run["view"], gates


def problems(view: dict, gates=(), *, host: str | None) -> list[str]:
    """Why ``view``'s ``approvals`` does not show the person's provenance for each approval; empty when it does.

    ``gates`` are the harness's own records of what it approved or acknowledged for this run (``Driver.approvals``);
    ``host`` is this machine's host name as AutoCode records it."""
    rows = view.get("approvals")
    if not isinstance(rows, list):
        return ["the status view has no approvals list"]
    expected = {"git_user_name": PERSON_NAME, "git_user_email": PERSON_EMAIL, "host": host}
    found = []
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            found.append(f"row {number} is not a record")
            continue
        label = f"row {number} ({row.get('kind')})"
        actor, provenance = row.get("actor"), row.get("provenance")
        if actor == POLICY:
            if provenance is not None:
                found.append(f"{label} was approved under the workflow policy but names a person")
            continue
        if actor != PERSON:
            found.append(f"{label} has actor {actor!r}, neither a person nor the workflow policy")
            continue
        if not isinstance(provenance, dict):
            found.append(f"{label} records no provenance")
            continue
        found += [
            f"{label} {key} is {provenance.get(key)!r}, not {value!r}"
            for key, value in expected.items()
            if provenance.get(key) != value
        ]
        command = provenance.get("command")
        words = command.split() if isinstance(command, str) else []
        if words[:1] != ["autocode"] or any(
            not word.startswith("--") and word not in COMMAND_WORDS for word in words[1:]
        ):
            found.append(f"{label} command {command!r} is not autocode and option names")
    made = [row for row in rows if isinstance(row, dict) and row.get("actor") == PERSON]
    for gate in gates:
        if gate.get("gate") == "approve_plan" and not any(
            row.get("kind") == "plan" and row.get("contract_token") == gate.get("token") for row in made
        ):
            found.append(f"the harness approved plan {gate.get('token')}, which no plan row lists")
        elif gate.get("gate") == "review" and not any(
            row.get("kind") == "review" and row.get("criterion") == gate.get("criterion") for row in made
        ):
            found.append(f"the harness approved the review of {gate.get('criterion')}, which no review row lists")
        elif gate.get("gate") == "acknowledge" and not any(
            row.get("kind") == "resume" and row.get("pause_status") == gate.get("pause") for row in made
        ):
            found.append(f"the harness acknowledged {gate.get('pause')}, which no resume row lists")
    return found


def described(view: dict, gates=()) -> str:
    """What a passing check saw: the rows, and the gates the harness gave this run that it matched to them."""
    rows = [row for row in view.get("approvals") or [] if isinstance(row, dict)]
    if not rows:
        return "No approval recorded"
    kinds = ", ".join(str(row.get("kind")) for row in rows)
    text = f"{len(rows)} recorded ({kinds}); each one a person made names {PERSON_NAME} <{PERSON_EMAIL}> on this host"
    matched = [str(gate.get("gate")) for gate in gates if gate.get("gate") in MATCHED]
    return text + (f"; each gate the harness passed for this run is listed ({', '.join(matched)})" if matched else "")


def checks(run) -> list[Check]:
    if not run or not run.get("report_expected"):
        return []  # check-only or a synthetic oracle unit input, not an executed CLI
    host = socket.gethostname() or None  # as AutoCode records it (autocode_approval_provenance.capture)
    results = []
    try:
        for identity, view, gates in views(run):
            found = problems(view, gates, host=host)
            results.append(
                Check(
                    "approval provenance: " + identity,
                    not found,
                    "; ".join(found) if found else described(view, gates),
                )
            )
    except (AttributeError, TypeError, KeyError) as error:
        results.append(Check("approval provenance metadata", False, str(error)))
    return results
