"""Owned event emitter for saved recordings and black-box scripted fixtures.

Recorded data never chooses a program or plugin. The only launched program is
this module under the current interpreter; normal runner supervision still owns
its stdin, immutable event sink, deadline, collected exit and process cleanup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import copy
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

try:
    from . import autocode_replay_bindings as identity_bindings
    from . import autocode_replay_recording as recording
    from . import autocode_util as util
    from .providers import opencode as native
except ImportError:
    import autocode_replay_bindings as identity_bindings
    import autocode_replay_recording as recording
    import autocode_util as util
    from providers import opencode as native


CONTEXT_KEYS = ("stage", "iteration", "turn", "milestone_id", "report_only", "original_stage")


def selected(settings):
    value = (settings or {}).get("replay_recording")
    if (
        not isinstance(value, dict)
        or set(value) != {"path", "sha256", "checks", "dispatch_ids"}
        or value["checks"] not in ("fresh", "recorded")
    ):
        raise recording.RecordingError("Replay recording authority is missing or malformed")
    return recording.load(value["path"], value["sha256"])


def configure(args, state: RunState):
    saved = (state.get("settings") or {}).get("replay_recording")
    path = getattr(args, "replay_recording", None)
    if not path and not saved:
        return None
    tape = recording.load(path or cast(dict, saved)["path"], saved["sha256"] if saved else None)
    checks = saved["checks"] if saved else getattr(args, "replay_checks", "fresh")
    if saved and path and str(tape.manifest_path) != saved["path"]:
        raise recording.RecordingError("A saved replay cannot change its recording")
    if state.get("task") != tape.document["launch"]["task"]:
        raise recording.RecordingError("Replay brief differs from its recording")
    settings = copy.deepcopy(tape.document["launch"]["settings"])
    settings.update(
        replay_recording={
            "path": str(tape.manifest_path),
            "sha256": tape.sha256,
            "checks": checks,
            "dispatch_ids": [row["id"] for row in tape.document["dispatches"]],
        },
        evidence_provenance={
            "kind": "fake",
            "basis": "recorded_observations",
            "declaration": "Saved-run replay; no live inference",
        },
        transport_identity={"engine": "replay", "manifest_sha256": tape.sha256},
    )
    if state.get("settings"):
        if state["settings"].get("replay_recording") != settings["replay_recording"]:
            raise recording.RecordingError("A saved replay cannot change its check policy")
        return state["settings"]
    return settings


def consumed(state: RunState):
    return len([row for row in state.get("stages", []) if isinstance(row.get("replay"), dict)])


def progress(state: RunState):
    authority = (state.get("settings") or {}).get("replay_recording")
    if not authority:
        return None
    tape = selected(state["settings"])
    count = consumed(state)
    return {
        "manifest_sha256": tape.sha256,
        "checks": authority["checks"],
        "consumed": count,
        "next_dispatch": tape.document["dispatches"][count]["id"]
        if count < len(tape.document["dispatches"])
        else "end",
    }


class Facade:
    CONFIGURED = True
    DEFAULT_MODELS = native.DEFAULT_MODELS
    DEFAULT_REASONING_EFFORTS = native.DEFAULT_REASONING_EFFORTS

    def __init__(self, settings, index=0):
        self.recording = selected(settings)
        rows = self.recording.document["dispatches"]
        row = rows[index] if index < len(rows) else {}
        self.OUTPUT = row.get("output_mode", "opencode_events")
        self.SUPPORTS_SESSIONS = row.get("supports_sessions", True)

    def check_models(self, roles, workspace=None, *, env=None, inventory=None):
        """Authenticate recorded routes without querying a live provider roster."""
        saved = self.recording.verified().document["launch"]["settings"].get("roles", {})
        if not isinstance(roles, dict) or any(
            role not in saved or config != saved[role] for role, config in roles.items()
        ):
            raise recording.RecordingError("Replay model routes differ from their recording")

    @staticmethod
    def prompt_for_schema(prompt, schema, events):
        return native.prompt_for_schema(prompt, schema, events)

    def final_report(self, path, **kwargs):
        if self.OUTPUT == "report_file":
            # Report-file routes have no native final-message contract.
            return util.read(Path(path).with_suffix(".json"))
        return native.final_report(path, **kwargs)


def facade(args, state: RunState):
    settings = state.get("settings") or {}
    if not settings.get("replay_recording") and getattr(args, "replay_recording", None):
        tape = recording.load(args.replay_recording)
        settings = {
            "replay_recording": {
                "path": str(tape.manifest_path),
                "sha256": tape.sha256,
                "checks": getattr(args, "replay_checks", "fresh"),
                "dispatch_ids": [row["id"] for row in tape.document["dispatches"]],
            }
        }
    return Facade(settings, consumed(state))


def prepare(
    *,
    settings,
    engine,
    adapter,
    role,
    route_role,
    workspace,
    run_dir,
    session,
    model,
    effort,
    report,
    schema,
    prompt_file,
    allow_write,
    context,
    **unused,
):
    tape = selected(settings)
    index = context.get("consumed")
    if type(index) is not int or not 0 <= index < len(tape.document["dispatches"]):
        raise recording.RecordingError("Replay diverged: a new stage has no recorded dispatch")
    row = tape.document["dispatches"][index]
    for key in CONTEXT_KEYS:
        if row[key] != context.get(key):
            raise recording.RecordingError("Replay diverged at dispatch " + row["id"] + ": " + key + " differs")
    for key, actual in (
        ("role", role),
        ("route_role", route_role),
        ("engine", engine),
        ("model", model),
        ("reasoning_effort", effort),
        ("provider", unused.get("provider")),
    ):
        if row[key] != actual:
            raise recording.RecordingError("Replay diverged at dispatch " + row["id"] + ": " + key + " differs")
    lineage = row["session"]
    if not isinstance(lineage, dict) or set(lineage) != {"kind", "id"} or lineage["kind"] not in ("fresh", "resume"):
        raise recording.RecordingError("Recording session lineage is malformed")
    if (lineage["kind"] == "resume") != (session is not None):
        raise recording.RecordingError("Replay diverged: session continuation differs")
    current = recording.verify_source(tape, row["before"], workspace)
    if tape.document["snapshots"][row["before"]] != tape.document["snapshots"][row["after"]] and not allow_write:
        raise recording.RecordingError("Recording has source writes in a read-only dispatch")
    symbols = {
        "workspace": str(Path(workspace)),
        "run_dir": str(Path(run_dir)),
        "output": str(report),
        "events": str(Path(report).with_suffix(".jsonl")),
        "session": session or "replay-" + uuid.uuid4().hex,
        "source_revision": current,
        "task_id": context.get("task_id"),
        "contract_hash": context.get("contract_hash"),
        "capture_nonce": None,
        "changed_code_gap": context.get("changed_code_proof"),
        "regression_proof": context.get("regression_proof_path"),
    }
    job = {
        "version": 1,
        "manifest": str(tape.manifest_path),
        "sha256": tape.sha256,
        "dispatch": row["id"],
        "workspace": str(Path(workspace)),
        "report": str(report),
        "schema": str(schema),
        "symbols": symbols,
        "context": context,
    }
    data = recording._bytes(job)
    if len(data) > recording.MAX_ARTIFACT_BYTES:
        raise recording.RecordingError("Replay dispatch description exceeds its bound")
    target = recording._root(run_dir) / "replay-transport" / (row["id"] + ".json")
    target.parent.mkdir(exist_ok=True)
    parent = recording._root(target.parent)
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
    except FileExistsError:
        raise recording.RecordingError("Replay dispatch was already prepared") from None
    finally:
        os.close(directory)
    runtime = str(Path(__file__).resolve().parent)
    bootstrap = "import sys;sys.path.insert(0,sys.argv.pop(1));import autocode_replay_transport as r;sys.exit(r.main(sys.argv[1:]))"
    command = [sys.executable, "-I", "-c", bootstrap, runtime, "emit", str(target), hashlib.sha256(data).hexdigest()]
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONNOUSERSITE": "1"}
    worker = {
        "engine": engine,
        "provider": row["provider"],
        "configured": True,
        "role": role,
        "planning": False,
        "model": model,
        "command": command,
        "environment": environment,
        "provider_session": session,
        "replay": {
            "manifest_sha256": tape.sha256,
            "dispatch_id": row["id"],
            "ordinal": index,
            **({"archived_prompts_hash": row["prompts_hash"]} if "prompts_hash" in row else {}),
        },
        "output_mode": row["output_mode"],
        "supports_sessions": row["supports_sessions"],
    }
    adapter.OUTPUT, adapter.SUPPORTS_SESSIONS = row["output_mode"], row["supports_sessions"]
    return command, environment, None, worker


def emit_events(events, report, output, *, exit_code=0):
    """One shared emission mechanism: caller owns scripted generation, not transport."""
    if report is not None:
        path = Path(output)
        parent = recording._root(path.parent)
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            # Exclusive, no-follow creation also rejects dangling symlinks and
            # the former exists/write race. The held parent owns the write.
            descriptor = os.open(
                path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(recording._bytes(report))
        except FileExistsError:
            raise recording.RecordingError("Replay refuses to overwrite a prior report") from None
        finally:
            os.close(directory)
    for event in events:
        print(json.dumps(event, ensure_ascii=False), flush=True)
    if type(exit_code) is not int:
        raise recording.RecordingError("Replay cannot fabricate an unknown collected exit")
    return exit_code


def _events(data):
    return [recording._json(line) for line in data.splitlines() if line.strip()]


def emit(job):
    tape = recording.load(job["manifest"], job["sha256"])
    row = next((row for row in tape.document["dispatches"] if row["id"] == job["dispatch"]), None)
    if row is None:
        raise recording.RecordingError("Replay dispatch is missing")
    if row["stopped"] != "normal" or type(row["exit_code"]) is not int:
        raise recording.RecordingError("Recording stopped before a trustworthy collected exit")
    symbols = dict(job["symbols"])
    capture = os.environ.get("AUTOCODE_CAPTURE_CONTEXT")
    if capture:
        context = recording._json(capture)
        symbols["capture_nonce"] = context["nonce"]
    payload = {
        "events": _events(tape.artifact(row["events"])),
        "report": recording._json(tape.artifact(row["report"])) if row["report"] else None,
        "response": recording._json(tape.artifact(row["response"])) if row["response"] else None,
    }
    original_report = copy.deepcopy(payload["report"])
    payload = recording.rebind(payload, row["bindings"], symbols)
    identity_bindings.final_message(payload["events"], original_report, payload["report"])
    recording.verify_source(tape, row["before"], job["workspace"])
    recording.materialize(tape, row["after"], job["workspace"])
    recording.verify_source(tape, row["after"], job["workspace"])
    return emit_events(payload["events"], payload["report"], job["report"], exit_code=row["exit_code"])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) != 3 or argv[0] != "emit":
            raise recording.RecordingError("Replay emitter requires an owned dispatch description")
        recording._hash(argv[2])
        path = Path(argv[1])
        data = recording._read(path.parent, path.name)[0]
        if hashlib.sha256(data).hexdigest() != argv[2]:
            raise recording.RecordingError("Replay dispatch description changed after admission")
        job = recording._json(data)
        _ = sys.stdin.read()  # Preserve runner stdin/EOF supervision without model execution.
        return emit(job)
    except (recording.RecordingError, OSError, KeyError, TypeError, ValueError):
        print("Replay transport stopped: recording divergence or unavailable evidence", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


def fixture_stream():
    """Private harness protocol, through the same event/report writer as replay.

    The harness owns the enclosing fixture root. Data cannot select a program,
    provider, configuration, or an output outside a runner iterations directory.
    Unknown event rows remain unknown; EOF never manufactures a completed turn.
    """
    try:
        root = recording._root(os.environ["AUTOCODE_REPLAY_FIXTURE_ROOT"])
        total = 0
        while True:
            line = sys.stdin.buffer.readline(recording.MAX_ARTIFACT_BYTES + 1)
            if not line:
                return 0
            total += len(line)
            if len(line) > recording.MAX_ARTIFACT_BYTES or total > recording.MAX_RECORDING_BYTES:
                raise recording.RecordingError("Fixture stream exceeds its bound")
            value = recording._json(line)
            if not isinstance(value, dict):
                raise recording.RecordingError("Fixture stream is malformed")
            if value.get("kind") == "event" and set(value) == {"kind", "event"} and isinstance(value["event"], dict):
                emit_events([value["event"]], None, None)
            elif (
                value.get("kind") == "report"
                and set(value) == {"kind", "output", "report"}
                and isinstance(value["report"], dict)
            ):
                path = Path(value["output"])
                if not path.is_absolute():
                    raise recording.RecordingError("Fixture report is not runner-owned")
                relative = path.relative_to(root)
                parts = relative.parts
                # Serial and worker task runs share stage_base's suffix.
                # Accept only the existing nested iteration/report layout, never
                # arbitrary JSON below a fixture's runner storage.
                if (
                    len(parts) < 6
                    or parts[-6:-4] != (".autocode", "runs")
                    or parts[-3] != "iterations"
                    or not re.fullmatch(r"[0-9]{3,}", parts[-2])
                    or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*-[0-9]{2,}\.json", parts[-1])
                ):
                    raise recording.RecordingError("Fixture report is not runner-owned")
                recording._root(path.parent)
                emit_events([], value["report"], path)
            else:
                raise recording.RecordingError("Fixture stream is malformed")
    except (OSError, ValueError, KeyError, TypeError):
        print("Fixture emitter stopped: malformed stream or unavailable owned output", file=sys.stderr)
        return 2
