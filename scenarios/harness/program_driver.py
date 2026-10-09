"""Drive a program scenario through ``autocode program``, serving its gates like a person.

A program scenario (category "program", scenarios/README.md "Programs") is one request
too large for one run. The driver takes it the way a person would, through the CLI only:

1. ``program plan BRIEF``: an ordinary planning run (``--unit autoplanner``). The driver
   answers its questions and approves its plan, then stops: the approved plan run is never
   relaunched, since its contract is all a program is derived from.
2. ``program derive`` writes the manifest from the approved plan; the scenario's
   ``[program] revise`` is the person's own edit to it before approving it. The scenario
   names workstreams by its ``[fake] milestones`` ids, which only the scripted planner
   keeps, so each one it names stands for the derived workstream that owns that
   milestone's paths (``workstream_ids``); under the scripted model that is the same id.
3. ``program show`` prints the agreement and its token; ``program approve`` approves that
   exact token, never another.
4. ``program run`` until the program completes or stops for a person. Every pass carries
   the full child flags: they configure only the workstream runs started in that pass.
   Between passes the driver approves each new agreement revision by the token ``program
   show`` displays, raises and decides the scenario's ``[[program.change]]`` requests, and
   serves each waiting workstream's gate (plan approval, a question with its proposed
   default, human review, planning budget) from that run's own status view.

What it never does: relaunch, resume or follow up a workstream's run (the program advances
each one once per pass), answer a question only a person may answer, authorize deployment,
or retry a failed workstream. A program that stops there is judged as it stopped.

It reads the program's state file and the workstream runs' ``state.json`` only afterwards,
for evidence and the oracle's run record. The product is the integration worktree, where
the program merged every workstream; the project's own branch never changes.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from .driver import DriveError, Driver, leaves_for_person, metrics
from .processes import CallTimeout, SupervisionUnavailable, run_cli

# Program statuses the driver leaves as they are: done, or waiting for what only a person decides.
FINAL = ("COMPLETE", "BLOCKED", "AUTHORIZATION_REQUIRED")
TOKEN = re.compile(r"^Approve with token: (\S+)$", re.M)


def tail(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout.strip() or proc.stderr.strip())[-500:]


def read_summary(proc: subprocess.CompletedProcess) -> dict:
    """The summary `program run` printed: its status and a row with an id and a status per workstream."""
    try:
        summary = json.loads(proc.stdout)
        if isinstance(summary["status"], str) and all(isinstance(row["id"], str) and isinstance(row["status"], str)
                                                      for row in summary["workstreams"]):
            return summary
    except (ValueError, KeyError, TypeError):
        pass
    raise DriveError("program run printed no summary: " + tail(proc))


class WorkstreamDriver(Driver):
    """One workstream's run, reached only to serve its gates; its CLI calls are logged by workstream."""

    def __init__(self, workstream: str, run_dir: Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.workstream, self.run_dir = workstream, run_dir

    def call(self, kind: str, *extra: str, **kwargs) -> subprocess.CompletedProcess:
        return super().call(f"{kind}:{self.workstream}", *extra, **kwargs)


def workstream_ids(manifest: dict, milestones, *, named_in: str | None = None) -> dict[str, str]:
    """{scenario id: derived workstream id} for each of the scenario's ``[fake] milestones`` rows given.

    The scripted planner keeps those ids; a live one names its milestones itself. So a scenario id stands for
    the one derived workstream whose ``owns`` cover every path of its milestone that the brief (``named_in``)
    names (the producer of the notes store is whoever owns notes/store.py); with no brief, or a row whose
    paths it never names, every path of the row. A path only the reference solution has, such as a dispatcher
    module the brief never asks for, cannot rule a live plan out (live run 12, 2026-10-06). Among the
    workstreams that cover those, the one covering the most of the row's paths stands for it, then the most
    specific owner, as when a skeleton that owns all of notes/ and an extension that owns
    notes/commands/search.py both cover search.py. Raises DriveError when no workstream covers one, several
    tie, or two scenario ids land on the same workstream: the live plan's split does not line up with the
    scenario's."""
    rows = [(row["id"], [str(own).rstrip("/") for own in row.get("owns") or []])
            for row in manifest.get("workstreams") or []]

    def covering(path, owns):
        return [own for own in owns if path == own or path.startswith(own + "/")]

    ids, problems = {}, []
    for milestone in milestones:
        paths = list(milestone.get("paths") or [])
        required = [path for path in paths if named_in and path in named_in] or paths
        covers = {wid: (sum(1 for path in paths if covering(path, owns)),
                        sum(max(map(len, covering(path, owns)), default=0) for path in paths))
                  for wid, owns in rows if required and all(covering(path, owns) for path in required)}
        owners = [wid for wid, score in covers.items() if score == max(covers.values())]
        if len(owners) == 1:
            ids[milestone["id"]] = owners[0]
        else:
            problems.append((f"{len(owners)} workstreams ({', '.join(owners)}) each own" if owners
                             else "no one workstream owns") + f" all of {milestone['id']}'s {', '.join(required)}")
    for wid in sorted(set(ids.values())):
        same = [sid for sid, found in ids.items() if found == wid]
        if len(same) > 1:
            problems.append(f"{' and '.join(same)} would be one workstream, {wid}")
    if problems:
        raise DriveError("the approved plan's workstreams do not line up with the scenario's: " + "; ".join(problems))
    return ids


def named_workstreams(edits: dict, changes) -> set[str]:
    """The workstream ids the scenario's ``[program]`` names: interface producers and consumers (in ``revise``
    and in each change's ``publish``), and each change's ``by`` and ``after``."""
    shared = edits.get("shared") if isinstance(edits.get("shared"), dict) else {}
    rows = [*(shared.get("interfaces") or []), *(step.get("publish") or {} for step in changes)]
    named = {wid for row in rows if isinstance(row, dict)
             for wid in (row.get("producer"), *(row.get("consumers") or []))}
    named |= {wid for step in changes for wid in (step["by"], step["after"].removeprefix("merged:"))}
    return {wid for wid in named if isinstance(wid, str)}


def renamed(row: dict, ids: dict[str, str]) -> dict:
    """A copy of an interface row (or a change's ``publish``) with its producer and consumers renamed."""
    row = json.loads(json.dumps(row))
    if isinstance(row.get("producer"), str):
        row["producer"] = ids.get(row["producer"], row["producer"])
    if isinstance(row.get("consumers"), list):
        row["consumers"] = [ids.get(wid, wid) for wid in row["consumers"]]
    return row


def revise(manifest: dict, edits: dict) -> dict:
    """The person's edits laid over the derived manifest: tables merge, anything else replaces."""
    for key, value in edits.items():
        if key == "workstreams" and isinstance(value, dict) and isinstance(manifest.get(key), list):
            for wid, fields in value.items():
                row = next((row for row in manifest[key] if row["id"] == wid), None)
                if row is None:
                    raise DriveError(f"program revision names unknown workstream {wid}")
                row.update(json.loads(json.dumps(fields)))
        elif isinstance(value, dict) and isinstance(manifest.get(key), dict):
            revise(manifest[key], value)
        else:
            manifest[key] = json.loads(json.dumps(value))
    return manifest


class ProgramDriver:
    def __init__(self, scenario, project: Path, root: Path, flags: list[str], env: dict, *,
                 autocode: list[str], max_steps: int, timeout_seconds: int, explicit_answers=()):
        self.scenario, self.project, self.root = scenario, project, root
        self.flags, self.autocode = list(flags), autocode
        self.child_env, self.explicit_answers = env, explicit_answers
        # The plan run's driver; every other driver shares its CLI-call budget, deadline, log and answers.
        self.plan = Driver(project, root, [*flags, "--unit", "autoplanner"], env, autocode=autocode,
                           max_steps=max_steps, timeout_seconds=timeout_seconds, explicit_answers=explicit_answers)
        self.env, self.steps, self.answers = self.plan.env, self.plan.steps, self.plan.answers
        self.max_steps, self.deadline = max_steps, self.plan.deadline
        self.manifest = root / "program.json"
        self.plan_steps = self.plan_answers = 0  # how many of ``steps`` and ``answers`` belong to the plan run
        self.summary: dict = {}      # the last program summary
        self.shown: list[str] = []   # agreement tokens `program show` displayed
        self.approved: list[str] = []
        self.child_views, self.plan_view = {}, {}
        # The scenario's [program] with its workstream ids renamed to the derived ones (map_workstreams).
        self.ids: dict[str, str] = {}
        self.edits = json.loads(json.dumps(scenario.program_revise))
        self.scripted = [json.loads(json.dumps(step)) for step in scenario.program_changes]
        # One record per [[program.change]]: its request id once raised, and whether it was decided.
        self.changes = [{"interface": step["interface"], "by": step["by"], "after": step["after"],
                         "decide": step["decide"], "request": None, "decided": False}
                        for step in scenario.program_changes]
        self.revisions = [{**step, "edited": False} for step in scenario.program_revisions]

    # --- CLI ----------------------------------------------------------------

    def program(self, kind: str, *args: str, flags: bool = False, codes=(0,),
                budget: bool = True) -> subprocess.CompletedProcess:
        """One ``autocode program`` call, recorded like the driver's own: ``flags`` adds the child flags, and
        ``codes`` are the exits it may end with. A call outside the ``budget`` is not recorded."""
        if budget and len(self.steps) >= self.max_steps:
            raise DriveError(f"step budget used up after {len(self.steps)} CLI calls")
        remaining = self.deadline - time.monotonic() if budget else max(self.deadline - time.monotonic(), 60)
        if remaining <= 0:
            raise DriveError(f"time budget used up after {len(self.steps)} CLI calls")
        started = time.monotonic()
        try:
            proc = run_cli([*self.autocode, "program", *args, *(self.flags if flags else ())], env=self.env,
                           cwd=self.root, timeout=remaining)
        except SupervisionUnavailable as error:
            raise DriveError(str(error)) from None
        except CallTimeout:
            raise DriveError(f"{kind} was still running when the time budget ran out") from None
        if budget:
            step = {"kind": kind, "args": list(args), "exit": proc.returncode,
                    "seconds": round(time.monotonic() - started, 1),
                    "stdout_tail": proc.stdout[-1500:], "stderr_tail": proc.stderr[-1500:]}
            self.steps.append(step)
            with self.plan.log.open("a") as handle:
                handle.write(json.dumps(step) + "\n")
        # A usage error also exits 2: recognize argparse's message rather than trusting the code.
        if proc.returncode not in codes or (proc.returncode == 2 and proc.stderr.startswith("usage:")):
            raise DriveError(f"{kind} exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-500:]}")
        return proc

    def on_manifest(self, kind: str, command: str, *args: str, **kwargs) -> subprocess.CompletedProcess:
        return self.program(kind, command, str(self.manifest), "--workspace", str(self.project), *args, **kwargs)

    # --- the sequence -------------------------------------------------------

    def drive(self) -> str:
        """Take the program as far as it goes; return its final status (the plan run's when no program started)."""
        status = self.plan_leg()
        if status:
            return status
        self.program("program-derive", "derive", "--run-dir", str(self.plan.run_dir), "--workspace",
                     str(self.project), "--output", str(self.manifest), "--name", self.scenario.id)
        shutil.copy2(self.manifest, self.root / "program-derived.json")
        self.map_workstreams(self.read_manifest())
        if self.edits:
            self.write_manifest(revise(self.read_manifest(), self.edits))
        self.approve()
        return self.run_loop()

    def map_workstreams(self, derived: dict) -> None:
        """Rename the workstreams the scenario's [program] names to the derived manifest's (workstream_ids)."""
        named = named_workstreams(self.scenario.program_revise, self.scenario.program_changes)
        named |= set(self.edits.get("workstreams", {}))
        named |= {step[key].removeprefix("merged:") for step in self.revisions for key in ("workstream", "after")}
        self.ids = workstream_ids(derived, [row for row in self.scenario.fake_milestones if row["id"] in named],
                                  named_in=self.scenario.brief)
        if isinstance(self.edits.get("workstreams"), dict):
            self.edits["workstreams"] = {self.ids.get(wid, wid): fields for wid, fields in self.edits["workstreams"].items()}
        shared = self.edits.get("shared")
        if isinstance(shared, dict) and isinstance(shared.get("interfaces"), list):
            shared["interfaces"] = [renamed(row, self.ids) if isinstance(row, dict) else row
                                    for row in shared["interfaces"]]
        for step, record in zip(self.scripted, self.changes):
            step["by"] = self.ids.get(step["by"], step["by"])
            after = step["after"].removeprefix("merged:")
            step["after"] = "merged:" + self.ids.get(after, after)
            if "publish" in step:
                step["publish"] = renamed(step["publish"], self.ids)
            record.update(by=step["by"], after=step["after"])
        for step in self.revisions:
            step["workstream"] = self.ids.get(step["workstream"], step["workstream"])
            after = step["after"].removeprefix("merged:")
            step["after"] = "merged:" + self.ids.get(after, after)

    def plan_leg(self) -> str:
        """Plan and approve the program; an empty string once approved, else the status it stopped at."""
        self.program("program-plan", "plan", self.scenario.brief, "--workspace", str(self.project), flags=True,
                     codes=(0, 2))
        runs = self.project / ".autocode" / "runs"
        found = sorted(runs.glob("*/state.json"), key=lambda path: path.stat().st_mtime) if runs.is_dir() else []
        if not found:
            raise DriveError("program plan created no plan run: " + self.steps[-1]["stderr_tail"].strip()[-500:])
        self.plan.run_dir = found[-1].parent
        try:
            view = self.plan.until_stopped(say_at="needs:approve_plan")
            if view["done"] or view["needs"]["kind"] != "approve_plan":
                return view["status"]  # the plan run stopped for a person: no program starts
            self.plan.serve(view["needs"])
            if not self.plan.view().get("approved_contract"):
                raise DriveError("the plan was approved but its status view reports no approved_contract")
        finally:
            self.plan_steps, self.plan_answers = len(self.steps), len(self.answers)
        return ""

    def show(self) -> str:
        proc = self.on_manifest("program-show", "show")
        with (self.root / "agreement.txt").open("a") as handle:
            handle.write(proc.stdout + "\n")
        found = TOKEN.search(proc.stdout)
        if not found:
            raise DriveError("program show printed no approval token")
        self.shown.append(found.group(1))
        return found.group(1)

    def run_loop(self) -> str:
        last = None
        while True:
            proc = self.on_manifest("program-run", "run", "--max-parallel", str(self.scenario.program_max_parallel),
                                    flags=True, codes=(0, 2))
            summary = self.summary = read_summary(proc)
            status = summary["status"]
            if status in FINAL or status.startswith("PAUSED_"):
                return status
            if status == "WAITING_AGREEMENT_APPROVAL":
                try:
                    pending = summary["agreement"]["pending"]["token"]
                except (KeyError, TypeError):
                    raise DriveError("program run waits for agreement approval but names no pending token: "
                                     + tail(proc)) from None
                self.approve(pending)
                continue
            if self.raise_due_change(summary) or self.decide_changes(summary) or self.revise_due_scope(summary) or self.serve_workstreams(summary):
                last = None
                continue
            rows = summary["workstreams"]
            if status != "RUNNING" and not any(row.get("run_status") == "RUNNING" and not row.get("blocked_reason")
                                               for row in rows):
                return status  # waiting for a person: nothing here the driver may answer
            # A workstream run waits only for the program to advance it, as it does once per pass.
            now = json.dumps([[row["id"], row["status"], row.get("run_status"), row.get("progress")] for row in rows])
            if now == last:
                raise DriveError(f"no progress at program status {status!r}")
            last = now

    def approve(self, pending: str | None = None) -> None:
        """Read the agreement and approve it by the exact token `program show` displays, once; ``pending`` is
        the token the program says it waits for (None before the first `program run`)."""
        shown = self.show()
        if pending is not None and shown != pending:
            raise DriveError(f"program show displays {shown}, but the program waits for {pending}")
        if shown in self.approved:
            raise DriveError(f"the program asked again for agreement revision {shown}, already approved")
        self.on_manifest("program-approve", "approve", "--token", shown)
        self.approved.append(shown)

    def raise_due_change(self, summary: dict) -> bool:
        """Raise the next scripted change request whose moment has come (``after = "merged:<id>"``)."""
        merged = {row["id"] for row in summary["workstreams"] if row["status"] == "MERGED"}
        for record, step in zip(self.changes, self.scripted):
            if record["request"] is None and step["after"].removeprefix("merged:") in merged:
                proc = self.on_manifest("program-request-change", "request-change", "--interface", step["interface"],
                                        "--by", step["by"], "--reason", step["reason"])
                try:
                    request = json.loads(proc.stdout)["change_request"]
                    record.update(request=request["id"], from_version=request["from_version"])
                except (ValueError, KeyError, TypeError):
                    raise DriveError("program request-change printed no change request: " + tail(proc)) from None
                return True
        return False

    def decide_changes(self, summary: dict) -> bool:
        """Decide a raised request once the program holds for it: reject it, or publish the new version."""
        if summary["status"] != "WAITING_CHANGE_REQUEST":
            return False
        open_ids = {row["id"] for row in summary.get("change_requests") or [] if row.get("status") == "open"}
        for record, step in zip(self.changes, self.scripted):
            if record["request"] not in open_ids or record["decided"]:
                continue
            if step["decide"] == "reject":
                self.on_manifest("program-resolve-change", "resolve-change", "--request", record["request"],
                                 "--reject", "--reason", step["resolution"])
            else:
                if step["publish"]["version"] != record["from_version"] + 1:
                    raise DriveError(f"[[program.change]] publishes {step['interface']} version "
                                     f"{step['publish']['version']}; the request is on version {record['from_version']}")
                manifest = self.read_manifest()
                row = next(row for row in manifest["shared"]["interfaces"] if row["id"] == step["interface"])
                row.update(json.loads(json.dumps(step["publish"])))
                self.write_manifest(manifest)  # the next pass waits for this revision's approval
            record["decided"] = True
            return True
        return False

    def serve_workstreams(self, summary: dict) -> bool:
        """Serve every waiting workstream's gate the way a cooperative person would; True if any was served."""
        served = False
        for row in summary["workstreams"]:
            if row["status"] not in ("WAITING", "PAUSED") or not row.get("run_dir") or row.get("blocked_reason"):
                continue
            child = WorkstreamDriver(row["id"], Path(row["run_dir"]), Path(row["workspace"]), self.root, [],
                                     self.child_env, autocode=self.autocode, max_steps=self.max_steps,
                                     timeout_seconds=self.deadline - time.monotonic(),
                                     explicit_answers=self.explicit_answers)
            child.steps, child.answers = self.steps, self.answers  # one budget and one record of answers
            view = child.view()
            need = view.get("needs")
            if view["done"] or not need or need["kind"] == "continue" or leaves_for_person(need):
                continue
            child.serve(need)
            served = True
        return served

    def read_manifest(self) -> dict:
        return json.loads(self.manifest.read_text())

    def revise_due_scope(self, summary):
        merged = {row["id"] for row in summary["workstreams"] if row["status"] == "MERGED"}
        for step in self.revisions:
            if not step["edited"] and step["after"].removeprefix("merged:") in merged:
                manifest = self.read_manifest()
                next(row for row in manifest["workstreams"] if row["id"] == step["workstream"])["brief"] = step["brief"]
                self.write_manifest(manifest)
                step["edited"] = True
                return True
        return False

    def write_manifest(self, manifest: dict) -> None:
        self.manifest.write_text(json.dumps(manifest, indent=2) + "\n")

    # --- evidence -----------------------------------------------------------

    def finish(self) -> dict:
        """Read the program's final summary (`program status`, outside the budget) and save the evidence."""
        if self.manifest.is_file():
            try:
                self.summary = read_summary(self.on_manifest("program-status", "status", budget=False))
            except DriveError:
                pass  # keep the last summary `program run` printed
        if self.summary:
            (self.root / "program-summary.json").write_text(json.dumps(self.summary, indent=2))
        for wid, runs in self.workstream_runs().items():
            for number, run in enumerate(runs, start=1):
                child = WorkstreamDriver(wid, Path(run["run_dir"]), Path(run["workspace"]), self.root, [], self.child_env,
                                         autocode=self.autocode, timeout_seconds=60, max_steps=1)
                try:
                    view = child.view()
                except DriveError as error:
                    view = {"unavailable": str(error)}
                self.child_views[run["run_dir"]] = view
                target = self.root / "workstreams" / wid / f"{number:02d}-view.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(view, indent=2))
        if self.plan.run_dir:
            try:
                self.plan_view = self.plan.view()
            except DriveError as error:
                self.plan_view = {"unavailable": str(error)}
            (self.root / "plan-view.json").write_text(json.dumps(self.plan_view, indent=2))
        return self.summary

    def product(self) -> Path:
        """Where the program delivered: its integration worktree, or the untouched project if it never started."""
        workspace = self.summary.get("integration_workspace")
        return Path(workspace) if workspace and Path(workspace).is_dir() else self.project

    def workstream_runs(self) -> dict[str, list[dict]]:
        """Every run each workstream had, retired ones first: {workstream: [{run_dir, retired}]}."""
        found = {}
        for row in self.summary.get("workstreams") or []:
            runs = [{"run_dir": item["run_dir"], "workspace": item.get("workspace") or row.get("workspace"),
                     "created_at": item.get("started_at"), "retired": True} for item in row.get("retired_runs") or []
                    if item.get("run_dir")]
            if row.get("run_dir"):
                runs.append({"run_dir": row["run_dir"], "workspace": row["workspace"], "created_at": row.get("started_at"), "retired": False})
            found[row["id"]] = runs
        return found

    def record(self) -> dict:
        """What an oracle may know about how the program went (harness.oracle.program_checks)."""
        plan_view = self.plan_view
        plan_state = _metric_input(plan_view)
        plan_steps = self.steps[:self.plan_steps]
        children, states = {}, []
        for wid, runs in self.workstream_runs().items():
            children[wid] = []
            for run in runs:
                view = self.child_views.get(run["run_dir"], {})
                state = _metric_input(view)
                states.append(state)
                numbers = metrics(state)
                children[wid].append({**view, **run, "created_at": run.get("created_at") or min(
                    (row["started_at"] for row in state["stages"] if row.get("started_at")), default=None),
                                      "stages": numbers["stage_names"],
                                      "model_stages": numbers["model_stage_names"]})
        program_state = {}
        if self.summary.get("state_file") and Path(self.summary["state_file"]).is_file():
            program_state = json.loads(Path(self.summary["state_file"]).read_text())
        interfaces, declared = [], {"program": [], "workstreams": {}}
        if self.manifest.is_file():
            manifest = self.read_manifest()
            interfaces = (manifest.get("shared") or {}).get("interfaces") or []
            declared = {"program": list(manifest.get("checks") or []),
                        "workstreams": {row.get("id"): list(row.get("checks") or [])
                                        for row in manifest.get("workstreams") or [] if row.get("checks")}}
        return {
            "status": self.summary.get("status") or plan_state.get("status", ""),
            "program": self.summary,
            "plan": {"status": plan_state.get("status", ""), "view": plan_view,
                     "stages": metrics(plan_state)["stage_names"], "model_stages": metrics(plan_state)["model_stage_names"],
                     "answers": self.answers[:self.plan_answers], "cli_calls": [step["kind"] for step in plan_steps],
                     "steps": [{"kind": step["kind"], "exit": step["exit"]} for step in plan_steps]},
            "children": children,
            "verifications": [{"workstream": row.get("workstream"), "verdict": row.get("verdict"), "at": row.get("at"),
                               "commands": [check.get("command") for check in row.get("checks") or []]}
                              for row in program_state.get("verifications") or []],
            "agreement": {"shown": list(self.shown), "approved": list(self.approved)},
            "workstream_ids": dict(self.ids),
            "interfaces": interfaces, "declared_checks": declared, "changes": [dict(record) for record in self.changes],
            "revisions": [dict(record) for record in self.revisions],
            "answers": self.answers, "cli_calls": [step["kind"] for step in self.steps],
            "steps": [{"kind": step["kind"], "exit": step["exit"]} for step in self.steps],
            # Every model stage of the plan run and of every workstream run, for [run] requires_stages.
            "model_stages": [*metrics(plan_state)["model_stage_names"],
                             *(name for state in states for name in metrics(state)["model_stage_names"])],
            "metrics": {**metrics({"stages": [stage for state in (plan_state, *states)
                                              for stage in state.get("stages") or []]}),
                        "runs": 1 + len(states)},
        }


def _metric_input(view):
    attempts = (view.get("usage", {}).get("accounting") or {}).get("attempts") or []
    return {"status": view.get("status", ""), "stages": [{**row, "metrics": {"provider_tokens": row.get("tokens")}} for row in attempts]}
