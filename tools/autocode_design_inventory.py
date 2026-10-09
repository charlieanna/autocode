"""Complete, deterministic coverage derived from read-only Figma metadata snapshots.

This module accepts connector-exported page metadata and design-system facts. It
does not call Figma, write to a Figma file, or import the task runner.
"""

from __future__ import annotations

import copy

try:
    from . import autocode_design_sources as sources
except ImportError:
    import autocode_design_sources as sources
import re
import xml.etree.ElementTree as ET
from pathlib import Path

NODE_ID = re.compile(r"[0-9]+:[0-9]+\Z")
DESCENDANT_ID = re.compile(r"I?[0-9]+:[0-9]+(?:;[0-9]+:[0-9]+)*\Z")


def _tag(element):
    return element.tag.rsplit("}", 1)[-1].upper()


def _parse_metadata_artifact(artifact, root, label):
    try:
        bundle_root = Path(root).resolve()
        path = (bundle_root / artifact["path"]).resolve()
        if path.is_symlink() or not path.is_relative_to(bundle_root) or not path.is_file():
            raise ValueError("metadata path is missing or outside the bundle")
        return ET.parse(path).getroot()
    except (KeyError, TypeError, ET.ParseError, OSError, ValueError) as error:
        raise ValueError(f"Unreadable Figma metadata: {label}") from error


def _file_metadata_pages(file, root):
    metadata = _parse_metadata_artifact(file["metadata_xml"], root, file["key"])
    kind = metadata.attrib.get("type", _tag(metadata)).upper()
    if kind != "DOCUMENT":
        raise ValueError(f"Figma file metadata must contain the complete document tree: {file['key']}")
    pages = [
        element.attrib.get("id")
        for element in metadata.iter()
        if element is not metadata and element.attrib.get("type", _tag(element)).upper() in ("CANVAS", "PAGE")
    ]
    if any(not page_id or not NODE_ID.fullmatch(page_id) for page_id in pages) or len(pages) != len(set(pages)):
        raise ValueError(f"Figma file metadata has invalid or duplicate pages: {file['key']}")
    return set(pages)


def _metadata_nodes(page, root):
    """Read a complete get_metadata page response and return its node table."""
    metadata = _parse_metadata_artifact(page["metadata_xml"], root, page.get("id", "<unknown>"))
    root_id = metadata.attrib.get("id")
    if root_id and root_id != page["id"]:
        raise ValueError(f"Figma page metadata identity differs from page {page['id']}")

    nodes, parents = {}, {}

    def visit(element, parent_id=None):
        node_id = element.attrib.get("id")
        kind = element.attrib.get("type", _tag(element)).upper()
        if node_id:
            if not DESCENDANT_ID.fullmatch(node_id) or node_id in nodes:
                raise ValueError(f"Invalid or duplicate Figma node in page {page['id']}: {node_id}")
            node = {"id": node_id, "type": kind, "name": element.attrib.get("name", "")}
            for key in ("width", "height", "x", "y"):
                if key in element.attrib:
                    try:
                        node[key] = float(element.attrib[key])
                    except (TypeError, ValueError) as error:
                        raise ValueError(f"Invalid {key} for Figma node {node_id}") from error
            nodes[node_id] = node
            parents[node_id] = parent_id
        for child in element:
            visit(child, node_id or parent_id)

    visit(metadata)
    if not root_id:
        raise ValueError(f"Figma page metadata has no page identity: {page['id']}")

    # A screen is a page-level/section-level frame, not an implementation frame
    # nested inside another screen or a component. This includes screen frames in
    # named Sections while avoiding every ordinary nested layout frame.
    frames = []
    for node_id, node in nodes.items():
        if node["type"] != "FRAME":
            continue
        parent = parents[node_id]
        nested = False
        while parent:
            parent_node = nodes.get(parent)
            if parent_node and parent_node["type"] in ("FRAME", "COMPONENT", "COMPONENT_SET", "INSTANCE"):
                nested = True
                break
            parent = parents.get(parent)
        if not nested:
            frames.append(node_id)
    frame_ids = set(frames)
    frame_for_node = {}
    for node_id in nodes:
        parent = node_id
        while parent and parent not in frame_ids:
            parent = parents.get(parent)
        if parent:
            frame_for_node[node_id] = parent
    return nodes, parents, sorted(frames), frame_for_node


def screen_frames(body, root):
    """Discovered page/Section-level screen frames in stable source order."""
    result = set()
    for file in body["files"]:
        for page in file["pages"]:
            _, _, frames, _ = _metadata_nodes(page, root)
            result.update((file["key"], page["id"], node_id) for node_id in frames)
    return sorted(result)


def case_references(body, case):
    """Resolve stable library keys and file-qualified resource IDs to their sources."""
    result = {}
    for section, key_name in (
        ("components", "key"),
        ("variables", "key"),
        ("fonts", "id"),
        ("assets", "id"),
        ("transitions", "id"),
    ):
        resolved = set()
        for ref in case["inventory_refs"][section]:
            if section in ("components", "variables"):
                sources_for_ref = {
                    (file["key"], row[key_name])
                    for file in body["files"]
                    for row in file[section]
                    if row[key_name] == ref
                }
            else:
                file_key, identity = ref.split("/", 1) if "/" in ref else (case["file_key"], ref)
                sources_for_ref = {
                    (file["key"], row[key_name])
                    for file in body["files"]
                    if file["key"] == file_key
                    for row in file[section]
                    if row[key_name] == identity
                }
            if not sources_for_ref or resolved.intersection(sources_for_ref):
                raise ValueError(f"Design case maps unknown or duplicate {section}: {case['id']} / {ref}")
            resolved.update(sources_for_ref)
        result[section] = resolved
    return result


def _under(node_id, ancestor, parents):
    while node_id is not None:
        if node_id == ancestor:
            return True
        node_id = parents.get(node_id)
    return False


def _validate_bindings(body, case, refs, node_tables, source_facts):
    # Include every variant of the components this screen uses, even from another
    # approved file. Library-only pages need no invented screen case.
    roots = {(case["file_key"], case["page_id"], case["node_id"])}
    for file in body["files"]:
        for component in file["components"]:
            if (file["key"], component["key"]) in refs["components"]:
                roots.add((file["key"], component["source_page_id"], component["source_node_id"]))
    for file_key, page_id, root_id in roots:
        if (file_key, page_id) not in node_tables:
            raise ValueError("Unknown component source page")
        _, parents = node_tables[(file_key, page_id)]
        file = next(row for row in body["files"] if row["key"] == file_key)
        facts = [
            row
            for (key, page, node), row in source_facts.items()
            if key == file_key and page == page_id and _under(node, root_id, parents)
        ]
        variable_ids = {key for row in facts for key in row["variables"]}
        variable_ids |= sources.aliases([row["transitions"] for row in facts])
        while True:
            expanded = variable_ids | sources.aliases(
                [row["value"] for row in file["variables"] if row["source_id"] in variable_ids]
            )
            if expanded == variable_ids:
                break
            variable_ids = expanded
        expected = {section: set() for section in refs}
        expected["variables"] = {
            (file_key, row["key"]) for row in file["variables"] if row["source_id"] in variable_ids
        }
        for row in facts:
            for font in row["fonts"]:
                expected["fonts"].update(
                    (file_key, item["id"])
                    for item in file["fonts"]
                    if (item["family"], item["style"]) == (font["family"], font["style"])
                )
            expected["assets"].update((file_key, item["id"]) for item in row["assets"])
            expected["transitions"].update((file_key, item["id"]) for item in row["transitions"])
            if row["type"] == "INSTANCE":
                key = (row.get("component_ref") or {}).get("key")
                linked = {
                    (source["key"], item["key"])
                    for source in body["files"]
                    for item in source["components"]
                    if item["key"] == key
                }
                if not linked:
                    raise ValueError(f"Unreadable or unrepresented instance component: {row['id']}")
                expected["components"].update(linked)
        for section in expected:
            if not expected[section] <= refs[section]:
                raise ValueError(f"Design case omits source-bound {section}: {case['id']}")


def validate(body, root):
    """Enforce source-derived frame/state coverage and reference integrity."""
    frames, node_tables, source_facts = set(), {}, {}
    frame_owners = {}
    for file in body["files"]:
        local_pages = set()
        file_node_ids = set()
        discovered_pages = _file_metadata_pages(file, root)
        declared_pages = {page["id"] for page in file["pages"]}
        if discovered_pages != declared_pages:
            raise ValueError(
                f"Figma file pages and metadata differ for {file['key']}; missing: "
                f"{sorted(discovered_pages - declared_pages)}; unknown: "
                f"{sorted(declared_pages - discovered_pages)}"
            )
        for page in file["pages"]:
            if page["id"] in local_pages:
                raise ValueError(f"Duplicate Figma page in {file['key']}: {page['id']}")
            local_pages.add(page["id"])
            nodes, parents, screen_frames, owners = _metadata_nodes(page, root)
            duplicate_nodes = file_node_ids.intersection(nodes)
            if duplicate_nodes:
                raise ValueError(f"Figma node IDs repeat across pages in {file['key']}: {sorted(duplicate_nodes)}")
            file_node_ids.update(nodes)
            node_tables[(file["key"], page["id"])] = (nodes, parents)
            frame_owners[(file["key"], page["id"])] = owners
            for node_id in screen_frames:
                frames.add((file["key"], page["id"], node_id))
        source_facts.update(
            {(file["key"], page, node): row for (page, node), row in sources.validate(file, root, node_tables).items()}
        )
        if not local_pages:
            raise ValueError(f"Figma file has no page inventory: {file['key']}")

    case_ids, coverage, state_identities = set(), set(), set()
    cases_by_frame = {}
    mapped_inventory = {
        file["key"]: {section: set() for section in ("components", "variables", "fonts", "assets", "transitions")}
        for file in body["files"]
    }
    for case in body["cases"]:
        if case["id"] in case_ids:
            raise ValueError(f"Duplicate design case: {case['id']}")
        case_ids.add(case["id"])
        ref = (case["file_key"], case["page_id"], case["node_id"])
        if ref not in frames:
            raise ValueError(f"Case is not a discovered Figma screen frame: {case['id']}")
        identity = (
            *ref,
            case["state"],
            case["viewport"]["width"],
            case["viewport"]["height"],
            case["viewport"]["device_scale_factor"],
        )
        if identity in state_identities:
            raise ValueError(f"Duplicate Figma frame/state/viewport: {case['id']}")
        state_identities.add(identity)
        source_node = node_tables[(case["file_key"], case["page_id"])][0][case["node_id"]]
        if case["native_size"] != {key: source_node.get(key) for key in ("width", "height")} or any(
            case["viewport"][key] != round(case["native_size"][key]) for key in ("width", "height")
        ):
            raise ValueError("Native Figma size and exact-reference viewport must match the source frame")
        coverage.add(ref)
        cases_by_frame.setdefault(ref, set()).add(case["state"])
        file = next((row for row in body["files"] if row["key"] == case["file_key"]), None)
        if file is None:
            raise ValueError(f"Design case references an unknown Figma file: {case['id']}")
        refs = case_references(body, case)
        for section, identities in refs.items():
            for file_key, identity in identities:
                mapped_inventory[file_key][section].add(identity)
        _validate_bindings(body, case, refs, node_tables, source_facts)
    if coverage != frames:
        missing = sorted(frames - coverage)
        raise ValueError(f"Discovered Figma screen frames lack a case: {missing}")

    for file in body["files"]:
        for section, key_name in (
            ("components", "key"),
            ("variables", "key"),
            ("fonts", "id"),
            ("assets", "id"),
            ("transitions", "id"),
        ):
            declared = {row[key_name] for row in file[section]}
            missing = declared - mapped_inventory[file["key"]][section]
            if missing:
                raise ValueError(f"Figma {section} inventory has no case mapping in {file['key']}: {sorted(missing)}")

    for file in body["files"]:
        seen_components = set()
        declared_component_nodes = set()
        for component in file["components"]:
            key = component["key"]
            if key in seen_components:
                raise ValueError(f"Duplicate component identity in {file['key']}: {key}")
            seen_components.add(key)
            source_page = component["source_page_id"]
            source_id = component["source_node_id"]
            identity = (file["key"], source_page, source_id)
            if (file["key"], source_page) not in node_tables:
                raise ValueError(f"Component source references an unknown page: {identity}")
            source_nodes, source_parents = node_tables[(file["key"], source_page)]
            source_node = source_nodes.get(source_id)
            if not source_node or source_node["type"] not in ("COMPONENT", "COMPONENT_SET"):
                raise ValueError(f"Component source is missing from page metadata: {identity}")
            if identity in declared_component_nodes:
                raise ValueError(f"Duplicate Figma component source: {identity}")
            declared_component_nodes.add(identity)
            seen_variants = set()
            actual_variants = set()
            for variant in component["variants"]:
                identity = (file["key"], variant["page_id"], variant["node_id"])
                if identity in seen_variants:
                    raise ValueError(f"Duplicate component variant: {identity}")
                seen_variants.add(identity)
                if (file["key"], variant["page_id"]) not in node_tables:
                    raise ValueError(f"Component variant references an unknown page: {identity}")
                nodes, _ = node_tables[(file["key"], variant["page_id"])]
                node = nodes.get(variant["node_id"])
                if not node or node["type"] not in ("COMPONENT", "COMPONENT_SET"):
                    raise ValueError(f"Component variant is missing from the source inventory: {identity}")
                declared_component_nodes.add(identity)
                actual_variants.add((variant["page_id"], variant["node_id"]))
            if source_node["type"] == "COMPONENT_SET":
                expected_variants = set()
                for node_id, node in source_nodes.items():
                    parent = source_parents.get(node_id)
                    if node["type"] == "COMPONENT" and parent == source_id:
                        expected_variants.add((source_page, node_id))
                if actual_variants != expected_variants:
                    raise ValueError(
                        f"Figma component set has incomplete variant coverage: {identity}; missing: "
                        f"{sorted(expected_variants - actual_variants)}; unknown: "
                        f"{sorted(actual_variants - expected_variants)}"
                    )
            elif actual_variants:
                raise ValueError(f"Standalone Figma component cannot claim set variants: {identity}")

        discovered_component_nodes = {
            (file["key"], page_id, node_id)
            for page in file["pages"]
            for page_id in (page["id"],)
            for node_id, node in node_tables[(file["key"], page_id)][0].items()
            if node["type"] in ("COMPONENT", "COMPONENT_SET")
        }
        if declared_component_nodes != discovered_component_nodes:
            raise ValueError(
                f"Figma component inventory differs from page metadata for {file['key']}; missing: "
                f"{sorted(discovered_component_nodes - declared_component_nodes)}; unknown: "
                f"{sorted(declared_component_nodes - discovered_component_nodes)}"
            )

        seen_variables = set()
        for variable in file["variables"]:
            if variable["key"] in seen_variables:
                raise ValueError(f"Duplicate Figma variable in {file['key']}: {variable['key']}")
            seen_variables.add(variable["key"])

        for asset in file["assets"]:
            page = asset.get("page_id")
            if (file["key"], page) not in node_tables or asset["node_id"] not in node_tables[(file["key"], page)][0]:
                raise ValueError(f"Asset references an unknown Figma node: {asset['id']}")

        file_page_ids = {page["id"] for page in file["pages"]}
        for transition in file["transitions"]:
            destinations = set()

            def destinations_in(value):
                if isinstance(value, list):
                    for item in value:
                        destinations_in(item)
                elif isinstance(value, dict):
                    if value.get("destinationId"):
                        destinations.add(value["destinationId"])
                    for item in value.values():
                        destinations_in(item)

            destinations_in(transition["action"])
            for node_id in {transition["source_node_id"], *destinations}:
                page_id = next((page for page in file_page_ids if node_id in node_tables[(file["key"], page)][0]), None)
                if page_id is None:
                    raise ValueError(f"Prototype transition references an unknown Figma node: {node_id}")
                frame = frame_owners[(file["key"], page_id)].get(node_id)
                if frame and (file["key"], page_id, frame) in cases_by_frame:
                    continue
                parents = node_tables[(file["key"], page_id)][1]
                component_keys = {
                    component["key"]
                    for component in file["components"]
                    if component["source_page_id"] == page_id and _under(node_id, component["source_node_id"], parents)
                }
                if not any(
                    component_keys.intersection(case["inventory_refs"]["components"])
                    and (file["key"], transition["id"]) in case_references(body, case)["transitions"]
                    for case in body["cases"]
                ):
                    raise ValueError(
                        f"Prototype transition lacks a covered screen/component endpoint: {transition['id']}"
                    )

    # An unavailable source asset/font is valid intake only as an explicit blocker.
    # It cannot silently disappear from the manifest or earn overall acceptance.
    return body


def blockers(body):
    result = []
    for file in body["files"]:
        for kind in ("fonts", "assets"):
            for row in file[kind]:
                if row["status"] == "missing":
                    result.append(f"{file['key']} {kind[:-1]} {row['id']}: {row['reason']}")
    return sorted(result)


def path_intersects(case_path, affected_path):
    """Match exact files, directory scopes and declared glob scopes portably."""
    from fnmatch import fnmatchcase

    case_path = str(case_path).strip("/")
    affected_path = str(affected_path).strip("/")
    if not case_path or not affected_path:
        return False
    if any(char in affected_path for char in "*?["):
        return fnmatchcase(case_path, affected_path) or fnmatchcase(case_path, affected_path.replace("**/", ""))
    return (
        case_path == affected_path
        or case_path.startswith(affected_path + "/")
        or affected_path.startswith(case_path + "/")
    )


def builder_slice(body, root, affected_paths):
    """Project the approved catalog onto cases owned by one Builder task."""
    full = catalog(body, root)
    affected = sorted({str(path).strip("/") for path in affected_paths if str(path).strip("/")})
    cases = [
        copy.deepcopy(case)
        for case in body["cases"]
        if any(
            path_intersects(implementation_path, path)
            for implementation_path in case["implementation_paths"]
            for path in affected
        )
    ]
    cases.sort(key=lambda row: row["id"])

    refs_by_file = {}
    for case in cases:
        refs_by_file.setdefault(case["file_key"], {section: set() for section in case["inventory_refs"]})
        for section, identities in case_references(body, case).items():
            for file_key, identity in identities:
                refs = refs_by_file.setdefault(file_key, {kind: set() for kind in case["inventory_refs"]})
                refs[section].add(identity)
    selected_files = set(refs_by_file)
    selected_pages = {(case["file_key"], case["page_id"]) for case in cases}
    selected_pages.update(
        (file["key"], component["source_page_id"])
        for file in body["files"]
        for component in file["components"]
        if component["key"] in refs_by_file.get(file["key"], {}).get("components", set())
    )
    selected_frames = {(case["file_key"], case["page_id"], case["node_id"]) for case in cases}
    selected_states = {
        (
            case["file_key"],
            case["page_id"],
            case["node_id"],
            case["state"],
            case["viewport"]["width"],
            case["viewport"]["height"],
            case["viewport"]["device_scale_factor"],
        )
        for case in cases
    }

    linked_component_keys = {key for file_key, refs in refs_by_file.items() for key in refs["components"]}
    linked_variable_keys = {key for refs in refs_by_file.values() for key in refs["variables"]}
    linked_font_ids = {(file_key, identity) for file_key, refs in refs_by_file.items() for identity in refs["fonts"]}
    linked_asset_ids = {(file_key, identity) for file_key, refs in refs_by_file.items() for identity in refs["assets"]}
    linked_transition_ids = {
        (file_key, identity) for file_key, refs in refs_by_file.items() for identity in refs["transitions"]
    }

    sliced = {
        "files": [
            {**file, "pages": [page for page in file["pages"] if (file["key"], page["id"]) in selected_pages]}
            for file in full["files"]
            if file["key"] in selected_files
        ],
        "screen_frames": [
            row for row in full["screen_frames"] if (row["file_key"], row["page_id"], row["node_id"]) in selected_frames
        ],
        "screen_states": [
            row
            for row in full["screen_states"]
            if (
                row["file_key"],
                row["page_id"],
                row["node_id"],
                row["state"],
                row["viewport"]["width"],
                row["viewport"]["height"],
                row["viewport"]["device_scale_factor"],
            )
            in selected_states
        ],
        "cases": cases,
        # Preserve all variants/sources of any shared component used by this slice.
        "components": [row for row in full["components"] if row["key"] in linked_component_keys],
        "variables": [row for row in full["variables"] if row["key"] in linked_variable_keys],
        "fonts": [
            row
            for row in full["fonts"]
            if any((source["file_key"], source["id"]) in linked_font_ids for source in row["sources"])
        ],
        "assets": [row for row in full["assets"] if (row["file_key"], row["id"]) in linked_asset_ids],
        "transitions": [row for row in full["transitions"] if (row["file_key"], row["id"]) in linked_transition_ids],
        "blockers": [],
    }
    selected_missing = []
    for file in body["files"]:
        refs = refs_by_file.get(file["key"], {})
        for section in ("fonts", "assets"):
            linked = set(refs.get(section, ()))
            for row in file[section]:
                if row["id"] in linked and row["status"] == "missing":
                    selected_missing.append(f"{file['key']} {section[:-1]} {row['id']}: {row['reason']}")
    sliced["blockers"] = sorted(selected_missing)
    matched_paths = {path for case in cases for path in case["implementation_paths"]}
    return {
        "affected_paths": affected,
        "case_ids": [case["id"] for case in cases],
        "unmapped_affected_paths": [
            path
            for path in affected
            if not any(path_intersects(implementation_path, path) for implementation_path in matched_paths)
        ],
        "catalog": sliced,
    }


def _variable_value(value, variables):
    if isinstance(value, list):
        return [_variable_value(row, variables) for row in value]
    if not isinstance(value, dict):
        return value
    if value.get("type") == "VARIABLE_ALIAS":
        keys = sorted(row["key"] for row in variables if row["source_id"] == value["id"])
        if not keys:
            raise ValueError("Unreadable shared variable alias")
        return {"type": "VARIABLE_ALIAS", "keys": keys}
    return {key: _variable_value(row, variables) for key, row in value.items()}


def catalog(body, root):
    """Return a sorted, de-duplicated public design-system view."""
    validate(body, root)
    components = {}
    variables = {}
    fonts = {}
    transitions = {}
    for file in sorted(body["files"], key=lambda row: row["key"]):
        for component in file["components"]:
            shared = components.setdefault(
                component["key"], {"key": component["key"], "name": component["name"], "sources": [], "variants": []}
            )
            if shared["name"] != component["name"]:
                raise ValueError(f"Shared component key has conflicting names: {component['key']}")
            shared["sources"].append(
                {
                    "file_key": file["key"],
                    "page_id": component["source_page_id"],
                    "node_id": component["source_node_id"],
                }
            )
            shared["variants"].extend({**variant, "file_key": file["key"]} for variant in component["variants"])
        for variable in file["variables"]:
            key = variable["key"]
            definition = {field: value for field, value in variable.items() if field != "source_id"}
            definition["value"] = _variable_value(variable["value"], file["variables"])
            previous = variables.setdefault(key, {**copy.deepcopy(variable), "sources": []})
            old_definition = {
                field: value for field, value in previous.items() if field not in ("source_id", "sources")
            }
            # Preserve original local IDs in the public row, but reconcile their
            # values against stable library keys from that row's source file.
            first_file = (
                next(row for row in body["files"] if row["key"] == previous["sources"][0]["file_key"])
                if previous["sources"]
                else file
            )
            old_definition["value"] = _variable_value(previous["value"], first_file["variables"])
            if old_definition != definition:
                raise ValueError(f"Shared Figma variable has conflicting definitions: {key}")
            previous["sources"].append({"file_key": file["key"], "source_id": variable["source_id"]})
        for font in file["fonts"]:
            identity = (
                font["family"],
                font["style"],
                font["status"],
                font.get("artifact", {}).get("sha256", font.get("reason", "")),
            )
            shared = fonts.setdefault(identity, {**copy.deepcopy(font), "sources": []})
            shared["sources"].append({"file_key": file["key"], "id": font["id"]})
        for transition in file["transitions"]:
            identity = (file["key"], transition["id"])
            previous = transitions.setdefault(identity, {**transition, "file_key": file["key"]})
            if previous != {**transition, "file_key": file["key"]}:
                raise ValueError(f"Conflicting Figma transition: {identity}")
    for component in components.values():
        component["sources"] = sorted(
            component["sources"], key=lambda row: (row["file_key"], row["page_id"], row["node_id"])
        )
        unique = {(row["file_key"], row["page_id"], row["node_id"]): row for row in component["variants"]}
        component["variants"] = [unique[key] for key in sorted(unique)]
    return {
        "files": [
            {
                "key": file["key"],
                "revision": file["revision"],
                "pages": [{"id": page["id"], "name": page["name"]} for page in file["pages"]],
            }
            for file in sorted(body["files"], key=lambda row: row["key"])
        ],
        "screen_frames": [
            {"file_key": key, "page_id": page, "node_id": node}
            for key, page, node in sorted({(c["file_key"], c["page_id"], c["node_id"]) for c in body["cases"]})
        ],
        "screen_states": sorted(
            [{**row, "file_key": file["key"]} for file in body["files"] for row in file["screen_states"]],
            key=lambda row: (
                row["file_key"],
                row["page_id"],
                row["node_id"],
                row["state"],
                row["viewport"]["width"],
                row["viewport"]["height"],
                row["viewport"]["device_scale_factor"],
            ),
        ),
        "cases": [
            {key: case[key] for key in ("id", "file_key", "page_id", "node_id", "state", "route")}
            for case in sorted(body["cases"], key=lambda row: row["id"])
        ],
        "components": [components[key] for key in sorted(components)],
        "variables": [variables[key] for key in sorted(variables)],
        "fonts": [
            {**fonts[key], "sources": sorted(fonts[key]["sources"], key=lambda row: (row["file_key"], row["id"]))}
            for key in sorted(fonts)
        ],
        "assets": sorted(
            [{**row, "file_key": file["key"]} for file in body["files"] for row in file["assets"]],
            key=lambda row: (row["file_key"], row["id"]),
        ),
        "transitions": [transitions[key] for key in sorted(transitions)],
        "blockers": blockers(body),
    }
