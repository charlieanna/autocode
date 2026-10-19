"""What a new terminal chat starts with, and the backends it can use: /mode, /engine and /connect.

The full-screen chat (autocode_chat_client) offers what the VS Code extension's mode
picker, engine picker and Connections view offer, from the same reads:
`autocode modes list --json` and `autocode connections list --json`. This module is
pure: it turns those listings and the person's choice into text and into the start
flags of the next new chat, and never runs anything. A choice applies to new chats
only; a saved run keeps the flow and the engine it started with.

The engine rules are the extension's (vscode-extension/src/settings.ts `applyEngine`):
`--engine` names the engine; `codex` takes no provider, and `claude` is the built-in
Claude Code connection, so the provider moves with it. "Use for new chats" on a
connection sets `--provider` to that connection.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path

ENGINES = ("opencode", "codex", "claude")  # the engines the core's --engine accepts (tools/autocode_args.py)
ENGINE_LABELS = {"opencode": "OpenCode", "codex": "Codex", "claude": "Claude Code"}
SETTINGS_FILE = "chat-settings.json"  # in the project's .autocode directory
# Connection states a backend cannot start a chat in (the extension disables "Use for new chats").
NOT_USABLE = ("unavailable", "setup_required", "sdk_required")


def strip_flag(options: list[str], flag: str) -> list[str]:
    """``options`` without ``flag VALUE`` or ``flag=VALUE``."""
    kept: list[str] = []
    skip = False
    for word in options:
        if skip:
            skip = False
            continue
        if word == flag:
            skip = True
            continue
        if not word.startswith(f"{flag}="):
            kept.append(word)
    return kept


def flag_value(options: list[str], flag: str) -> str | None:
    """The last value given for ``flag``, as the CLI reads it (a later flag wins)."""
    value = None
    for index, word in enumerate(options):
        if word == flag and index + 1 < len(options):
            value = options[index + 1]
        elif word.startswith(f"{flag}="):
            value = word[len(flag) + 1 :]
    return value


def engine_of(options: list[str]) -> str:
    """The engine new chats start on: an explicit --engine, else the provider, else OpenCode."""
    engine = flag_value(options, "--engine")
    return engine if engine in ENGINES else (flag_value(options, "--provider") or "opencode")


def with_engine(options: list[str], engine: str) -> list[str]:
    """The start flags with ``engine`` chosen, as the extension's applyEngine sets them."""
    result = strip_flag(options, "--engine")
    if engine in ("codex", "claude"):
        result = strip_flag(result, "--provider")
    if engine == "claude":
        result += ["--provider", "claude"]
    return result + ["--engine", engine]


def with_provider(options: list[str], provider: str) -> list[str]:
    """The start flags using connection ``provider`` for new chats; an explicit engine gives way to it."""
    return strip_flag(strip_flag(options, "--engine"), "--provider") + ["--provider", provider]


@dataclass
class NewChat:
    """The mode and start options of the next new chat: what /mode, /engine and /connect change."""

    mode: str | None = None
    options: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, workspace: Path) -> NewChat:
        """The project's saved choice; none when there is no readable one."""
        with contextlib.suppress(OSError, ValueError, TypeError):
            saved = json.loads((Path(workspace) / ".autocode" / SETTINGS_FILE).read_text(encoding="utf-8"))
            options = saved.get("options")
            if isinstance(options, list) and all(isinstance(word, str) for word in options):
                mode = saved.get("mode")
                return cls(mode if isinstance(mode, str) else None, list(options))
        return cls()

    def save(self, workspace: Path) -> bool:
        """Keep the choice for the project's next chats, once AutoCode has made its .autocode."""
        folder = Path(workspace) / ".autocode"
        if not folder.is_dir():
            return False
        with contextlib.suppress(OSError):
            (folder / SETTINGS_FILE).write_text(
                json.dumps({"mode": self.mode, "options": self.options}, indent=2) + "\n", encoding="utf-8"
            )
            return True
        return False


def mode_label(listed: dict, mode: str | None) -> str:
    """The label of ``mode``, or of the core's default."""
    wanted = mode or listed.get("default")
    for row in listed.get("modes") or []:
        if row.get("id") == wanted:
            return str(row.get("label") or wanted)
    return str(wanted or "default")


def summary(listed: dict | None, chosen: NewChat) -> str:
    """One line: what a new chat starts with."""
    mode = mode_label(listed or {}, chosen.mode) if listed else (chosen.mode or "the default mode")
    engine = engine_of(chosen.options)
    return f"New chats: {mode} · {ENGINE_LABELS.get(engine, engine)}. /mode and /engine change them."


def modes_text(listed: dict, chosen: NewChat) -> str:
    """What /mode prints: each mode the core lists, with its description and the current one marked."""
    current = chosen.mode or listed.get("default")
    lines = []
    for row in listed.get("modes") or []:
        mark = "  (current)" if row.get("id") == current else ""
        lines.append(f"  {row.get('id')}  {row.get('label') or ''}{mark}")
        if row.get("description"):
            lines.append(f"      {row['description']}")
    lines.append("Type /mode ID to start new chats in it. Chats already started keep their own.")
    return "\n".join(lines)


def choose_mode(listed: dict, wanted: str) -> str | None:
    """The mode id the core lists for ``wanted`` (by id or label), or None."""
    word = wanted.strip().lower()
    for row in listed.get("modes") or []:
        if word in (str(row.get("id") or "").lower(), str(row.get("label") or "").lower()):
            return str(row["id"])
    return None


def engines_text(chosen: NewChat) -> str:
    current = engine_of(chosen.options)
    lines = [
        f"  {engine}  {ENGINE_LABELS[engine]}" + ("  (current)" if engine == current else "") for engine in ENGINES
    ]
    lines.append("Type /engine NAME for new chats; /connect checks a backend or signs in to it.")
    return "\n".join(lines)


def choose_engine(wanted: str) -> str | None:
    word = wanted.strip().lower()
    for engine, label in ENGINE_LABELS.items():
        if word in (engine, label.lower()):
            return engine
    return None


def connections(listed: dict) -> list[dict]:
    """The connection rows of `autocode connections list --json` (version 1), or none."""
    if not isinstance(listed, dict) or listed.get("version") != 1:
        return []
    return [row for row in listed.get("connections") or [] if isinstance(row, dict) and row.get("id")]


def connections_text(rows: list[dict], chosen: NewChat) -> str:
    """What /connect prints: each backend's state and the actions it offers."""
    if not rows:
        return "AutoCode lists no connections."
    provider = flag_value(chosen.options, "--provider")
    lines = []
    for row in rows:
        mark = "  (new chats use it)" if row["id"] == provider else ""
        lines.append(f"  {row['id']}  {row.get('label') or row['id']}: {state(row)}{mark}")
        if row.get("detail"):
            lines.append(f"      {row['detail']}")
        offered = [action.get("id") for action in row.get("actions") or [] if isinstance(action, dict)]
        if row.get("connection_status") not in NOT_USABLE:
            offered.append("use")
        if offered:
            lines.append(f"      /connect {row['id']} " + "|".join(str(word) for word in offered))
    lines.append("check tests a backend, login and setup open its own sign-in, use makes new chats use it.")
    return "\n".join(lines)


def state(row: dict) -> str:
    return str(row.get("connection_status") or "unknown").replace("_", " ")


def action(rows: list[dict], name: str, action_id: str) -> dict | None:
    """The action a connection offers by that id; only an offered action is ever run."""
    for row in rows:
        if row["id"] == name:
            for offered in row.get("actions") or []:
                if isinstance(offered, dict) and offered.get("id") == action_id:
                    words = offered.get("next_command")
                    if isinstance(words, list) and all(isinstance(word, str) for word in words):
                        return offered
    return None
