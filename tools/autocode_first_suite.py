"""Preservation evidence for a positively identified documentation-only base."""

try:
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_verification_schedule as schedule


def absent_base_suite(base_receipt, candidate_receipt, *, document_only):
    """A new suite may not exist on base; the candidate must actually run it.

    The caller establishes document_only from the pinned Git inventory, matching
    baseline identity and command, and new-behavior mode without a base patch.
    This is evidence of no old behavior to preserve, not a successful base run.
    It never substitutes for the separate new-behavior proof.
    """
    candidate = candidate_receipt.get("results") or {}
    return bool(document_only
                and base_receipt.get("results_expected") is True
                and base_receipt.get("results") is None
                and base_receipt.get("exit_code") == 1
                and base_receipt.get("timed_out") is False
                and candidate_receipt.get("timed_out") is False
                and candidate_receipt.get("exit_code") == 0
                and schedule.complete_results(candidate_receipt)
                and candidate.get("passed") and not candidate.get("failed")
                and not candidate.get("skipped"))
