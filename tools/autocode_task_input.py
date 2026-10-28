"""Literal task input and its UTF-8 identity; no runner or provider dependencies.

assign() owns task_sha256, written when a run starts or its follow-up rewrites
task. The status view computes the same digest from saved text, including for
legacy runs; neither reading input nor hashing it evaluates shell syntax.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import hashlib
from pathlib import Path
from typing import TextIO


def digest(task: str) -> str:
    """Hash precisely the UTF-8 text supplied to the run, without normalization."""
    return hashlib.sha256(task.encode("utf-8")).hexdigest()


def status_digest(task: object) -> str | None:
    """Legacy JSON may contain non-UTF-8 text; a read-only view stays available."""
    if not isinstance(task, str):
        return None
    try:
        return digest(task)
    except UnicodeEncodeError:
        return None


def assign(state: RunState, task: str) -> None:
    """Update the existing task and its saved identity together."""
    state["task"] = task
    state["task_sha256"] = digest(task)


def load(task: str | None, *, brief: str | None, brief_file: Path | None, stdin: TextIO) -> str | None:
    """Read an explicit brief literally; positional task behavior stays intact."""
    if brief is None and brief_file is None:
        if task is not None:
            task.encode("utf-8")
            if not task.strip():
                # `autocode "   "` started a worktree and a run for a blank task (#2404).
                raise ValueError('The task is blank; say it in words, for example: autocode "Build a greeting CLI"')
            if not any(c.isspace() for c in task) and Path(task).is_file():
                # `autocode spec.md` ran the literal two words as the task; nothing said so (#2323).
                raise ValueError(
                    f"{task} is a file, and the positional task is literal text. To run the file's contents: "
                    f"autocode --brief-file {task}; to run the words themselves: autocode --brief {task}"
                )
        return task
    if task is not None:
        raise ValueError("Use either the positional task, --brief, or --brief-file")
    if brief is not None and brief_file is not None:
        raise ValueError("--brief and --brief-file cannot be combined")
    text: str | None
    if brief_file is not None:
        text = brief_file.read_bytes().decode("utf-8")
    elif brief == "-":
        stream = getattr(stdin, "buffer", stdin)
        value = stream.read()
        text = value.decode("utf-8") if isinstance(value, bytes) else value
    else:
        text = brief
    if text is None or not text.strip():
        raise ValueError("The brief must contain non-whitespace UTF-8 text")
    text.encode("utf-8")
    return text
