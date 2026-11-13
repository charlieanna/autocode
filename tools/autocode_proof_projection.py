"""Authenticate only Core's known presentations of an already authenticated proof."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

try:
    from . import autocode_context as context
    from . import autocode_handoff_dedup as handoff
    from . import autocode_proof_detail as detail
    from . import autocode_util as util
except ImportError:
    import autocode_context as context
    import autocode_handoff_dedup as handoff
    import autocode_proof_detail as detail
    import autocode_util as util


def _same(left, right):
    # Python equality conflates False/0 and True/1; proof identity must not.
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def _artifact(artifact, moved, run_dir):
    if not isinstance(artifact, dict) or run_dir is None:
        return False
    root = Path(run_dir).absolute() / "context"
    path = Path(artifact["path"])
    if (
        root.is_symlink()
        or root.resolve().parent != Path(run_dir).resolve()
        or path.parent != root
        or path.is_symlink()
        or path.resolve().parent != root.resolve()
    ):
        return False
    raw = path.read_bytes()
    saved = json.loads(raw)
    if not isinstance(saved, dict) or path != root / (util.digest(saved) + ".json"):
        return False
    expected = {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "fields": {
            key: {"total": len(value) if isinstance(value, (dict, list)) else None} for key, value in saved.items()
        },
        "instruction": context.ARTIFACT_INSTRUCTION,
    }
    proof_fields = {detail.FILES, detail.SURVIVORS, detail.INPUTS, detail.OBLIGATIONS, detail.RECEIPTS}
    return (
        _same(artifact, expected)
        and saved.keys() & proof_fields == moved.keys()
        and all(key in saved and _same(saved[key], value) for key, value in moved.items())
    )


def matches(data, canonical, run_dir=None):
    """Bind the entire displayed proof, with each moved field still pinned whole.

    The caller first authenticates executed checks against current source. This
    function grants no completion or changed-line-review authority. Projection
    values come only from that canonical proof and existing Core formatters,
    never from model/adapter summaries or a claimed source revision.
    """
    if not isinstance(data, dict) or not isinstance(canonical, dict):
        return False
    try:
        presented = data.get("regression_proof")
        if _same(presented, canonical):
            return True
        # Whole-packet dedup savings choose whether Core keeps its exact proof
        # references. Another field can also keep obligation rows inline. Both
        # choices only select known transformations of the canonical proof;
        # arbitrary packet/model content never supplies accepted proof values.
        for deduplicated in (False, True):
            for referenced in (False, True):
                source = {"stage": "tester", "regression_proof": copy.deepcopy(canonical)}
                if referenced:
                    source["_projection_pointer"] = detail.POINTER  # local formatter input, never serialized
                projected, moved = copy.deepcopy(source), {}
                if deduplicated:
                    handoff._changed_code_references(projected)
                context.proof_receipts(source, projected, moved)
                detail.move(source, projected, moved)
                if _same(presented, projected["regression_proof"]) and (
                    not moved or _artifact(data.get("context_artifact"), moved, run_dir)
                ):
                    return True
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return False
