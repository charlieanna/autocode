"""Move bulky handoff detail to an immutable, retrievable artifact."""

import copy
import json
from pathlib import Path

HISTORY_FIELDS = ("brief_feedback", "saved_answers")
ARTIFACT_INSTRUCTION = (
    "Full material is preserved here. Retrieve relevant fields before relying on their evidence. "
    "This index does not change requirements or authorize skipping checks."
)


def proof_receipts(source, result, moved):
    """Keep every receipt outcome inline; archive its bulky execution details whole."""
    if result.get("stage") not in ("tester", "review_completion", "review_checkpoint") or result.get(
        "context_artifact"
    ):
        return
    proof = result.get("regression_proof")
    original = source.get("regression_proof")
    receipts = original.get("changed_code_receipts") if isinstance(original, dict) else None
    if (
        not isinstance(proof, dict)
        or not isinstance(receipts, list)
        or not receipts
        or not all(isinstance(row, dict) for row in receipts)
        or "receipt_details_instruction" in proof
        or len(json.dumps(proof.get("changed_code_receipts"))) <= 65536
    ):
        return
    receipts = copy.deepcopy(receipts)
    summaries = [
        {
            **{key: value for key, value in row.items() if key not in ("command", "tail", "results", "supervision")},
            "details_artifact": {"field": "regression_proof_receipts", "index": index},
        }
        for index, row in enumerate(receipts)
    ]
    if len(json.dumps(summaries)) + 2048 >= len(json.dumps(proof["changed_code_receipts"])):
        return
    moved["regression_proof_receipts"] = receipts
    proof["changed_code_receipts"] = summaries
    proof["receipt_details_instruction"] = (
        "Each changed_code_receipts entry keeps its outcome and evidence identities inline. Its "
        "details_artifact selects the complete original receipt at that field and list index in "
        "context_artifact (path and SHA-256 below). Read the complete receipt before relying on its "
        "command, test results, output tail or supervision evidence; those details are not omitted "
        "from the proof. Missing, unreadable or hash-mismatched artifacts are unverified evidence, "
        "never permission to infer PASS. Current obligations, mutation results and all gates still apply."
    )


def compact(base, state_path):
    try:
        from . import autocode_handoff_dedup as handoff
        from . import autocode_handoff_history as history
        from . import autocode_passing_detail as passing_detail
        from . import autocode_proof_detail as proof_detail
        from . import autocode_util as util
    except ImportError:
        import autocode_handoff_dedup as handoff
        import autocode_handoff_history as history
        import autocode_passing_detail as passing_detail
        import autocode_proof_detail as proof_detail
        import autocode_util as util

    def build(source, condensed):
        result, moved = history.condense(source) if condensed else (source, {})
        result = handoff.deduplicate(result)
        proof_receipts(source, result, moved)
        proof_detail.move(source, result, moved)
        passing_detail.move(result, moved)
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
                "instruction": ARTIFACT_INSTRUCTION,
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
