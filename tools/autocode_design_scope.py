"""Pure admission and dependency closure over already verified full Figma facts.

Callers must first run the existing complete source/topology/resource validators.
This helper cannot make incomplete source valid, read authority from a bundle,
grant image acceptance, or qualify a source supplied only as a display slice.
No files, run state, native tools or controllers are read or written here.

Fact inputs contain ``nodes`` and ``parents`` keyed by (file, page, node), and
``resources`` keyed by section then (file, local resource ID/stable key). The
owner index maps actual main-component stable keys to all verified original
COMPONENT identities. It is not built from an embedded instance definition.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

try:
    from . import autocode_design_sources as sources
    from . import autocode_util as util
except ImportError:
    import autocode_design_sources as sources
    import autocode_util as util


SECTIONS = ("components", "variables", "fonts", "assets", "transitions")
KINDS = frozenset({"approved_brief", "approved_scope_decision", "reviewed_native_case_binding"})
SHA = re.compile(r"[a-f0-9]{64}\Z")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _identity(value, size):
    _require(
        isinstance(value, tuple) and len(value) == size and all(isinstance(part, str) and part for part in value),
        "Malformed qualified source identity",
    )
    return value


def _case_ids(cases):
    _require(isinstance(cases, list) and cases, "Approved scope needs the complete reviewed case packet")
    ids = []
    for case in cases:
        _require(
            isinstance(case, dict) and isinstance(case.get("id"), str) and case["id"],
            "Approved scope case has no stable ID",
        )
        ids.append(case["id"])
    _require(len(ids) == len(set(ids)), "Duplicate reviewed case ID")
    return tuple(ids)


@dataclass(frozen=True)
class Admission:
    """Ephemeral verified authority; only its original artifact/pin is retained."""

    manifest_hash: str
    approved_case_ids: tuple[str, ...]
    approval_sha256: str
    case_packet_hash: str


def admit(approval_bytes, expected_sha256, manifest_hash, cases):
    """Authenticate external operator-pinned bytes against the complete body.

    The operator supplies expected_sha256 independently of the provider bundle.
    Callers retain original bytes/pin; this result is not another manifest schema.
    """
    _require(isinstance(approval_bytes, bytes) and approval_bytes, "Missing external approval artifact bytes")
    _require(isinstance(expected_sha256, str) and SHA.fullmatch(expected_sha256), "Missing trusted approval SHA256")
    _require(hashlib.sha256(approval_bytes).hexdigest() == expected_sha256, "External scope artifact SHA256 changed")
    _require(isinstance(manifest_hash, str) and SHA.fullmatch(manifest_hash), "Invalid complete manifest hash")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "Duplicate scope artifact field")
            result[key] = value
        return result

    try:
        artifact = json.loads(
            approval_bytes,
            object_pairs_hook=unique,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite scope value")),
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Unreadable external scope artifact") from error
    _require(
        isinstance(artifact, dict)
        and set(artifact) == {"version", "manifest_hash", "approved_case_ids", "approval_evidence"},
        "Unexpected scope artifact fields",
    )
    _require(type(artifact["version"]) is int and artifact["version"] == 1, "Unsupported scope artifact version")
    _require(artifact["manifest_hash"] == manifest_hash, "Scope authority belongs to another complete manifest")
    ids = _case_ids(cases)
    approved = artifact["approved_case_ids"]
    _require(
        isinstance(approved, list)
        and all(isinstance(row, str) and row for row in approved)
        and len(approved) == len(set(approved))
        and set(approved) == set(ids),
        "External scope must name exactly the complete reviewed case packet",
    )
    evidence = artifact["approval_evidence"]
    _require(isinstance(evidence, list) and evidence, "Missing reviewed scope evidence")
    seen = set()
    for row in evidence:
        _require(
            isinstance(row, dict)
            and set(row) == {"kind", "sha256"}
            and isinstance(row["kind"], str)
            and row["kind"] in KINDS
            and isinstance(row["sha256"], str)
            and SHA.fullmatch(row["sha256"]),
            "Invalid reviewed scope evidence",
        )
        identity = (row["kind"], row["sha256"])
        _require(identity not in seen, "Duplicate scope evidence")
        seen.add(identity)
    return Admission(manifest_hash, ids, expected_sha256, util.digest(cases))


def partition(full_source_frame_ids, cases, admission):
    """Return exact approved/retained frame complements; None stays legacy strict.

    Retained-frame ledgers copy their original source facts outside this helper.
    This set partition invents neither facts, exclusions, exports nor cases.
    """
    frames = list(full_source_frame_ids)
    for frame in frames:
        _identity(frame, 3)
    _require(len(frames) == len(set(frames)), "Duplicate full-source frame")
    covered = {_identity((case["file_key"], case["page_id"], case["node_id"]), 3) for case in cases}
    _require(covered <= set(frames), "Approved case is not an actual full-source screen frame")
    if admission is None:
        _require(covered == set(frames), "Legacy full-source screen frames lack implementation cases")
    else:
        _require(
            isinstance(admission, Admission)
            and _case_ids(cases) == admission.approved_case_ids
            and util.digest(cases) == admission.case_packet_hash,
            "Approved case packet changed after admission",
        )
    return tuple(sorted(covered)), tuple(sorted(set(frames) - covered))


def _validated_facts(facts):
    _require(
        isinstance(facts, dict) and set(facts) == {"nodes", "parents", "resources"},
        "Expected complete verified source fact projection",
    )
    nodes, parents, resources = facts["nodes"], facts["parents"], facts["resources"]
    _require(
        isinstance(nodes, dict) and nodes and isinstance(parents, dict) and set(nodes) == set(parents),
        "Incomplete full-source node/parent projection",
    )
    for identity, node in nodes.items():
        _identity(identity, 3)
        _require(
            isinstance(node, dict) and node.get("id") == identity[2] and isinstance(node.get("type"), str),
            "Source node identity differs from its complete projection",
        )
        _require(
            all(isinstance(node.get(section), list) for section in ("variables", "fonts", "assets", "transitions")),
            "Incomplete verified source resource facts",
        )
        parent = parents[identity]
        if parent is not None:
            _identity(parent, 3)
            _require(parent in nodes and parent[:2] == identity[:2], "Unknown or cross-page source parent")
        seen, current = set(), identity
        while current is not None:
            _require(current not in seen, "Cyclic original source parent chain")
            seen.add(current)
            current = parents[current]
    _require(isinstance(resources, dict) and set(resources) == set(SECTIONS), "Incomplete resource sections")
    for section, entries in resources.items():
        _require(isinstance(entries, dict), "Malformed full-resource projection")
        field = "key" if section in ("components", "variables") else "id"
        for identity, row in entries.items():
            _identity(identity, 2)
            _require(isinstance(row, dict) and row.get(field) == identity[1], "Resource identity changed")
    return nodes, parents, resources


def _under(node, root, parents):
    while node is not None:
        if node == root:
            return True
        node = parents[node]
    return False


def compute_use(full_source_facts, complete_owner_index, cases):
    """Compute exact app use, all owner variants and transitive token aliases.

    Unknown actual owners, missing variants, modes/aliases or required prototype
    targets block. Caller-supplied case refs never drive this closure.
    """
    nodes, parents, resources = _validated_facts(full_source_facts)
    ids = _case_ids(cases)
    _require(isinstance(complete_owner_index, dict), "Missing complete native owner index")
    approved_roots = {_identity((case["file_key"], case["page_id"], case["node_id"]), 3) for case in cases}
    _require(
        all(root in nodes and nodes[root]["type"] == "FRAME" for root in approved_roots),
        "Approved app root is absent from complete original source",
    )
    node_lookup = {}
    actual_owner_index = {}
    children = {identity: [] for identity in nodes}
    for identity in nodes:
        node_lookup.setdefault((identity[0], identity[2]), []).append(identity)
        if nodes[identity]["type"] == "COMPONENT":
            key = nodes[identity].get("component_key")
            _require(isinstance(key, str) and key, "Actual component has no original stable key")
            actual_owner_index.setdefault(key, set()).add(identity)
        if parents[identity] is not None:
            children[parents[identity]].append(identity)

    def descendants(ancestor):
        pending = [ancestor]
        while pending:
            identity = pending.pop()
            pending.extend(children[identity])
            yield identity, nodes[identity]

    expected, variant_rows, used_node_rows = {}, {}, {}
    for case, case_id in zip(cases, ids, strict=False):
        root = (case["file_key"], case["page_id"], case["node_id"])
        roots, processed, owner_roots, variants = {root}, set(), set(), set()
        uses = {section: set() for section in SECTIONS}
        variable_ids = set()
        transitions = []
        while roots:
            ancestor = roots.pop()
            for identity, row in descendants(ancestor):
                if identity in processed:
                    continue
                processed.add(identity)
                file_key = identity[0]
                for variable in row["variables"]:
                    variable_ids.add((file_key, variable))
                variable_ids.update((file_key, variable) for variable in sources.aliases(row["transitions"]))
                for font in row["fonts"]:
                    matching = {
                        key
                        for key, item in resources["fonts"].items()
                        if key[0] == file_key and (item["family"], item["style"]) == (font["family"], font["style"])
                    }
                    _require(matching, "Used font is absent from full resource declarations")
                    uses["fonts"].update(matching)
                for section in ("assets", "transitions"):
                    for item in row[section]:
                        key = (file_key, item["id"])
                        _require(key in resources[section], "Used resource is absent from complete declarations")
                        uses[section].add(key)
                        if section == "transitions":
                            transitions.append((file_key, item))
                if row["type"] != "INSTANCE":
                    continue
                ref = row.get("component_ref") or {}
                key = ref.get("key")
                # An instance publishes its COMPONENT_SET owner's key, while
                # node_id still names a variant whose own stable key is distinct.
                # Resolve that real file-local main before consulting its index;
                # neither an embedded getter nor the owner's key substitutes it.
                candidates = node_lookup.get((file_key, ref.get("node_id")), ())
                _require(
                    len(candidates) == 1 and nodes[candidates[0]]["type"] == "COMPONENT",
                    "Original main component is missing from complete source",
                )
                original_main = candidates[0]
                main_key = nodes[original_main].get("component_key")
                _require(
                    isinstance(ref.get("variant_properties"), dict)
                    and ref["variant_properties"] == nodes[original_main].get("variant_properties"),
                    "Instance variant coordinates differ from its actual main component",
                )
                owners = complete_owner_index.get(main_key, ())
                _require(isinstance(owners, (tuple, list)) and owners, "Unresolved actual native component owner")
                for owner in owners:
                    _identity(owner, 3)
                _require(len(owners) == len(set(owners)), "Duplicate native owner source")
                _require(original_main in owners, "Original main component is missing from complete source")
                _require(
                    set(owners) == actual_owner_index.get(main_key, set()),
                    "Native owner index omits or invents actual component definitions",
                )
                original = (row.get("visual") or {}).get("main_component_owner")
                _require(isinstance(original, dict), "Unreadable original native component owner")
                for main in owners:
                    _identity(main, 3)
                    _require(
                        main in nodes
                        and nodes[main]["type"] == "COMPONENT"
                        and nodes[main].get("component_key") == main_key,
                        "Native owner index is not an actual component definition",
                    )
                    _require(
                        nodes[main].get("variant_properties") == ref["variant_properties"],
                        "Shared main variant coordinates differ from the original reference",
                    )
                    parent = parents[main]
                    owner = parent if parent and nodes[parent]["type"] == "COMPONENT_SET" else main
                    _require(
                        nodes[owner].get("component_key") == key,
                        "Instance owner key differs from its actual main/set owner",
                    )
                    if main == original_main:
                        _require(
                            original.get("id") == owner[2] and original.get("type") == nodes[owner]["type"],
                            "Native getter owner differs from the full source definition",
                        )
                    resource_id = (owner[0], nodes[owner].get("component_key"))
                    _require(
                        resource_id in resources["components"], "Used owner is absent from full component declarations"
                    )
                    declared = resources["components"][resource_id]
                    _require(
                        (declared["source_page_id"], declared["source_node_id"]) == owner[1:],
                        "Component source identity differs from actual owner",
                    )
                    actual = {node for node in children[owner] if nodes[node]["type"] == "COMPONENT"}
                    declared_variants = [
                        (owner[0], variant["page_id"], variant["node_id"]) for variant in declared["variants"]
                    ]
                    _require(
                        len(declared_variants) == len(set(declared_variants)), "Duplicate declared component variant"
                    )
                    if nodes[owner]["type"] == "COMPONENT_SET":
                        _require(
                            set(declared_variants) == actual, "Used component set omits or invents actual variants"
                        )
                        for variant in declared["variants"]:
                            _require(
                                variant["properties"]
                                == nodes[(owner[0], variant["page_id"], variant["node_id"])]["variant_properties"],
                                "Variant coordinates changed",
                            )
                        variants.update(actual)
                    else:
                        _require(not declared_variants, "Standalone component cannot claim set variants")
                    uses["components"].add(resource_id)
                    if owner not in owner_roots:
                        owner_roots.add(owner)
                        roots.add(owner)
        seen_variables = set()
        while variable_ids:
            file_key, source_id = variable_ids.pop()
            if (file_key, source_id) in seen_variables:
                continue
            seen_variables.add((file_key, source_id))
            matching = {
                key
                for key, item in resources["variables"].items()
                if key[0] == file_key and item["source_id"] == source_id
            }
            _require(matching, "Used variable alias/mode lacks a complete source definition")
            # Case refs use stable keys and resolve every original source of
            # that key. Preserve their local IDs and expand each source's own
            # aliases instead of treating another verified copy as false use.
            stable_keys = {key[1] for key in matching}
            matching.update(key for key in resources["variables"] if key[1] in stable_keys)
            uses["variables"].update(matching)
            for key in matching:
                variable_ids.update((key[0], alias) for alias in sources.aliases(resources["variables"][key]["value"]))

        def destinations(value):
            if isinstance(value, list):
                return set().union(*(destinations(item) for item in value))
            if not isinstance(value, dict):
                return set()
            found = {value["destinationId"]} if value.get("destinationId") else set()
            return found | set().union(*(destinations(item) for item in value.values()))

        for file_key, transition in transitions:
            for destination in destinations(transition["action"]) | (
                {transition["target_node_id"]} if transition["target_node_id"] else set()
            ):
                candidates = node_lookup.get((file_key, destination), ())
                _require(len(candidates) == 1, "Prototype target is missing or ambiguous in full source")
                _require(
                    any(_under(candidates[0], covered, parents) for covered in approved_roots | owner_roots),
                    "Used prototype target is outside approved app/component scope",
                )
        expected[case_id] = {section: tuple(sorted(uses[section])) for section in SECTIONS}
        variant_rows[case_id] = tuple(sorted(variants))
        used_node_rows[case_id] = tuple(sorted(processed))
    return {"cases": expected, "component_variants": variant_rows, "used_nodes": used_node_rows}


def require_exact_use(case_refs, computed_uses, full_resource_ids):
    """Require exact case use and return the disjoint retained resource complement."""
    expected = computed_uses["cases"]
    _require(
        isinstance(case_refs, dict) and set(case_refs) == set(expected),
        "Case resource coverage differs from approved cases",
    )
    _require(
        isinstance(full_resource_ids, dict) and set(full_resource_ids) == set(SECTIONS),
        "Missing full resource universe",
    )
    full, used = {}, {section: set() for section in SECTIONS}
    for section, values in full_resource_ids.items():
        values = list(values)
        for value in values:
            _identity(value, 2)
        _require(len(values) == len(set(values)), "Duplicate full resource identity")
        full[section] = set(values)
    for case_id, refs in case_refs.items():
        _require(isinstance(refs, dict) and set(refs) == set(SECTIONS), "Incomplete case resource sections")
        for section, values in refs.items():
            values = list(values)
            for value in values:
                _identity(value, 2)
            _require(
                len(values) == len(set(values)) and set(values) == set(expected[case_id][section]),
                "Case resource refs omit actual use or claim false use",
            )
            _require(set(values) <= full[section], "Computed use is absent from the complete resource universe")
            used[section].update(values)
    return {
        "used": {section: tuple(sorted(used[section])) for section in SECTIONS},
        "retained": {section: tuple(sorted(full[section] - used[section])) for section in SECTIONS},
    }
