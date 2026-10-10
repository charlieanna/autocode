"""Verify implementation capture provenance separately from visual judgment.

No run-state writes. The capture CLI writes immutable bundles; stage context,
independent report decoding and completion recheck them against current inputs.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope

import copy
from pathlib import Path
from urllib.parse import urlsplit

try:
    from . import autocode_contract_identity as contract
    from . import autocode_design_identity as design_identity
    from . import autocode_design_manifest as design
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as contract
    import autocode_design_identity as design_identity
    import autocode_design_manifest as design
    import autocode_util as util


def reference_hash(settings):
    record = settings.get("design_manifest")
    return (
        record["manifest_hash"]
        if record
        else (util.digest({"figma_file": settings["figma_file"]}) if settings.get("figma_file") else None)
    )


def case_binding(case):
    return {key: copy.deepcopy(case[key]) for key in ("id", "route", "state", "viewport")}


def local_file(root, value):
    root = Path(root).resolve()
    raw = Path(value)
    raw = raw if raw.is_absolute() else root / raw
    if any(part.is_symlink() for part in (raw, *raw.parents) if part != root and part.is_relative_to(root)):
        raise ValueError(f"Capture input must not be a symlink: {value}")
    path = raw.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Missing or external capture input: {value}")
    return path


def verify(state, capture_ref, capture_sha256=None, *, case=None, current=None):
    """Return the validated bundle and evidence files, never a visual PASS."""
    try:
        return _verify(state, capture_ref, capture_sha256, case=case, current=current)
    except (KeyError, TypeError, AttributeError, IndexError) as error:
        raise ValueError(f"Invalid or incomplete implementation capture manifest: {error}") from error


def _verify(state, capture_ref, capture_sha256=None, *, case=None, current=None):
    root = Path(state["workspace"]).resolve()
    path = local_file(root, capture_ref)
    if not path.is_relative_to(root / ".autocode" / "captures") or path.name != "manifest.json":
        raise ValueError("Implementation capture needs its retained capture manifest")
    if capture_sha256 is not None and util.file_hash(path) != capture_sha256:
        raise ValueError("Implementation capture manifest changed after review selection")
    body = util.read_object(path)
    if body.get("version") != 1 or body.get("kind") != "implementation_capture":
        raise ValueError("Missing implementation capture provenance")
    if body.get("reference_hash") != reference_hash(state.get("settings", {})) and not design_identity.matches(
        state.get("settings", {}), body.get("reference_hash"), (body.get("case") or {}).get("id")
    ):
        raise ValueError("Implementation capture belongs to a different design reference or an affected case")
    current = current or source_scope.snapshot(root, state)
    if body.get("source_revision") != current["revision"]:
        raise ValueError(
            "Stale implementation capture: source changed since capture; recapture the current implementation"
        )
    if case is not None and body.get("case") != case_binding(case):
        raise ValueError("Implementation capture has a different case, route, state or viewport")
    inputs = body.get("inputs")
    if not isinstance(inputs, dict) or not inputs:
        raise ValueError("Capture is missing its exact input identities")
    refs = [str(path)]
    for name, expected in inputs.items():
        input_path = local_file(root, name)
        if util.file_hash(input_path) != expected:
            raise ValueError(f"Capture input changed: {name}; recapture before review")
        refs.append(str(input_path))
    config_path = local_file(root, body["config_ref"])
    if str(config_path.relative_to(root)) not in inputs:
        raise ValueError("Capture configuration has no identity")
    config = util.read_object(config_path)
    if config["case"] != body["case"] or config["reference_hash"] != body["reference_hash"]:
        raise ValueError("Capture configuration disagrees with the captured case")
    if config["fixture"] not in inputs:
        raise ValueError("Capture script identity is missing")
    for name, expected in body["engine"].items():
        if name not in ("autocode_visual_capture.py", "autocode_visual_browser.cjs"):
            raise ValueError("Unknown capture engine")
        if util.file_hash(Path(__file__).with_name(name)) != expected:
            raise ValueError("Capture engine changed; create a fresh capture")
    if set(body["engine"]) != {"autocode_visual_capture.py", "autocode_visual_browser.cjs"}:
        raise ValueError("Capture engine identity is missing")
    artifacts = body["artifacts"]
    for item in artifacts.values():
        target = local_file(root, item["path"])
        if target.parent != path.parent or util.file_hash(target) != item["sha256"]:
            raise ValueError("Implementation capture artifact was changed or substituted")
        refs.append(str(target))
    browser = util.read_object(root / artifacts["browser"]["path"])
    binding = body["case"]
    if (
        browser.get("status") != "CAPTURED"
        or browser.get("errors")
        or browser.get("setup") is not True
        or browser.get("teardown") is not True
        or browser.get("viewport") != binding["viewport"]
        or browser.get("ready") != config["ready"]
        or urlsplit(browser["url"]).path != binding["route"]
        or browser.get("screenshot_sha256") != artifacts["candidate"]["sha256"]
    ):
        raise ValueError("Browser capture does not establish the declared rendered state")
    viewport = binding["viewport"]
    expected_size = tuple(round(viewport[key] * viewport["device_scale_factor"]) for key in ("width", "height"))
    if design.png_dimensions(root / artifacts["candidate"]["path"]) != expected_size:
        raise ValueError("Implementation capture dimensions differ from its viewport")
    declared = {item["url"]: item["path"] for item in config["assets"]}
    seen = set()
    for response in browser["responses"]:
        name = declared.get(response["asset"])
        if name not in inputs or response["sha256"] != inputs[name] or not 200 <= response["status"] < 300:
            raise ValueError("Served asset differs from the current implementation inputs")
        seen.add(response["asset"])
    if not seen or seen != set(declared):
        raise ValueError("Capture does not account for all declared served assets")
    if any(name not in current["files"] for name in declared.values()) and not body.get("build_executed"):
        raise ValueError("Generated assets need a fresh build bound to the captured source")
    if body.get("build_executed"):
        build = util.read_object(root / artifacts["build"]["path"])
        if (
            not config["build_command"]
            or build.get("command") != config["build_command"]
            or build.get("exit_code") != 0
            or build.get("timed_out") is not False
            or build.get("source_revision") != body["source_revision"]
        ):
            raise ValueError("Capture build receipt is not bound to its source and command")
    return body, list(dict.fromkeys(refs))


def bundle_file(settings, workspace, path):
    """Whether `path` is a file its own capture bundle's manifest names for this design reference.

    The capture command writes each bundle in its own .autocode/captures/<id>/ and binds it, in
    manifest.json, to the design reference it was captured for. The manifest and each artifact it
    lists with the hash it recorded are runner-written evidence of that design; nothing else there is.
    Ownership only: verify() still decides freshness, and the reviewer the visual verdict.
    """
    root, path = Path(workspace), Path(path)
    captures = root / ".autocode" / "captures"
    if not path.is_relative_to(captures) or len(path.relative_to(captures).parts) != 2:
        return False
    manifest = path.parent / "manifest.json"
    try:
        body = util.read_object(local_file(root, manifest))
        reference = body.get("reference_hash")
        if (
            body.get("version") != 1
            or body.get("kind") != "implementation_capture"
            or not reference
            or reference != reference_hash(settings)
            and not design_identity.matches(settings, reference, (body.get("case") or {}).get("id"))
        ):
            return False
        return path == manifest or any(
            root / item["path"] == path and util.file_hash(path) == item["sha256"]
            for item in body["artifacts"].values()
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return False


def context(state, current):
    """Select current bundles explicitly; historical captures stay on disk."""
    reference = reference_hash(state.get("settings", {}))
    if reference is None:
        return None
    selected, rejected = {}, []
    root = Path(state["workspace"]).resolve()
    inventory = (state["settings"].get("design_manifest") or {}).get("body", {}).get("cases", [])
    cases = {case["id"]: case for case in inventory}
    for path in sorted((root / ".autocode" / "captures").glob("*/manifest.json")):
        try:
            body = util.read_object(path)
            cid = body["case"]["id"]
            if cases and cid not in cases:
                continue
            body, _ = verify(state, str(path), case=cases.get(cid), current=current)
            item = {
                "case": body["case"],
                "capture_ref": str(path),
                "capture_sha256": util.file_hash(path),
                "candidate_ref": str(root / body["artifacts"]["candidate"]["path"]),
            }
            selected[cid] = item
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
            rejected.append({"capture_ref": str(path), "reason": str(error)})
    return {
        "reference_hash": reference,
        "source_paths": source_scope.paths(state),
        "current": list(selected.values()),
        "unavailable_cases": [cid for cid in cases if cid not in selected],
        "rejected": rejected,
        "visual_acceptance": None,
    }


def native_refs(state, report):
    """Native Figma runs without an exported inventory still need bound captures."""
    if not state.get("settings", {}).get("figma_file") or state["settings"].get("design_manifest"):
        return []
    rows = report.get("implementation_captures") or []
    outcomes = report.get("criterion_results", [])
    complete = {row.get("id") for row in outcomes} == {
        row["id"] for row in state.get("acceptance_criteria", [])
    } and all(row.get("status") == "PASS" for row in outcomes)
    body = (state.get("goal_contract") or {}).get("body") or {}
    strict = any(
        isinstance(row, str) and row.startswith(("VISUAL_CASE_CRITERIA=", "VISUAL_REVIEW_PROFILE="))
        for row in body.get("constraints", [])
    )
    functional_only = (
        contract.approved(state)
        and not strict
        and "visual acceptance"
        in {row.strip().casefold() for row in body.get("scope_exclusions", []) if isinstance(row, str)}
    )
    if report.get("verdict") == "PASS" and complete and not rows and not functional_only:
        raise ValueError("Visual PASS needs current implementation capture receipts, not historical screenshots")
    refs, candidates = [], set()
    cited = {
        str(local_file(state["workspace"], ref))
        for result in report.get("criterion_results", [])
        for ref in result.get("evidence_refs", [])
        if not ref.startswith(("event:", "check:"))
    }
    for row in rows:
        body, files = verify(state, row["capture_ref"], row["capture_sha256"])
        candidate = str(Path(state["workspace"]).resolve() / body["artifacts"]["candidate"]["path"])
        if candidate not in cited:
            raise ValueError("Visual review must cite the captured implementation image in its criterion evidence")
        candidates.add(candidate)
        refs.extend(files)
    require_current_image_citations(state, report, candidates)
    return list(dict.fromkeys(refs))


def require_current_image_citations(state, report, candidates, references=()):
    allowed = set(candidates) | set(references)
    for result in report.get("criterion_results", []):
        if result.get("status") != "PASS":
            continue
        for ref in result.get("evidence_refs", []):
            if ref.startswith(("event:", "check:")):
                continue
            path = local_file(state["workspace"], ref)
            with path.open("rb") as stream:
                signature = stream.read(12)
            image = signature.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")) or signature[8:12] == b"WEBP"
            if image and str(path) not in allowed:
                raise ValueError(
                    "Passing criterion cites an image outside the current capture bundle; historical images cannot earn acceptance"
                )


INSTRUCTION = prompts.get("fragments/visual-evidence/instruction.md")
