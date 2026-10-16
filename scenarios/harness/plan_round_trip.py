"""Black-box check at each plan approval stop: the plan file round trip reproduces the goal hash (#720).

A team reviews a plan as the file `autocode plan --export PATH` writes, and loads the merged file back with
`autocode --goal-file PATH` (docs/workflow.md, "Reviewing a plan in a pull request"). The first acceptance
check of #720 is that export then import of every catalog scenario's plan reproduces the same goal hash.

At each approval stop a catalog run reaches, before the harness approves the plan (``Driver.serve``, the
conformance client's ``record_approvals``), ``take`` exports that plan to ``plan-files/NN.md`` in the
evidence directory and reads the file back with `autocode plan --check PATH`. That check reads the file
with the reader `--goal-file` uses and prints the goal hash `--goal-file` records for it, without loading
it: a real load would install a new plan revision and the harness could no longer approve the one shown.
Both calls must leave the run as it was: ``take`` records each file of the run directory either one
changed, which must be none. The harness then approves the token it was shown, which AutoCode refuses
once the plan on show changed.

``checks`` judges what ``take`` recorded in the approval row (``plan_file``): the file reads back with the
goal hash of the run's plan, as the export reported it, and, once the run reports that plan approved, the
approved plan's goal hash in its status view (``approved_contract``) is that same hash. A plan holding
credential-shaped text is masked on export, so its file's goal hash differs from the run's by design
(docs/workflow.md); the file must then read back as written, and the check says it was masked.

Only `autocode` commands and the status view the harness already read are used; nothing from tools/ is
imported.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .oracle import Check

FOLDER = "plan-files"  # in the evidence directory, beside steps.jsonl
NAME = "plan round trip"


def take(driver) -> dict:
    """Export the plan ``driver``'s run shows for approval and read the file back; what happened, for the
    approval row's ``plan_file``. A command that exits non-zero is recorded for ``checks`` to judge; the two
    calls are no steps, but the drive's time budget bounds them as it bounds every call."""
    started = time.monotonic()
    folder = Path(driver.root) / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    number = 1
    while (folder / f"{number:02d}.md").exists():  # one folder for a program's plan and workstream runs
        number += 1
    path = folder / f"{number:02d}.md"
    run_dir = Path(driver.run_dir)
    before = listing(run_dir)
    where = ["--workspace", str(driver.project), "--run-dir", str(run_dir)]
    exported = outcome(
        driver.call("plan-export", "plan", "--export", str(path), *where, "--json", record=False, subcommand=True)
    )
    checked = None
    if exported["exit"] == 0:
        checked = outcome(
            driver.call("plan-check", "plan", "--check", str(path), "--json", record=False, subcommand=True)
        )
    after = listing(run_dir)
    return {
        "file": str(path),
        "export": exported,
        "check": checked,
        "run_changed": sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name)),
        "seconds": round(time.monotonic() - started, 1),
    }


def listing(run_dir: Path) -> dict[str, tuple[int, int]]:
    """Each file in the run directory with its size and modification time: what a write would change."""
    found = {}
    for path in run_dir.rglob("*") if run_dir.is_dir() else ():
        try:
            if path.is_file():
                stat = path.stat()
                found[path.relative_to(run_dir).as_posix()] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            continue
    return found


def outcome(proc) -> dict:
    """The command's exit code and the JSON it printed: on stdout when it succeeds, on stderr when it fails."""
    text = (proc.stdout if proc.returncode == 0 else proc.stderr).strip()
    payload = None
    for candidate in (text, text.splitlines()[-1] if text else ""):
        try:
            payload = json.loads(candidate)
            break
        except ValueError:
            continue
    if not isinstance(payload, dict):
        payload = {"output": (proc.stdout + proc.stderr).strip()[-500:]}
    return {"exit": proc.returncode, **payload}


def problems(trip: dict, approved_hash: str | None = None) -> list[str]:
    """Why ``trip`` (``take``'s record) does not show the plan file reproducing the run's goal hash; empty when
    it does. ``approved_hash`` is the goal hash the final status view reports for this approval, if it does."""
    exported, checked = trip.get("export") or {}, trip.get("check") or {}
    if exported.get("exit") != 0:
        return [f"`autocode plan --export` exited {exported.get('exit')}: {message(exported)}"]
    goal, written = exported.get("goal_hash"), exported.get("written_goal_hash")
    found = []
    if checked.get("exit") != 0:
        found.append(f"`autocode plan --check` exited {checked.get('exit')}: {message(checked)}")
    elif checked.get("goal_hash") != written:
        found.append(
            f"the file reads back as goal hash {checked.get('goal_hash')}, not the {written} it was written with"
        )
    elif not exported.get("masked") and (checked.get("goal_hash") != goal or not checked.get("matches")):
        found.append(f"the file reads back as goal hash {checked.get('goal_hash')}, not the run's plan's {goal}")
    elif checked.get("revision") != exported.get("revision"):
        found.append(f"the file names revision {checked.get('revision')}, not the exported {exported.get('revision')}")
    if trip.get("run_changed"):
        found.append(f"exporting and checking the plan changed the run: {', '.join(trip['run_changed'])}")
    if approved_hash is not None and approved_hash != goal:
        found.append(f"the run reports the approved plan's goal hash as {approved_hash}, not the exported {goal}")
    return found


def message(result: dict) -> str:
    error = result.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return str(result.get("message") or result.get("output") or "no message")


def approved_hashes(run: dict) -> dict:
    """{approval token: goal hash} for each plan a status view in ``run`` reports approved (``approved_contract``)."""
    views = [run.get("view"), (run.get("plan") or {}).get("view"), run.get("architecture_view")]
    views += [child for children in (run.get("children") or {}).values() for child in children or ()]
    components = (run.get("components") or {}).get("components") if isinstance(run.get("components"), dict) else {}
    views += [(child or {}).get("view") for child in (components or {}).values()]
    found = {}
    for view in views:
        approved = view.get("approved_contract") if isinstance(view, dict) else None
        if isinstance(approved, dict) and approved.get("token"):
            found[approved["token"]] = approved.get("goal_hash")
    return found


def described(gate: dict, approved_hash: str | None) -> str:
    exported = (gate.get("plan_file") or {}).get("export") or {}
    text = f"revision {exported.get('revision')} goal hash {exported.get('goal_hash')}"
    if exported.get("masked"):
        text += (
            f" (credential-shaped text masked on export, so the file's goal hash {exported.get('written_goal_hash')}"
            " differs by design)"
        )
    return text + (", approved" if approved_hash is not None else "")


def checks(run) -> list[Check]:
    if not run or not run.get("report_expected"):
        return []  # check-only or a synthetic oracle unit input, not an executed CLI
    try:
        gates = [
            gate
            for gate in run.get("approvals") or ()
            if isinstance(gate, dict) and gate.get("gate") == "approve_plan" and "plan_file" in gate
        ]
        if not gates:
            return []  # no plan approval was reached
        approved = approved_hashes(run)
        found, shown = [], []
        for gate in gates:
            approved_hash = approved.get(gate.get("token"))
            found += [f"plan {gate.get('token')}: {problem}" for problem in problems(gate["plan_file"], approved_hash)]
            shown.append(described(gate, approved_hash))
    except (AttributeError, TypeError, KeyError) as error:
        return [Check(NAME, False, f"plan round trip metadata: {error}")]
    if found:
        return [Check(NAME, False, "; ".join(found))]
    return [
        Check(
            NAME,
            True,
            f"{len(gates)} plan(s) exported at the approval stop and read back by `autocode plan --check` with the "
            "same goal hash, the run left as it was: " + "; ".join(shown),
        )
    ]
