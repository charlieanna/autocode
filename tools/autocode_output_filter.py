"""Conservative display filtering. Original bytes and proof belong to the caller.

Only verified unittest output is eligible. Unknown commands, JSON, diagnostics,
and line order remain verbatim. No deduplication of arbitrary repeated lines.
"""
import re
import shlex
from pathlib import Path


def command_kind(command):
    try:
        args = shlex.split(command) if isinstance(command, str) else list(command or [])
    except ValueError:
        return None
    if (len(args) >= 3 and re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(args[0]).name)
            and args[1:3] == ["-m", "unittest"]):
        return "python-unittest"
    return None


def compact_output(text, *, enabled=True, command=None, exit_code=None):
    result = {"format": "text", "content": text, "omitted_progress_lines": 0,
              "repeated_lines": {}, "omitted_sections": [], "filter": "raw"}
    if not enabled or command_kind(command) != "python-unittest":
        return result
    # An interrupted run or custom runner without the standard footer is raw.
    footer = re.search(r"(?m)^Ran \d+ tests? in [\d.]+s\r?\n\r?\n(OK(?: \([^\r\n]*\))?|FAILED \([^\r\n]*\))\r?\n?\Z", text)
    if not footer or (exit_code == 0) != footer[1].startswith('OK') or exit_code not in (0, 1):
        return result
    # Use the retained byte reader's boundaries: Unicode separators in messages
    # must not shift references to later physical output lines.
    lines = [line.decode('utf-8') for line in text.encode('utf-8').splitlines(keepends=True)]
    kept, pending = [], []
    diagnostics = False

    def flush():
        if len(pending) >= 3 or (pending and all(re.fullmatch(r"\.+", line.strip()) for _, line in pending)):
            start, end = pending[0][0], pending[-1][0]
            result["omitted_sections"].append({"start_line": start, "end_line": end,
                                                "kind": "passing-tests-or-progress"})
            result["omitted_progress_lines"] += len(pending)
            kept.append(f"[AutoCode omitted passing test/progress lines {start}-{end}; exact original retained]\n")
        else:
            kept.extend(line for _, line in pending)
        pending.clear()

    for number, line in enumerate(lines, 1):
        bare = line.rstrip("\r\n")
        if (bare.startswith(("FAIL:", "ERROR:", "Traceback", "FAILED", "ERROR "))
                or re.fullmatch(r"={5,}", bare)):
            diagnostics = True
        passing = re.fullmatch(r"test[^\r\n]* \([^\r\n]+\) \.\.\. ok", bare)
        progress = re.fullmatch(r"\.+", bare)
        if not diagnostics and (passing or progress):
            pending.append((number, line))
        else:
            flush()
            kept.append(line)
    flush()
    if result["omitted_sections"]:
        result.update(content="".join(kept), filter="python-unittest-v1")
    return result
