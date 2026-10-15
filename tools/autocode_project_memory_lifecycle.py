"""Current-run provenance for advisory project memory, never goal authority.

Only this module writes state.project_memory. Launch supplies a frozen store
snapshot; accepted Requirements reports tag question IDs. Completion joins them
to this run's actual issued human answers without reading another run's files.
"""

from __future__ import annotations

import copy
import hashlib
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_go_tests as go_tests
    from . import autocode_node_tests as node_tests
    from . import autocode_project_memory as store
    from . import autocode_python_tests as python_tests
    from . import autocode_util as util
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_go_tests as go_tests
    import autocode_node_tests as node_tests
    import autocode_project_memory as store
    import autocode_python_tests as python_tests
    import autocode_util as util

KEY = "project_memory"
REUSABLE_CATEGORIES = frozenset({"technical", "behavior", "other"})
MAX_QUESTIONS = 128


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _snapshot(value):
    value = _mapping(value)
    status = value.get("status")
    if status == "loaded":
        try:
            record = store.sanitize_record(copy.deepcopy(value.get("record")))
        except (TypeError, ValueError):
            status = "invalid"
        else:
            pin = value.get("sha256")
            return {"status": "loaded", "record": record, "sha256": pin if isinstance(pin, str) else None, "note": ""}
    return {
        "status": "invalid" if status == "invalid" else "absent",
        "record": None,
        "sha256": None,
        "note": "Project memory was invalid at launch." if status == "invalid" else "",
    }


def install(state: RunState, snapshot) -> None:
    """Install once at fresh launch; resumes never reload or replace the input."""
    if KEY not in state:
        state["project_memory"] = {
            "version": 1,
            "input": _snapshot(snapshot),
            "questions": [],
            "tagged_questions": [],
        }


def context(state: RunState) -> dict:
    """Historical Requirements data, outside current answers and source IDs."""
    return copy.deepcopy(store.context(_mapping(state.get(KEY)).get("input")))


def _questions(report):
    report = _mapping(report)
    rows = report.get("open_questions")
    if not isinstance(rows, list):
        rows = _mapping(report.get("requirements")).get("open_blocking_questions")
    return [copy.deepcopy(row) for row in _rows(rows) if isinstance(row.get("id"), str)]


def capture(state: RunState, report) -> None:
    """Bind an accepted Requirements tag to its question, even before an answer.

    A tag is model data. Publication still needs the exact issued clarification,
    saved human answer event and live source hash. Legacy runs import nothing.
    """
    memory = state.get(KEY)
    if not isinstance(memory, dict):
        return
    tags = _mapping(report).get("project_conventions", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("project_conventions must contain Requirements question IDs")
    known = _rows(memory.get("questions"))
    reports = [*_rows(state.get("requirements_history")), _mapping(state.get("requirements_handoff"))]
    for item in reports:
        known.extend(_questions(item.get("report")))
    known.extend(_questions(report))
    known = list({util.digest(question): question for question in known}.values())[-MAX_QUESTIONS:]
    if set(tags) - {question.get("id") for question in known}:
        raise ValueError("project_conventions names a question Requirements did not ask")
    tagged = [*_rows(memory.get("tagged_questions")), *(question for question in known if question.get("id") in tags)]
    memory["questions"] = known
    memory["tagged_questions"] = list({util.digest(question): question for question in tagged}.values())[
        -MAX_QUESTIONS:
    ]


def _issued(state: RunState):
    ledger = _mapping(_mapping(state.get("resolver")).get("human_escalations"))
    for key, entry in ledger.items():
        entry = _mapping(entry)
        identity = _mapping(entry.get("identity"))
        if (
            not identity
            or key != util.digest(identity)
            or entry.get("receipt_hash") != key
            or not entry.get("issued_at")
        ):
            continue
        proposal = _mapping(identity.get("proposal"))
        if proposal:
            yield proposal


def _conventions(state: RunState):
    tagged = _rows(_mapping(state.get(KEY)).get("tagged_questions"))
    answers, events = _mapping(state.get("answers")), _rows(state.get("user_events"))
    sources = _rows(state.get("source_log"))
    issued = [
        question
        for proposal in _issued(state)
        if proposal.get("scope") == "clarification"
        for question in _rows(proposal.get("questions"))
    ]
    result = {}
    for question in tagged:
        if question.get("kind") != "decision" or question.get("category") not in REUSABLE_CATEGORIES:
            continue
        ident = question.get("id")
        event = answers.get(ident)
        if (
            not isinstance(event, dict)
            or event not in events
            or event.get("kind") != "answer"
            or event.get("actor") != "user_cli"
            or event.get("question_id") != ident
            or event.get("question") != question
            or question not in issued
            or not isinstance(event.get("text"), str)
            or not event["text"].strip()
        ):
            continue
        text = event["text"]
        expected = hashlib.sha256(text.encode()).hexdigest()
        if not any(
            source.get("id") == "answer:" + ident
            and source.get("kind") == "user_answer"
            and source.get("text") == text
            and source.get("sha256") == expected
            for source in sources
        ):
            continue
        prompt = question.get("question")
        if (
            isinstance(prompt, str)
            and prompt.strip()
            and len(prompt) <= store.MAX_QUESTION
            and len(text) <= store.MAX_ANSWER
        ):
            result[prompt] = {"question": prompt, "answer": text}
    return list(result.values())[-store.MAX_CONVENTIONS :]


def _routes(state: RunState):
    result = {}
    for row in _rows(state.get("stages")):
        if row.get("runner_owned") or not row.get("started_at"):
            continue
        route = _mapping(row.get("launch_route"))
        role = row.get("route_role") or row.get("role") or row.get("stage")
        if (
            not isinstance(role, str)
            or len(role) > store.MAX_ROUTE_VALUE
            or re.fullmatch(r"[a-z][a-z0-9_]*", role) is None
        ):
            continue
        values = {name: route.get(name) or row.get(name) for name in ("engine", "provider", "model")}
        values = {name: value if isinstance(value, str) and value else None for name, value in values.items()}
        if any(values.values()) and all(
            value is None or len(value) <= store.MAX_ROUTE_VALUE for value in values.values()
        ):
            result[role] = {"role": role, **values}
    return list(result.values())[-store.MAX_ROUTES :]


def capture_suite(state: RunState, framework, protected) -> None:
    """Bind the existing fresh-launch discovery, without performing discovery here."""
    memory, framework, protected = state.get(KEY), _mapping(framework), _mapping(protected)
    if (
        not isinstance(memory, dict)
        or memory.get("version") != 1
        or "input" not in memory
        or "detected_suite" in memory
        or not framework.get("suite")
        or framework.get("suite") != protected.get("command")
        or not isinstance(protected.get("binding_hash"), str)
        or re.fullmatch("[0-9a-f]{64}", protected["binding_hash"]) is None
    ):
        return
    memory["detected_suite"] = {
        "framework": {key: framework.get(key) for key in ("name", "python", "suite")},
        "binding_hash": protected["binding_hash"],
    }


def _bound_suite(state: RunState):
    observed = _mapping(_mapping(state.get(KEY)).get("detected_suite"))
    framework = _mapping(observed.get("framework"))
    protected = _mapping(_mapping(state.get("settings")).get("protected_tests"))
    if (
        not observed.get("binding_hash")
        or observed["binding_hash"] != protected.get("binding_hash")
        or not framework.get("suite")
        or framework["suite"] != protected.get("command")
    ):
        return {}
    return framework


def _observed_framework(state: RunState):
    replay = _mapping(_mapping(state.get("validation")).get("check_replay"))
    if replay.get("verdict") != "PASS" or replay.get("simulated"):
        return None
    rows = _rows(replay.get("checks"))
    protected = _mapping(replay.get("protected_tests"))
    if protected.get("verdict") == "PASS" and isinstance(protected.get("candidate"), dict):
        rows = [*rows, protected["candidate"]]
    families = set()
    for row in rows:
        if (
            type(row.get("exit_code")) is not int
            or row["exit_code"] != 0
            or row.get("timed_out")
            or row.get("error")
            or not all(key in row for key in command_receipt.OWNERSHIP_FIELDS)
            or not command_receipt.completed(row)
        ):
            continue
        command = row.get("command")
        python = python_tests.parse(python_tests.verbose_unittest(command))
        if python:
            families.add(python.kind)
        elif go_tests.parse(command):
            families.add("go")
        elif node_tests.parse(command):
            families.add("node")
    return next(iter(families)) if len(families) == 1 else None


def _test_command(state: RunState, proof, framework):
    configured = _mapping(_mapping(state.get("settings")).get("regression")).get("test_command")
    if configured:
        return configured  # Keep the operator's exact portable spelling; the store screens it.
    protected = _mapping(_mapping(state.get("settings")).get("protected_tests"))
    commands = _mapping(proof.get("commands"))
    command = protected.get("command") or commands.get("suite")
    if protected.get("command"):
        framework = _bound_suite(state)
        if not framework:
            return command  # Retained explicit/custom spelling is never guessed or rewritten.
        commands = {"suite_source": "detected:" + str(framework.get("name"))}
    if not isinstance(command, str) or any(char in command for char in "\n\r;&|<>`$()"):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    name = _mapping(framework).get("name")
    if name in ("unittest", "pytest"):
        # Only the runner's recorded generated suite may lose its local Python
        # executable path. Custom commands, wrappers and other private paths
        # remain subject to the store's unchanged portable-string policy.
        if (
            len(words) < 3
            or words[0] != framework.get("python")
            or words[1:3] != ["-m", name]
            or framework.get("suite") != command
            or commands.get("suite_source") != "detected:" + name
        ):
            return None
        return shlex.join(["python3", *words[1:]])
    return command


def _record(state: RunState):
    proof = _mapping(state.get("regression_proof"))
    framework = _mapping(proof.get("execution_context")).get("detected_framework") or proof.get("framework")
    command = _test_command(state, proof, framework)
    framework = framework.get("name") if isinstance(framework, dict) else framework
    framework = framework or _bound_suite(state).get("name") or _observed_framework(state)
    pauses = set()
    for proposal in _issued(state):
        for value in (proposal.get("status"), _mapping(proposal.get("origin")).get("pause_status")):
            if store.valid_pause_state(value):
                pauses.add(value)
    return store.sanitize_record(
        {
            "version": 1,
            "framework": framework
            if isinstance(framework, str) and framework.strip() and len(framework) <= store.MAX_FRAMEWORK
            else None,
            "test_command": command
            if isinstance(command, str) and command.strip() and len(command) <= store.MAX_COMMAND
            else None,
            "conventions": _conventions(state),
            "routes": _routes(state),
            "human_pause_states": sorted(pauses)[: store.MAX_PAUSE_STATES],
        }
    )


def publish(path, state: RunState) -> bool:
    """Publish current successful parent-run facts; report diagnostic changes.

    The loaded input remains frozen. Only this run's observations reach write;
    no snapshot can resurrect forgotten memory. Failure cannot pause completion.
    """
    memory = state.get(KEY)
    if (
        not isinstance(memory, dict)
        or state.get("status") != "TASK_COMPLETE"
        or state.get("parent_run")
        or state.get("active_stage")
        or state.get("uncertain_artifacts")
        or Path(path).name != "state.json"
    ):
        return False
    previous = memory.get("publication")
    try:
        record = _record(state)
        pin = util.digest(record)
        previous_record = _mapping(previous)
        if previous_record.get("status") == "written" and previous_record.get("input_sha256") == pin:
            return False
        root = state.get("project_workspace") or state.get("workspace")
        if not isinstance(root, str) or not root:
            raise ValueError("No selected project")
        written = store.write(Path(root), record)
        diagnostic = {"status": "written", "input_sha256": pin, "record_sha256": util.digest(written)}
    except (OSError, TypeError, ValueError, KeyError):
        diagnostic = {
            "status": "unavailable",
            "note": "Project memory could not be published; task completion is unchanged.",
        }
    if previous == diagnostic:
        return False
    memory["publication"] = diagnostic
    return True
