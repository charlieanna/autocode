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

import re
import subprocess
from pathlib import Path, PurePosixPath

MAX_FILES = 2
MAX_LINES = 60
MAX_CHARS = 3000
TEST_FILE = re.compile(r"(^test_.*|.*_test|.*\.test|.*\.spec|.*_spec)\.(py|go|js|jsx|ts|tsx|rb|rs)$")
HEADER = """
EXISTING TEST STYLE: the excerpts below are the opening lines of this project's own tests, the ones closest
to your task. They are examples to follow, not files to edit (unless current_task.affected_paths lists
them). Write every new test the same way: the same framework, imports, fixtures and helpers, file layout,
naming and assertion style. When the task names English test cases, still name each test after its case id
(test_<id>_...), unless the plan declared its test name right after test: or guard: (test: TestFixedReturnsTwo):
then use that name exactly.
"""


def is_test_file(path: str) -> bool:
    return bool(TEST_FILE.match(PurePosixPath(path).name))


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


def excerpt(workspace, path: str) -> str:
    try:
        lines = (Path(workspace) / path).read_text(errors="replace").splitlines()
    except OSError:
        return ""
    shown = "\n".join(lines[:MAX_LINES])[:MAX_CHARS]
    more = f" of {len(lines)}" if len(lines) > MAX_LINES else ""
    return f"--- {path} (lines 1-{min(len(lines), MAX_LINES)}{more}) ---\n{shown}\n"


def section(workspace, current_task: dict | None) -> str:
    """The prompt section for a Builder, or "" when the project has no test near the task."""
    task = current_task or {}
    chosen = choose(list(task.get("affected_paths") or []), tracked_tests(workspace))
    parts = [part for part in (excerpt(workspace, path) for path in chosen) if part]
    return HEADER + "\n" + "\n".join(parts) if parts else ""


def add_to_prompt(prompt: str, workspace, current_task: dict | None) -> str:
    """Insert the section before the handoff data (providers read the JSON after that marker)."""
    extra = section(workspace, current_task)
    if not extra:
        return prompt
    marker = "\nCURRENT HANDOFF DATA\n"
    return prompt.replace(marker, extra + marker, 1) if marker in prompt else prompt + extra
