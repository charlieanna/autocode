"""What a Tester's command line shows about one command it ran inside a longer line (#4165).

A Tester may run ``python3 -m pytest tests/test_number.py -q 2>&1 | tail -3`` or
``git diff --stat HEAD; git diff --name-only HEAD`` as one Bash call and then cite the one
command it cared about. The call's own exit code is the line's, not that command's, so the
check was refused as "not supported by an exact executed Validator event", and a report-only
repair (which cannot run anything) was refused again for the same reason. In the 2026-11-24
Claude starter sweep that was the most common rejected Tester answer.

The line still shows something exact. Read as the shell runs it:

- ``exact``: the cited command is the last one the line runs and nothing after it can change the
  exit code (``a; b`` cited ``b``), or the line exited 0 and the cited command is one of the
  commands every one of which must have exited 0 for that (``cd <workspace> && a && b``). Its exit
  code is then known from the line's own, and nothing else is needed.
- ``ran``: the cited command certainly ran to the end (the first command after a ``;``, or the
  producer of a pipeline whose filters read all of their input, such as ``| tail -3``), but its
  exit code is hidden behind the line's. Only an observation the runner makes itself can supply
  it: the caller accepts this only where its clean-copy replay re-runs that command and sees it
  exit 0 (``autopilot.apply_review_result``), and refuses it everywhere else, as before.

Anything the line could make different from the cited command run alone (a substitution, a
background job, ``||``, a subshell, a here-document, ``set``, ``cd`` out of the workspace, a
variable assignment, ``exit``) gives no answer, and the check is refused exactly as before.

Pure functions over recorded strings; imports only autocode_event_matching.
"""

from __future__ import annotations

import shlex
from pathlib import Path

try:
    from . import autocode_event_matching as event_matching
except ImportError:
    import autocode_event_matching as event_matching

EXACT, RAN = "exact", "ran"

# Pipeline stages that read all of their input before they exit, so the command before them ran to
# its end. head, sed, awk and grep -q/-m can stop early and cut that command off with SIGPIPE.
_FULL_READERS = {"tail", "grep", "egrep", "fgrep", "cut", "wc", "sort", "uniq", "cat", "tr", "tee", "nl"}
_EARLY_EXIT = ("--quiet", "--silent", "--max-count")
# Commands that change how the rest of the line runs, or what a later command means.
_STATEFUL = {
    "set",
    "shopt",
    "setopt",
    "unsetopt",
    "trap",
    "exec",
    "eval",
    "source",
    ".",
    "alias",
    "unalias",
    "export",
    "unset",
    "declare",
    "typeset",
    "local",
    "readonly",
    "exit",
    "return",
    "pushd",
    "popd",
    "function",
    "ulimit",
    "umask",
    "builtin",
    "enable",
    "wait",
}


def _split(body):
    """``[[pipeline, ...], ...]``: groups run one after another (``;`` or a newline), each an ``&&``
    chain of pipelines (``|`` or ``|&``), each a list of command texts. None for anything else."""
    groups, pipelines, commands = [], [], []
    start, quote, escaped, index = 0, "", False, 0

    def take(end):
        text = body[start:end].strip()
        if not text:
            raise ValueError("empty command")
        commands.append(text)

    try:
        while index < len(body):
            char = body[index]
            nxt = body[index + 1] if index + 1 < len(body) else ""
            if escaped:
                escaped = False
            elif char == "\\" and quote != "'":
                escaped = True
            elif quote:
                if char == quote:
                    quote = ""
                elif char == "`" or (char == "$" and nxt == "(" and quote == '"'):
                    return None
            elif char in "'\"":
                quote = char
            elif char in "`()#{}" or (char == "$" and nxt in "({") or char == "<" and nxt in "<(":
                return None
            elif char == "|":
                if nxt == "|":
                    return None
                take(index)
                index += 2 if nxt == "&" else 1
                start = index
                continue
            elif char == "&":
                if nxt == "&":
                    take(index)
                    pipelines.append(commands)
                    commands = []
                    index += 2
                    start = index
                    continue
                if not (index and body[index - 1] in "<>") and nxt != ">":
                    return None  # a background job
            elif char in ";\n":
                if nxt == ";":
                    return None
                if body[start:index].strip() or commands or pipelines:
                    take(index)
                    pipelines.append(commands)
                    groups.append(pipelines)
                    pipelines, commands = [], []
                start = index + 1
            index += 1
        if quote or escaped:
            return None
        if body[start:].strip() or commands or pipelines:
            take(len(body))
            pipelines.append(commands)
            groups.append(pipelines)
    except ValueError:
        return None
    return groups or None


def _words(text):
    try:
        return shlex.split(text)
    except ValueError:
        return None


def _allowed(command, workspace):
    """Whether a command in the line leaves the shell as the next command finds it."""
    words = _words(command)
    if not words:
        return False
    first = words[0]
    if "=" in first and first.split("=", 1)[0].isidentifier() and len(words) == 1:
        return False  # an assignment the next command would read
    if first == "cd":
        if workspace is None or len(words) != 2:
            return False
        target = Path(words[1])
        target = target if target.is_absolute() else Path(workspace) / target
        return target.resolve() == Path(workspace).resolve()
    return first not in _STATEFUL


def _full_reader(command):
    words = _words(command)
    if not words or Path(words[0]).name not in _FULL_READERS:
        return False
    if Path(words[0]).name not in ("grep", "egrep", "fgrep"):
        return True
    for word in words[1:]:
        if word == "--":
            break
        if word.startswith("--"):
            if word.split("=", 1)[0] in _EARLY_EXIT:
                return False
        elif word.startswith("-") and set(word[1:]) & {"q", "m"}:
            return False  # grep -q or -m (also inside -qE, -m1) stops reading early
    return True


def _same(element, cited):
    """The cited text is the element, or the element with its stderr joined to its output."""
    element, cited = element.strip(), cited.strip()
    if element == cited:
        return True
    for suffix in (" 2>&1", " 2>/dev/null"):
        if element.endswith(suffix) and element[: -len(suffix)].rstrip() == cited:
            return True
    return False


def resolve(cited, body, exit_code, *, workspace=None):
    """``(EXACT, code)``, ``(RAN, None)`` or None for ``cited`` run inside the command line ``body``.

    ``exit_code`` is the line's own recorded exit code (an int; anything else gives None).
    """
    if not isinstance(cited, str) or not cited.strip() or type(exit_code) is not int:
        return None
    if body.strip() == cited.strip():
        return None  # not a compound line: the caller's exact match decides
    groups = _split(body)
    if not groups or not all(_allowed(c, workspace) for g in groups for p in g for c in p):
        return None
    last = len(groups) - 1
    found = None
    for g, pipelines in enumerate(groups):
        for p, commands in enumerate(pipelines):
            # The pipeline certainly ran: it starts its group (a ; never skips one), or the line
            # exited 0 with this pipeline in the last group's && chain, so every one of them ran.
            ran = p == 0 or (g == last and exit_code == 0)
            for c, command in enumerate(commands):
                if not _same(command, cited):
                    continue
                if len(commands) == 1:
                    if g == last and len(pipelines) == 1:
                        answer = (EXACT, exit_code)
                    elif g == last and exit_code == 0:
                        answer = (EXACT, 0)
                    elif ran:
                        answer = (RAN, None)
                    else:
                        continue
                elif c == 0 and ran and all(_full_reader(rest) for rest in commands[1:]):
                    answer = (RAN, None)
                else:
                    continue
                if found is not None and found != answer:
                    return None  # the same command twice with different answers: none is the cited one
                found = answer
    return found


def hidden_message(cited, line):
    """Why a cited command whose exit code the line hid needs the runner's own run of it."""
    shown = line if len(line) <= 160 else line[:157] + "..."
    return (
        f"Check `{cited}` ran only inside `{shown}`, which hides its own exit code. The runner accepts it only"
        " when its clean-copy replay of a passing report runs it and sees it exit 0; this report is not replayed"
        " or the replay did not show that. Run each command you cite as its own Bash call, exactly as cited, or"
        " capture it."
    )


def require_replayed(hidden, replay):
    """Refuse unless the runner's clean-copy replay ran each hidden-exit check and saw it exit 0.

    ``hidden`` lists what ``autocode_support.verify_checks`` bound with a hidden exit code;
    ``replay`` is the validation's ``check_replay`` (None when the report was not replayed). A row the
    replay excused as the base's own failure, or one that errored or timed out, shows no exit 0.
    """
    rows = (replay or {}).get("checks") if isinstance(replay, dict) else None
    for check in hidden:
        seen = [
            row
            for row in rows or []
            if isinstance(row, dict)
            and row.get("command") == check["command"]
            and row.get("evidence_ref") == check["evidence_ref"]
        ]
        if not seen or not all(
            row.get("exit_code") == 0
            and not row.get("timed_out")
            and not row.get("error")
            and not row.get("base_attribution")
            for row in seen
        ):
            raise ValueError(hidden_message(check["command"], check["line"]))


def part(executed, cited, exit_code, workspace):
    """``resolve`` on the recorded line ``executed``, as recorded or unwrapped from its login shell; None when
    it shows nothing, or when the cited command is a capture run (its receipt, not the line, proves it)."""
    if not isinstance(executed, str) or event_matching.capture_invocation(cited or "") is not None:
        return None
    bodies = event_matching._command_bodies(executed)
    answers = {resolve(cited, body, exit_code, workspace=workspace) for body in bodies} - {None}
    return answers.pop() if len(answers) == 1 else None


def bind(check, item, workspace, missing_exit, hidden_exit):
    """Bind ``check`` to the executed line ``item`` it ran inside; True when bound, ``check`` unchanged otherwise.

    An exact answer must equal a claimed exit code. A hidden one binds only a check claiming exit 0, and
    only when the caller passes ``hidden_exit``, where it is listed for ``require_replayed``.
    """
    if not isinstance(item.get("id"), str) or not item["id"]:
        return False
    answer = part(item.get("command"), check.get("command"), item.get("exit_code"), workspace)
    if answer is None:
        return False
    kind, code = answer
    if kind == RAN:
        if hidden_exit is None or check.get("exit_code") not in (0, None):
            return False
        code = 0
        hidden_exit.append(
            {"command": check["command"], "evidence_ref": "event:" + item["id"], "line": item["command"]}
        )
    elif not missing_exit and code != check["exit_code"]:
        return False
    check["evidence_ref"] = "event:" + item["id"]
    check["exit_code"] = code
    return True
