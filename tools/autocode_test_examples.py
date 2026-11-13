"""Excerpts of the project's own tests for the Builder, so new tests match their style.

A Builder that writes tests without seeing the project's existing ones invents
its own framework, helpers and naming. This picks the existing test files
closest to the current task and adds their opening lines to the Builder's
prompt: imports, fixtures, helpers and the first few tests, which is where a
project's test conventions show. They are read-only examples; the task's
affected_paths still decide what the Builder may edit.

Order of choice: test files the task itself will edit, then tests named after
a source file the task edits (weeks.py -> test_weeks.py), then tests in the same
directories; with none of those, the shallowest test in the repository. Only
tracked files are read. Imports nothing from the runner.
"""

from __future__ import annotations

try:
    from . import autocode_project_kinds as project_kinds
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_project_kinds as project_kinds
    import autocode_prompts as prompts


import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

MAX_FILES = 2
MAX_LINES = 60
MAX_CHARS = 3000
HEADER = prompts.get("fragments/test-examples/header.md")


def is_test_file(path: str) -> bool:
    """A test file by its name, in any language autocode_project_kinds knows."""
    return project_kinds.is_test_file(path)


def tracked_tests(workspace) -> list[str]:
    result = subprocess.run(["git", "-C", str(workspace), "ls-files", "-z"], capture_output=True, text=True)
    if result.returncode:
        return []
    return sorted(path for path in result.stdout.split("\0") if path and is_test_file(path))


def _stem(path: str) -> str:
    """weeks.py, test_weeks.py, weeks_test.go and weeks.spec.ts all have the stem 'weeks'."""
    name = PurePosixPath(path).name.split(".")[0]
    return re.sub(r"^test_|_test$|_spec$", "", name).lower()


def choose(task_paths: list[str], tests: list[str]) -> list[str]:
    """The test files closest to the task, best first, at most MAX_FILES."""
    task_paths = [str(path).strip("/") for path in task_paths if str(path).strip()]
    sources = [path for path in task_paths if not is_test_file(path)]
    stems = {_stem(path) for path in sources}
    folders = {str(PurePosixPath(path).parent) for path in task_paths}
    # Among equals, prefer tests in the task's own language (a .py task learns from .py tests).
    languages = {PurePosixPath(path).suffix for path in sources}
    other = lambda test: bool(languages) and PurePosixPath(test).suffix not in languages

    def rank(test: str):
        own = any(test == path or test.startswith(path.rstrip("/") + "/") for path in task_paths)
        named = _stem(test) in stems
        near = str(PurePosixPath(test).parent) in folders
        return (not own, not named, not near, other(test), test)

    chosen = [test for test in sorted(tests, key=rank) if rank(test)[:3] != (True, True, True)]
    # Nothing near the task (a new area of the code): any one test still shows the conventions.
    return chosen[:MAX_FILES] or sorted(tests, key=lambda test: (other(test), len(PurePosixPath(test).parts), test))[:1]


def excerpt(workspace, path: str, *, attached_files=()) -> str:
    try:
        content = (Path(workspace) / path).read_bytes()
    except OSError:
        return ""
    text = content.decode("utf-8", errors="replace")
    lines = text.splitlines()
    shown = "\n".join(lines[:MAX_LINES])[:MAX_CHARS]
    more = f" of {len(lines)}" if len(lines) > MAX_LINES else ""
    original = f"--- {path} (lines 1-{min(len(lines), MAX_LINES)}{more}) ---\n{shown}\n"
    for index, row in enumerate(attached_files):
        if (
            isinstance(row, dict)
            and row.get("path") == path
            and row.get("content") == text
            and text.encode("utf-8") == content
            and row.get("sha256") == hashlib.sha256(content).hexdigest()
        ):
            reference = prompts.get("fragments/test-examples/source-reference.md").format(path, index)
            if len(reference.encode()) < len(original.encode()):
                return reference
    return original


def section(workspace, current_task: dict | None, *, attached_files=()) -> str:
    """The prompt section for a Builder, or "" when the project has no test near the task."""
    task = current_task or {}
    chosen = choose(list(task.get("affected_paths") or []), tracked_tests(workspace))
    parts = [part for part in (excerpt(workspace, path, attached_files=attached_files) for path in chosen) if part]
    return HEADER + "\n" + "\n".join(parts) if parts else ""


def add_to_prompt(prompt: str, workspace, current_task: dict | None) -> str:
    """Insert the section before the handoff data (providers read the JSON after that marker)."""
    marker = "\nCURRENT HANDOFF DATA\n"
    # A small clear bug fix can already carry these complete tests as authenticated
    # source. Keep that canonical packet byte-identical and reference only an exact
    # duplicate; missing, stale or partial attachments retain the ordinary excerpt.
    attached_files: list[object] | tuple[()] = ()
    try:
        data = json.loads(prompt.partition(marker)[2])
        source = data.get("review_source") if isinstance(data, dict) and data.get("stage") == "builder" else None
        files = source.get("files") if isinstance(source, dict) else None
        if isinstance(files, list):
            attached_files = files
    except ValueError:
        pass
    extra = section(workspace, current_task, attached_files=attached_files)
    if not extra:
        return prompt
    return prompt.replace(marker, extra + marker, 1) if marker in prompt else prompt + extra


def builder_prompt(prompt: str, workspace, current_task: dict | None, notes: str = "") -> str:
    """A Builder's stage prompt: static notes, then the attempt-specific section, then the handoff (A5, #2113)."""
    if notes:
        marker = "\nCURRENT HANDOFF DATA\n"
        prompt = prompt.replace(marker, notes + marker, 1) if marker in prompt else prompt + notes
    return add_to_prompt(prompt, workspace, current_task)
