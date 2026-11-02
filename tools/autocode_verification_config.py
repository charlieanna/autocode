"""Explicit verification-command repair at a reconciled stopped run boundary."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_junit as junit
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_junit as junit
    import autocode_run_status as run_status
    import autocode_util as util

# The names autocode_resolver_human.PRIVATE and PUBLIC hold; that module sits above this one.
BLOCKER_PROPOSAL: Final = "resolver_human_proposal"
BLOCKER_REQUEST: Final = "resolver_human_request"
BLOCKER_STAGES: Final = ("astra_review", "astra_resolve")
TESTER_STAGES: Final = {"glm_first_v1": "astra_checkpoint", "glm_final_audit_v2": "terra"}


def add_arguments(parser):
    """Trusted verification inputs share one CLI/settings boundary."""
    parser.add_argument(
        "--test-command",
        help="Shell command for the project's test suite (default: detected); "
        "repair a saved command at a reconciled pause or Completion Owner blocker hold with --resume-paused",
    )
    parser.add_argument(
        "--regression-command",
        help="Shell command for new or changed regression tests (default: derived); "
        "repair a saved command at a reconciled pause or Completion Owner blocker hold with --resume-paused",
    )
    parser.add_argument(
        "--test-root",
        help="New runs: caller-selected workspace directory whose Python suite the "
        "regression proof detects and runs; changes outside it remain unverified",
    )
    parser.add_argument(
        "--test-report",
        type=junit.normalize,
        help="Named test evidence from a fresh JUnit report: junit:relative/path.xml; requires --test-command",
    )


def validate(options):
    """A report format never grants authority to a Builder-suggested command."""
    if options.get("test_report") is not None:
        options["test_report"] = junit.normalize(options["test_report"])
        if not isinstance(options.get("test_command"), str) or not options["test_command"].strip():
            raise ValueError("--test-report requires an explicit trusted --test-command")
        if options.get("test_root") is not None:
            raise ValueError("--test-root selects Python discovery and cannot accompany --test-report")
    return options


def _blocker(proposal) -> bool:
    origin = proposal.get("origin") if isinstance(proposal, dict) else None
    return isinstance(origin, dict) and proposal.get("scope") == "blocker" and origin.get("stage") in BLOCKER_STAGES


def blocker_hold(state: RunState):
    """(proposal, ledger entry or None) when the run is held for a Completion Owner blocker, else None.

    Queued, the Resolver's proposal is private and the run waits at RESOLVER_PENDING. Published, the
    request is answerable at WAITING_FOR_USER and its pending ledger entry must still match the
    identity it was sealed under, issued by the Resolver. Anything else is not this hold.
    """
    status = state.get("status")
    if status == "RESOLVER_PENDING":
        proposal = state.get(BLOCKER_PROPOSAL)
        return (proposal, None) if _blocker(proposal) else None
    if status != "WAITING_FOR_USER":
        return None
    key = (state.get(BLOCKER_REQUEST) or {}).get("request_id")
    ledger = (state.get("resolver") or {}).get("human_escalations") or {}
    entry = ledger.get(key) if isinstance(key, str) else None
    if not isinstance(entry, dict) or entry.get("status") != "pending":
        return None
    identity = entry.get("identity")
    if not isinstance(identity, dict) or util.digest(identity) != key or identity.get("issuer") != "resolver":
        return None
    proposal = identity.get("proposal")
    return (proposal, entry) if _blocker(proposal) else None


def _retire_blocker(state: RunState, proposal, entry, reason: str) -> str:
    """Archive and withdraw the blocker, leaving no question or request for a person; return the pause status.

    Mirrors autocode_resolver_human.supersede_operational for a published request, which this layer
    cannot import.
    """
    state.setdefault("validation_archive", []).append(
        {
            "at": util.now(),
            "reason": reason,
            "resolver_proposal": copy.deepcopy(proposal),
            "regression_proof": copy.deepcopy(state.get("regression_proof")),
            "validation": copy.deepcopy(state.get("validation") or {}),
        }
    )
    state.pop(BLOCKER_PROPOSAL, None)
    if entry is not None:
        public = state.get(BLOCKER_REQUEST) or {}
        issued = public.get("questions") or proposal.get("questions") or []
        state["pending_questions"] = [q for q in state.get("pending_questions") or [] if q not in issued]
        # The ledger entry's status, not the run's; a merge keeps the run-status ratchet from matching it.
        entry |= {"status": "superseded", "superseded_at": util.now(), "superseded_reason": reason}
        state.pop(BLOCKER_REQUEST, None)
    if state.get("user_request") == proposal.get("request"):
        state.pop("user_request", None)
    origin = proposal.get("origin") or {}
    previous = origin.get("pause_status") or proposal.get("previous_status")
    return previous if isinstance(previous, str) and previous.startswith("PAUSED_") else "PAUSED_RESOLVER"


def configure_resume(state: RunState, settings, args):
    """Update existing regression settings; retain proofs for their context check.

    Only this function writes command-change user_events. Status/history readers
    retain the receipt; regression.prove compares settings to cached evidence.
    The caller owns the run lock and persists settings and events together.

    A Completion Owner blocker hold accepts the change as well as a stopped checkpoint: the blocker
    is retired with a recorded reason and the run goes through the Tester before the Completion
    Reviewer can accept anything on the old command's proof.
    """
    selected = {key: getattr(args, key, None) for key in ("test_command", "regression_command", "test_report")}
    selected = {key: value for key, value in selected.items() if value is not None}
    previous = dict(settings.get("regression") or {})
    changed = {key: value for key, value in selected.items() if value != previous.get(key)}
    if not changed:
        return settings
    if any(not isinstance(value, str) or not value.strip() for value in changed.values()):
        raise ValueError("Verification commands must not be empty")
    hold = blocker_hold(state)
    if not getattr(args, "resume_paused", False) or not (str(state.get("status", "")).startswith("PAUSED_") or hold):
        raise ValueError("Changing saved verification commands requires a paused run and --resume-paused")
    # The pre-build acceptance proof pause, before its proof has run, is such a checkpoint too (#2609).
    before_proof = (
        state.get("status") == "PAUSED_ACCEPTANCE_TESTS" and state.get("next_stage") == "write_acceptance_tests"
    )
    if not hold and not before_proof and state.get("next_stage") not in ("sol", "astra_checkpoint"):
        raise ValueError(
            "Changing verification commands requires a stopped checkpoint before the Validator "
            "or combined checkpoint; the new proof must run before any acceptance"
        )
    if any(
        state.get(key)
        for key in (
            "active_stage",
            "pending_report_repair",
            "uncertain_artifacts",
            "active_runner_check",
            "runner_check",
        )
    ):
        raise ValueError("Reconcile the active or uncertain attempt before changing verification commands")
    current = validate({**previous, **changed})
    if hold:
        mode = (settings.get("workflow") or {}).get("mode")
        paused = _retire_blocker(
            state,
            hold[0],
            hold[1],
            "The saved verification command changed on resume, so the Completion Owner blocker is withdrawn "
            "and the new proof runs before any acceptance",
        )
        run_status.pause(state, paused, next_stage=TESTER_STAGES.get(cast(str, mode), "sol"))
    settings["regression"] = current
    state.setdefault("user_events", []).append(
        {
            "kind": "verification_commands_changed",
            "actor": "user_cli",
            "at": util.now(),
            "previous": {key: previous.get(key) for key in changed},
            "current": changed,
        }
    )
    return settings
