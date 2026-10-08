"""The program agreement: the parent contract every workstream of a program is built from.

A program manifest (autocode_program) is the agreement. Before any workstream
starts, a person approves it by exact token, the same way a plan is approved
(``a<revision>:<hash>``). This module holds its rules and is pure: it imports
nothing from AutoCode except ``autocode_util``.

What it decides:

- **Rules beyond the manifest's shape** (``validate``): named user journeys,
  one walking-skeleton workstream every other part extends, versioned
  interfaces with a producer and consumers, every requirement assigned to a
  workstream, the checks that re-verify the integrated product.
- **Fingerprints.** ``digest`` covers the whole agreement. ``scope_digest``
  covers only what one workstream is built from: the program-wide rules, its own
  row, the definitions of the requirements it inherits, the interfaces it
  produces or consumes, and the journeys when it is the skeleton or the
  integration workstream. A workstream records the scope digest it was launched
  under; when a revision changes it, that workstream is stale and must be
  planned, approved and checked again. Workstreams whose scope did not change
  keep their approval.
- **Revisions** (``revision_problems``): the workstream graph is frozen, and an
  interface definition never changes without a new version. Both are compared
  ignoring list order and spelled-out defaults.
- **Inheritance** (``inherited``, ``dropped``): a child plan must keep every
  inherited requirement as an acceptance criterion with the same id; the
  integration workstream inherits every journey and every requirement assigned
  to a workstream other than a deployment one (deployment runs after it).
- **Interface changes after delivery** (``quiet_interface_changes``): a delivered
  interface version is never edited in place.
"""
from __future__ import annotations

import copy
import json
import re

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
SHARED_LISTS = ("constraints", "permission_boundaries", "end_to_end_flow", "technical_approach", "deliverables")
INTERFACE_KEYS = {"id", "summary", "paths", "version", "producer", "consumers", "schema", "behavior"}
JOURNEY_KEYS = {"id", "name", "steps", "simulated", "does_not_prove"}
REQUIREMENT_KEYS = {"id", "criterion", "verification_method", "human_review"}
# What makes up the workstream graph. A revision may change briefs, requirements,
# journeys, interfaces and checks, but never these: that is a different program.
TOPOLOGY_KEYS = ("kind", "owns", "depends_on", "skeleton", "skeleton_exempt")
# Execution details that are not part of what a workstream is built from.
NOT_SCOPE = ("engine",)


def token(revision, value):
    return f"a{revision}:{value}"


def digest(manifest):
    """The whole agreement's fingerprint. source_run is provenance, not agreement content."""
    return util.digest({key: value for key, value in manifest.items() if key != "source_run"})


def requirements(manifest):
    """The program's requirements by id: the parent contract's criteria, else the manifest's own list."""
    body = (manifest.get("contract") or {}).get("body") or {}
    rows = body.get("acceptance_criteria") if body else manifest.get("requirements")
    return {row["id"]: row for row in rows or [] if isinstance(row, dict) and isinstance(row.get("id"), str)}


def journeys(manifest):
    return list(manifest.get("journeys") or [])


def interfaces(manifest):
    return list((manifest.get("shared") or {}).get("interfaces") or [])


def rows_by_id(manifest):
    return {row["id"]: row for row in manifest["workstreams"]}


def depends(manifest, node, target):
    """Whether workstream ``node`` (transitively) depends on ``target``."""
    rows = rows_by_id(manifest)
    todo, seen = list(rows[node]["depends_on"]), set()
    while todo:
        current = todo.pop()
        if current == target:
            return True
        if current not in seen:
            seen.add(current)
            todo.extend(rows[current]["depends_on"])
    return False


def skeleton(manifest):
    """The walking-skeleton workstream's id (validated manifests have exactly one)."""
    return next((row["id"] for row in manifest["workstreams"] if row.get("skeleton")), None)


def needs_skeleton(manifest, wid):
    """Whether a workstream may only start once the walking skeleton is merged and verified."""
    row = rows_by_id(manifest)[wid]
    return not row.get("skeleton") and not row.get("skeleton_exempt")


def _strings(value, where, *, allow_empty=True):
    if (not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value)
            or (not allow_empty and not value)):
        raise ValueError(f"{where} must be a {'' if allow_empty else 'nonempty '}list of nonempty strings")
    return value


def _within(path, roots):
    return any(path == root or path.startswith(root + "/") for root in roots)


def validate(manifest):
    """Check the agreement rules on a manifest whose shape autocode_program already checked.

    Normalizes interface versions (default 1) in place; returns the manifest.
    """
    rows = rows_by_id(manifest)
    body = (manifest.get("contract") or {}).get("body") or {}
    for key in ("constraints", "permission_boundaries", "scope_exclusions"):
        if key in body:
            _strings(body[key], f"contract.body.{key}")
    if "requirements" in manifest:
        if manifest.get("contract"):
            raise ValueError("A manifest takes its requirements from the parent contract or from requirements, not both")
        if not isinstance(manifest["requirements"], list):
            raise ValueError("requirements must be a list")
        for row in manifest["requirements"]:
            if (not isinstance(row, dict) or set(row) - REQUIREMENT_KEYS or not isinstance(row.get("id"), str)
                    or not ID_RE.fullmatch(row["id"]) or not isinstance(row.get("criterion"), str)
                    or not row["criterion"].strip()):
                raise ValueError("requirements entries need an id and a criterion, and only "
                                 + ", ".join(sorted(REQUIREMENT_KEYS)))
        listed = manifest["requirements"]
    else:
        # The contract's own schema governs its criteria's fields; a row requirements() would skip is refused here.
        listed = ((manifest.get("contract") or {}).get("body") or {}).get("acceptance_criteria") or []
        if not isinstance(listed, list) or not all(
                isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].strip()
                and isinstance(row.get("criterion"), str) and row["criterion"].strip() for row in listed):
            raise ValueError("The parent contract's acceptance_criteria entries need an id and a criterion")
    ids = [row["id"] for row in listed]
    if len(ids) != len(set(ids)):
        raise ValueError("Requirement ids must be unique")
    known = requirements(manifest)
    if known:
        for row in rows.values():
            for cid in row.get("acceptance_criteria", []):
                if cid not in known:
                    raise ValueError(f"Workstream {row['id']} lists {cid!r}, which is not a requirement of the "
                                     f"agreement ({', '.join(sorted(known))})")
        assigned = {cid for row in rows.values() for cid in row.get("acceptance_criteria", [])}
        missing = sorted(set(known) - assigned)
        if missing:
            raise ValueError(f"Requirement(s) {', '.join(missing)} are assigned to no workstream; every requirement "
                             "needs a workstream that inherits it")

    stories = manifest.get("journeys")
    if not isinstance(stories, list) or not stories:
        raise ValueError("The agreement needs journeys: the named user journeys the final product check follows, "
                         "each {id, name, steps}")
    for row in stories:
        if not isinstance(row, dict) or set(row) - JOURNEY_KEYS:
            raise ValueError(f"Journey fields must be within {sorted(JOURNEY_KEYS)}")
        if not isinstance(row.get("id"), str) or not ID_RE.fullmatch(row["id"]):
            raise ValueError("Every journey needs an id of letters, digits, dot, underscore or dash")
        if not isinstance(row.get("name"), str) or not row["name"].strip():
            raise ValueError(f"Journey {row['id']} needs a name")
        _strings(row.get("steps"), f"journey {row['id']}.steps", allow_empty=False)
        if not isinstance(row.get("simulated", False), bool):
            raise ValueError(f"Journey {row['id']}: simulated must be true or false")
        if row.get("simulated") and not (isinstance(row.get("does_not_prove"), str) and row["does_not_prove"].strip()):
            raise ValueError(f"Journey {row['id']} is simulated: say what it does not prove (does_not_prove)")
    journey_ids = [row["id"] for row in stories]
    if len(journey_ids) != len(set(journey_ids)):
        raise ValueError("Journey ids must be unique")
    if set(journey_ids) & set(known):
        raise ValueError("Journey ids and requirement ids must differ: the integration workstream inherits both by id")

    skeletons = []
    for row in rows.values():
        if not isinstance(row.get("skeleton", False), bool):
            raise ValueError(f"Workstream {row['id']}: skeleton must be true or false")
        if row.get("skeleton"):
            skeletons.append(row["id"])
        if "skeleton_exempt" in row:
            reason = row["skeleton_exempt"]
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"Workstream {row['id']}: skeleton_exempt must give the reason it need not "
                                 "wait for the walking skeleton")
            if row["kind"] != "content" or row.get("skeleton"):
                raise ValueError(f"Workstream {row['id']}: only a content workstream may be skeleton_exempt")
        if "journeys" in row:
            _strings(row["journeys"], f"workstream {row['id']}.journeys", allow_empty=False)
            if not row.get("skeleton") or len(set(row["journeys"])) != len(row["journeys"]) or set(row["journeys"]) - set(journey_ids):
                raise ValueError(f"Workstream {row['id']}: journeys names distinct agreement journeys for the skeleton")
        if "checks" in row:
            _strings(row["checks"], f"workstream {row['id']}.checks")
    if len(skeletons) != 1:
        raise ValueError("Mark exactly one code workstream skeleton: true: the thinnest version that works from "
                         "start to finish, which every other workstream extends")
    base = rows[skeletons[0]]
    if base["kind"] != "code" or base["depends_on"]:
        raise ValueError(f"The walking skeleton {base['id']} must be a code workstream with no dependencies")
    for row in rows.values():
        if needs_skeleton(manifest, row["id"]) and not depends(manifest, row["id"], base["id"]):
            raise ValueError(f"Workstream {row['id']} must (transitively) depend on the walking skeleton {base['id']}, "
                             "or give skeleton_exempt with the reason it need not")
    if "checks" in manifest:
        _strings(manifest["checks"], "checks")

    seen = set()
    for row in interfaces(manifest):
        if set(row) - INTERFACE_KEYS:
            raise ValueError(f"Interface fields must be within {sorted(INTERFACE_KEYS)}")
        iid = row["id"]
        if iid in seen:
            raise ValueError("Interface ids must be unique")
        seen.add(iid)
        row.setdefault("version", 1)
        if type(row["version"]) is not int or row["version"] < 1:
            raise ValueError(f"Interface {iid}: version must be a positive integer")
        producer = row.get("producer")
        consumers = row.get("consumers", [])
        if producer is not None and (not isinstance(producer, str) or producer not in rows):
            raise ValueError(f"Interface {iid}: producer {producer!r} is not a workstream")
        if not isinstance(consumers, list) or not all(isinstance(item, str) for item in consumers) or len(
                consumers) != len(set(consumers)) or any(item not in rows for item in consumers):
            raise ValueError(f"Interface {iid}: consumers must be distinct workstream ids")
        if consumers and producer is None:
            raise ValueError(f"Interface {iid}: consumers need a producer")
        if producer in consumers:
            raise ValueError(f"Interface {iid}: the producer cannot also be a consumer")
        for wid in [producer, *consumers]:
            if wid is not None and rows[wid].get("skeleton_exempt"):
                raise ValueError(f"Interface {iid}: skeleton_exempt content {wid} cannot produce or consume a runtime interface")
        for consumer in consumers:
            if not depends(manifest, consumer, producer):
                raise ValueError(f"Interface {iid}: consumer {consumer} must (transitively) depend on its producer "
                                 f"{producer}, so it is built on the merged interface")
        if producer is not None:
            outside = [path for path in row.get("paths", []) if not _within(path, rows[producer]["owns"])]
            if outside:
                raise ValueError(f"Interface {iid}: producer {producer} does not own {', '.join(outside)}")
    return manifest


def inherited(manifest, wid):
    """Requirement and journey ids a workstream's own plan must keep as acceptance criteria (same ids)."""
    row = rows_by_id(manifest)[wid]
    known = requirements(manifest)
    if row["kind"] == "integration":
        # Deployment workstreams run after the final check, which may not deploy: what only they own is theirs.
        built = {cid for other in manifest["workstreams"] if other["kind"] != "deployment"
                 for cid in other.get("acceptance_criteria", [])}
        return [cid for cid in known if cid in built] + [journey["id"] for journey in journeys(manifest)]
    own_journeys = row.get("journeys", [story["id"] for story in journeys(manifest)]) if row.get("skeleton") else []
    return [cid for cid in row.get("acceptance_criteria", []) if cid in known] + list(own_journeys)


def definitions(manifest, wid):
    """Definitions inherited by a child; its verification methods may adapt to its proof base."""
    known = requirements(manifest)
    stories = {row["id"]: {"id": row["id"], "criterion": row["name"] + ": " + " -> ".join(row["steps"])}
               for row in journeys(manifest)}
    return {cid: copy.deepcopy(known.get(cid) or stories[cid]) for cid in inherited(manifest, wid)}


def normalized(text):
    # Case, punctuation and inner whitespace can be part of a literal path, token or protocol value.
    return text.strip() if isinstance(text, str) else None


def dropped(manifest, wid, criteria):
    """Missing, duplicated, weakened or human-review-stripped inherited criteria, in agreement order."""
    rows = {}
    for row in criteria or []:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            rows.setdefault(row["id"], []).append(row)
    return [cid for cid, expected in definitions(manifest, wid).items()
            if len(rows.get(cid, [])) != 1 or normalized(rows[cid][0].get("criterion")) != normalized(expected["criterion"])
            or (expected.get("human_review") is True and rows[cid][0].get("human_review") is not True)]


def lost_boundaries(manifest, body, *, approved=True):
    """The parent and shared agreement's constraints cannot be weakened by a child."""
    parent = (manifest.get("contract") or {}).get("body") or {}
    shared = manifest.get("shared") or {}
    problems = []
    for key in ("constraints", "permission_boundaries", *(("scope_exclusions",) if approved else ())):
        expected = parent.get(key, []) + shared.get(key, [])
        values = body.get(key)
        kept = {normalized(item) for item in values if isinstance(item, str)} if isinstance(values, list) else set()
        problems += [f"{key}: {item}" for item in dict.fromkeys(expected) if normalized(item) not in kept]
    return problems


def _involved(row, wid, kind):
    if kind == "integration":
        return True
    if row.get("producer") is None and not row.get("consumers"):
        return True  # an interface with no declared producer binds every workstream
    return row.get("producer") == wid or wid in row.get("consumers", [])


def scope(manifest, wid):
    """Everything one workstream is built from; see the module docstring."""
    row = rows_by_id(manifest)[wid]
    shared = manifest.get("shared") or {}
    body = (manifest.get("contract") or {}).get("body") or {}
    known = requirements(manifest)
    with_journeys = row["kind"] == "integration" or row.get("skeleton")
    return copy.deepcopy({
        "program": {"brief": manifest["brief"], "shared": {key: shared.get(key) for key in SHARED_LISTS},
                    "contract": {key: value for key, value in body.items()
                                 if key not in ("acceptance_criteria", "milestones")}},
        "workstream": _row_scope(row),
        "inherits": {cid: known[cid] for cid in inherited(manifest, wid) if cid in known},
        "interfaces": [{**_definition(item), "version": item["version"],
                        **({"consumers": sorted(item.get("consumers") or [])} if row["kind"] == "integration" else {})} for item in
                       sorted(interfaces(manifest), key=lambda item: item["id"]) if _involved(item, wid, row["kind"])],
        "journeys": [story for story in journeys(manifest) if row["kind"] == "integration"
                     or story["id"] in row.get("journeys", [item["id"] for item in journeys(manifest)])] if with_journeys else [],
    })


def _row_scope(row):
    """A workstream row as its scope counts it: list order, a spelled-out ``skeleton: false`` and the wording of
    a skeleton exemption's reason (which no brief carries) change nothing the workstream is built from."""
    value = {key: item for key, item in row.items() if key not in NOT_SCOPE}
    for key in ("owns", "depends_on"):
        value[key] = sorted(value[key])
    if not value.get("skeleton"):
        value.pop("skeleton", None)
    if "skeleton_exempt" in value:
        value["skeleton_exempt"] = True
    return value


def scope_digest(manifest, wid):
    return util.digest(scope(manifest, wid))


def affected(old, new):
    """Workstreams whose scope a revision changes; the others keep their approval."""
    return [row["id"] for row in new["workstreams"] if scope_digest(old, row["id"]) != scope_digest(new, row["id"])]


def topology(manifest):
    """The workstream graph, ignoring list order and spelled-out defaults (skeleton_exempt counts by presence)."""
    return {row["id"]: {"kind": row["kind"], "owns": sorted(row["owns"]), "depends_on": sorted(row["depends_on"]),
                        "skeleton": bool(row.get("skeleton")), "skeleton_exempt": "skeleton_exempt" in row}
            for row in manifest["workstreams"]}


def revision_problems(old, new):
    """Why ``new`` cannot be a revision of the approved ``old`` agreement (empty when it can)."""
    problems = []
    if old["name"] != new["name"]:
        problems.append("the program name changed")
    if topology(old) != topology(new):
        problems.append("workstream ids, kinds, ownership, dependencies or the skeleton changed; that is a new program")
    before = {row["id"]: row for row in interfaces(old)}
    for row in interfaces(new):
        previous = before.get(row["id"])
        if previous is None:
            continue
        changed = _definition(row) != _definition(previous)
        wanted = previous["version"] + int(changed)
        if row["version"] != wanted:
            problems.append(f"interface {row['id']} " + ("changed without a new version; " if changed and
                            row["version"] == previous["version"] else "has an invalid version; ") +
                            (f"publish the changed definition as exactly version {wanted}" if changed else
                             f"keep version {wanted} when its definition is unchanged"))
    return problems


def _definition(row):
    """An interface's definition without its version, ignoring list order and spelled-out defaults."""
    value = {key: item for key, item in row.items() if key not in ("version", "consumers") and item is not None}
    for key in ("paths",):
        if key in value:
            value[key] = sorted(value[key])
    return value


def bumped_interfaces(old, new):
    """{interface id: (old version, new version)} for interfaces a revision publishes anew."""
    before = {row["id"]: row["version"] for row in interfaces(old)}
    return {row["id"]: (before[row["id"]], row["version"]) for row in interfaces(new)
            if row["id"] in before and row["version"] > before[row["id"]]}


def changes(old, new):
    """Plain-language lines naming what a revision changes."""
    lines = []
    if old["brief"] != new["brief"]:
        lines.append("program outcome")
    for key in SHARED_LISTS:
        if (old.get("shared") or {}).get(key) != (new.get("shared") or {}).get(key):
            lines.append(f"shared {key.replace('_', ' ')}")
    contract_old, contract_new = old.get("contract") or {}, new.get("contract") or {}
    for key in sorted((set(contract_old) | set(contract_new)) - {"body"}):
        if contract_old.get(key) != contract_new.get(key):
            lines.append(f"parent contract {key.replace('_', ' ')}")
    body_old = contract_old.get("body") or {}
    body_new = contract_new.get("body") or {}
    for key in sorted(set(body_old) | set(body_new)):
        if key != "acceptance_criteria" and body_old.get(key) != body_new.get(key):
            lines.append(_what(f"parent contract {key.replace('_', ' ')}", body_old.get(key), body_new.get(key)))
    req_old, req_new = requirements(old), requirements(new)
    for cid in sorted(set(req_old) | set(req_new)):
        if req_old.get(cid) != req_new.get(cid):
            lines.append(f"requirement {cid} " + ("added" if cid not in req_old else "removed" if cid not in req_new
                                                  else "changed"))
    if _reordered(list(req_old), list(req_new)):
        lines.append("order of requirements")
    j_old = {row["id"]: row for row in journeys(old)}
    j_new = {row["id"]: row for row in journeys(new)}
    for jid in sorted(set(j_old) | set(j_new)):
        if j_old.get(jid) != j_new.get(jid):
            lines.append(f"journey {jid} " + ("added" if jid not in j_old else "removed" if jid not in j_new
                                              else "changed"))
    if _reordered(list(j_old), list(j_new)):
        lines.append("order of journeys")
    i_old = {row["id"]: row for row in interfaces(old)}
    for row in interfaces(new):
        previous = i_old.get(row["id"])
        if previous is None:
            lines.append(f"interface {row['id']} added (v{row['version']})")
        elif previous != row:
            lines.append(f"interface {row['id']} v{previous['version']} -> v{row['version']}"
                         + (" (order or spelled-out defaults only)" if _definition(previous) == _definition(row)
                            else ""))
    for iid in sorted(set(i_old) - {row["id"] for row in interfaces(new)}):
        lines.append(f"interface {iid} removed")
    if _reordered(list(i_old), [row["id"] for row in interfaces(new)]):
        lines.append("order of interfaces")
    rows_old = rows_by_id(old)
    for row in new["workstreams"]:
        before = rows_old.get(row["id"], {})
        for key in ("brief", "acceptance_criteria", "checks", "engine", *TOPOLOGY_KEYS):
            if before.get(key) != row.get(key):
                lines.append(_what(f"workstream {row['id']} {key.replace('_', ' ')}", before.get(key), row.get(key)))
    if _reordered(list(rows_old), [row["id"] for row in new["workstreams"]]):
        lines.append("order of workstreams")
    if old.get("checks") != new.get("checks"):
        lines.append(_what("program checks", old.get("checks"), new.get("checks")))
    if old.get("derivation_notes") != new.get("derivation_notes"):
        lines.append("derivation notes")
    if not lines and digest(old) != digest(new):
        keys = sorted(key for key in set(old) | set(new) if key != "source_run" and old.get(key) != new.get(key))
        lines.append("the manifest changed in a way no workstream is built from: " + ", ".join(keys))
    return lines


def _reordered(before, after):
    """Whether the ids both lists keep appear in a different order."""
    return [item for item in before if item in after] != [item for item in after if item in before]


def _what(label, before, after):
    """``label``, followed by "order" when a list kept its items and only their order changed."""
    if isinstance(before, list) and isinstance(after, list) and sorted(map(_text, before)) == sorted(map(_text, after)):
        return label + " order"
    return label


def _text(value):
    return json.dumps(value, sort_keys=True)


def quiet_interface_changes(manifest, wid, paths, published):
    """Interfaces a delivery changes in place: by anyone but the producer, or after its version was delivered.

    ``published`` maps an interface id to the version its producer already delivered.
    Returns [(interface id, reason)].
    """
    found = []
    for row in interfaces(manifest):
        roots = row.get("paths") or []
        touched = sorted(path for path in paths if _within(path, roots))
        if not touched:
            continue
        producer = row.get("producer")
        if producer is None:
            if rows_by_id(manifest)[wid]["kind"] == "integration":
                found.append((row["id"], f"the integration workstream changed {', '.join(touched)}"))
        elif wid != producer:
            found.append((row["id"], f"{wid} is not its producer ({producer}) and changed {', '.join(touched)}"))
        elif published.get(row["id"]) == row["version"]:
            found.append((row["id"], f"version {row['version']} was already delivered and {wid} changed "
                                     f"{', '.join(touched)}"))
    return found


def render(manifest, *, revision, value, previous=None):
    """The agreement as a person reads it before approving its exact token."""
    lines = [f"PROGRAM AGREEMENT {manifest['name']!r}, revision {revision}",
             f"Approve with token: {token(revision, value)}", "", "Outcome: " + manifest["brief"].strip(), ""]
    if previous is not None:
        hit = affected(previous, manifest)
        lines += ["Changes since the approved revision:"] + [f"- {line}" for line in changes(previous, manifest)]
        lines += ["Workstreams that lose their approval and must be planned, approved and checked again: "
                  + (", ".join(hit) or "none"), ""]
    shared = manifest.get("shared") or {}
    for key in SHARED_LISTS:
        if shared.get(key):
            lines += [key.replace("_", " ").capitalize() + ":"] + [f"- {item}" for item in shared[key]] + [""]
    known = requirements(manifest)
    if known:
        lines.append("Requirements:")
        lines += [f"- {cid}: {row.get('criterion', '')}" for cid, row in known.items()] + [""]
    lines.append("User journeys (the final product check follows these by name):")
    for row in journeys(manifest):
        lines.append(f"- {row['id']} {row['name']}: " + " -> ".join(row["steps"]))
        if row.get("simulated"):
            lines.append(f"  simulated; does not prove: {row['does_not_prove']}")
    lines.append("")
    lines.append("Workstreams:")
    for row in manifest["workstreams"]:
        label = "walking skeleton, built and verified first" if row.get("skeleton") else row["kind"]
        lines.append(f"- {row['id']} ({label}) owns {', '.join(row['owns']) or 'nothing'}; depends on "
                     f"{', '.join(row['depends_on']) or 'nothing'}")
        if row.get("skeleton_exempt"):
            lines.append(f"  need not wait for the skeleton: {row['skeleton_exempt']}")
        if inherited(manifest, row["id"]):
            lines.append("  inherits: " + ", ".join(inherited(manifest, row["id"])))
        lines.append("  " + row["brief"].strip().splitlines()[0])
        if row.get("checks"):
            lines.append("  checks, re-run on the integration branch after every merge:")
            lines += [f"  - {command}" for command in row["checks"]]
    lines.append("")
    if interfaces(manifest):
        lines.append("Interfaces (changed only by an approved change request and a new version):")
        for row in interfaces(manifest):
            who = (f"; produced by {row['producer']}" if row.get("producer") else "") + (
                f", used by {', '.join(row['consumers'])}" if row.get("consumers") else "")
            lines.append(f"- {row['id']} v{row['version']}: {row['summary']}{who} "
                         f"[{', '.join(row.get('paths', [])) or 'no paths'}]")
            for key in ("schema", "behavior"):
                if row.get(key) is not None:
                    text = row[key] if isinstance(row[key], str) else json.dumps(row[key], sort_keys=True)
                    lines.append(f"  {key}: {text}")
        lines.append("")
    if manifest.get("checks"):
        lines += ["Program checks, re-run on the integration branch after every merge:"]
        lines += [f"- {command}" for command in manifest["checks"]] + [""]
    lines.append("Approving this agreement approves no workstream's plan: each child run still asks for its own.")
    return "\n".join(lines)
