"""A project's ``serve`` setting: the command that runs its development server (#730).

A team names the command that serves its app in ``.autocode/config.toml``, as a string or a ``[serve]`` table
(autocode_project_config, FILE_ONLY), or as a plain ``serve`` string under ``[tool.autocode]`` in
``pyproject.toml`` (sub-tables there belong to other tooling). ``{port}`` marks where the free loopback port
goes; ``ready-path`` is the path that answers once the server is ready, and ``ready-seconds`` how long to wait for
it. A new run saves the checked value in its settings (``SETTING``) when it starts, like ``test-command``, so a
changed file, including one a Builder edits, never alters the command a run serves with. ``autocode serve``
(autocode_dev_server) runs it.

Bottom layer: imports nothing from AutoCode.
"""

from __future__ import annotations

KEY = "serve"  # the project-file key (autocode_project_config.FILE_ONLY)
SETTING = "serve"  # the run's settings key, and the attribute autocode_project_config sets on args
PLACEHOLDER = "{port}"
READY_SECONDS = 60
MAX_READY_SECONDS = 600
PATH_LIMIT = 2048
_FIELDS = ("command", "ready-path", "ready-seconds")


def checked(value, source: str) -> dict:
    """The saved form of a project's ``serve``: ``command``, ``ready_path`` (None for any answer) and
    ``ready_seconds``. Anything a server cannot be started from raises ValueError naming ``source``."""
    table = {"command": value} if isinstance(value, str) else value
    if not isinstance(table, dict):
        raise ValueError(f'{source}: {KEY} takes a command string such as "npm run dev -- --port {PLACEHOLDER}"')
    unknown = sorted(set(table) - set(_FIELDS))
    if unknown:
        raise ValueError(f"{source}: {KEY} has unknown {', '.join(unknown)}; it takes {', '.join(_FIELDS)}")
    command = table.get("command")
    if not isinstance(command, str) or not command.strip() or "\x00" in command:
        raise ValueError(f"{source}: {KEY} needs a nonempty command string")
    command = command.strip()
    if PLACEHOLDER not in command:
        raise ValueError(f"{source}: {KEY} command must contain {PLACEHOLDER}, where the server's port goes")
    path = table.get("ready-path")
    if path is not None and (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or len(path) > PATH_LIMIT
        or not path.isprintable()
        or any(character.isspace() for character in path)
    ):
        raise ValueError(f'{source}: {KEY} ready-path must be a path on the server, such as "/" or "/health"')
    seconds = table.get("ready-seconds", READY_SECONDS)
    if type(seconds) is not int or not 0 < seconds <= MAX_READY_SECONDS:
        raise ValueError(f"{source}: {KEY} ready-seconds must be a whole number from 1 to {MAX_READY_SECONDS}")
    return {"command": command, "ready_path": path, "ready_seconds": seconds}


def command_for(saved: dict, port: int) -> str:
    """The saved command with every ``{port}`` replaced; other braces are the shell's."""
    return saved["command"].replace(PLACEHOLDER, str(int(port)))
