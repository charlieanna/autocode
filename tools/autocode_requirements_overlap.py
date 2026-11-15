"""The Requirements reading-time overlap: the full pass runs while the person reads (#3233 part 1).

At a chat blocking-question pause the controller blocks in ``chat_checkpoint`` and nothing runs
until the person answers, and only then does the full Requirements pass start. This module holds
what changes that, all of it over run state:

- **Eligibility** follows the original request's step 1: a live chat paused on the Requirements
  handoff's own unresolved blocking/open questions, every one offering a nonempty default, and cap
  admission. That includes a questions-only/triage handoff, a spec-first complete handoff's open
  questions and a default-flow complete handoff's business questions alike — whether the old flow
  set ``needs_refresh`` never mattered to the overlap (a differing answer explicitly schedules the
  ordinary full pass with the real answers). Pauses that are not the handoff's own blocking
  questions — the spec-first review confirmation, a scope decision, a resolver question — and runs
  under an approved goal contract keep today's path, as does any pause with a defaultless
  question: the adoption rule could not be evaluated exactly without the person's answer.
- **Cap admission**: the background call counts toward the run's existing caps, so it is not
  started when that could exceed one. A finite cost cap disables it (the existing accounting
  exposes estimates, not an enforceable maximum for one call); a finite active-time cap admits it
  only when the enforced stage deadline plus its bounded termination allowance fits the remaining
  active-time allowance.
- **The assumed-answers digest** binds a kept background report to the exact question ids and
  answer values its call assumed, so the Requirements handoff can refuse a report computed under
  different answers than the person actually gave.
- **The adopt/discard comparison**: every typed answer equal to its offered default — compared
  literally, at the chat input boundary, before whitespace stripping or offered-choice
  canonicalization — keeps the background report; any difference discards it and the ordinary
  pass runs with the real answers. ``/default`` selects the offered default exactly.
- **Ownership**: the background call is one bounded call owned by the existing controller inside
  ``chat_checkpoint`` (autocode.py), never a second controller or cross-run layer (AGENTS.md rule
  5). Live cancellation and crash/resume disposal settle only a process whose recorded execution
  identity still verifies (autocode_process); a reused PID is never signalled.

The dispatch machinery lives here too — ``RequirementsOverlapPass`` and the ``Controller`` that
starts, settles and records it. The module stays a lower layer (AGENTS.md rule 2): the
launch-path callables that live in autocode.py or autopilot.py — the stage launch, request
preparation, accounting, commit, record saving and launch guard, with the support helpers and
schema location they use — are injected by ``chat_checkpoint`` and never imported, so no module
joins an import cycle. The main-thread input wait that lets a queued Stop or boundary pause reach
the controller while the person reads is ``autocode_requirements_overlap_input``.
"""

from __future__ import annotations

import contextlib
import copy
import json
import math
import os
import signal
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_interventions as interventions
    from . import autocode_job_deadline as job_deadline
    from . import autocode_process as processes
    from . import autocode_util as util
except ImportError:
    import autocode_interventions as interventions
    import autocode_job_deadline as job_deadline
    import autocode_process as processes
    import autocode_util as util

#: The sidecar the background pass checkpoints its candidate state to, so the paused run's own
#: ``state.json`` stays authoritative while the call runs.
SIDECAR_NAME = "requirements-overlap-state.json"

#: The bounded termination allowance added to the enforced stage deadline when admitting the
#: call against an active-time cap: the supervisor's own SIGTERM→SIGKILL escalation (its
#: ``DEFAULT_KILL_AFTER`` of 2 s) plus process collection, held generously.
TERMINATION_ALLOWANCE_SECONDS = 10.0

#: Per-signal grace while stopping a verified owned provider: SIGTERM, then SIGKILL.
SIGNAL_GRACE_SECONDS = 2.0


def answer_texts(answers) -> dict:
    """``{question id: answer text}`` from the saved answers mapping (a row or a plain string)."""
    rows = {}
    for question_id, row in (answers or {}).items():
        rows[str(question_id)] = row.get("text") if isinstance(row, dict) else row
    return rows


def answers_digest(answers) -> str:
    """The digest of the exact question ids and answer values a pass assumed or received.

    Order-independent, so a mapping's serialization order never changes the binding.
    """
    return util.digest(sorted((question_id, str(text)) for question_id, text in answer_texts(answers).items()))


def assumed_defaults(questions) -> dict:
    """``{question id: offered default}`` for a pause whose every question offers a default."""
    return {str(row["id"]): str(row.get("proposed_default")) for row in questions}


def answers_match(questions, answers) -> tuple[bool, str]:
    """Whether every person answer equals its offered default exactly, and why not when it doesn't."""
    given = answer_texts(answers)
    for row in questions or []:
        if str(given.get(row.get("id"), "")) != str(row.get("proposed_default") or ""):
            return False, str(row.get("id")) + " was answered differently from its offered default"
    return True, "answers matched offered defaults"


def eligible(state: RunState, questions) -> dict:
    """Whether the controller may start the background full Requirements pass at this pause.

    Returns ``{"eligible": bool, "reason": str}``; the reason is recorded in the run's overlap
    evidence either way. Requires every pause question to be one of the handoff's own open
    questions offering a nonempty default; cap admission is a separate decision (``admission``).
    """
    rows = list(questions or [])
    if not rows:
        return {"eligible": False, "reason": "the pause asks nothing"}
    if state.get("goal_contract"):
        return {"eligible": False, "reason": "an approved goal contract owns this run's requirements"}
    report = (state.get("requirements_handoff") or {}).get("report") or {}
    own = {row.get("id") for row in report.get("open_questions") or []}
    if not all(row.get("id") in own for row in rows):
        return {
            "eligible": False,
            "reason": "the pause carries a question that is not the handoff's own open question "
            "(a review confirmation, a scope decision or a resolver question)",
        }
    defaultless = sorted(str(row.get("id")) for row in rows if not str(row.get("proposed_default") or "").strip())
    if defaultless:
        return {
            "eligible": False,
            "reason": "question(s) " + ", ".join(defaultless) + " offer no default to assume",
        }
    return {
        "eligible": True,
        "reason": "the handoff's blocking questions all offer defaults; the background pass assumes them "
        "while the person reads",
    }


def _finite(value) -> float | None:
    """A positive finite limit, or None for absent, zero, negative, boolean or unusable values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _recorded_active_seconds(value) -> float | None:
    """A recorded, finite, nonnegative usage measurement; absent, boolean or invalid values are unknown."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def admission(state: RunState) -> dict:
    """Whether the run's existing caps admit one more bounded Requirements call before it starts.

    Conservative by construction (#3233 item 4). A finite cost cap (``max_cost_usd`` as a positive
    number — the same reading as ``autocode_cost.cap``, where absent and 0 mean no cap) disables
    the speculative launch: the existing accounting exposes estimates rather than an enforceable
    maximum cost for one call, and missing pricing, missing history or an estimated projection is
    never proof of headroom. Every attempt requires a positive enforced effective stage deadline,
    including an authorized retry extension. With a finite active-time cap (``max_seconds``), that
    deadline plus its bounded termination allowance must fit in the remaining active-time allowance;
    when that bound or the remaining allowance cannot be established, the call is skipped. Every configured cap and model choice is preserved; the
    skip is recorded and the ordinary post-answer path runs.
    """
    limits = (state.get("settings") or {}).get("limits") or {}
    if _finite(limits.get("max_cost_usd")) is not None:
        return {
            "admitted": False,
            "reason": "a finite cost cap cannot bound one speculative call's spend; the existing post-answer flow continues",
        }
    deadline = _finite(job_deadline.launch_limit(state, "requirements_gather", limits.get("stage_timeout_seconds")))
    if deadline is None:
        return {
            "admitted": False,
            "reason": "no positive enforced effective stage deadline bounds the speculative call",
        }
    active_cap = _finite(limits.get("max_seconds"))
    if active_cap is not None:
        usage = _recorded_active_seconds(state.get("active_seconds"))
        if usage is None:
            return {
                "admitted": False,
                "reason": "the run's active-time usage is not a recorded finite measurement, so the remaining "
                "allowance cannot be established",
            }
        remaining = active_cap - usage
        bound = deadline + TERMINATION_ALLOWANCE_SECONDS
        if not math.isfinite(remaining) or remaining < bound:
            return {
                "admitted": False,
                "reason": f"the bounded speculative call (a {deadline:g}s stage deadline plus its termination "
                f"allowance) does not fit the remaining active-time allowance",
            }
    return {
        "admitted": True,
        "reason": "no finite cost cap and the bounded call fits the remaining active-time allowance",
    }


def provider_identity(record) -> dict | None:
    """The authenticated launch identity a saved attempt recorded; a bare PID is never authority."""
    supervision = record.get("supervision") if isinstance(record, dict) else None
    provider = supervision.get("provider") if isinstance(supervision, dict) else None
    if isinstance(provider, dict) and type(provider.get("pid")) is int and provider.get("birth_identity") is not None:
        return provider
    return None


def _default_live(identities):
    return processes.live_processes(identities)


def _default_kill(pid, sig) -> bool:
    try:
        # Providers are launched with start_new_session=True, so a verified provider pid leads a
        # private process group; recheck that invariant before signalling a group, as the stage
        # supervisor does, and only ever the process the identity verified.
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _default_pause(seconds):
    time.sleep(seconds)


def _alive(identity, *, live):
    """True/False for a verified live/dead process, None when its identity cannot be established."""
    try:
        return bool(live([identity]))
    except (processes.ProcessError, OSError, ValueError, KeyError, TypeError):
        return None


def _await_exit(identity, *, live, pause, grace) -> bool:
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if _alive(identity, live=live) is False:
            return True
        pause(0.05)
    return False


def stop_verified(record, *, live=None, kill=None, pause=None) -> dict:
    """Stop the provider a saved attempt owned, only once its recorded identity still verifies.

    ``live`` is the existing process-identity boundary (``autocode_process.live_processes``): the
    saved birth identity must match the current process, so a reused PID is a different process
    and is never signalled, and a record without an authenticated identity is reported rather
    than guessed at. Returns what happened, for the run's evidence.
    """
    live = _default_live if live is None else live
    kill = _default_kill if kill is None else kill
    pause = _default_pause if pause is None else pause
    identity = provider_identity(record)
    if identity is None:
        return {
            "settled": False,
            "signalled": False,
            "reason": "the saved attempt carries no authenticated process identity",
        }
    alive = _alive(identity, live=live)
    if alive is None:
        return {
            "settled": False,
            "signalled": False,
            "reason": "the owned process's identity could not be verified",
        }
    if not alive:
        return {"settled": True, "signalled": False, "reason": "the owned process already exited"}
    signalled = False
    for sig in (signal.SIGTERM, signal.SIGKILL):
        state_now = _alive(identity, live=live)
        if state_now is False:
            return {"settled": True, "signalled": signalled, "reason": "the owned process was stopped"}
        if state_now is None:
            return {
                "settled": False,
                "signalled": signalled,
                "reason": "the owned process's identity became unavailable while stopping it",
            }
        if kill(identity["pid"], sig):
            signalled = True
        if _await_exit(identity, live=live, pause=pause, grace=SIGNAL_GRACE_SECONDS):
            return {"settled": True, "signalled": signalled, "reason": "the owned process was stopped"}
    return {
        "settled": False,
        "signalled": signalled,
        "reason": "the owned process was signalled; its exit stayed unconfirmed",
    }


class RequirementsOverlapPass:
    """The one bounded background full Requirements call a chat blocking-question pause owns (#3233).

    Started inside ``chat_checkpoint`` when every blocking question offers a default and the
    handoff's answers start the full pass today, so the person's reading time overlaps the call.
    The candidate state is a frozen copy whose assumed defaults exist only as that call's saved
    answers; its state checkpoints go to a sidecar so the paused ``state.json`` stays
    authoritative. Not a second controller: one thread, one provider call, settled by the same
    controller when the person answers.
    """

    STOP_JOIN_SECONDS = 30

    def __init__(self, controller: Controller, state: RunState, run_dir, questions, save):
        self.controller = controller
        self.save = save
        self.run_dir = Path(run_dir)
        self.state_path = self.run_dir / "state.json"
        self.questions = copy.deepcopy(list(questions))
        self.assumed = {
            **answer_texts(state.get("answers") or {}),
            **assumed_defaults(self.questions),
        }
        self.digest = answers_digest(self.assumed)
        self.nonce = uuid.uuid4().hex
        self.workspace = Path(state["workspace"])
        self.candidate = {
            **copy.deepcopy(state),
            "status": "RUNNING",
            "phase": "DISCOVERING",
            "next_stage": "requirements_gather",
            "pending_questions": [],
            "answers": {key: {"text": text} for key, text in self.assumed.items()},
        }
        self.candidate.pop("active_stage", None)
        self.cancelled = threading.Event()
        self.settlement = None
        self.value = None
        self.record = None
        self.error = None
        self.outcome = None  # finished | failed | cancelled
        self.candidate["requirements_overlap"] = self.describe("running")
        self.thread = threading.Thread(target=self._run, name="requirements-overlap", daemon=True)

    def describe(self, status: str, reason: str = "") -> dict:
        return {
            "at": self.controller.now(),
            "status": status,
            "reason": reason,
            "questions": [row.get("id") for row in self.questions],
            "assumed_answers_digest": self.digest,
            "attempt_nonce": self.nonce,
            "speculative": True,
            **({"error": type(self.error).__name__ + ": " + str(self.error)} if self.error is not None else {}),
        }

    def _persist(self, path, value):
        """The candidate never owns the run's state: its checkpoints go to a sidecar."""
        if Path(path).name == "state.json" and Path(path).parent == self.run_dir:
            if isinstance(value.get("active_stage"), dict):
                value["active_stage"]["requirements_overlap_nonce"] = self.nonce
                if self.cancelled.is_set():
                    self.settlement = self.controller.stop_owned(value["active_stage"])
            return self.controller.support.atomic_json(self.run_dir / SIDECAR_NAME, value)
        return self.save(path, value)

    def _run(self):
        if self.cancelled.is_set():
            self.outcome = "cancelled"
            return
        try:
            try:
                from .units import common as units_common
            except ImportError:
                from units import common as units_common
            self.controller.guard(self.candidate, self.workspace, self.run_dir)
            request = self.controller.prepare_request(
                self.candidate, "requirements_gather", self.state_path, self.controller.schema_dir
            )
            schema_path = self.run_dir / "schemas" / "v3-requirements_gather.json"
            self._persist(schema_path, self.controller.support.model_output_schema(request.schema))
            if self.cancelled.is_set():
                self.outcome = "cancelled"
                return
            value, record = self.controller.run_role(
                role=request.role,
                prompt=request.prompt,
                sandbox=units_common.launch_sandbox("requirements_gather", request.allow_write),
                workspace=self.workspace,
                run_dir=self.run_dir,
                state=self.candidate,
                schema=schema_path,
                model=self.candidate["settings"]["roles"][request.route_role]["model"],
                allow_write=request.allow_write,
                dry_run=False,
                persist=self._persist,
            )
            record["requirements_overlap_nonce"] = self.nonce
            self.value, self.record = value, record
            self.outcome = "finished"
        except BaseException as error:  # settled by the controller, never raised into the chat
            self.error = error
            self.record = self.record or self.candidate.get("active_stage")
            self.outcome = "failed"

    def wait(self):
        """Wait for the call to finish; bounded by its own supervision, as an ordinary dispatch is."""
        self.thread.join()

    def stop(self):
        """Stop the background call; a call that already finished is simply not adopted."""
        self.cancelled.set()
        if self.outcome is None:
            record = self.record or self.candidate.get("active_stage") or {}
            self.settlement = self.controller.stop_owned(record)
        self.thread.join(timeout=self.STOP_JOIN_SECONDS)
        if self.outcome == "cancelled":
            self.settlement = {"settled": True, "reason": "cancelled before provider dispatch"}
        elif self.outcome == "finished":
            self.settlement = {"settled": True, "reason": "the supervised provider already finished"}
        if self.thread.is_alive():
            self.settlement = {"settled": False, "reason": "the background dispatch has not exited"}


class Controller:
    """The chat controller's wiring for the background pass (#3233).

    This module is a lower layer (AGENTS.md rule 2), so the launch-path callables that live in
    autocode.py or autopilot.py — the stage launch (``run_role``), request preparation, stage
    accounting, the result commit, record saving and the launch guard, plus the support helpers
    and the schema location they use — are injected here by ``chat_checkpoint`` and never
    imported. One bounded background call per eligible pause, owned by that controller. The
    ownership hooks (``owned_live``/``owned_kill``/``owned_pause``) default to the existing
    process-identity boundary and are injectable for deterministic tests.
    """

    def __init__(
        self,
        *,
        run_role,
        prepare_request,
        account_stage,
        commit_stage_result,
        save_record,
        guard,
        support,
        now,
        schema_dir,
        owned_live=None,
        owned_kill=None,
        owned_pause=None,
    ):
        self.run_role = run_role
        self.prepare_request = prepare_request
        self.account_stage = account_stage
        self.commit_stage_result = commit_stage_result
        self.save_record = save_record
        self.guard = guard
        self.support = support
        self.now = now
        self.schema_dir = schema_dir
        self.owned_live = owned_live
        self.owned_kill = owned_kill
        self.owned_pause = owned_pause

    @classmethod
    def wired(cls, runner, *, now=None, schema_dir=None) -> Controller:
        """Build the controller from the chat controller's own callables (autocode.chat_checkpoint).

        ``runner`` is the autocode module object itself (never imported here), so the injected
        callables are exactly the ones the ordinary stage path uses.
        """
        return cls(
            run_role=runner.run_role,
            prepare_request=runner.autopilot.prepare_request,
            account_stage=runner.account_stage,
            commit_stage_result=runner.commit_stage_result,
            save_record=runner.save_record,
            guard=runner.launch_inputs.guard,
            support=runner.support,
            now=now or runner.now,
            schema_dir=schema_dir or runner.SCHEMA_DIR,
        )

    @classmethod
    def resume(cls, state: RunState, run_dir, runner) -> dict | None:
        """Reconcile an owned unfinished background pass on the ordinary resume path (``recover``)."""
        result = recover(
            state,
            run_dir,
            save=runner.write_json,
            account_stage=runner.account_stage,
            save_record=runner.save_record,
            event_metrics=runner.support.event_metrics,
        )
        if result and result.get("cleanup_pending"):
            raise runner.support.Paused("PAUSED_PROCESS_CLEANUP", result["uncertainty"])
        return result

    def stop_owned(self, record) -> dict:
        """Stop the provider a background attempt owns, through the verified-identity boundary."""
        return stop_verified(
            record,
            live=self.owned_live,
            kill=self.owned_kill,
            pause=self.owned_pause,
        )

    def _charge(self, state: RunState, record, outcome: str, *, save: bool, reason: str = ""):
        """Account a background attempt exactly once, its annotation on the attempt itself (#3233).

        The speculative/outcome/reason binding lives on the authoritative stage record that
        carries the attempt's observed tokens and cost — the identity the run's stage history,
        resume reconciliation and the usage view already read — never on a second unbound
        attempt. The reason is the final keep/discard decision's own reason, so a refused
        adoption re-annotates the attempt it had first marked kept, without charging again.
        Usage the attempt's events cannot establish is left as an explicit uncertainty (no
        metrics invented), named in the outcome's reason.
        """
        if not isinstance(record, dict):
            return record
        record["speculative"] = True
        record["outcome"] = outcome
        record["reason"] = reason
        if not record.get("metrics") and record.get("events") and Path(record["events"]).is_file():
            with contextlib.suppress(OSError, ValueError, TypeError):
                record["metrics"] = self.support.event_metrics(record["events"])
        if not record.get("overlap_accounted"):
            # run_role may have charged the isolated candidate. Charge the parent once,
            # including when adoption rejects the report after this first accounting.
            record.pop("accounted", None)
            self.account_stage(state, record)
            record["overlap_accounted"] = True
        if save and not any(row is record for row in state.get("stages") or []):
            self.save_record(state, record)
        return record

    def record(self, state: RunState, entry: dict | None):
        """Move the current overlap record aside, keeping its own settled status and reason.

        A superseded entry is spend evidence of the pass it described: it keeps the status and
        reason it settled under, and a later pass's reason belongs to that later pass's entry
        alone.
        """
        current = state.get("requirements_overlap") or {}
        if current:
            state.setdefault("requirements_overlap_history", []).append(dict(current))
        if entry is not None:
            state["requirements_overlap"] = entry

    def start(self, state: RunState, run_dir, save):
        """Start the background full Requirements pass for an eligible chat blocking-question pause.

        ``None`` keeps today's behavior: an ineligible pause, a queued intervention, denied cap
        admission, or a run without the ordinary terminal chat (native visual runs own their
        launches; ``--no-chat``, durable and resumed-later runs never reach ``chat_checkpoint``).
        """
        questions = state.get("pending_questions") or []
        decision = eligible(state, questions)
        if not decision.get("eligible"):
            return None
        if interventions.pending(run_dir):
            return None  # queued input takes precedence; the ordinary flow applies it
        gate = admission(state)
        if not gate.get("admitted"):
            self.record(
                state,
                {
                    "at": self.now(),
                    "status": "skipped",
                    "reason": gate.get("reason"),
                    "questions": [row.get("id") for row in questions],
                    "speculative": True,
                },
            )
            save(Path(run_dir) / "state.json", state)
            return None
        self.record(state, None)
        background = RequirementsOverlapPass(self, state, run_dir, questions, save)
        state["requirements_overlap"] = {**background.describe("running"), "reason": decision["reason"]}
        save(Path(run_dir) / "state.json", state)
        background.thread.start()
        return background

    def settle(self, background: RequirementsOverlapPass, state: RunState, run_dir, save, *, literal=None):
        """Keep or discard the background pass once the person answered the pause's questions.

        The keep decision compares the exact typed replies (``literal``, captured at the chat
        input boundary before whitespace stripping or offered-choice canonicalization) with the
        offered defaults; a case-only or whitespace difference discards the report even when the
        answer validator saved the same canonical option, and ``/default`` selects the offered
        default exactly. The saved answers themselves stay canonical, and the ordinary
        replacement call receives those actual accepted answers.
        """
        given = literal if literal is not None else state.get("answers") or {}
        keep, reason = answers_match(background.questions, given)
        if keep:
            background.wait()
        else:
            background.stop()
        record = background.record
        kept = False
        if keep and background.value is not None and record is not None:
            self._charge(state, record, "kept", save=False, reason=reason)  # the commit below saves the attempt
            state["requirements_overlap"] = {
                **state.get("requirements_overlap", {}),
                "status": "adopting",
                "record_output": record.get("output"),
            }
            try:
                self.commit_stage_result(
                    state, "requirements_gather", background.value, record, Path(state["workspace"]), run_dir
                )
                kept = True
            except (ValueError, KeyError, self.support.Paused) as error:
                reason = f"the background report was refused: {error}"
        if not kept:
            if keep and background.value is None:
                reason = "the background call produced no report; the ordinary pass runs with the real answers"
            self._charge(state, record, "discarded", save=record is not None, reason=reason)
            state.update({"next_stage": "requirements_gather", "phase": "DISCOVERING"})
        entry = background.describe("kept" if kept else "discarded", reason)
        if background.settlement is not None and not background.settlement.get("settled"):
            entry["uncertainty"] = background.settlement.get("reason")
        self.record(state, entry)
        save(Path(run_dir) / "state.json", state)
        if entry.get("uncertainty"):
            raise self.support.Paused("PAUSED_PROCESS_CLEANUP", entry["uncertainty"])

    def cancel(self, background: RequirementsOverlapPass, state: RunState, run_dir, save, *, reason: str = ""):
        """Stop a background pass the chat left unsettled and account it once (#3233 item 5).

        Stop and a boundary pause taken while the person has not answered, EOF, Ctrl-C,
        ``/pause``, feedback and every other unsettled exit take this path: the owned call is
        stopped through the verified-identity boundary, its observed spend is accounted exactly
        once as speculative-discarded, the discarded outcome is persisted, and a queued
        intervention stays for the existing controller intervention consumer — before any answer
        or result can be adopted.
        """
        reason = reason or "the chat stopped or paused before the answers were taken"
        background.stop()
        self._charge(state, background.record, "discarded", save=background.record is not None, reason=reason)
        entry = background.describe("discarded", reason)
        if background.settlement is not None and not background.settlement.get("settled"):
            entry["uncertainty"] = background.settlement.get("reason")
        self.record(state, entry)
        if run_dir is not None:
            save(Path(run_dir) / "state.json", state)
        if entry.get("uncertainty"):
            raise self.support.Paused("PAUSED_PROCESS_CLEANUP", entry["uncertainty"])

    def abandon(self, background: RequirementsOverlapPass, state: RunState, run_dir=None, save=None):
        """The unsettled-exit path ``chat_checkpoint``'s finally block takes."""
        self.cancel(background, state, run_dir, save)


def recover(
    state: RunState,
    run_dir,
    *,
    save,
    account_stage,
    save_record,
    event_metrics,
    settle_owned=None,
    now=None,
) -> dict | None:
    """Reconcile an owned unfinished background pass when the run is loaded again (#3233 item 5).

    Runs in the ordinary run-loading/reconciliation path, before the early return for a missing
    main ``active_stage``, on every resume — chat or not. A persisted ``running`` overlap record
    means the last controller never settled its background call: that report is never adopted or
    replayed. The saved sidecar is authenticated against this run (its task and workspace) and
    the attempt against its recorded process identity, which must still verify before anything is
    signalled; observed spend is recovered exactly once, the discarded outcome persisted, and a
    repeated resume no-ops. Authentication or usage that cannot be established stays an explicit
    recorded uncertainty — never invented zero spend, never an adopted report.
    """
    current = state.get("requirements_overlap") or {}
    if current.get("status") != "running" and not current.get("cleanup_pending"):
        return None
    run_dir = Path(run_dir)
    reason = "the run resumed before the background pass finished; its unfinished report is never adopted"
    entry = {**current, "status": "discarded", "at": (now or util.now)(), "reason": reason}
    if current.get("cleanup_pending"):
        state.setdefault("requirements_overlap_history", []).append(dict(current))
    entry.pop("cleanup_pending", None)
    entry.pop("uncertainty", None)
    attempt = None
    sidecar = run_dir / SIDECAR_NAME
    try:
        saved = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
    except (OSError, ValueError):
        saved = {}
    if (
        isinstance(saved, dict)
        and saved.get("task_id") == state.get("task_id")
        and saved.get("workspace") == state.get("workspace")
        and saved.get("run_dir") == state.get("run_dir")
        and current.get("attempt_nonce")
        and (saved.get("requirements_overlap") or {}).get("attempt_nonce") == current["attempt_nonce"]
        and isinstance(saved.get("active_stage"), dict)
        and saved["active_stage"].get("stage") == "requirements_gather"
        and saved["active_stage"].get("requirements_overlap_nonce") == current["attempt_nonce"]
    ):
        attempt = saved["active_stage"]
    else:
        entry["uncertainty"] = "no attempt authenticated to this run was found; the pass's spend is not observed"
        entry["cleanup_pending"] = True
    if attempt is not None:
        owned = (settle_owned or stop_verified)(attempt)
        entry["process"] = owned
        if not owned.get("settled"):
            entry["uncertainty"] = owned.get("reason")
            entry["cleanup_pending"] = True
        attempt["speculative"] = True
        attempt["outcome"] = "discarded"
        attempt["reason"] = reason
        if not attempt.get("metrics"):
            if attempt.get("events") and Path(attempt["events"]).is_file():
                with contextlib.suppress(OSError, ValueError, TypeError):
                    attempt["metrics"] = event_metrics(attempt["events"])
            if not attempt.get("metrics"):
                # Neither saved metrics nor authenticated events establish the attempt's spend:
                # keep that gap explicit on the entry and the attempt — never invented zero
                # spend presented as completely accounted.
                entry.setdefault(
                    "uncertainty", "the attempt's observed usage could not be read; its spend is not observed"
                )
                attempt["uncertainty"] = entry["uncertainty"]
        previous = next(
            (
                row
                for row in state.get("stages") or []
                if row.get("requirements_overlap_nonce") == current["attempt_nonce"]
            ),
            None,
        )
        if previous is None:
            attempt.pop("accounted", None)
            account_stage(state, attempt)
            attempt["overlap_accounted"] = True
            save_record(state, attempt)
    state["requirements_overlap"] = entry
    save(run_dir / "state.json", state)
    return entry
