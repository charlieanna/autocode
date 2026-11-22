"""Authenticate a captured check against a runner-owned attempt or executed event."""

import contextlib
import json
import os
import re
import shlex
import shutil
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
    """Shell words; an unquoted newline separates commands as ``;`` does, so it is its own ``;`` word."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    words = []
    for word in lexer:
        if word and all(char in ";&|<>()\n" for char in word) and "\n" in word:
            word = word.replace("\n", "") or ";"
        if word != ";" or (words and words[-1] not in (";", "&&")):
            words.append(word)
    while words and words[0] == ";":
        words.pop(0)
    while words and words[-1] == ";":
        words.pop()
    return words


_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LITERAL = re.compile(r"[^$`\\*?\[\]~{}\n]*")


def _setup(step, cwd, names, workspace):
    """The directory after one step that prints nothing a receipt could be read from, else None.

    Live Testers wrote ``cd <workspace>; C="<capture>"; $C --output ... -- ...``: a ``cd`` inside the
    workspace, ``mkdir -p`` and assignments of literal text, joined to the capture runs by ``;``,
    ``&&`` or a newline. None of them can print a receipt or its attestation. An assignment is kept
    in ``names``; a ``cd`` out of the workspace is refused as before.
    """
    if len(step) == 2 and step[0] == "cd":
        target = Path(_expand(step[1], names))
        cwd = (target if target.is_absolute() else cwd / target).resolve()
        return cwd if cwd.is_relative_to(Path(workspace).resolve()) else False
    if len(step) >= 3 and step[:2] == ["mkdir", "-p"] and not any(word.startswith("-") for word in step[2:]):
        return cwd
    if len(step) == 1 and "=" in step[0] and _NAME.fullmatch(step[0].split("=", 1)[0]):
        name, value = step[0].split("=", 1)
        if not _LITERAL.fullmatch(value):
            return False
        names[name] = value
        return cwd
    return None


def _expand(word, names):
    """A ``$NAME`` or ``${NAME}`` word set by a literal assignment, as the shell splits it."""
    match = re.fullmatch(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})", word)
    name = match and (match.group(1) or match.group(2))
    return names.get(name, word)


def _substitutes(command):
    """Whether the shell running this command line substitutes another program into it.

    `` ` `` and ``$(`` run a program before the line runs, so its words are not what executed.
    In single quotes, ANSI-C ``$'...'`` quotes, a comment or after a backslash they are literal
    text, such as the ``sh -c '...'`` program a capture runs, which its receipt records as argv.
    The scan reads quotes and comments as the shell does, so no quote it imagines hides a live
    substitution; an unterminated quote counts as one.
    """
    quote, escaped, index = "", False, 0
    while index < len(command):
        character = command[index]
        if escaped:
            escaped = False
        elif quote in ("'", "$'"):
            if quote == "$'" and character == "\\":
                escaped = True
            elif character == "'":
                quote = ""
        elif character == "\\":
            escaped = True
        elif character == "`" or command.startswith("$(", index):
            return True
        elif quote:
            quote = "" if character == '"' else quote
        elif command.startswith("$'", index):
            quote, index = "$'", index + 1
        elif character in "'\"":
            quote = character
        elif character == "#" and (index == 0 or command[index - 1] in " \t\n;&|()<>"):
            newline = command.find("\n", index)
            index = len(command) if newline < 0 else newline
            continue
        index += 1
    return bool(quote) or escaped


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
    if not words:
        return False
    # A bare name is the system program when this host's PATH finds it there, as the shell's did.
    program = shutil.which(words[0]) if "/" not in words[0] else words[0]
    if (
        words[0] not in ("grep", "head", "tail", "cut")
        and "/" not in words[0]
        or program
        not in {f"{root}/{name}" for root in ("/bin", "/usr/bin") for name in ("grep", "head", "tail", "cut")}
    ):
        return False
    name, args = Path(words[0]).name, words[1:]
    if name == "grep":
        options = [arg for arg in args if arg.startswith("-")]
        return all(re.fullmatch(r"-[EFGioSvwxz]+", arg) for arg in options) and len(args) - len(options) == 1
    if name in ("head", "tail"):
        return bool(re.fullmatch(r"(?:-[cn]\s*[-+]?\d+|[-+]\d+)", " ".join(args)))
    return bool(re.fullmatch(r"-[bcf]\s*[\d,-]+", " ".join(args)))


def _shown(tool_outputs, names, receipt, attested):
    """Whether an event that names this receipt printed it or capture's attestation of it."""
    for item in tool_outputs:
        command = item.get("command")
        if not isinstance(command, str) or "capture" not in command or not any(name in command for name in names):
            continue
        for line in str(item.get("aggregated_output", "")).splitlines():
            with contextlib.suppress(ValueError):
                value = json.loads(line)
                if value == receipt or (attested is not None and receipts.attests(value, attested)):
                    return True
    return False


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


def _run(words, cwd, names, path, workspace, receipt, *, allow_batch, output):
    """Whether one capture run, with output-only filters, wrote this receipt; None when it is not one run."""
    words = _without_redirects(words)
    chunks = [[]]
    for word in words:
        if word == "|":
            chunks.append([])
        elif word and all(char in ";&|<>()" for char in word):
            return None
        else:
            chunks[-1].append(word)
    if not chunks[0] or not all(_filter(chunk) for chunk in chunks[1:]):
        return None
    args = [part for word in chunks[0] for part in (_expand(word, names).split() if word.startswith("$") else [word])]
    executable = Path(args[0])
    if executable.is_absolute() and executable == Path(sys.executable):
        if len(args) < 3:
            return None
        script = Path(args[1])
        expected = Path(__file__).resolve().with_name("autocode_ready_capture_batch.py")
        if script == expected:
            if (
                not allow_batch
                or len(args) != 3
                or not (match := re.fullmatch(r"ready-[0-9a-f]{32}-(\d+)\.json", path.name))
            ):
                return None
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
            return None
        args = args[1:]
    else:
        return None
    if len(args) < 4 or args[1] != "capture" or "--" not in args:
        return None
    end = args.index("--")
    options = args[2:end]
    outputs = [options[i + 1] for i, word in enumerate(options[:-1]) if word == "--output"]
    outputs += [word.split("=", 1)[1] for word in options if word.startswith("--output=")]
    if len(outputs) != 1:
        return None
    if args[end + 1 :] != receipt.get("command"):
        return False
    if "sphinx_reference_control" in receipt:
        proof = receipt["sphinx_reference_control"]
        documents = [options[i + 1] for i, word in enumerate(options[:-1]) if word == "--reference-doc"]
        documents += [word.split("=", 1)[1] for word in options if word.startswith("--reference-doc=")]
        if not isinstance(proof, dict) or sorted(documents) != proof.get("documents"):
            return False
    target = Path(outputs[0])
    return (target if target.is_absolute() else cwd / target).resolve() == path.resolve()


def _invocation(command, path, workspace, receipt, *, allow_batch, output):
    """Bind an event to an actual direct capture or the runner-owned batch helper.

    Shell wrappers, setup steps that print nothing (``_setup``), other capture runs and output-only
    filters are allowed. Other shell chains are not proof: a reprint plus a commented or inert
    `capture` word is not an execution.
    """
    if not isinstance(command, str) or not isinstance(output, str) or _substitutes(command):
        return False
    try:
        words = _tokens(command)
        for _ in range(2):
            if (
                len(words) == 3
                and words[0] in ("/bin/sh", "/bin/bash", "/bin/zsh", "/usr/bin/bash", "/usr/bin/zsh")
                and words[1] in ("-c", "-lc")
            ):
                body = words[2]
            elif (
                len(words) == 4
                and words[0] in ("/bin/bash", "/bin/zsh", "/usr/bin/bash", "/usr/bin/zsh")
                and words[1:3] == ["-l", "-c"]
            ):
                body = words[3]
            else:
                break
            # The wrapper's shell runs its body as a new command line.
            if _substitutes(body):
                return False
            words = _tokens(body)
        cwd, names, runs, step = Path(workspace).resolve(), {}, [], []
        for word in [*words, ";"]:
            if word not in (";", "&&"):
                step.append(word)
                continue
            if not step:
                return False
            after = _setup(step, cwd, names, workspace)
            if after is False:
                return False
            if after is None:
                runs.append((step, cwd, dict(names)))
            else:
                cwd = after
            step = []
        if not runs:
            return False
        matched = False
        for step, cwd, names in runs:
            # Every other run in the line is capture too, which prints only its own receipt lines.
            found = _run(
                step, cwd, names, path, workspace, receipt, allow_batch=allow_batch and len(runs) == 1, output=output
            )
            if found is None:
                return False
            matched |= found
        return matched
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
        if _shown(tool_outputs, names, receipt, attested):
            # The receipt reached the output, so no filter dropped it: the command line around the
            # capture run is what the runner could not read as one run (saying "dropped" sent
            # fresh attempts back to the same command, #4110).
            detail = (
                ": the event's output shows this receipt, but its command line ran capture together with"
                " other commands, so it is not one capture run. Run capture as its own Bash command, with"
                " at most `cd <workspace> &&` before it and `2>&1 | tail -c N` after it, and cite that receipt."
            )
        elif shortened:
            detail = (
                f": its capture command sent the output through `{shortened}`, which dropped both the receipt"
                " line and capture's short exit_code line. Run capture again without filtering its output"
                " and cite that new receipt."
            )
        else:
            detail = ""
        raise ValueError(
            "No independently executed Validator tool event matches receipt; use this stage's exact capture_command"
            + detail
        )
    return "executed_capture_event"
