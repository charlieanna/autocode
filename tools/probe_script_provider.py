#!/usr/bin/env python3
"""The scripted stages of a Tester or Completion Reviewer probe (#3630): a tool that never calls a model.

`autocode probe --stage tester` (or ``completion``) measures one checking stage on changes with a known
answer. Every other stage of the probe's run is played by this script through the bundled
``probe_script`` provider config (``--role-transport ROLE=probe_script``): Requirements and the Planner
write the greeting plan of ``live_fixture_provider``, and the Builder writes the files the probe chose
for the request, a sound change or one with a planted flaw. The checking stage the probe does not
measure passes, as that fixture's does, so only the measured stage decides whether a change goes back.

The probe writes its script, a JSON file, and names it in ``AUTOCODE_PROBE_SCRIPT``:

- ``builds``: one entry per Builder launch, ``{"files": {path: text}, "reasons": {line: reason}}``; a
  launch past the last entry writes the last one again. ``reasons`` are the scripted Tester's own
  reasons for the changed lines that have no mutation site, keyed by the stripped line, as
  ``live_fixture_provider._changed_code_reasons`` gives them for its one greeting.
- ``log``: a file this script appends one JSON line to per launch (``stage``, ``model``, ``build``),
  which the probe reads to see how many times the Builder was sent back.

It prints OpenCode's JSON events, as the command providers AutoCode reads (``output = "opencode_events"``).
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

# One name per job, so no scripted stage checks a model of its own name (autocode_verifier_independence).
MODELS = {
    "requirements": "probe-script-plan",
    "planner": "probe-script-plan",
    "task_planner": "probe-script-plan",
    "plan_reviewer": "probe-script-review",
    "builder": "probe-script-build",
    "tester": "probe-script-check",
    "completion": "probe-script-check",
}
SCRIPT_ENV = "AUTOCODE_PROBE_SCRIPT"
VERSION = "1.0.0"


def _fixture():
    spec = importlib.util.spec_from_file_location(
        "probe_script_fixture", Path(__file__).with_name("live_fixture_provider.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _launches(log: Path, stage: str) -> int:
    if not log.is_file():
        return 0
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sum(1 for row in rows if row.get("stage") == stage)


def _script_build(fixture, build: dict) -> None:
    """Make the fixture's Builder write ``build``'s files and its Tester give ``build``'s reasons."""
    files = {str(path): str(text) for path, text in (build.get("files") or {}).items()}
    reasons = {str(line): str(reason) for line, reason in (build.get("reasons") or {}).items()}

    def write_files():
        for path, text in files.items():
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return sorted(files)

    def changed_code_reasons(data):
        data = fixture._review_data(data)
        if data is None:
            return []
        proof = data.get("regression_proof") or {}
        if not proof.get("path"):
            return []
        result = []
        for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
            path, line = row.get("path"), row.get("line")
            if row.get("status") != "reason_required" or path not in files or row.get("mutation_outcomes"):
                continue
            product = files[path].encode("utf-8")
            source = files[path].splitlines()
            if row.get("sha256") != hashlib.sha256(product).hexdigest() or type(line) is not int:
                continue
            if not 0 < line <= len(source) or Path(path).read_bytes() != product:
                continue
            reason = reasons.get(source[line - 1].strip())
            if reason:
                result.append(
                    {
                        "gap_id": row["gap_id"],
                        "decision": "accept",
                        "reason_kind": "instrumentation_unavailable",
                        "reason": reason,
                        "evidence_refs": [proof["path"], path],
                    }
                )
        return result

    fixture._write_files = write_files
    fixture._changed_code_reasons = changed_code_reasons


def _follow_findings(stage: str, report: dict, data: dict, paths=()) -> dict:
    """A scripted Completion Reviewer, and the Resolver after it, send the change back when it failed checks.

    In a Tester probe the measured Tester's verdict decides (``validation.verdict``), with the runner's own
    blocking findings; this script adds no judgment of its own, so it neither completes over them nor
    finds anything they did not. The report follows ``scenarios/harness/completion_rework_provider.py``.
    """
    blocking = [row for row in data.get("unresolved_findings") or [] if isinstance(row, dict) and row.get("blocking")]
    verdict = (data.get("validation") or {}).get("verdict")
    # The Resolver is only asked about a review that sent the work back.
    if stage != "resolve" and verdict != "FAIL" and not blocking:
        return report
    return rework(stage, report, data, paths)


def rework(stage: str, report: dict, data: dict, paths=()) -> dict:
    """A Completion Reviewer's (or Resolver's) REWORK of the current task: the Builder corrects it.

    It names the files the task writes: the task's, else its milestone's, else ``paths``, the files the
    script's Builder writes."""
    task = data.get("current_task") or {}
    body = (data.get("goal_contract") or {}).get("body") or {}
    milestone = task.get("milestone_id") or "M1"
    owned: list = next(
        (row.get("affected_paths") for row in body.get("milestones") or [] if row.get("id") == milestone), []
    )
    objective = "Address the findings the checks reported, keeping the approved plan"
    result = {
        **report,
        "status": "REWORK",
        "acceptance_criteria": [
            {**row, "status": "unverified", "evidence": "event:check"}
            for row in report.get("acceptance_criteria") or []
        ],
        "evidence": ["event:check"],
        "next_objective": objective,
        "blocker": "",
        "plan": [objective],
        "affected_paths": list(task.get("affected_paths") or owned or paths),
        "findings": [],
        "finding_dispositions": [],
        "next_task": {
            "kind": "implement",
            "milestone_id": milestone,
            "requirements": list(task.get("requirements") or ["Print Hello, NAME for one nonempty name"]),
            "acceptance_criteria": list(task.get("acceptance_criteria") or ["C1"]),
            "validation_plan": list(task.get("validation_plan") or ["Run the greeting tests"]),
            "findings": [],
        },
    }
    failure = data.get("builder_failure") or (data.get("investigation_context") or {}).get("builder_failure") or {}
    if failure.get("failure_id"):
        result.update(
            failure_id=failure["failure_id"],
            evidence_refs=list(failure.get("evidence_refs") or []),
            failure_class="execution",
            cause="stage_output",
            example=objective,
            probe="",
            untestable="A scripted probe stage runs no command that could show more than the checks' findings.",
        )
    if stage == "resolve":
        result["diagnosis"] = "The checks did not pass; the Builder addresses their findings within the approved plan"
    return result


def _investigate(report: dict, data: dict) -> dict:
    """A scripted Investigator: a change the checks sent back is the Builder's to correct, so retry it.

    It answers only the classification a send-back asks for (``builder_failure``); any other stop it
    leaves paused, as ``live_fixture_provider`` does, so a probe run never hides a stop of AutoCode's own.
    """
    report = {**report, "counterexample": {"file": "", "find": "", "replace": ""}, "project_notes": []}
    failure = data.get("builder_failure") or {}
    if not failure.get("failure_id"):
        return report
    return {
        **report,
        "diagnosis": "The checks sent the change back; the Builder corrects it within the approved plan.",
        "cause": "stage_output",
        "guidance": "Address the findings the checks reported, keeping the approved plan.",
        "recommendation": "retry",
        "user_question": "",
        "evidence_refs": list(failure.get("evidence_refs") or [])[:1],
        "example": "The checks reported a finding the Builder's change does not meet.",
        "probe": "",
        "untestable": "A scripted probe stage runs no command that could show more than the checks' findings.",
        "failure_class": "execution",
        "failure_id": failure["failure_id"],
    }


def _logged(stage: str, model: str, build_index: int, data: dict) -> dict:
    """One launch, as the probe reads it: a Builder launch names the build it wrote and which stages'
    blocking findings were open when it was launched, the stages that sent the previous build back."""
    row: dict = {"stage": stage, "model": model, "build": None}
    if stage == "builder":
        row["build"] = build_index
        row["sent_back_by"] = sorted(
            {
                str(finding.get("source") or "")
                for finding in data.get("open_findings") or []
                if isinstance(finding, dict) and finding.get("blocking", True)
            }
        )
    return row


def _prompt_schema(text: str) -> dict:
    """OpenCode-style tools carry the output schema in the prompt: a JSON line before the handoff data."""
    head = text.split("\nCURRENT HANDOFF DATA\n", 1)[0]
    if "\nTOOL OUTPUT CONTRACT\n" in head:
        contract = head.rsplit("\nTOOL OUTPUT CONTRACT\n", 1)[1]
        return json.JSONDecoder().raw_decode(contract[contract.index("\n{") + 1 :])[0]
    last = (head.rstrip().splitlines() or [""])[-1]
    return json.loads(last) if last.startswith("{") else {}


def main(argv=None, *, models=None, judge=None) -> int:
    """Play one stage launch. ``models`` and ``judge`` let a test play a measured model on this script:
    the names it answers to, and ``judge(stage, report, data)`` returning the report it gives instead."""
    argv = sys.argv[1:] if argv is None else argv
    offered = sorted(set(models or MODELS.values()))
    if argv == ["--version"]:
        print(VERSION)
        return 0
    if argv == ["models"]:
        print("\n".join(offered))
        return 0
    script_path = os.environ.get(SCRIPT_ENV)
    if not script_path:
        print(f"{SCRIPT_ENV} names no probe script: this tool runs only inside `autocode probe`", file=sys.stderr)
        return 2
    script = json.loads(Path(script_path).read_text(encoding="utf-8"))
    log = Path(script["log"])
    prompt = sys.stdin.read()
    if "CURRENT HANDOFF DATA\n" not in prompt:
        print("no handoff data", file=sys.stderr)
        return 2
    data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
    stage = data.get("stage") or (data.get("original") or {}).get("stage") or ""
    model = argv[argv.index("--model") + 1] if "--model" in argv else ""
    if model not in offered:
        print(f"this tool offers only {', '.join(offered)}, not {model}", file=sys.stderr)
        return 9
    fixture = _fixture()
    builds = script.get("builds") or [{}]
    build_index = min(_launches(log, "builder"), len(builds) - 1)
    # The files the Builder last wrote (or writes now) are the ones the scripted Tester explains.
    _script_build(fixture, builds[build_index if stage == "builder" else max(_launches(log, "builder") - 1, 0)])
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(_logged(stage, model, build_index, data)))
        stream.write("\n")
    session_arg = argv[argv.index("--session") + 1] if "--session" in argv else None
    session = session_arg or f"probe_{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="probe-script-") as temporary:
        output = Path(argv[argv.index("--report") + 1]) if "--report" in argv else Path(temporary) / "report.json"
        schema = Path(temporary) / "schema.json"
        schema.write_text(json.dumps(_prompt_schema(prompt)), encoding="utf-8")
        saved_argv, saved_stdin = sys.argv, sys.stdin
        sys.argv = ["fixture", "-o", str(output), "--output-schema", str(schema)]
        sys.argv += ["resume", session] if session_arg else []
        captured = io.StringIO()
        try:
            sys.stdin = io.StringIO(prompt)
            with contextlib.redirect_stdout(captured):
                code = fixture.main()
        finally:
            sys.argv, sys.stdin = saved_argv, saved_stdin
        if code:
            return code
        report = json.loads(output.read_text(encoding="utf-8"))
        if judge is None and stage in ("review_completion", "resolve"):
            report = _follow_findings(stage, report, data, sorted(builds[-1].get("files") or {}))
        if judge is None and stage == "investigate_stuck":
            report = _investigate(report, data)
        if judge is not None:
            report = judge(stage, report, data)
            output.write_text(json.dumps(report), encoding="utf-8")
    commands = [
        json.loads(line)["item"]
        for line in captured.getvalue().splitlines()
        if line.strip() and json.loads(line).get("type") == "item.completed"
    ]
    rows = [{"type": "step_start", "sessionID": session, "part": {"id": "start", "messageID": "message"}}]
    for item in commands:
        rows.append(
            {
                "type": "tool_use",
                "sessionID": session,
                "part": {
                    "id": item["id"],
                    "messageID": "message",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": item["command"]},
                        "metadata": {"exit": item["exit_code"]},
                        "output": item["aggregated_output"],
                    },
                },
            }
        )
    rows += [
        {
            "type": "text",
            "sessionID": session,
            "part": {"id": "report", "messageID": "message", "text": json.dumps(report)},
        },
        {
            "type": "step_finish",
            "sessionID": session,
            "part": {
                "id": "finish",
                "messageID": "message",
                "reason": "stop",
                "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            },
        },
    ]
    for row in rows:
        print(json.dumps(row), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
