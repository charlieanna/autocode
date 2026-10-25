"""Reproduction-first budget for the bug Investigator (#1902).

The Investigator's stage window used to be enforced only as a kill at its end. Live traces showed
a model reading 30 to 103 source files, running no probe, and losing the whole window with no
report. This module classifies each completed tool call in the provider's event stream, with no
model, as source exploration or as an attempt at reproduction. A budget of exploration calls that
passes before the first reproduction attempt is a stop reason: the supervisor ends the stage early,
and the existing timeout pause and its retry question apply unchanged.

A call that is neither (an unrecognized tool or command) counts as nothing, so a classification
miss delays the stop and never causes one. Pure module: it imports nothing from AutoCode and
reads no files. The activity monitor feeds it raw event rows; the supervisor asks it for a reason.
"""

from __future__ import annotations

import json
import re
import shlex

DEFAULT_BUDGET = 30  # completed exploration calls before the first reproduction attempt; 0 disables
SCRATCH_MARKER = ".autocode/investigation"
EXPLORATION_TOOLS = frozenset({"read", "grep", "glob", "list", "ls", "webfetch", "websearch", "codesearch"})
WRITE_TOOLS = frozenset({"edit", "write", "patch", "multiedit", "apply_patch", "applypatch"})
READ_ONLY = frozenset(
    {"cat", "head", "tail", "sed", "grep", "egrep", "fgrep", "rg", "ag", "find", "fd", "ls", "tree", "wc", "stat",
     "file", "sort", "uniq", "cut", "awk", "nl", "less", "more", "pwd", "echo", "which", "basename", "dirname"}
)  # fmt: skip
GIT_READ_ONLY = frozenset({"log", "show", "grep", "blame", "diff", "status", "ls-files", "rev-parse", "branch"})
NEUTRAL = frozenset({"cd", "export", "set", "true", ":", "time", "env", "timeout"})
RUNNERS = frozenset(
    {"python", "python3", "pytest", "py.test", "unittest", "node", "deno", "bun", "go", "cargo", "npm", "npx",
     "pnpm", "yarn", "make", "mvn", "gradle", "ruby", "php", "dotnet", "swift", "tsc", "vitest", "jest", "bash", "sh"}
)  # fmt: skip
SEPARATORS = re.compile(r"\|\||&&|[|;&\n]")
EXPLORATION, REPRODUCTION = "exploration", "reproduction"


def _program(segment: str) -> str | None:
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    while words and ("=" in words[0] and not words[0].startswith("-") and words[0].split("=")[0].isidentifier()):
        words = words[1:]  # leading VAR=value assignments
    return words[0].rsplit("/", 1)[-1] if words else None


def classify_command(command: str) -> str | None:
    """``exploration`` when every part of a shell command only reads, ``reproduction`` when any part
    runs code or a test, else None. Whole-command reading is deliberate: a pipeline that ends in a
    runner is a probe, one that only reads and filters is exploration."""
    segments = [part.strip() for part in SEPARATORS.split(command) if part.strip()]
    kinds = []
    for segment in segments:
        program = _program(segment)
        if program is None or program in NEUTRAL:
            continue
        if program == "git":
            words = segment.split()
            sub = next((w for w in words[1:] if not w.startswith("-")), "")
            kinds.append(EXPLORATION if sub in GIT_READ_ONLY else None)
        elif program == "sed" and re.search(r"(^|\s)-\w*i", segment):
            kinds.append(REPRODUCTION if SCRATCH_MARKER in segment else None)
        elif program in READ_ONLY:
            kinds.append(EXPLORATION)
        elif program in RUNNERS or program.startswith(("python", "pytest")) or SCRATCH_MARKER in segment:
            kinds.append(REPRODUCTION)
        else:
            kinds.append(None)
    if REPRODUCTION in kinds:
        return REPRODUCTION
    return EXPLORATION if kinds and all(kind == EXPLORATION for kind in kinds) else None


def classify_tool(tool: str, given: dict) -> str | None:
    tool = tool.lower()
    if tool in EXPLORATION_TOOLS:
        return EXPLORATION
    if tool in WRITE_TOOLS:
        return REPRODUCTION  # the Investigator may write only to its scratch copy
    if tool in ("bash", "shell") and isinstance(given.get("command"), str):
        return classify_command(given["command"])
    return None


def classify_row(row: dict) -> tuple[str, str] | None:
    """(call id, class) for a completed tool event in either provider shape, else None."""
    item = row.get("item")
    if isinstance(item, dict):
        if row.get("type") != "item.completed" or not isinstance(item.get("id"), (str, int)):
            return None
        kind = item.get("type")
        if kind == "command_execution" and isinstance(item.get("command"), str):
            found = classify_command(item["command"])
        elif kind == "file_change":
            found = REPRODUCTION
        else:
            return None
        return (str(item["id"]), found) if found else None
    part = row.get("part")
    if row.get("type") != "tool_use" or not isinstance(part, dict) or not isinstance(part.get("id"), str):
        return None
    state = part.get("state")
    if not isinstance(state, dict) or state.get("status") not in ("completed", "error"):
        return None
    given: dict = state["input"] if isinstance(state.get("input"), dict) else {}
    found = classify_tool(str(part.get("tool") or ""), given)
    return (part["id"], found) if found else None


PATH_KEYS = ("filePath", "file_path", "path")
MAX_PATHS = 40
READ_BYTES = 1 << 20


def summarize(rows, workspace=None) -> dict:
    """What a stopped attempt explored, for the exact retry: call counts and the files it read.

    Display-grade facts about a failed attempt, never evidence. ``rows`` are raw event objects.
    """
    explored, reproduced = 0, 0
    seen: set[str] = set()
    paths: list[str] = []
    prefix = str(workspace).rstrip("/") + "/" if workspace else None
    for row in rows:
        found = classify_row(row) if isinstance(row, dict) else None
        if found is None or found[0] in seen:
            continue
        seen.add(found[0])
        if found[1] == REPRODUCTION:
            reproduced += 1
            continue
        explored += 1
        part = row.get("part")
        given = ((part or {}).get("state") or {}).get("input") if isinstance(part, dict) else None
        for key in PATH_KEYS:
            path = given.get(key) if isinstance(given, dict) else None
            if isinstance(path, str) and path and len(paths) < MAX_PATHS:
                path = path[len(prefix) :] if prefix and path.startswith(prefix) else path
                if path not in paths:
                    paths.append(path)
    return {"explored_calls": explored, "reproduction_attempts": reproduced, "explored_paths": paths}


def tail_rows(path) -> list[dict]:
    """The JSON object rows in the last megabyte of an event file; unreadable input has none."""
    try:
        with open(path, "rb") as source:
            size = source.seek(0, 2)
            source.seek(max(0, size - READ_BYTES))
            lines = source.read(READ_BYTES).decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    found = []
    for line in lines[1:] if size > READ_BYTES else lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            found.append(row)
    return found


class Budget:
    """Counts completed exploration calls until the first reproduction attempt."""

    MAX_SEEN = 32768

    def __init__(self, limit: int | None = DEFAULT_BUDGET):
        self.limit = limit if type(limit) is int and limit > 0 else 0
        self.explored = 0
        self.reproduced = False
        self._seen: set[str] = set()

    def observe(self, row) -> None:
        if not self.limit or self.reproduced or not isinstance(row, dict):
            return
        found = classify_row(row)
        if found is None or found[0] in self._seen or len(self._seen) >= self.MAX_SEEN:
            return
        self._seen.add(found[0])
        if found[1] == REPRODUCTION:
            self.reproduced = True
        else:
            self.explored += 1

    def reason(self) -> str | None:
        if not self.limit or self.reproduced or self.explored < self.limit:
            return None
        return (
            f"Stopped after {self.explored} source-exploration calls without attempting a "
            f"reproduction (budget {self.limit}; limits.investigation_exploration_budget)"
        )
