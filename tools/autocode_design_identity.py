"""Content identity of one design case, independent of unrelated reference changes."""

import copy
from pathlib import Path

try:
    from . import autocode_design_manifest as manifest
    from . import autocode_util as util
except ImportError:
    import autocode_design_manifest as manifest
    import autocode_util as util


def _content_refs(value):
    if isinstance(value, list):
        return [_content_refs(row) for row in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"path", "sha256"}:
        return {"sha256": value["sha256"]}
    return {key: _content_refs(row) for key, row in value.items()}


def case_hash(record, identity):
    case = next((row for row in record["body"]["cases"] if row["id"] == identity), None)
    if not case:
        return None
    body = record["body"]
    bound = copy.deepcopy(case)
    if body["version"] == 2:
        refs = manifest.inventory.case_references(body, case)
        bound["inventory"] = {}
        roots = {(case["file_key"], case["page_id"], case["node_id"])}
        for section, key in (
            ("components", "key"),
            ("variables", "key"),
            ("fonts", "id"),
            ("assets", "id"),
            ("transitions", "id"),
        ):
            rows = [
                {**copy.deepcopy(row), "file_key": file["key"]}
                for file in body["files"]
                for row in file[section]
                if (file["key"], row[key]) in refs[section]
            ]
            bound["inventory"][section] = rows
            if section == "components":
                roots.update((row["file_key"], row["source_page_id"], row["source_node_id"]) for row in rows)
        bound["source_nodes"] = []
        for file in body["files"]:
            for page in file["pages"]:
                selected_roots = {node for key, pid, node in roots if key == file["key"] and pid == page["id"]}
                if not selected_roots:
                    continue
                _, parents = manifest.inventory._metadata_nodes(page, record["root"])[:2]
                receipt = util.read_object(Path(record["root"]) / page["source_json"]["path"])
                bound["source_nodes"].extend(
                    {"file_key": file["key"], "page_id": page["id"], **row}
                    for row in receipt["nodes"]
                    if any(manifest.inventory._under(row["id"], node, parents) for node in selected_roots)
                )
        bound["responsive_targets"] = [
            row
            for row in body["responsive_targets"]
            if row["source_case_id"] == identity or row["reference_case_id"] == identity
        ]
    return util.digest(_content_refs(bound))


def delta(previous, current):
    old = {row["id"] for row in previous["body"]["cases"]}
    new = {row["id"] for row in current["body"]["cases"]}
    changed = {identity for identity in old & new if case_hash(previous, identity) != case_hash(current, identity)}
    return {
        "added": sorted(new - old),
        "removed": sorted(old - new),
        "changed": sorted(changed),
        "unchanged": sorted((old & new) - changed),
    }


def matches(settings, reference_hash, identity):
    current = settings.get("design_manifest")
    if not current:
        return False
    if reference_hash == current["manifest_hash"]:
        return True
    old = next(
        (row for row in settings.get("design_manifest_history", []) if row["manifest_hash"] == reference_hash), None
    )
    if not old:
        return False
    manifest.verify(old)
    manifest.verify(current)
    expected = case_hash(current, identity)
    return expected is not None and expected == case_hash(old, identity)
