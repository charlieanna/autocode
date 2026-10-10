"""Native connected-Figma intake inside the existing single task controller.

Owns design_intake: queue writes the return stage; apply writes the retained
receipt/status. The public view reads it. No extra controller or source edits.
"""

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from . import autocode_design_manifest as manifest
    from . import autocode_stage_access as stage_access
    from . import autocode_stray_writes as stray_writes
    from . import autocode_util as util
except ImportError:
    import autocode_design_manifest as manifest
    import autocode_stage_access as stage_access
    import autocode_stray_writes as stray_writes
    import autocode_util as util

STAGE = "collect_design"
SCHEMA = manifest.obj(
    {
        "status": {"type": "string", "enum": ["READY", "BLOCKED"]},
        "manifest_path": {"type": "string"},
        "blockers": {"type": "array", "items": manifest.TEXT},
        "summary": manifest.TEXT,
    }
)


PROMPT = prompts.get("design-inventory.md")


def queue(state):
    settings = state.get("settings") or {}
    if not settings.get("figma_inventory_required"):
        return
    record = settings.get("design_manifest")
    if record and record["body"]["version"] == 2:
        return
    state["design_intake"] = {
        "version": 1,
        "status": "PENDING",
        "return_stage": state["next_stage"],
        "references": settings.get("figma_references") or [settings["figma_file"]],
    }
    state["next_stage"] = STAGE


def require_references(record, references, *, exact=True):
    if record["body"]["version"] != 2:
        raise ValueError("Native intake needs the complete version 2 inventory")
    files = {file["key"] for file in record["body"]["files"]}
    expected = {urlparse(url).path.strip("/").split("/")[1] for url in references}
    if (files != expected) if exact else (not expected <= files):
        raise ValueError("Native inventory must include exactly the approved Figma files")
    nodes = {
        (file["key"], node)
        for file in record["body"]["files"]
        for page in file["pages"]
        for node in manifest.inventory._metadata_nodes(page, record["root"])[0]
    }
    for url in references:
        parsed = urlparse(url)
        node = parse_qs(parsed.query).get("node-id", [""])[0].replace("-", ":")
        if node and (parsed.path.strip("/").split("/")[1], node) not in nodes:
            raise ValueError("Native inventory omits a requested starting node")


def prompt(state, inventory=None, soft_budget_tokens=10000, engine=None):
    packet = {
        "stage": STAGE,
        "task": state["task"],
        "workspace": state["workspace"],
        "references": state["design_intake"]["references"],
        "manifest_schema": manifest.SCHEMA_V2,
        "collector": str(Path(__file__).with_name("figma_inventory_page.js")),
    }
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet, indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def apply(state, value, record, workspace, **unused):
    stray = stage_access.stray(STAGE, record.get("changed_files"))
    if stray:
        raise stray_writes.StrayWrites(
            "Figma inventory collection must not modify source files: " + ", ".join(stray), stray
        )
    util.validate_schema(value, SCHEMA)
    intake = state["design_intake"]
    if value["status"] == "BLOCKED":
        if not value["blockers"]:
            raise ValueError("Blocked native inventory needs a concrete unreadable input or conflict")
        intake.update(status="BLOCKED", blockers=value["blockers"], output=record.get("output"))
        state.update(
            status="PAUSED_DESIGN_INPUT",
            phase="PAUSED_OR_BLOCKED",
            stop_reason="Figma inventory: " + "; ".join(value["blockers"]),
        )
        return
    if value["blockers"]:
        raise ValueError("Native inventory cannot be READY with uncollected references")
    root = Path(workspace).resolve()
    path = Path(value["manifest_path"])
    path = path if path.is_absolute() else root / path
    if not path.resolve().is_relative_to(root / ".autocode" / "native-design-inputs"):
        raise ValueError("Native input must be retained under its task private input directory")
    selected = manifest.load(path)
    require_references(selected, intake["references"])
    state["settings"]["design_manifest"] = manifest.retain(selected, workspace)
    intake.update(
        status="READY",
        manifest_hash=selected["manifest_hash"],
        output=record.get("output"),
        blockers=manifest.blockers(selected),
    )
    state.update(status="RUNNING", phase="PLANNING", next_stage=intake["return_stage"])


def owns(state):
    return False


def render(state):
    return ""
