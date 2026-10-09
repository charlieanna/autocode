"""Move bulky historical material to an immutable, retrievable handoff artifact."""

import json
from pathlib import Path

HISTORY_FIELDS = ("brief_feedback", "saved_answers")


def compact(base, state_path):
    try:
        from . import autocode_handoff_dedup as handoff
        from . import autocode_handoff_history as history
        from . import autocode_util as util
    except ImportError:
        import autocode_handoff_dedup as handoff
        import autocode_handoff_history as history
        import autocode_util as util

    def build(source, condensed):
        result, moved = history.condense(source) if condensed else (source, {})
        result = handoff.deduplicate(result)
        # Never remove requirements, human decisions or current findings. Saved answers and feedback are only
        # shortened for review stages once an approved contract reflects them (autocode_handoff_history).
        for key in (
            "evidence_locations",
            "deferred_backlog",
            "preserved_checkpoint",
            "prior_validation_reports",
            "source_snapshot",
        ):
            value = result.get(key)
            if not isinstance(value, (dict, list)) or len(json.dumps(value).encode()) <= 1200:
                continue
            moved[key] = value
            if key == "source_snapshot":
                result[key] = {k: value[k] for k in ("revision", "head") if k in value}
            elif isinstance(value, list):
                result[key] = value[-3:]
            else:
                result.pop(key, None)
        if moved:
            archive = Path(state_path).parent / "context" / (util.digest(moved) + ".json")
            if not archive.exists():
                util.atomic_json(archive, moved)
            elif util.read(archive) != moved:
                raise ValueError("Saved context artifact changed: " + str(archive))
            result["context_artifact"] = {
                "path": str(archive),
                "sha256": util.file_hash(archive),
                "fields": {
                    key: {"total": len(value) if isinstance(value, (dict, list)) else None}
                    for key, value in moved.items()
                },
                "instruction": "Earlier material is preserved here. Retrieve relevant fields before relying on historical evidence. This index does not change requirements or authorize skipping checks.",
            }
        return result, moved

    result, moved = build(base, True)
    if set(moved) <= set(HISTORY_FIELDS) and len(json.dumps(result)) >= len(json.dumps(base)):
        # Settled-history shortening alone did not pay for the artifact index it
        # caused; produce the packet an uncondensed run would have. The archive
        # already written for the discarded attempt stays: it is content-addressed
        # and a saved run may still reference the identical record.
        result, moved = build(base, False)
    return result, list(moved)
