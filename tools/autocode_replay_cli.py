"""Export a saved run or replay it through the public task-run interface.

This interface never reads state.json or imports controller internals. The
recording owns data validation and the ordinary task runner owns progression.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

try:
    from . import autocode_replay_recording as recording
    from . import autocode_util as util
    from .autocode_taskrun import TaskRun, TaskRunError
except ImportError:
    import autocode_replay_recording as recording
    import autocode_util as util
    from autocode_taskrun import TaskRun, TaskRunError


def _literal_path(path: Path) -> Path:
    """Keep lexical custody: normalization must never erase a link or traversal."""
    path = Path(path).absolute()
    if path.resolve() != path or any(parent.is_symlink() for parent in (path, *path.parents)):
        raise recording.RecordingError("replay path is aliased")
    return path


def _workspace(run_dir: Path) -> Path:
    if run_dir.parent.name != "runs" or run_dir.parent.parent.name != ".autocode":
        raise recording.RecordingError("run directory must be inside workspace/.autocode/runs")
    return run_dir.parent.parent.parent


def export_saved(source: Path, destination: Path) -> Path:
    """Read authenticated public projection and export into a new bundle."""
    source = _literal_path(source)
    task = TaskRun(_workspace(source), source)
    context = task.status(inspect_evidence=True).get("replay_source")
    if not isinstance(context, dict):
        raise recording.RecordingError("saved run has no supported recording projection")
    return recording.export(context, destination)


def _input(source: Path, temporary: Path):
    source = _literal_path(source)
    if source.is_file():
        return recording.load(source)
    manifest = source / "recording.json"
    if manifest.is_file():
        return recording.load(manifest)
    return recording.load(export_saved(source, temporary / "recording"))


def _action(task: TaskRun, action: dict, view: dict, tape) -> dict:
    """A recorded semantic decision uses only this fresh run's current tokens."""
    kind, payload = action["kind"], action["payload"]
    if kind == "approve_plan":
        task.show_goal()
        current = task.status()
        recording.match_action(tape, action, current)
        return task.approve_plan(current["needs"]["token"])
    recording.match_action(tape, action, view)
    need = view["needs"]
    if kind == "answer":
        return task.answer(payload["question_id"], payload["text"], resolver_token=need["resolver_token"])
    if kind == "approve_review":
        return task.approve_review(payload["criterion"], need["token"])
    if kind == "feedback":
        return task.feedback(payload["text"])
    if kind == "follow_up":
        return task.follow_up(payload["text"])
    if kind == "resume":
        return task.resume_paused()
    raise recording.RecordingError("unsupported recorded operator action")


def drive(tape, task: TaskRun, *, checks="fresh") -> dict:
    """Advance using public needs; never invent a response after tape divergence."""
    actions = tape.document["actions"]
    consumed_actions = 0
    view = task.status()
    bound = len(tape.document["dispatches"]) + len(actions) + 4
    previous_consumed = 0
    for _ in range(bound):
        progress = view.get("replay_progress")
        if not isinstance(progress, dict) or progress.get("manifest_sha256") != tape.sha256:
            raise recording.RecordingError("replay has no matching public recording authority")
        if progress.get("checks") != checks:
            raise recording.RecordingError("replay check policy differs from the requested mode")
        result = view.get("replay_result") or {}
        if checks == "recorded" and (view.get("done") or view.get("completion_current")):
            raise recording.RecordingError("recorded checks cannot establish current completion")
        if checks == "fresh" and result.get("simulated"):
            raise recording.RecordingError("fresh replay returned a simulated verdict")
        consumed = progress.get("consumed")
        if type(consumed) is not int or not previous_consumed <= consumed <= len(tape.document["dispatches"]):
            raise recording.RecordingError("replay dispatch progress is invalid")
        previous_consumed = consumed
        expected_next = (
            tape.document["dispatches"][consumed]["id"] if consumed < len(tape.document["dispatches"]) else "end"
        )
        next_dispatch = progress.get("next_dispatch")
        if next_dispatch != expected_next:
            raise recording.RecordingError("replay next dispatch differs from the recording")
        if consumed_actions < len(actions) and actions[consumed_actions]["before_dispatch"] == next_dispatch:
            view = _action(task, actions[consumed_actions], view, tape)
            consumed_actions += 1
            continue
        if next_dispatch == "end" or view.get("done") or (result and not result.get("pending")):
            recording.verify_finished(tape, view, consumed_actions=consumed_actions)
            return view
        need = view.get("needs") or {}
        if need.get("kind") != "continue":
            raise recording.RecordingError(
                f"replay stopped at {view.get('status')}: no matching recorded {need.get('kind')} action"
            )
        before = (view.get("status"), view.get("next_stage"), view.get("iteration"), view.get("phase"), progress)
        view = task.advance()
        after = (
            view.get("status"),
            view.get("next_stage"),
            view.get("iteration"),
            view.get("phase"),
            view.get("replay_progress") or {},
        )
        if after == before:
            raise recording.RecordingError("replay made no public progress")
    raise recording.RecordingError("replay exceeded its recorded action/dispatch bound")


def replay(source: Path, destination: Path, *, checks="fresh") -> dict:
    """Create one isolated run at the exact target; source and bundle stay unchanged."""
    destination = _literal_path(destination)
    workspace = _workspace(destination)
    if destination.exists() or destination.is_symlink():
        raise recording.RecordingError("replay destination already exists")
    with tempfile.TemporaryDirectory(prefix="autocode-replay-") as tmp:
        tape = _input(Path(source), Path(tmp).resolve())
        recording.prepare_workspace(tape, workspace)
        tape = recording.copy_into(tape, workspace / ".autocode" / "replay" / "recording")
    options = (
        "--replay-recording",
        str(tape.manifest_path),
        "--replay-checks",
        checks,
        "--evidence-provenance",
        "fake",
    )
    pin = tape.document["launch"].get("workflow_pin")
    task = TaskRun.start(
        workspace,
        tape.document["launch"]["task"],
        options=options,
        start_options=("--workflow", pin) if pin is not None else (),
        run_dir=destination,
    )
    view = drive(tape, task, checks=checks)
    return {"mode": checks, "run_dir": str(task.run_dir), "recording_sha256": tape.sha256, "view": view}


def parser() -> argparse.ArgumentParser:
    """The arguments `autocode replay` reads; `autocode completion` reads them too."""
    result = argparse.ArgumentParser(
        description="Re-drive a saved run with recorded provider events and no model calls"
    )
    result.add_argument("source", type=Path, help="Saved run directory or exported recording manifest/directory")
    target = result.add_mutually_exclusive_group(required=True)
    target.add_argument("--into", type=Path, help="Exact fresh target: ISOLATED_WORKSPACE/.autocode/runs/NAME")
    target.add_argument("--export", type=Path, help="Create a trimmed, redacted recording bundle without replaying")
    result.add_argument(
        "--recorded-checks", action="store_true", help="Simulate archived checks; never current delivery evidence"
    )
    result.add_argument("--json", action="store_true", help="Print a structured result or error")
    return result


def cli(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--emit-fixture-events"]:
        try:
            from . import autocode_replay_transport as transport
        except ImportError:
            import autocode_replay_transport as transport
        return transport.fixture_stream()
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.recorded_checks and args.export:
        arguments.error("--recorded-checks requires --into")
    try:
        if args.export:
            manifest = export_saved(args.source, _literal_path(args.export))
            result = {"mode": "export", "manifest": str(manifest), "recording_sha256": recording.load(manifest).sha256}
        else:
            result = replay(args.source, args.into, checks="recorded" if args.recorded_checks else "fresh")
    except (recording.RecordingError, TaskRunError, OSError, ValueError) as error:
        message = util.redact(str(error))
        if args.json:
            print(json.dumps({"code": "REPLAY_REFUSED", "message": message}), file=sys.stderr)
        else:
            print("Replay refused: " + message, file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(result))
    elif result["mode"] == "export":
        print("Recording exported: " + result["manifest"])
    else:
        view = result["view"]
        verdict = (view.get("replay_result") or {}).get("recorded_verdict") or view.get("status")
        print(f"Replay {result['mode']}: {verdict}\nRun: {result['run_dir']}")
    return 0
