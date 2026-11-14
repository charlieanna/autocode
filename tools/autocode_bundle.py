"""`autocode bundle RUN_DIR`: one redacted archive of a run, for a bug report (#716).

It holds what a maintainer needs to see what happened and to reproduce it, and none of the
workspace's source unless --with-diff adds the run's diff:

- ``recording/``: the replay recording ``autocode replay RUN --export`` writes (#708), so
  ``autocode replay BUNDLE/recording --into ...`` re-drives the run with no model;
- ``run/``: ``state.json``, ``activity.jsonl``, ``model-preflight.json``, every stage report
  (``iterations/**/*.json``, not the before/after source snapshots), the check-replay receipts
  (``check-replay/**/*.json``), the model-access preflight reports and supervision receipts
  (``model-preflight/**/*.json``, plus any pre-move preflight attempt receipts under
  ``pre-move-model-preflight/...``) and the last TAIL_LINES lines of each provider event stream
  (``iterations/**/*.jsonl`` and ``model-preflight/**/*.jsonl``);
- ``settings.json`` (the run's resolved settings), ``doctor.json`` (an explicit not-run record
  for live diagnostics) and ``version.txt``. Export never probes backend health.

Every file AutoCode writes into the bundle goes through util.redact (#712), with the values of the
secret-named environment variables (autocode_agent_env.is_secret) as known secrets. The recording
is exported already redacted and is bound to its own hashes, so it is never rewritten: when any of
its files would still change under redaction, the recording is left out and the manifest says why.
``manifest.json`` lists each file's sha256 and size, and the archive is reproducible (sorted
entries, fixed times and owners), so a note in docs/bugs/ can cite the manifest's hash instead of
pasting logs. The run directory itself is only read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import argparse
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

try:
    from . import autocode_agent_env as agent_env
    from . import autocode_build_worktree as build_worktree
    from . import autocode_preflight_attempts as preflight_attempts
    from . import autocode_subcommands as subcommands
    from . import autocode_util as util
except ImportError:
    import autocode_agent_env as agent_env
    import autocode_build_worktree as build_worktree
    import autocode_preflight_attempts as preflight_attempts
    import autocode_subcommands as subcommands
    import autocode_util as util

TAIL_LINES = 200
MAX_FILE_BYTES = 4 * 1024 * 1024
SNAPSHOT_SUFFIXES = (".before.json", ".after.json")


def secret_values(environ=None) -> list[str]:
    """The values of the environment's secret-named variables, which redaction treats as known secrets."""
    environ = os.environ if environ is None else environ
    return [value for name, value in environ.items() if agent_env.is_secret(name, value)]


def _text(path: Path, *, tail: int | None = None) -> str:
    """A run file as text; the last ``tail`` lines when given, and never more than MAX_FILE_BYTES."""
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        handle.seek(max(0, size - MAX_FILE_BYTES))
        data = handle.read()
    text = data.decode("utf-8", errors="replace")
    if size > MAX_FILE_BYTES:
        text = text.split("\n", 1)[-1]  # drop the line cut in half
    if tail is not None:
        text = "\n".join(text.splitlines()[-tail:]) + "\n"
    return text


def _has_symlink(path: Path, *, stop_at: Path | None = None) -> bool:
    """Whether ``path`` or any parent directory down to ``stop_at`` is a symbolic link."""
    current = path
    while True:
        if current.is_symlink():
            return True
        if stop_at is not None and current == stop_at:
            return False
        if current.parent == current:
            return False
        current = current.parent


def _add_preflight_dir(
    chosen: dict[str, tuple[Path, int | None]], directory: Path, prefix: str, *, stop_at: Path
) -> None:
    if not directory.is_dir() or _has_symlink(directory, stop_at=stop_at):
        return
    for path in sorted(directory.rglob("*.json")):
        if path.is_file() and not _has_symlink(path, stop_at=directory) and not path.name.endswith(SNAPSHOT_SUFFIXES):
            chosen[f"{prefix}/{path.relative_to(directory).as_posix()}"] = (path, None)
    for path in sorted(directory.rglob("*.jsonl")):
        if path.is_file() and not _has_symlink(path, stop_at=directory):
            chosen[f"{prefix}/{path.relative_to(directory).as_posix()}"] = (path, TAIL_LINES)


def run_files(
    run_dir: Path, *, state: RunState | None = None, origin_dir: Path | None = None
) -> dict[str, tuple[Path, int | None]]:
    """The run files a bundle carries: name inside the bundle -> (source file, tail lines or None)."""
    chosen: dict[str, tuple[Path, int | None]] = {}
    for name in ("state.json", "activity.jsonl", "model-preflight.json"):
        candidate = run_dir / name
        if candidate.is_file() and not candidate.is_symlink():
            chosen[f"run/{name}"] = (candidate, None)
    for path in sorted((run_dir / "iterations").rglob("*.json")) if (run_dir / "iterations").is_dir() else []:
        if path.is_file() and not _has_symlink(path, stop_at=run_dir) and not path.name.endswith(SNAPSHOT_SUFFIXES):
            chosen[f"run/{path.relative_to(run_dir).as_posix()}"] = (path, None)
    for path in sorted((run_dir / "iterations").rglob("*.jsonl")) if (run_dir / "iterations").is_dir() else []:
        if path.is_file() and not _has_symlink(path, stop_at=run_dir):
            chosen[f"run/{path.relative_to(run_dir).as_posix()}"] = (path, TAIL_LINES)
    for path in sorted((run_dir / "check-replay").rglob("*.json")) if (run_dir / "check-replay").is_dir() else []:
        if path.is_file() and not _has_symlink(path, stop_at=run_dir):
            chosen[f"run/{path.relative_to(run_dir).as_posix()}"] = (path, None)
    _add_preflight_dir(chosen, run_dir / "model-preflight", "run/model-preflight", stop_at=run_dir)
    if origin_dir is not None and isinstance(state, dict):
        origin_preflight = origin_dir / "model-preflight"
        if origin_preflight.is_dir() and not _has_symlink(origin_preflight, stop_at=origin_dir):
            origin_root = origin_preflight.resolve()
            for record in preflight_attempts.records(state, run_dir, earlier_dirs=(origin_dir,)):
                attempt_dir = Path(str(record.get("events") or "")).parent
                if _has_symlink(attempt_dir, stop_at=origin_dir):
                    continue
                resolved_attempt = attempt_dir.resolve()
                if resolved_attempt.is_dir() and resolved_attempt.is_relative_to(origin_root):
                    rel = resolved_attempt.relative_to(origin_root).as_posix()
                    _add_preflight_dir(
                        chosen, resolved_attempt, f"run/pre-move-model-preflight/{rel}", stop_at=origin_dir
                    )
    return chosen


def _workspace(run_dir: Path) -> Path:
    """The workspace whose host checks and default bundle path the run takes.

    A live run sits at WORKSPACE/.autocode/runs/RUN. `autocode clean-worktrees` copies a finished
    task worktree's records to PROJECT/.autocode/archive/TREE/ (#2377), so an archived run sits at
    PROJECT/.autocode/archive/TREE/runs/RUN and takes the project.
    """
    if run_dir.parent.name == "runs" and run_dir.parent.parent.name == ".autocode":
        return run_dir.parent.parent.parent
    if _archive(run_dir) is not None:
        return run_dir.parents[4]
    raise ValueError(
        "the run directory must be WORKSPACE/.autocode/runs/RUN, or an archived PROJECT/.autocode/archive/TREE/runs/RUN"
    )


def _archive(run_dir: Path) -> dict | None:
    """The archived worktree's saved task record (its branch and base commit) when ``run_dir`` is archived."""
    if run_dir.parent.name != "runs" or len(run_dir.parents) < 4 or run_dir.parents[2].name != "archive":
        return None
    if run_dir.parents[3].name != ".autocode":
        return None
    record = run_dir.parents[1] / "task-workspace.json"
    try:
        loaded = json.loads(record.read_text()) if record.is_file() else {}
    except (OSError, ValueError):
        loaded = {}
    return loaded if isinstance(loaded, dict) else {}


def _doctor(workspace: Path) -> dict:
    """Keep the diagnostic artifact without depending on the live backend being healthy."""
    return {
        "status": "not_run",
        "ok": None,
        "checks": [],
        "reason": "Live host and provider diagnostics are not run during retained evidence export.",
    }


def _recording(run_dir: Path, scratch: Path) -> Path:
    try:
        from . import autocode_replay_cli as replay
    except ImportError:
        import autocode_replay_cli as replay
    return replay.export_saved(run_dir, scratch / "recording")


DIFF_ERRORS = "surrogateescape"


def _diff(workspace: Path, state: RunState, archive: dict | None = None) -> tuple[str, str]:
    """The run's diff: against its base commit when it recorded one, else the uncommitted work.

    An archived run's worktree is gone; its kept branch holds the work, so the diff is the base
    commit against that branch.
    """
    base = state.get("base_commit") or "HEAD"
    if archive is not None:
        branch = str(archive.get("branch") or "")
        base = state.get("base_commit") or str(archive.get("base_commit") or "")
        if not branch or not base:
            raise ValueError(
                "this archived run's worktree was removed and its archive names no branch and base commit;"
                " bundle it without --with-diff"
            )
        target: list[str] = [base, branch]
    else:
        target = [base]
    # The diff's bytes are kept as they are (surrogateescape), so a file that is not UTF-8 (a Latin-1
    # source) neither stops the bundle nor changes in it; collect() encodes it the same way.
    result = subprocess.run(
        ["git", "-C", str(workspace), "diff", *target], capture_output=True, encoding="utf-8", errors=DIFF_ERRORS
    )
    if result.returncode:
        raise ValueError(f"git diff {' '.join(target)} failed: {result.stderr.strip()}")
    return result.stdout, "..".join(target)


def _canonical_run(run_dir: Path) -> tuple[Path, Path, RunState, Path | None, str | None]:
    """Resolve ``run_dir`` across an authenticated ``moved_to`` worktree pointer, refusing symlinks or tampering."""
    workspace = _workspace(run_dir)
    if _has_symlink(run_dir, stop_at=workspace) or (run_dir / "state.json").is_symlink():
        raise ValueError(f"{run_dir} cannot be bundled across a symbolic link")
    if not (run_dir / "state.json").is_file():
        raise ValueError(f"{run_dir} has no state.json")
    state: RunState = json.loads((run_dir / "state.json").read_text())
    if build_worktree.moved(state) is not None:
        resolved = build_worktree.resolve(workspace, run_dir, state)
        if resolved is None:
            raise ValueError(build_worktree.gone(workspace, state))
        tree, child, saved = resolved
        if (
            _has_symlink(tree, stop_at=workspace)
            or _has_symlink(child, stop_at=tree)
            or (child / "state.json").is_symlink()
        ):
            raise ValueError(build_worktree.gone(workspace, state))
        moved_note = f"{workspace.name}/.autocode/runs/{run_dir.name}"
        return tree, child, saved, run_dir, moved_note
    return workspace, run_dir, state, build_worktree.origin(state, run_dir), None


def collect(run_dir, *, with_diff=False, environ=None, doctor=None, recording=None) -> tuple[dict, dict]:
    """The bundle's files (name -> bytes) and its manifest; the run directory is only read.

    ``doctor`` and ``recording`` stand in for the diagnostic record and the replay export (tests).
    """
    doctor, recording = doctor or _doctor, recording or _recording
    run_dir = Path(run_dir).absolute()
    workspace, run_dir, state, origin_dir, moved_note = _canonical_run(run_dir)
    secrets = secret_values(environ)
    files: dict[str, bytes] = {}
    for name, (path, tail) in run_files(run_dir, state=state, origin_dir=origin_dir).items():
        files[name] = util.redact(_text(path, tail=tail), secrets).encode()
    files["settings.json"] = util.redact(
        json.dumps(state.get("settings") or {}, indent=2, sort_keys=True), secrets
    ).encode()
    files["version.txt"] = (subcommands.version_line() + "\n").encode()
    notes: dict[str, str] = {}
    if moved_note is not None:
        notes["moved_from"] = util.redact(moved_note, secrets)
    try:
        report = doctor(workspace)
    except Exception as error:  # an injected diagnostic failure must not prevent retained evidence export
        report = {"error": f"{type(error).__name__}: {error}"}
    files["doctor.json"] = util.redact(json.dumps(report, indent=2, sort_keys=True), secrets).encode()
    if with_diff:
        diff, base = _diff(workspace, state, _archive(run_dir))
        files["diff.patch"] = util.redact(diff, secrets).encode("utf-8", DIFF_ERRORS)
        notes["diff_base"] = base
    with tempfile.TemporaryDirectory(prefix="autocode-bundle-") as scratch:
        try:
            # The export refuses an aliased destination; a temporary directory can sit behind a link (/var).
            exported = recording(run_dir, Path(scratch).resolve())
        except Exception as error:  # a run with no recording projection still bundles its files
            notes["recording"] = f"not included: {type(error).__name__}: {error}"
        else:
            directory = exported if exported.is_dir() else exported.parent  # the export returns its manifest
            copied = {
                f"recording/{path.relative_to(directory).as_posix()}": path.read_bytes()
                for path in sorted(directory.rglob("*"))
                if path.is_file()
            }
            leaking = sorted(
                name
                for name, data in copied.items()
                if util.redact(data.decode("utf-8", errors="replace"), secrets)
                != data.decode("utf-8", errors="replace")
            )
            if not copied:
                notes["recording"] = "not included: the export wrote no files"
            elif leaking:
                notes["recording"] = "not included: redaction would change " + ", ".join(leaking[:5])
            else:
                files.update(copied)
    manifest = {
        "bundle_version": 1,
        "run": run_dir.name,
        "autocode": subcommands.version_line(),
        "redaction": "util.redact (#712) with the secret-named environment values",
        "with_diff": bool(with_diff),
        "notes": notes,
        "files": [
            {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in sorted(files.items())
        ],
    }
    return files, manifest


def write(path, root: str, files: dict, manifest: dict) -> str:
    """Write a reproducible .tar.gz with every file under ``root/``; return the manifest's sha256."""
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as packed:
            with tarfile.open(fileobj=packed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name, data in sorted({**files, "manifest.json": manifest_bytes}.items()):
                    info = tarfile.TarInfo(f"{root}/{name}")
                    info.size, info.mode, info.mtime = len(data), 0o644, 0
                    archive.addfile(info, io.BytesIO(data))
    temporary.replace(path)
    return hashlib.sha256(manifest_bytes).hexdigest()


def parser() -> argparse.ArgumentParser:
    """The arguments `autocode bundle` reads; `autocode completion` reads them too."""
    result = argparse.ArgumentParser(
        prog="autocode bundle", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    result.add_argument("run_dir", nargs="?", type=Path, help="the run: WORKSPACE/.autocode/runs/RUN")
    # Every other command names the run with --run-dir; accept it here too (#2385).
    result.add_argument(
        "--run-dir", dest="run_dir_option", type=Path, help="the same run, named as the other commands name it"
    )
    result.add_argument(
        "--out", type=Path, help="where to write the archive (default: WORKSPACE/.autocode/bundles/RUN.tar.gz)"
    )
    result.add_argument("--with-diff", action="store_true", help="add the run's diff of the workspace source")
    result.add_argument("--json", action="store_true", help="print the archive path and manifest as JSON")
    return result


def cli(argv=None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    if bool(args.run_dir) == bool(args.run_dir_option):
        arguments.error("name the run once: RUN or --run-dir RUN")
    run_dir = (args.run_dir or args.run_dir_option).absolute()
    try:
        files, manifest = collect(run_dir, with_diff=args.with_diff)
        out = args.out or _workspace(run_dir) / ".autocode" / "bundles" / f"{run_dir.name}.tar.gz"
        digest = write(out, f"autocode-bundle-{run_dir.name}", files, manifest)
    except (OSError, ValueError) as error:
        print(f"autocode bundle: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"bundle": str(out), "manifest_sha256": digest, "manifest": manifest}, indent=2))
    else:
        print(f"Bundle written: {out}\nManifest sha256: {digest} ({len(manifest['files'])} files)")
        for key, note in manifest["notes"].items():
            print(f"{key}: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
