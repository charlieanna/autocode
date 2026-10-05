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
  interface definition never changes without a new version.
- **Inheritance** (``inherited``, ``dropped``): a child plan must keep every
  inherited requirement as an acceptance criterion with the same id; the
  integration workstream inherits every requirement and every journey.
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
        ids = [row["id"] for row in manifest["requirements"]]
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
            if row["kind"] != "code" or row.get("skeleton"):
                raise ValueError(f"Workstream {row['id']}: only a code workstream other than the skeleton may be exempt")
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
        if producer is not None and producer not in rows:
            raise ValueError(f"Interface {iid}: producer {producer!r} is not a workstream")
        if not isinstance(consumers, list) or len(consumers) != len(set(consumers)) or any(
                item not in rows for item in consumers):
            raise ValueError(f"Interface {iid}: consumers must be distinct workstream ids")
        if consumers and producer is None:
            raise ValueError(f"Interface {iid}: consumers need a producer")
        if producer in consumers:
            raise ValueError(f"Interface {iid}: the producer cannot also be a consumer")
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
        return list(known) + [journey["id"] for journey in journeys(manifest)]
    return [cid for cid in row.get("acceptance_criteria", []) if cid in known]


def dropped(manifest, wid, criteria):
    """Inherited ids missing from a child plan's acceptance criteria (rows with ids, or ids)."""
    ids = {row.get("id") if isinstance(row, dict) else row for row in criteria or []}
    return [cid for cid in inherited(manifest, wid) if cid not in ids]


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
        "workstream": {key: value for key, value in row.items() if key not in NOT_SCOPE},
        "inherits": {cid: known[cid] for cid in inherited(manifest, wid) if cid in known},
        "interfaces": sorted((item for item in interfaces(manifest) if _involved(item, wid, row["kind"])),
                             key=lambda item: item["id"]),
        "journeys": journeys(manifest) if with_journeys else [],
    })


def scope_digest(manifest, wid):
    return util.digest(scope(manifest, wid))


def affected(old, new):
    """Workstreams whose scope a revision changes; the others keep their approval."""
    return [row["id"] for row in new["workstreams"] if scope_digest(old, row["id"]) != scope_digest(new, row["id"])]


def topology(manifest):
    return {row["id"]: {key: row.get(key) for key in TOPOLOGY_KEYS} for row in manifest["workstreams"]}


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
        if row["version"] < previous["version"]:
            problems.append(f"interface {row['id']} went back from version {previous['version']} to {row['version']}")
        elif row["version"] == previous["version"] and _definition(row) != _definition(previous):
            problems.append(f"interface {row['id']} changed without a new version; raise a change request and "
                            f"publish it as version {previous['version'] + 1}")
    return problems


def _definition(row):
    return {key: value for key, value in row.items() if key != "version"}


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
    body_old = (old.get("contract") or {}).get("body") or {}
    body_new = (new.get("contract") or {}).get("body") or {}
    for key in sorted(set(body_old) | set(body_new)):
        if key not in ("acceptance_criteria", "milestones") and body_old.get(key) != body_new.get(key):
            lines.append(f"parent contract {key.replace('_', ' ')}")
    req_old, req_new = requirements(old), requirements(new)
    for cid in sorted(set(req_old) | set(req_new)):
        if req_old.get(cid) != req_new.get(cid):
            lines.append(f"requirement {cid} " + ("added" if cid not in req_old else "removed" if cid not in req_new
                                                  else "changed"))
    j_old = {row["id"]: row for row in journeys(old)}
    j_new = {row["id"]: row for row in journeys(new)}
    for jid in sorted(set(j_old) | set(j_new)):
        if j_old.get(jid) != j_new.get(jid):
            lines.append(f"journey {jid} " + ("added" if jid not in j_old else "removed" if jid not in j_new
                                              else "changed"))
    i_old = {row["id"]: row for row in interfaces(old)}
    for row in interfaces(new):
        previous = i_old.get(row["id"])
        if previous is None:
            lines.append(f"interface {row['id']} added (v{row['version']})")
        elif previous != row:
            lines.append(f"interface {row['id']} v{previous['version']} -> v{row['version']}")
    for iid in sorted(set(i_old) - {row["id"] for row in interfaces(new)}):
        lines.append(f"interface {iid} removed")
    rows_old = rows_by_id(old)
    for row in new["workstreams"]:
        before = rows_old.get(row["id"], {})
        for key in ("brief", "acceptance_criteria", "checks", "engine"):
            if before.get(key) != row.get(key):
                lines.append(f"workstream {row['id']} {key.replace('_', ' ')}")
    if old.get("checks") != new.get("checks"):
        lines.append("program checks")
    return lines


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
    lines.append("")
    if interfaces(manifest):
        lines.append("Interfaces (changed only by an approved change request and a new version):")
        for row in interfaces(manifest):
            who = (f"; produced by {row['producer']}" if row.get("producer") else "") + (
                f", used by {', '.join(row['consumers'])}" if row.get("consumers") else "")
            lines.append(f"- {row['id']} v{row['version']}: {row['summary']}{who}")
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
