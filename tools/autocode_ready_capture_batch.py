"""An executable presentation of plain approved checks, using the existing capture CLI."""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as contracts
    from . import autocode_regression_state as regression_state
    from . import autocode_stall_recovery as stall_recovery
    from . import autocode_stop_kinds as stop_kinds
    from . import autocode_util as util
    from .autocode_capture_batch_plan import MAX_ENCODED_BYTES as MAX_ENCODED_BYTES
    from .autocode_capture_batch_plan import MAX_PLAN_BYTES, _capture, _capture_mode, _plan
except ImportError:
    import autocode_contract_identity as contracts
    import autocode_regression_state as regression_state
    import autocode_stall_recovery as stall_recovery
    import autocode_stop_kinds as stop_kinds
    import autocode_util as util
    from autocode_capture_batch_plan import MAX_ENCODED_BYTES as MAX_ENCODED_BYTES
    from autocode_capture_batch_plan import MAX_PLAN_BYTES, _capture, _capture_mode, _plan

# The plan is data, never shell/Python source. Keep the tool call copyable without
# the nested shell quotes that failed before capture in the ISO-week trial.


BUILTINS = frozenset(
    [
        ".",
        "!",
        ":",
        "alias",
        "bg",
        "break",
        "builtin",
        "case",
        "cd",
        "command",
        "continue",
        "coproc",
        "declare",
        "dirs",
        "disown",
        "do",
        "done",
        "elif",
        "else",
        "enable",
        "esac",
        "eval",
        "exec",
        "exit",
        "export",
        "fc",
        "fg",
        "fi",
        "for",
        "function",
        "hash",
        "help",
        "history",
        "if",
        "in",
        "jobs",
        "let",
        "local",
        "popd",
        "pushd",
        "read",
        "readonly",
        "return",
        "select",
        "set",
        "shift",
        "source",
        "then",
        "time",
        "trap",
        "typeset",
        "ulimit",
        "umask",
        "unalias",
        "unset",
        "until",
        "wait",
        "while",
    ]
)


def argv(command):
    """Reject shell interpretation rather than changing an approved command's meaning."""
    if not isinstance(command, str) or not command or re.search(r"[$`*?\[\]~{}#\n\r]", command):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return None
    if (
        not words
        or words[0] in BUILTINS
        or "=" in words[0]
        or any(word and all(char in ";&|<>()" for char in word) for word in words)
    ):
        return None
    return words


def context(capture, commands, evidence_directory):
    """All checks in order, or no affordance; individual execution remains available."""
    if os.name != "posix" or not isinstance(commands, list) or not 2 <= len(commands) <= 16:
        return None
    prepared = [argv(command) for command in commands]
    if any(command is None for command in prepared):
        return None
    if not isinstance(capture, str):
        return None
    try:
        mode = _capture_mode(shlex.split(capture))
    except ValueError:
        return None
    if mode is None:
        return None
    payload = {"mode": mode, "commands": prepared, "directory": str(Path(evidence_directory))}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    if len(raw) > MAX_PLAN_BYTES:
        return None
    encoded = base64.urlsafe_b64encode(raw).decode("ascii")
    return {
        "command": shlex.join([sys.executable, str(Path(__file__).resolve()), encoded]),
        "instruction": (
            "When ready, copy and execute this complete command once as one unfiltered tool call. "
            "Its encoded plan is opaque data; do not decode, rewrite or re-quote it. It runs every exact approved "
            "required_command in sequential order through capture, including after a failed check, and "
            "prints a short attestation and check reference for each fresh receipt. Full receipts and "
            "test output remain in the referenced files; read failure details there when needed. "
            "Its aggregate exit is failure "
            "if any check fails. Report each individual command/result/receipt; the batch is not a check "
            "and grants no verification authority. Native tools use their current writable scratch. "
            "Keep the complete output. Additional probes and the "
            "individual capture_command remain available. If your shell tool cannot report its outer "
            "exit code, use individual captures instead; a batch does not loosen receipt authentication."
        ),
    }


def builder_context(state: RunState, capture, evidence_directory):
    """Offer only the current approved implementation's unchanged initial command list."""
    try:
        contract = cast(dict, state["goal_contract"])
        first = contract["body"]["initial_task"]
        task = cast(dict, state["current_task"])
        if (
            not contracts.approved(state)
            or first.get("kind") != "implement"
            or task.get("kind") != "implement"
            or task.get("contract_hash") != contract["hash"]
            or task.get("contract_revision") != contract["revision"]
            or task.get("validation_plan") != first.get("validation_plan")
        ):
            return None
        batch = context(capture, first.get("validation_plan"), evidence_directory)
        if batch:
            batch["instruction"] += (
                " Preserve the approved phase sequence: when it requires fail-first checks, add the "
                "necessary regression tests, run this batch once before the product fix, retain each "
                "expected failure, implement the fix, and run the same command fresh again afterward. "
                "Each invocation creates new receipts; "
                "never reuse an earlier phase's receipts. This offer does not advance the task or "
                "decide whether a failure was expected."
            )
        return batch
    except (KeyError, TypeError, AttributeError):
        return None


def _norm_command(command: object) -> str | None:
    if isinstance(command, (list, tuple)):
        words = [w for w in command if isinstance(w, str) and w]
        return shlex.join(words) if words and len(words) == len(command) else None
    if not isinstance(command, str):
        return None
    stripped = command.strip()
    if not stripped:
        return None
    if parsed := argv(stripped):
        return shlex.join(parsed)
    try:
        words = shlex.split(stripped)
        if words:
            return shlex.join(words)
    except ValueError:
        pass
    return " ".join(stripped.split())


def _collect_baseline_timeouts(state: Mapping[str, Any], source_revision: str | None) -> set[str]:
    timed_out: set[str] = set()
    reg_base = state.get("base_commit")
    baseline_slice = regression_state.BaselineStateSlice.from_state(state)
    if baseline_slice.is_dict:
        cached = baseline_slice.mapping()
        settled = cached.get("settled_timeouts")
        entries: list[object] = [
            cached,
            cached.get("whole"),
            *(settled.values() if isinstance(settled, Mapping) else ()),
        ]
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            raw_binding = entry.get("binding")
            binding: Mapping[str, Any] = raw_binding if isinstance(raw_binding, Mapping) else {}
            entry_base = binding.get("base")
            if (
                isinstance(reg_base, str)
                and reg_base
                and isinstance(entry_base, str)
                and entry_base
                and entry_base != reg_base
            ):
                continue
            entry_rev = entry.get("source_revision")
            if source_revision and isinstance(entry_rev, str) and entry_rev and entry_rev != source_revision:
                continue
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                continue
            try:
                loaded = util.read(Path(path))
            except (OSError, ValueError, TypeError, KeyError):
                continue
            if not isinstance(loaded, Mapping):
                continue
            loaded_rev = loaded.get("source_revision")
            if source_revision and isinstance(loaded_rev, str) and loaded_rev and loaded_rev != source_revision:
                continue
            raw_receipt = loaded.get("receipt")
            receipt: Mapping[str, Any] = raw_receipt if isinstance(raw_receipt, Mapping) else {}
            if loaded.get("timed_out") or receipt.get("timed_out"):
                cmd = loaded.get("command") or receipt.get("command") or binding.get("command")
                if norm := _norm_command(cmd):
                    timed_out.add(norm)
    proof = state.get("regression_proof")
    if isinstance(proof, Mapping):
        sf = proof.get("suite_fallback")
        proof_rev = proof.get("source_revision")
        proof_base = proof.get("base")
        same_context = (
            (bool(source_revision) and proof_rev == source_revision)
            or (isinstance(reg_base, str) and bool(reg_base) and proof_base == reg_base)
            or ("source_revision" not in proof and "base" not in proof)
        )
        if isinstance(sf, Mapping) and sf.get("whole_suite_outcome") == "timed_out" and same_context:
            for raw_cmd in (sf.get("whole_suite"), sf.get("timed_out_focused_suite")):
                if norm := _norm_command(raw_cmd):
                    timed_out.add(norm)
        if source_revision and proof_rev == source_revision:
            checks = proof.get("checks")
            if isinstance(checks, Mapping):
                for chk in checks.values():
                    if (
                        isinstance(chk, Mapping)
                        and chk.get("timed_out")
                        and (norm := _norm_command(chk.get("command")))
                    ):
                        timed_out.add(norm)
    return timed_out


def _collect_replay_timeouts(run_dir: Path | None, source_revision: str | None) -> set[str]:
    timed_out: set[str] = set()
    if run_dir is None or not source_revision:
        return timed_out
    replay_root = Path(run_dir) / "check-replay"
    if not replay_root.is_dir():
        return timed_out
    for replay_file in [replay_root / "replay.json", *sorted(replay_root.glob("*/replay.json"))]:
        try:
            data = util.read(replay_file)
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(data, Mapping) or data.get("source_revision") != source_revision:
            continue
        for chk in data.get("checks") or ():
            if isinstance(chk, Mapping) and chk.get("timed_out") and (norm := _norm_command(chk.get("command"))):
                timed_out.add(norm)
    return timed_out


def _batch_progress(
    decoded: dict[str, Any], evidence_directory: Path, row: Mapping[str, Any], source_revision: str | None
) -> int:
    dirs: list[Path] = []
    for candidate in (Path(decoded["directory"]), Path(evidence_directory)):
        if candidate not in dirs and candidate.is_dir():
            dirs.append(candidate)
    events = row.get("events")
    if isinstance(events, str) and events:
        parent = Path(events).parent
        if parent not in dirs and parent.is_dir():
            dirs.append(parent)
    row_attempt_id = stall_recovery._attempt_id(row)
    best_rank: tuple[int, float] | None = None
    best_loc: tuple[Path, str] | None = None
    first_cmd = decoded["commands"][0]
    for directory in dirs:
        for path in directory.glob("ready-*-1.json"):
            matched = re.fullmatch(r"ready-([0-9a-f]{32})-1\.json", path.name)
            if not matched:
                continue
            token = matched.group(1)
            try:
                r1 = util.read(path)
                mtime = path.stat().st_mtime
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(r1, Mapping) or r1.get("command") != first_cmd:
                continue
            raw_ctx = r1.get("capture_context")
            ctx: Mapping[str, Any] = raw_ctx if isinstance(raw_ctx, Mapping) else {}
            ctx_rev = ctx.get("source_revision")
            if source_revision and isinstance(ctx_rev, str) and ctx_rev and ctx_rev != source_revision:
                continue
            ctx_attempt = ctx.get("attempt")
            if row_attempt_id and isinstance(ctx_attempt, str) and ctx_attempt and ctx_attempt != row_attempt_id:
                continue
            rank = (1 if row_attempt_id and ctx_attempt == row_attempt_id else 0, mtime)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best_loc = (directory, token)
    if best_loc is None:
        return 0
    top_dir, top_token = best_loc
    completed = 0
    for idx, expected_cmd in enumerate(decoded["commands"], 1):
        receipt_path = top_dir / f"ready-{top_token}-{idx}.json"
        try:
            rec = util.read(receipt_path)
        except (OSError, ValueError, TypeError):
            break
        if not isinstance(rec, Mapping) or rec.get("command") != expected_cmd:
            break
        completed = idx
    return completed


def _collect_stage_timeouts(
    state: Mapping[str, Any],
    planned_norm: list[str],
    evidence_directory: Path,
    source_revision: str | None,
) -> tuple[set[str], list[str], bool]:
    timed_out: set[str] = set()
    completed_batch: list[str] = []
    batch_timed_out = False
    if not source_revision:
        return timed_out, completed_batch, batch_timed_out
    for row in state.get("stages") or ():
        if (
            not isinstance(row, Mapping)
            or row.get("runner_owned")
            or row.get("report_only")
            or not row.get("timed_out")
            or stop_kinds.unfailed(row.get("timeout_kind"))
            or stall_recovery.attempt_revision(row) != source_revision
        ):
            continue
        for raw_cmd in stall_recovery.raw_stalled_commands(row):
            words: list[str] | None = None
            try:
                words = shlex.split(raw_cmd)
            except ValueError:
                words = None
            if words and len(words) >= 2 and Path(words[-2]).name == "autocode_ready_capture_batch.py":
                try:
                    decoded = _plan(words[-1])
                except ValueError:
                    decoded = None
                if decoded is not None:
                    dec_norm = [shlex.join(cmd) for cmd in decoded["commands"]]
                    progress = _batch_progress(decoded, evidence_directory, row, source_revision)
                    if dec_norm == planned_norm:
                        batch_timed_out = True
                    if 0 < progress <= len(dec_norm):
                        completed_batch = dec_norm[:progress]
                    if progress < len(dec_norm):
                        timed_out.add(dec_norm[progress])
                    continue
            if words and "--" in words and "capture" in words[: words.index("--")]:
                inner = words[words.index("--") + 1 :]
                if inner:
                    timed_out.add(shlex.join(inner))
                continue
            if norm := _norm_command(raw_cmd):
                timed_out.add(norm)
    return timed_out, completed_batch, batch_timed_out


def withheld_context(
    capture,
    commands,
    evidence_directory,
    *,
    state: Mapping[str, Any] | None = None,
    run_dir: str | Path | None = None,
    stage: str | None = None,
    source_revision: str | None = None,
) -> dict[str, Any] | None:
    """Withhold the batch convenience when a planned check or identical batch already timed out."""
    del stage
    if context(capture, commands, evidence_directory) is None:
        return None
    planned_norm = [shlex.join(cast(list[str], argv(command))) for command in commands]
    norm_to_orig = {norm: command for command, norm in zip(reversed(commands), reversed(planned_norm), strict=True)}
    timed_out_norm: set[str] = set()
    completed_batch_norm: list[str] = []
    batch_timed_out = False
    if isinstance(state, Mapping):
        timed_out_norm.update(_collect_baseline_timeouts(state, source_revision))
        stage_timeouts, completed_batch_norm, batch_timed_out = _collect_stage_timeouts(
            state, planned_norm, Path(evidence_directory), source_revision
        )
        timed_out_norm.update(stage_timeouts)
    if run_dir is not None:
        timed_out_norm.update(_collect_replay_timeouts(Path(run_dir), source_revision))
    matched = list(
        dict.fromkeys(command for command, norm in zip(commands, planned_norm, strict=True) if norm in timed_out_norm)
    )
    if not matched and not batch_timed_out:
        return None
    completed = list(
        dict.fromkeys(
            norm_to_orig.get(norm, norm)
            for norm in completed_batch_norm
            if norm in norm_to_orig and norm not in timed_out_norm
        )
    )
    reason = (
        "Withheld ready_capture_batch because a planned check or identical batch already timed out on "
        f"source_revision {source_revision}."
        if source_revision
        else "Withheld ready_capture_batch because a planned check already timed out against the baseline."
    )
    result: dict[str, Any] = {
        "reason": reason,
        "timed_out_commands": matched,
        "instruction": (
            "The sequential batch convenience is withheld because at least one planned command or identical batch "
            "already timed out on this source_revision. Every required check remains mandatory via capture_command: "
            "capture fast checks individually first, then inspect and bound the timed-out check."
        ),
    }
    if completed:
        result["completed_batch_commands"] = completed
    return result


def run(encoded):
    """Use canonical capture for every check; this helper grants no authority."""
    plan = _plan(encoded)
    directory = Path(plan["directory"])
    control = os.environ.get("AUTOCODE_TOOL_CONTAINMENT_ID")
    if control is not None:
        scratch = Path(os.environ.get("TMPDIR", ""))
        manifest = os.environ.get("AUTOCODE_VERIFICATION_COPY")
        pin = os.environ.get("AUTOCODE_VERIFICATION_COPY_SHA256")
        valid = re.fullmatch("tool-containment-[0-9a-f]{32}", control) is not None and scratch.is_absolute()
        expected = Path.cwd() / ".autocode" / control / "scratch"
        try:
            valid = valid and scratch == expected and scratch.resolve(strict=True) == scratch and scratch.is_dir()
            if not valid:
                raise ValueError("Invalid current native scratch")
            copy_path = scratch.parent / "verification-copy.json"
            if manifest is None and pin is None and not copy_path.exists() and not copy_path.is_symlink():
                # Only prepare(allow_write=True) creates this marker, outside the
                # shell's writable scratch. It selects a receipt location; the
                # existing kernel policy and capture still own all authority.
                writer = scratch.parent / "writer-capture.json"
                valid = not writer.is_symlink() and writer.is_file() and writer.stat().st_mode & 0o222 == 0
                if valid:
                    binding = json.loads(writer.read_text())
                    valid = isinstance(binding, dict) and type(binding.get("version")) is int
                    valid = valid and binding == {
                        "version": 1,
                        "workspace": str(Path.cwd()),
                        "scratch": str(scratch),
                        "control": control,
                    }
            else:
                valid = valid and manifest == str(copy_path)
                valid = valid and copy_path.is_file() and not copy_path.is_symlink() and bool(pin)
        except (OSError, ValueError):
            valid = False
        if not valid:
            raise ValueError("Prepared capture batch requires the current native scratch directory")
        directory = scratch
    prefix = str(directory) + "/ready-" + uuid.uuid4().hex
    codes = []
    for index, command in enumerate(plan["commands"], 1):
        receipt = prefix + "-" + str(index) + ".json"
        # Capture's stdout embeds the entire display inside its receipt. Repeating it here can make
        # a provider truncate the batch, including other checks' authentication. The fresh receipt
        # and full output remain on disk; stderr retains capture's exact-byte attestation.
        result = subprocess.run(
            _capture() + ["--mode", plan["mode"], "--output", receipt, "--"] + command,
            stdout=subprocess.DEVNULL,
        )
        codes.append(result.returncode)
        print(json.dumps({"command": command, "exit_code": result.returncode, "evidence_ref": receipt}), flush=True)
    return 1 if any(codes) else 0


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Prepared capture batch requires one encoded plan")
        sys.exit(run(sys.argv[1]))
    except ValueError as error:
        sys.exit(str(error))
