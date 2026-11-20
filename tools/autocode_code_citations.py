"""Validate source citations, and authenticate them against the files a stage actually read.

``require`` keeps its original contract: the caller's planning unit supplies inventory, path
resolution and citation parsing, and this module owns the citation requirements and line bounds,
without importing the controller or planning unit.

``read_paths`` and ``verify_citation`` are the one answer to "did this stage read the file it
cites" (board #13, interface 1). A file counts as read when the stage's own provider events show
its contents reaching the model:

- a native file-read tool named it: Claude Code ``Read`` (its ``file_path``, as the stream adapter
  records it in ``display_input``) and OpenCode/Kilo ``read`` (its ``filePath``);
- a successful shell command (Codex, Claude Code ``Bash``, OpenCode/Kilo ``bash``, the direct
  runtime) named it as an argument, or printed a ``path:LINE:`` hit for it (grep/rg output);
- a shell command that failed but still printed it: a file it names whose whole text is in its
  output (not a ``path:LINE:`` line, which a failed build or test prints as an error). ``cat a b c`` exits 1 when one of the three is missing or denied,
  and the other two still reached the model (2026-11-17: a sandbox refused one linked file and the
  two printed beside it were refused as unread, so Requirements paused).

A name seen only in a listing (Glob, ``ls``, ``rg -l``, the workspace inventory) is not a read.
Reads are taken from the current turn's records of the stage, including rejected, timed-out and
budget-stopped attempts and their report-only repairs, whose tools are disabled: such a repair
may cite what the attempt it repairs read, and nothing else. An empty workspace (greenfield, no
inventory files) has nothing to read, so every citation check there passes, as ``require`` already
did. The events log the runner wrote is read, and a workspace file a failed command names is opened
only to compare its text with that command's output.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
    from . import autocode_workspace_inventory as workspace_inventory
    from . import autocode_workspace_links as workspace_links
except ImportError:
    import autocode_util as util
    import autocode_workspace_inventory as workspace_inventory
    import autocode_workspace_links as workspace_links

# Planning stages whose reads stage=None accepts: the default flow and the v2 flow.
PLANNING_STAGES = (
    "requirements_gather",
    "draft_plan",
    "revise_plan",
    "review_plan",
    "finalize_plan",
    "requirements",
    "plan",
    "plan_review",
    "plan_revise",
    "plan_finalize",
)
REPAIR_SUFFIX = "_report_repair"
NATIVE_READ_TOOLS = frozenset({"Read", "NotebookRead", "read", "Grep", "grep"})
READ_ARGUMENTS = ("file_path", "filePath", "notebook_path", "path")
SHELL_TOOLS = frozenset({"bash", "shell"})
MAX_EVENTS_BYTES = 64 * 1024 * 1024
MAX_SHELL_DEPTH = 3
# A file a failed command names is compared with its output only up to this size.
MAX_SHOWN_BYTES = 1024 * 1024
SHOWN_READS = 24
_OUTPUT_HIT = re.compile(r"^(?:\./)?([^\s:]+):\d+[:-]", re.MULTILINE)
_GREP_BLOCK = re.compile(r"^(?:\./)?([^\s:]+):\r?\n\s+Line \d+:", re.MULTILINE)
_LINES = re.compile(r"(\d+)(?:-(\d+))?(?:\s+.*)?", re.DOTALL)
# ``require`` has always refused a line citation whose explanation spans lines; it keeps that syntax.
_REQUIRE_LINES = re.compile(r"(\d+)(?:-(\d+))?(?:\s+.*)?")
_APPLYING: contextvars.ContextVar[dict | None] = contextvars.ContextVar("applying_stage_record", default=None)


@contextlib.contextmanager
def applying(record):
    """Include an in-flight stage record not yet in ``state['stages']`` while accepting its report."""
    token = _APPLYING.set(record if isinstance(record, dict) else None)
    try:
        yield
    finally:
        _APPLYING.reset(token)


def stage_evidenced(state: RunState, stage, record=None) -> bool:
    """True when the turn or in-flight record carries provider event evidence for ``stage``.

    Every provider dispatch records ``events`` (even when it read no file); synthetic
    ``apply_planning`` unit helpers pass only ``{"output": ...}`` with no stage history.
    """
    if isinstance(record, dict) and record.get("events"):
        return True
    names = _stage_names(stage)
    return any(
        isinstance(row, dict) and _matches(row, names) and bool(row.get("events")) for row in _turn_records(state)
    )


def require(state: RunState, refs, field="code_refs", *, planning_unit, stage=None):
    """Every ref names an existing workspace file and in-bounds lines; with ``stage``, one it read.

    Every bad citation is named in one rejection (#3866): stopping at the first cost a repair per fault."""
    if not state.get("workspace"):
        return
    root = Path(state.get("workspace") or "")
    if not root.is_dir():
        return
    files = planning_unit.workspace_inventory(root, state.get("task", ""))["files"]
    if not files:
        return
    if not refs:
        raise ValueError(f"Report must cite existing source in {field}")
    found = [(ref, *required_citation(root, ref, field)) for ref in refs]
    problems = [problem for _ref, problem, _target in found if problem is not None]
    if problems:
        raise ValueError("; ".join(problems))
    if stage is not None:
        _require_read(state, stage, field, [(ref, _relative(root.resolve(), target)) for ref, _, target in found])


def _require_read(state: RunState, stage, field, cited):
    read = read_paths(state, stage)
    unread = [str(ref) for ref, relative in cited if relative not in read]
    if not unread:
        return
    if not read:
        # A report-only repair has no tools: with nothing read, no repair can supply a citation,
        # so the stage runs again with its tools instead of spending the bounded repair loop.
        raise util.ExecutionEvidenceRequired(
            f"{field} cites source this stage never read ({', '.join(unread)}), and it read no file: "
            "read the files you cite with a file-read tool or command before citing them"
        )
    shown = sorted(read)
    listed = ", ".join(shown[:SHOWN_READS]) + (
        f" and {len(shown) - SHOWN_READS} more" if len(shown) > SHOWN_READS else ""
    )
    raise ValueError(
        f"{field} cites source this stage never read: {', '.join(unread)}. Cite only files whose contents "
        f"you read in this stage (read: {listed}); a name seen only in workspace_inventory, a file listing "
        "or a search for file names is not evidence"
    )


def greenfield(state: RunState) -> bool:
    """True when there is nothing to read: no workspace directory, or one with no inventory files."""
    workspace = state.get("workspace")
    if not workspace or not Path(workspace).is_dir():
        return True
    return not workspace_inventory.inventory(Path(workspace), state.get("task", "") or "")["files"]


def split_ref(root, ref: str):
    """(path, line citation) of a cited source entry: ``path``, ``path:12``, ``path:12-20 why``,
    ``path — why``. Prose after an existing path is the model's explanation, not a malformed citation."""
    token = ref.split(maxsplit=1)[0] if ref.strip() else ref
    if token != ref and ":" not in token and (Path(root) / token).is_file():
        return token, ""
    head, _, rest = ref.partition(":")
    if rest[:1].isspace() and (Path(root) / head).is_file():
        return head, ""
    return head, rest


def cited_file(root, path, field, ref):
    """The workspace file a citation names, or ValueError: it must exist inside the workspace and must not
    be one the runner or a tool owns or generates. A file the project reaches through its own link out of
    the workspace counts, by the path it is cited as (``workspace_links.linked_file``)."""
    root = Path(root).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        linked = workspace_links.linked_file(root)(path)
        if linked is None:
            raise ValueError(f"{field} entry {ref} is not a file in the workspace")
        target = root / linked
    if target.suffix == ".pyc" or any(
        part in workspace_inventory.RUNNER_OWNED_PARTS for part in target.relative_to(root).parts
    ):
        raise ValueError(
            f"{field} entry {ref} is a file the runner or a tool owns (.autocode/, .git/, "
            "__pycache__/ and caches): cite source files, which outlive the run"
        )
    return target


def verify_citation(state: RunState, stage, ref, *, field="citation") -> str | None:
    """None when ``ref`` cites in-bounds lines of a workspace file the stage read (or the workspace is
    greenfield); otherwise the reason, naming the ref. Never raises for a malformed or non-path ref.

    ``stage`` is a stage name, a tuple of names, or None for any planning stage (``PLANNING_STAGES``).
    """
    if greenfield(state):
        return None
    if not isinstance(ref, str) or not ref.strip():
        return f"{field} entry {ref!r} names no workspace file"
    root = Path(state["workspace"]).resolve()
    problem, target = _citation(root, ref, field, _LINES)
    if problem is not None:
        return problem
    relative = _relative(root, target)
    if relative not in read_paths(state, stage):
        return f"{field} entry {ref} cites {relative}, which this stage never read"
    return None


def citation_problem(root, ref: str, field: str) -> str | None:
    """Why ``ref`` names no existing workspace file or in-bounds lines of one, or None. Never raises.

    The workspace-only part of a citation check, decidable without the stage's events: `autocode
    report-check` and admission ask it the same question (#3865); whether the stage read the file
    stays with ``verify_citation`` and ``require``."""
    return _citation(Path(root).resolve(), ref, field, _LINES)[0]


def required_citation(root, ref, field: str) -> tuple[str | None, Path | None]:
    """(problem, None) or (None, the cited file) for one ``require`` citation, with ``require``'s own line
    syntax, which refuses an explanation spanning lines. Never raises. ``require`` and the fault list that
    report-check and admission share (``autocode_requirements_faults``) ask it about ``source_refs`` (#3865)."""
    return _citation(Path(root).resolve(), str(ref), field, _REQUIRE_LINES)


def _citation(root: Path, ref: str, field: str, lines: re.Pattern) -> tuple[str | None, Path | None]:
    """(problem, None) for a bad citation, else (None, the cited file); ``lines`` is the caller's line syntax."""
    try:
        raw, line_text = split_ref(root, ref)
        target = cited_file(root, raw, field, ref)
    except (ValueError, OSError) as error:
        return str(error), None
    if line_text:
        citation = lines.fullmatch(line_text)
        if not citation:
            return f"{field} entry {ref} has no line number", None
        line, end = int(citation[1]), int(citation[2] or citation[1])
        try:
            count = len(target.read_text(errors="replace").splitlines())
        except OSError as error:
            return f"{field} entry {ref} cannot be read: {error}", None
        if line < 1 or end < line or end > max(count, 1):
            return f"{field} entry {ref} points past the end of the file ({count} lines)", None
    return None, target


def read_paths(state: RunState, stage) -> frozenset[str]:
    """Workspace-relative POSIX paths of files the stage's provider read in the current turn."""
    workspace = state.get("workspace")
    if not workspace or not Path(workspace).is_dir():
        return frozenset()
    root = Path(workspace).resolve()
    run_dir = Path(state.get("run_dir") or root)
    names = _stage_names(stage)
    found: set[str] = set()
    for record in _turn_records(state):
        if not isinstance(record, dict) or not _matches(record, names):
            continue
        raw = _events(run_dir, record.get("events"))
        if raw:
            found.update(read_targets(raw, root))
    return frozenset(found)


def read_targets(raw, workspace) -> list[str]:
    """Workspace files a provider events log shows were read, in first-read order (see module doc)."""
    text = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
    root = Path(workspace).resolve()
    found: dict[str, None] = {}
    linked = workspace_links.linked_file(root)

    def add(name):
        relative = _workspace_file(root, name, linked)
        if relative is not None:
            found.setdefault(relative, None)

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        item, part = row.get("item"), row.get("part")
        if row.get("type") == "item.completed" and isinstance(item, dict):
            kind = item.get("type")
            if kind == "command_execution" and type(item.get("exit_code")) is int:
                _shell(
                    item.get("command"),
                    item.get("aggregated_output"),
                    add,
                    None if item["exit_code"] == 0 else root,
                    linked,
                )
            elif kind == "tool_call" and item.get("name") in NATIVE_READ_TOOLS and item.get("status") == "completed":
                shown = item.get("display_input")
                for key in READ_ARGUMENTS:
                    add((shown or {}).get(key) if isinstance(shown, dict) else None)
        elif row.get("type") == "tool_use" and isinstance(part, dict):
            part_state = part.get("state")
            if not isinstance(part_state, dict):
                continue
            status = part_state.get("status")
            raw_input = part_state.get("input")
            arguments: dict[str, object] = raw_input if isinstance(raw_input, dict) else {}
            if part.get("tool") in NATIVE_READ_TOOLS and status in ("completed", None):
                for key in READ_ARGUMENTS:
                    add(arguments.get(key))
                if part.get("tool") == "grep" and isinstance(part_state.get("output"), str):
                    for pattern in (_OUTPUT_HIT, _GREP_BLOCK):
                        for hit in pattern.findall(part_state["output"]):
                            add(hit)
            elif part.get("tool") in SHELL_TOOLS and status == "completed" and _shell_exit(part_state) is not None:
                failed = root if _shell_exit(part_state) != 0 else None
                _shell(arguments.get("command"), part_state.get("output"), add, failed, linked)
    return list(found)


def _shell(command, output, add, failed=None, linked=None):
    """Credit a command's reads; ``failed`` (the workspace root) credits a named file only if its text was shown."""
    if isinstance(command, str):
        for token in _tokens(command, 0):
            if failed is None or isinstance(output, str) and _shown(failed, token, output, linked):
                add(token)
    if isinstance(output, str) and failed is None:  # a failed build or test also prints path:LINE: errors
        for hit in _OUTPUT_HIT.findall(output):
            add(hit)


def _shown(root: Path, name, output: str, linked=None) -> bool:
    """Whether the whole, non-blank text of the workspace file ``name`` appears in ``output`` as whole lines."""
    relative = _workspace_file(root, name, linked)
    if relative is None:
        return False
    try:
        path = root / relative
        if path.stat().st_size > MAX_SHOWN_BYTES:
            return False
        text = path.read_text(errors="strict").strip()
    except (OSError, UnicodeDecodeError):
        return False
    lines = "\n" + output.replace("\r\n", "\n") + "\n"
    return bool(text) and "\n" + text + "\n" in lines  # whole lines, so a name in an error line is not its text


def _tokens(command: str, depth: int):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        if depth < MAX_SHELL_DEPTH and any(character.isspace() for character in token):
            yield from _tokens(token, depth + 1)  # bash -lc "sed -n 1,80p app.py"
            continue
        yield token
        if token.startswith("-") and "=" in token:
            yield token.split("=", 1)[1]


def _shell_exit(part_state):
    """Integer exit of an OpenCode/Kilo shell part (1.x ``metadata.exit`` or 2.x nested metadata)."""
    metadata = part_state.get("metadata")
    if not isinstance(metadata, dict):
        return None
    code = metadata.get("exit")
    if "exit" not in metadata and isinstance(metadata.get("metadata"), dict):
        code = metadata["metadata"].get("exit")
    return code if type(code) is int else None


def _workspace_file(root: Path, name, linked=None) -> str | None:
    """The workspace-relative name of a file a stage named, or None; ``linked`` is the
    ``workspace_links.linked_file`` test for a file reached through the project's own link out."""
    if not isinstance(name, str) or not name.strip() or "\x00" in name or len(name) > 4096:
        return None
    path = Path(name.strip())
    try:
        target = (path if path.is_absolute() else root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return linked(name) if linked is not None else None
    except (OSError, RuntimeError, ValueError):
        return None
    return _relative(root, target)


def _relative(root: Path, target: Path) -> str:
    return target.relative_to(root).as_posix()


def _stage_names(stage) -> tuple[str, ...]:
    if stage is None:
        return PLANNING_STAGES
    return (stage,) if isinstance(stage, str) else tuple(stage)


def _matches(record: dict, names) -> bool:
    name = record.get("stage")
    if name in names or record.get("original_stage") in names:
        return True
    return isinstance(name, str) and name.endswith(REPAIR_SUFFIX) and name[: -len(REPAIR_SUFFIX)] in names


def _turn_records(state: RunState) -> list:
    stages = list(state.get("stages") or [])
    turns = state.get("turns") or []
    start = turns[-1].get("stage_index") if turns and isinstance(turns[-1], dict) else 0
    if type(start) is not int or not 0 <= start <= len(stages):
        start = 0
    records = stages[start:]
    for candidate in (state.get("active_stage"), _APPLYING.get()):
        if isinstance(candidate, dict) and not any(candidate is record for record in records):
            records.append(candidate)
    return records


def _events(run_dir: Path, name) -> bytes:
    if not isinstance(name, str) or not name:
        return b""
    path = Path(name)
    path = path if path.is_absolute() else run_dir / path
    try:
        if not path.is_file() or path.stat().st_size > MAX_EVENTS_BYTES:
            return b""
        return path.read_bytes()
    except OSError:
        return b""
