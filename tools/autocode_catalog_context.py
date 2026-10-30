"""Pinned local source inventory and file citations for read-only task intake.

The catalog grants read access to discovered checkouts, never Builder authority.
Each task retains its first inventory; a changed catalog or revision requires a
new investigation rather than silently replacing the evidence under a plan.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

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


def context(state):
    """Expose the same pinned catalog to intake stages, independent of launch cwd."""
    run = state.get("run_dir")
    receipt = Path(run) / "planning/source-catalog.json" if run else None
    saved = util.read_object(receipt) if receipt and receipt.is_file() else None
    discovery = catalog.discover()
    if not discovery["complete"]:
        codes = ", ".join(sorted({row["code"] for row in discovery["diagnostics"]}))
        raise ValueError("Source discovery is incomplete: " + codes)
    if saved and any(saved.get(key) != discovery.get(key) for key in ("catalog", "catalog_identity", "source_snapshot")):
        raise ValueError("The task's source catalog or pinned source revisions changed; rediscover the task scope")
    rows = []
    for group in discovery["repositories"]:
        for source in group["candidates"]:
            rows.append({
                "id": hashlib.sha256((source["path"] + "\0" + source["head"]).encode()).hexdigest()[:16],
                "name": Path(source["path"]).name,
                "path": source["path"],
                "head": source["head"],
                "tree": source["tree"],
                "remote": source.get("remote"),
                "ambiguous_baseline": group["ambiguous_baseline"],
            })
    if not rows:
        return None
    if len(rows) > 256:
        raise ValueError("Source discovery exceeds the bounded intake inventory of 256 repositories")
    result = {
        **{key: discovery[key] for key in ("catalog", "catalog_identity", "source_snapshot")},
        "repositories": rows,
        "instruction": INSTRUCTION,
    }
    if saved is not None:
        if saved != result:
            raise ValueError("The task's saved source inventory changed; rediscover the task scope")
        return result
    if receipt:
        util.atomic_json(receipt, result)
    return result


def read_roots(state):
    """Only the discovered repository directories receive read authority."""
    return [row["path"] for row in (context(state) or {}).get("repositories", [])]


def evidence(state, reference):
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
    relative = cited[:span.start()] if span else cited
    root = Path(row["path"])
    path = root / relative
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        return None
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            return None
        source = subprocess.run(
            ["git", "-C", str(root), "show", row["head"] + ":" + relative],
            capture_output=True, timeout=15,
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
