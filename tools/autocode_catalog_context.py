"""Pinned local source inventory and file citations for read-only task intake.

The catalog grants read access to discovered checkouts, never Builder authority.
Each task retains its first inventory; a changed catalog or revision requires a
new investigation rather than silently replacing the evidence under a plan.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_repo_catalog as catalog
    from . import autocode_util as util
except ImportError:
    import autocode_repo_catalog as catalog
    import autocode_util as util


INSTRUCTION = (
    "Investigate this authorized source catalog read-only before selecting affected repositories. "
    "The launch workspace is optional context and is not a selected repository. Follow dependencies, "
    "calls, schemas and consumers from the ticket; unrelated catalog entries are not task scope. "
    "Report every affected repository in workspace_scope.external_parts, using its actual name. "
    "Ground each involvement in inspected source using catalog/ID/relative/file:line citations "
    "(ID comes from the inventory below), and retain verbatim ticket quotations where available. "
    "Do not modify catalog sources, ask the person to select repositories or supply a joint-test command. "
    "When the launch directory has no affected implementation, report not_reproduced with fix_size none, "
    "no local fix or probe, and grounded external scope; member investigators reproduce their actual code. "
    "Repository plans and the real combined regression will be prepared for one coordinated approval."
)


UNREADABLE_INSTRUCTION = (
    "AutoCode could not read the checkouts listed in unreadable, so they are not in this inventory and "
    "cannot be cited. If the ticket points at one of them, say so in the report rather than guessing its code."
)


NOT_SEARCHED_INSTRUCTION = (
    "Discovery searched each source root to a bounded depth; the directories listed in not_searched "
    "were not searched below. If the ticket points at a checkout under one of them, say so in the "
    "report rather than guessing its code."
)


# Every chat on this computer shares the configured source roots, so a root one chat added stops the
# others too (#3073). The stop names the command that removes it, and where to run it: typed into a
# chat, a command is a new request.
OUTSIDE_CHAT = "in a terminal, not in a chat (where it would be read as a new request)"


def owning_roots(rows, roots):
    """The configured roots that hold the blocking rows, deepest match first; a root's own row is itself."""
    configured = sorted(
        (Path(row["path"]) for row in roots if isinstance(row, dict) and isinstance(row.get("path"), str)),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    found = []
    for row in rows:
        path = row.get("path")
        if not isinstance(path, str):
            continue
        owner = next((root for root in configured if Path(path).is_relative_to(root)), None)
        if owner is not None and str(owner) not in found:
            found.append(str(owner))
    return found


class DiscoveryIncomplete(ValueError):
    """Discovery could not see every readable source: the person's source roots need a change."""

    status = "PAUSED_SOURCE_UNAVAILABLE"

    def __init__(self, rows, roots=()):
        shown = "; ".join(row["code"] + (f" at {row['path']}" if row.get("path") else "") for row in rows[:3])
        more = f"; and {len(rows) - 3} more" if len(rows) > 3 else ""
        owners = owning_roots(rows, roots)
        if owners:
            commands = "; ".join(f"`autocode registry roots remove {shlex.quote(root)}`" for root in owners)
            fix = (
                "Configured source roots are shared by every chat on this computer. "
                f"To stop {'this root' if len(owners) == 1 else 'these roots'} blocking tasks, run {OUTSIDE_CHAT}: "
                f"{commands}, or fix the folder so discovery can see every checkout under it. Then continue."
            )
        else:
            fix = (
                "Change the configured source roots so discovery can see every checkout "
                f"(`autocode registry roots list` shows them; run it {OUTSIDE_CHAT}), then continue."
            )
        super().__init__(f"Source discovery is incomplete: {shown}{more}. {fix}")


class CatalogChanged(ValueError):
    """The recorded source inventory no longer describes current discovery."""

    status = "PAUSED_SOURCE_UNAVAILABLE"

    def __init__(self, changes):
        self.changes = changes
        super().__init__("The task's source catalog changed: " + "; ".join(changes))


def binding_changes(saved, discovery):
    """Identify changed inputs without replacing the receipt or accepting new source."""
    if not isinstance(saved, dict):
        return []
    changes = []
    for key in ("catalog", "catalog_identity"):
        if saved.get(key) != discovery.get(key):
            changes.append(key)
    previous = {row["path"]: row for row in saved.get("repositories", []) if isinstance(row, dict) and "path" in row}
    current = {row["path"]: row for group in discovery.get("repositories", []) for row in group.get("candidates", [])}
    for path in sorted(previous.keys() | current.keys()):
        if path not in previous:
            changes.append(f"{path}: added")
        elif path not in current:
            changes.append(f"{path}: removed")
        else:
            for key in ("head", "tree", "remote"):
                if previous[path].get(key) != current[path].get(key):
                    changes.append(f"{path}: {key}")
    if not changes and saved.get("source_snapshot") != discovery.get("source_snapshot"):
        changes.append("source_snapshot")
    return changes


def inventory(discovery, *, allow_empty=False):
    """Build the canonical intake receipt from a complete discovery without writing it."""
    if stopped := catalog.blocking(discovery):
        raise DiscoveryIncomplete(stopped, discovery.get("catalog_roots") or ())
    rows = []
    for group in discovery["repositories"]:
        for source in group["candidates"]:
            rows.append(
                {
                    "id": hashlib.sha256((source["path"] + "\0" + source["head"]).encode()).hexdigest()[:16],
                    "name": Path(source["path"]).name,
                    "path": source["path"],
                    "head": source["head"],
                    "tree": source["tree"],
                    "remote": source.get("remote"),
                    "ambiguous_baseline": group["ambiguous_baseline"],
                }
            )
    if not rows and not allow_empty:
        return None
    if len(rows) > 256:
        raise ValueError("Source discovery exceeds the bounded intake inventory of 256 repositories")
    result = {
        **{key: discovery[key] for key in ("catalog", "catalog_identity", "source_snapshot")},
        "repositories": rows,
        "instruction": INSTRUCTION,
    }
    if skipped := catalog.unreadable(discovery):
        # Recorded in the task's pinned inventory, so the stages and reviewers see what was left out and why.
        result["unreadable"] = skipped
        result["instruction"] = INSTRUCTION + " " + UNREADABLE_INSTRUCTION
    if unsearched := catalog.not_searched(discovery):
        result["not_searched"] = unsearched
        result["instruction"] += " " + NOT_SEARCHED_INSTRUCTION
    return result


def receipt_path(state: RunState):
    run = state.get("run_dir")
    if not run:
        return None
    selected = state.get("source_catalog_receipt")
    if selected is None:
        return Path(run) / "planning/source-catalog.json"
    if not isinstance(selected, str) or not re.fullmatch(r"[0-9a-f]{64}", selected):
        raise ValueError("The saved source inventory receipt identity is invalid")
    return Path(run) / "planning/source-catalogs" / (selected + ".json")


def saved_inventory(state: RunState):
    receipt = receipt_path(state)
    selected = state.get("source_catalog_receipt")
    if not receipt or not receipt.is_file():
        if selected is not None:
            raise ValueError("The saved source inventory receipt is missing")
        return None
    saved = util.read_object(receipt)
    if selected is not None and (not isinstance(saved, dict) or util.digest(saved) != selected):
        raise ValueError("The saved source inventory receipt does not match its identity")
    return saved


def context(state: RunState):
    """Expose the same pinned catalog to intake stages, independent of launch cwd."""
    receipt = receipt_path(state)
    saved = saved_inventory(state)
    discovery = catalog.discover()
    if stopped := catalog.blocking(discovery):
        raise DiscoveryIncomplete(stopped, discovery.get("catalog_roots") or ())
    if saved and (changes := binding_changes(saved, discovery)):
        raise CatalogChanged(changes)
    result = inventory(discovery, allow_empty=state.get("source_catalog_receipt") is not None)
    if saved is not None:
        if saved != result:
            raise ValueError("The task's saved source inventory changed; rediscover the task scope")
        return result if result.get("repositories") else None
    if receipt and result is not None:
        util.atomic_json(receipt, result)
    return result


def read_roots(state: RunState):
    """Only the discovered repository directories receive read authority."""
    return [row["path"] for row in (context(state) or {}).get("repositories", [])]


def evidence(state: RunState, reference):
    """Resolve a catalog citation only when the file still matches its pinned Git blob."""
    if not isinstance(reference, str) or not reference.startswith("catalog/"):
        return None
    parts = reference.split("/", 2)
    if len(parts) != 3:
        return None
    row = next((row for row in (context(state) or {}).get("repositories", []) if row["id"] == parts[1]), None)
    if not row:
        return None
    cited = parts[2]
    span = re.search(r":([0-9]+)(?:-([0-9]+))?$", cited)
    relative = cited[: span.start()] if span else cited
    root = Path(row["path"])
    path = root / relative
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        return None
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            return None
        source = subprocess.run(
            ["git", "-C", str(root), "show", row["head"] + ":" + relative],
            capture_output=True,
            timeout=15,
        )
        if source.returncode or source.stdout != path.read_bytes():
            return None
        if span:
            first, last = int(span[1]), int(span[2] or span[1])
            if first < 1 or last < first or last > len(source.stdout.splitlines()):
                return None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return {"repository": row["path"], "revision": row["head"], "file": cited}
