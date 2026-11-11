"""Long lists of passing test names in a review handoff: counted inline, kept whole in the context artifact.

The runner re-runs the Validator's checks in a clean copy (``validation.check_replay``) and proves the
change against its base (``regression_proof``); both keep every test that passed. On a mid-size Go
repository (#3095) the replayed ``./...`` run named over a megabyte of passing tests, another check
376 KB and ``pass_to_pass`` 198 KB, so the Completion Reviewer's prompt reached 1.96 MB and its model
refused it as too long after the Tester had already checked every criterion.

For the Completion Reviewer and the checkpoint review, a passing list over ``LIMIT`` bytes moves whole
into the hash-pinned context artifact, and the handoff keeps its count. Everything a reviewer judges
from stays inline: verdicts, exit codes, failed, skipped and uncollected tests, the receipt's output
path and hash, and the proof's failing, new and unstable tests. The Tester keeps its lists: its
contained launch binds the exact proof it was shown (``autocode_provider_launch.containment_prompt``).
Pure: the caller passes the packet it already copied and the dict of moved fields.
"""

from __future__ import annotations

import copy
import json

LIMIT = 16384
STAGES = ("review_completion", "review_checkpoint")
PASS_TO_PASS = "regression_proof_pass_to_pass"
REPLAY_PASSED = "check_replay_passed"
INSTRUCTION = (
    "Long lists of passing test names are summarized here by their count and kept whole in "
    "context_artifact (path and SHA-256 below) under {fields}; details_artifact names the field and, for a "
    "replayed check, the key of its list. Every verdict, exit code, failed, skipped or uncollected test, "
    "output path and output hash stays inline. Read the complete list before relying on whether one "
    "particular test passed. Missing, unreadable or hash-mismatched artifacts are unverified evidence, "
    "never permission to infer PASS. Every obligation and gate still applies."
)


def _long(value) -> bool:
    return isinstance(value, list) and len(json.dumps(value)) > LIMIT


def move(result, moved) -> None:
    """Summarize ``result``'s long passing-test lists in place; put the originals in ``moved``."""
    if result.get("stage") not in STAGES or result.get("context_artifact"):
        return
    proof = result.get("regression_proof")
    if isinstance(proof, dict) and "passing_detail_instruction" not in proof and _long(proof.get("pass_to_pass")):
        moved[PASS_TO_PASS] = copy.deepcopy(proof["pass_to_pass"])
        proof["pass_to_pass"] = {
            "count": len(proof["pass_to_pass"]),
            "details_artifact": {"field": PASS_TO_PASS},
        }
        proof["passing_detail_instruction"] = INSTRUCTION.format(fields=PASS_TO_PASS)
    validation = result.get("validation")
    replay = validation.get("check_replay") if isinstance(validation, dict) else None
    if not isinstance(replay, dict):
        return
    rows = replay.get("checks")
    if isinstance(rows, list) and "passing_detail_instruction" not in replay:
        lists = {}
        for index, row in enumerate(rows):
            results = row.get("results") if isinstance(row, dict) else None
            if isinstance(results, dict) and _long(results.get("passed")):
                lists[str(index)] = copy.deepcopy(results["passed"])
                results["passed"] = {
                    "count": len(results["passed"]),
                    "details_artifact": {"field": REPLAY_PASSED, "key": str(index)},
                }
        if lists:
            moved[REPLAY_PASSED] = lists
            replay["passing_detail_instruction"] = INSTRUCTION.format(fields=REPLAY_PASSED)
