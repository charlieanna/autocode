"""Authenticate a captured check against a runner-owned attempt or executed event."""

import contextlib
import json
import os
import re
import shlex
import sys
from pathlib import Path

try:
    from . import autocode_capture_batch_plan as batch_plan
    from . import autocode_receipts as receipts
    from .autocode_util import file_hash
except ImportError:
    import autocode_capture_batch_plan as batch_plan
    import autocode_receipts as receipts
    from autocode_util import file_hash


def _tokens(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    return list(lexer)


def _without_redirects(words):
    """Only display redirections, never a second source of receipt text."""
    result = []
    i = 0
    while i < len(words):
        if words[i : i + 3] == ["2", ">&", "1"]:
            i += 3
        elif words[i : i + 2] == [">", "/dev/null"]:
            i += 2
        else:
            result.append(words[i])
            i += 1
    return result


def _filter(words):
    if not words or words[0] not in {
        f"{root}/{name}" for root in ("/bin", "/usr/bin") for name in ("grep", "head", "tail", "cut")
    }:
        return False
    name, args = Path(words[0]).name, words[1:]
    if name == "grep":
        options = [arg for arg in args if arg.startswith("-")]
        return all(re.fullmatch(r"-[EFGioSvwxz]+", arg) for arg in options) and len(args) - len(options) == 1
    if name in ("head", "tail"):
        return bool(re.fullmatch(r"(?:-[cn]\s*[-+]?\d+|[-+]\d+)", " ".join(args)))
    return bool(re.fullmatch(r"-[bcf]\s*[\d,-]+", " ".join(args)))


def _batch_location(plan, path, workspace, receipt):
    directory = Path(plan["directory"])
    directory = directory if directory.is_absolute() else Path(workspace) / directory
    if path.parent.resolve() == directory.resolve():
        return True
    scratch = path.parent
    control = scratch.parent.name
    if scratch.name != "scratch" or not re.fullmatch(r"tool-containment-[0-9a-f]{32}", control):
        return False
    if scratch.resolve() != (Path(workspace).resolve() / ".autocode" / control / "scratch"):
        return False
    manifest = scratch.parent / "verification-copy.json"
    verification = receipt.get("verification_copy") or {}
    if not manifest.is_symlink() and manifest.is_file() and verification.get("manifest_sha256") == file_hash(manifest):
        return True
    writer = scratch.parent / "writer-capture.json"
    if writer.is_symlink() or not writer.is_file() or writer.stat().st_mode & 0o222:
        return False
    return json.loads(writer.read_text()) == {
        "version": 1,
        "workspace": str(Path(workspace).resolve()),
        "scratch": str(scratch),
        "control": control,
    }


def _invocation(command, path, workspace, receipt, *, allow_batch, output):
    """Bind an event to an actual direct capture or the runner-owned batch helper.

    Shell wrappers, a workspace `cd`, and output-only filters are allowed. Other shell chains
    are not proof: a reprint plus a commented or inert `capture` word is not an execution.
    """
    if not isinstance(command, str) or not isinstance(output, str) or "`" in command or "$(" in command:
        return False
    try:
        words = _tokens(command)
        for _ in range(2):
            if (
                len(words) == 3
                and words[0] in ("/bin/sh", "/bin/bash", "/bin/zsh", "/usr/bin/bash", "/usr/bin/zsh")
                and words[1] in ("-c", "-lc")
            ):
                words = _tokens(words[2])
            elif (
                len(words) == 4
                and words[0] in ("/bin/bash", "/bin/zsh", "/usr/bin/bash", "/usr/bin/zsh")
                and words[1:3] == ["-l", "-c"]
            ):
                words = _tokens(words[3])
            else:
                break
        cwd = Path(workspace).resolve()
        if len(words) >= 3 and words[0] == "cd" and words[2] == "&&":
            target = Path(words[1])
            cwd = (target if target.is_absolute() else cwd / target).resolve()
            if not cwd.is_relative_to(Path(workspace).resolve()):
                return False
            words = words[3:]
        words = _without_redirects(words)
        chunks = [[]]
        for word in words:
            if word == "|":
                chunks.append([])
            elif word and all(char in ";&|<>()" for char in word):
                return False
            else:
                chunks[-1].append(word)
        if not chunks[0] or not all(_filter(chunk) for chunk in chunks[1:]):
            return False
        args = chunks[0]
        executable = Path(args[0])
        if executable.is_absolute() and executable == Path(sys.executable):
            if len(args) < 3:
                return False
            script = Path(args[1])
            expected = Path(__file__).resolve().with_name("autocode_ready_capture_batch.py")
            if script == expected:
                if (
                    not allow_batch
                    or len(args) != 3
                    or not (match := re.fullmatch(r"ready-[0-9a-f]{32}-(\d+)\.json", path.name))
                ):
                    return False
                plan = batch_plan._plan(args[2])
                index = int(match.group(1)) - 1
                if not 0 <= index < len(plan["commands"]) or plan["commands"][index] != receipt.get("command"):
                    return False
                if not _batch_location(plan, path, workspace, receipt):
                    return False
                references = []
                for line in output.splitlines():
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict) and value.get("evidence_ref") == str(path):
                            references.append(value)
                    except ValueError:
                        continue
                return (
                    len(references) == 1
                    and references[0].get("command") == receipt.get("command")
                    and references[0].get("exit_code") == receipt.get("exit_code")
                )
            if script != Path(__file__).resolve().with_name("autocode.py"):
                return False
            args = args[1:]
        else:
            return False
        if len(args) < 4 or args[1] != "capture" or "--" not in args:
            return False
        end = args.index("--")
        options = args[2:end]
        outputs = [options[i + 1] for i, word in enumerate(options[:-1]) if word == "--output"]
        outputs += [word.split("=", 1)[1] for word in options if word.startswith("--output=")]
        if len(outputs) != 1 or args[end + 1 :] != receipt.get("command"):
            return False
        target = Path(outputs[0])
        return (target if target.is_absolute() else cwd / target).resolve() == path.resolve()
    except (OSError, TypeError, ValueError, IndexError):
        return False


def authenticate(receipt, path, workspace, tool_outputs, *, receipt_only, capture_context):
    """The existing receipt checks, shared by acceptance and optional repair context."""
    raw = Path(receipt["full_output"])
    if (
        not raw.resolve().is_relative_to(Path(workspace).resolve() / ".autocode")
        or file_hash(raw) != receipt["full_output_sha256"]
    ):
        raise ValueError("Full check output missing or changed")
    if (capture_context or receipt.get("capture_context")) and receipt.get("capture_context") != capture_context:
        raise ValueError("Capture receipt does not belong to this validation attempt")
    if receipt_only:
        if not capture_context:
            raise ValueError("Capture receipt does not belong to this validation attempt")
        return "attempt_bound_capture"
    # capture prints one JSON object, then its attestation of the receipt's bytes on stderr
    # (autocode_receipts.attestation_line). Match structurally even if event text adds shell
    # notices or a display filter dropped the receipt line. No word-search for PASS/COMPLETE.
    try:
        receipt_bytes = path.read_bytes()
        attested = receipt_bytes if json.loads(receipt_bytes) == receipt else None
    except (OSError, ValueError):
        attested = None
    matched = False
    for item in tool_outputs:
        if not _invocation(
            item.get("command"),
            path,
            workspace,
            receipt,
            allow_batch=item.get("type") == "command_execution" and type(item.get("exit_code")) is int,
            output=item.get("aggregated_output", ""),
        ):
            continue
        for line in item.get("aggregated_output", "").splitlines():
            with contextlib.suppress(ValueError):
                value = json.loads(line)
                matched |= value == receipt or (attested is not None and receipts.attests(value, attested))
    if not matched:
        names = {str(path), os.path.relpath(path.resolve(), Path(workspace).resolve())}
        shortened = receipts.display_filter([item.get("command") for item in tool_outputs], names)
        raise ValueError(
            "No independently executed Validator tool event matches receipt; use this stage's exact capture_command"
            + (
                f": its capture command sent the output through `{shortened}`, which dropped both the receipt"
                " line and capture's short exit_code line. Run capture again without filtering its output"
                " and cite that new receipt."
                if shortened
                else ""
            )
        )
    return "executed_capture_event"
