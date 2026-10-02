"""Clarification and rejected-assumption obligation policy for planning reports.

Extracted from ``autopilot`` unchanged: only a technical fact, or one with no
policy weight, may be read from the workspace; a rejected assumption becomes a
runner-owned obligation that agent output alone cannot discharge. Orchestration
stays in Autopilot, which passes the workspace citation checker and the
planning unit's investigation stages as arguments so this module depends only
on the contract module and shared utilities.
"""
from __future__ import annotations

import copy
from pathlib import Path

try:
    from . import autocode_goals as goals, autocode_util as util
except ImportError:
    import autocode_goals as goals
    import autocode_util as util

# Only a technical fact, or one with no policy weight, can be read from the
# workspace. Cost, quota, permission, side-effect and requested-outcome choices
# (and any question without a category) always stay with the user.
RESOLVABLE_CATEGORIES = frozenset({"technical", "other"})


def stage_questions(stage, value):
    return value["open_questions"] if stage == "requirements_gather" else value["contract"]["open_blocking_questions"]


def check_resolution_refs(state, refs, check_code_refs):
    """Strict: unlike the code-ref checker, an absent or empty workspace is not a pass."""
    root = Path(state.get("workspace") or "")
    if not state.get("workspace") or not root.is_dir():
        raise ValueError("A machine resolution needs an inspectable workspace")
    if not refs:
        raise ValueError("A machine resolution must cite the workspace source it was read from")
    for ref in refs:
        target = (root / str(ref).partition(":")[0]).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise ValueError(f"machine_resolutions source_refs entry {ref} is not a file in the workspace")
    check_code_refs(state, refs, "machine_resolutions.source_refs")


def clarify_discoverable(state, stage, value, record, *, investigation_stages, check_code_refs):
    """Keep discoverable questions away from the user (issue #62, rules E1-E3).

    The first report in a clarification episode that asks a discoverable
    question is not installed: the runner spends the episode's single
    investigation pass by re-running the same stage with an explicit packet
    (the questions, the report that raised them, and its hash). That pass must
    resolve each question from cited workspace source, reclassify it as a
    decision, or record an access blocker. Whatever is still discoverable after
    the pass, or once the budget is spent, becomes a decision question the
    runner labels as such; there is no second pass and no fabricated answer.

    Returns (value, deferred); deferred means the pass was queued.
    """
    if stage == "astra_finalize":
        # The final reviewer stage gets no investigation pass; it still cannot
        # show the user a question labelled as a workspace fact.
        value = copy.deepcopy(value)
        label_unresolved(state, value["contract"]["open_blocking_questions"])
        return value, False
    if stage not in investigation_stages:
        return value, False
    request = state.get("investigation_request")
    if request and request.get("stage") != stage:
        request = None
    raised_hash = util.digest(value)
    value = copy.deepcopy(value)
    questions = stage_questions(stage, value)
    by_id = {question["id"]: question for question in questions}
    resolutions = value.get("machine_resolutions") or []
    if resolutions and not request:
        raise ValueError("machine_resolutions are accepted only in the investigation pass for outstanding "
                         "discoverable questions")
    accepted, resolved = [], set()
    for row in resolutions:
        question_id = row["question_id"]
        original = next((q for q in request["questions"] if q["id"] == question_id), None)
        if question_id in resolved:
            raise ValueError(f"Duplicate machine_resolution for {question_id}")
        resolved.add(question_id)
        if original is None:
            raise ValueError(f"machine_resolution {question_id} does not name an outstanding discoverable question")
        if question_id in state.get("answers", {}):
            raise ValueError(f"Question {question_id} already has a saved user answer")
        category = original.get("category", "requested_outcome")
        if category not in RESOLVABLE_CATEGORIES:
            raise ValueError(f"Question {question_id} is a {category} choice; only a technical fact can be "
                             "resolved from the workspace")
        if row["handoff_hash"] != request["handoff_hash"]:
            raise ValueError(f"machine_resolution {question_id} is bound to a different report")
        if not row["resolution"].strip():
            raise ValueError(f"machine_resolution {question_id} needs the resolved fact")
        check_resolution_refs(state, row["source_refs"], check_code_refs)
        if question_id in by_id:
            raise ValueError(f"Question {question_id} was resolved from the workspace and must not also be asked")
        accepted.append({**copy.deepcopy(row), "stage": stage, "episode_id": request["episode_id"],
                         "requirements_handoff": goals.handoff_ref(state), "at": util.now()})
    for row in value.get("access_blockers") or []:
        question = by_id.get(row["question_id"])
        if not row["reason"].strip() or question is None or question.get("kind", "decision") != "decision":
            raise ValueError("An access_blocker must name a question that stays open as kind=decision, with a reason")
    if request:
        # Every question the held-back report asked must survive the pass, not
        # just the discoverable ones: the original report was never installed,
        # so no later gate could recover a decision dropped here.
        asked = set(request.get("all_question_ids") or request["question_ids"])
        dropped = asked - resolved - set(by_id) - set(state.get("answers", {}))
        if dropped:
            raise ValueError("Investigation dropped questions without a machine_resolution: "
                             + ", ".join(sorted(dropped)))
        state.setdefault("machine_resolutions", []).extend(accepted)
        state.pop("investigation_request")
    else:
        discoverable = [question for question in questions if question.get("kind") == "discoverable"]
        episode = goals.clarification_episode(state) if discoverable else None
        if episode and not episode["investigation_used"]:
            episode.update(investigation_used=True, used_at=util.now(), used_stage=stage)
            state["investigation_request"] = {
                "stage": stage, "episode_id": episode["id"], "handoff_hash": raised_hash,
                "question_ids": [question["id"] for question in discoverable],
                "all_question_ids": [question["id"] for question in questions],
                "questions": copy.deepcopy(discoverable), "prior_output": record.get("output"),
                "prior_report": copy.deepcopy(value), "created_at": util.now()}
            state.update(status="RUNNING", phase="PLANNING", next_stage=stage)
            return value, True
    label_unresolved(state, questions)
    return value, False


def check_handoff_questions(state, value):
    """A Planner draft keeps every question the requirements handoff left open until it is answered or settled.
    A fact settled from the workspace stays settled while the handoff that asked it is unchanged; answering
    another question renews the episode but not the handoff. A refreshed handoff must settle it again."""
    handoff = state.get("requirements_handoff")
    if not handoff:
        return
    pending = {question["id"] for question in handoff["report"]["open_questions"]}
    preserved = {question["id"] for question in value["contract"]["open_blocking_questions"]}
    current = goals.handoff_ref(state)
    resolved = {row["question_id"] for row in state.get("machine_resolutions", [])
                if row.get("requirements_handoff") == current}
    missing = pending - preserved - set(state.get("answers", {})) - resolved
    if missing:
        raise ValueError("Planner dropped unresolved requirements questions: " + ", ".join(sorted(missing)))


def label_unresolved(state, questions):
    """Runner-authored, visible relabelling; never a fabricated answer."""
    for index, question in enumerate(questions):
        if question.get("kind") == "discoverable":
            questions[index] = {**question, "kind": "decision", "why": (
                "Not determinable from the workspace within this clarification episode's single "
                "investigation pass. " + question["why"])}
            goals.clarification_episode(state).setdefault("converted_to_decision", []).append(question["id"])


def surface_obligations(obligations, questions, where):
    """An unresolved obligation returns to the user as a decision question under
    its own id. The three-question cap limits presentation per round, not the
    obligation: with the cap full it waits for the next round."""
    ids = {ob["id"] for ob in obligations}
    for question in questions:
        if question["id"] in ids and question.get("kind", "decision") != "decision":
            raise ValueError(f"Obligation {question['id']} must be asked as a kind=decision question")
    missing = sorted(ids - {question["id"] for question in questions})
    if missing and len(questions) < 3:
        raise ValueError(f"{where}: unresolved obligations must return to the user as decision questions "
                         "under their own ids: " + ", ".join(missing))


def apply_obligations(state, stage, value, *, check_code_refs):
    """Rule E8 (issue #62): rejected assumptions become runner-owned obligations.

    A remediation obligation is discharged only by a Plan Reviewer decision on
    astra_challenge or astra_finalize, bound to the hash of the exact remediation
    record it judged; a repaired record resets it to pending review. A human
    decision obligation, or any obligation still open at final review, goes back
    to the user as a question under its own id. Agent output alone never
    discharges an obligation. At finalize, the report's own decisions are applied
    before the gate is evaluated.
    """
    obligations = {ob["id"]: ob for ob in goals.open_obligations(state)}
    if stage == "requirements_gather":
        # Resolving how to proceed without an assumption is not permission to
        # restore it; there is no reinstatement path, so history always applies.
        rejected = {ob.get("assumption_id") for ob in state.get("deferred_obligations", [])}
        reused = sorted(rejected & {row.get("id") for row in value.get("proposed_assumptions") or []
                                    if isinstance(row, dict)})
        if reused:
            raise ValueError("A rejected assumption cannot reappear: " + ", ".join(reused))
        return
    if stage in ("astra_discovery", "glm_revise"):
        trace = {row.get("requirement_id"): row.get("disposition") for row in value.get("requirement_trace") or []}
        episode_id = goals.clarification_episode(state)["id"]
        seen = set()
        for record in value.get("remediation_records") or []:
            obligation_id = record["obligation_id"]
            obligation = obligations.get(obligation_id)
            if obligation_id in seen:
                raise ValueError(f"Duplicate remediation record for {obligation_id}")
            seen.add(obligation_id)
            if obligation is None or obligation["kind"] != "remediation":
                raise ValueError(f"remediation_records {obligation_id} does not name an open remediation obligation")
            if record["assumption_id"] != obligation["assumption_id"]:
                raise ValueError(f"remediation_records {obligation_id} names a different assumption")
            if not record["approach"].strip() or not record["evidence_refs"]:
                raise ValueError(f"remediation_records {obligation_id} needs an approach and evidence")
            check_code_refs(state, record["evidence_refs"], "remediation_records.evidence_refs")
            covered = record["covered_requirements"]
            if len(covered) != len(set(covered)) or set(covered) != set(obligation.get("supports") or []):
                raise ValueError(f"remediation_records {obligation_id} must cover exactly the requirements "
                                 "the rejected assumption supported")
            uncovered = sorted(rid for rid in covered if trace.get(rid) != "covered")
            if uncovered:
                raise ValueError(f"remediation_records {obligation_id} claims requirements the trace does not "
                                 "cover: " + ", ".join(uncovered))
            if record["episode_id"] != episode_id:
                raise ValueError(f"remediation_records {obligation_id} is bound to a previous clarification episode")
            obligation.update(remediation=copy.deepcopy(record), remediation_hash=util.digest(record),
                              status="pending_review")
        if stage == "astra_discovery":
            human = [ob for ob in obligations.values() if ob["kind"] == "human_decision"]
            if human:
                contract = value["contract"]
                if (contract.get("milestones") or contract.get("technical_approach")
                        or contract.get("initial_task", {}).get("kind") in ("implement", "validate")):
                    raise ValueError("A rejected assumption with policy weight needs the user's decision first; "
                                     "return a clarification-only draft")
                surface_obligations(human, contract["open_blocking_questions"], "Discovery")
        return
    if stage not in ("astra_challenge", "astra_finalize"):
        return
    report_hash = util.digest(value)
    pending = {oid: ob for oid, ob in obligations.items() if ob["status"] == "pending_review"}
    decided = set()
    for decision in value.get("obligation_decisions") or []:
        obligation_id = decision["obligation_id"]
        obligation = pending.get(obligation_id)
        if obligation_id in decided:
            raise ValueError(f"Duplicate obligation decision for {obligation_id}")
        decided.add(obligation_id)
        if obligation is None:
            raise ValueError(f"obligation_decisions {obligation_id} does not name a remediation awaiting review")
        if decision["remediation_hash"] != obligation["remediation_hash"]:
            raise ValueError(f"Decision for {obligation_id} refers to a superseded remediation")
        if (obligation.get("remediation") or {}).get("episode_id") != goals.clarification_episode(state)["id"]:
            raise ValueError(f"Remediation {obligation_id} was proposed in a previous clarification episode; "
                             "it must be proposed again")
        if decision["resolved"]:
            if not decision["rationale"].strip() or not decision["evidence_refs"]:
                raise ValueError(f"Accepting remediation {obligation_id} needs a rationale and evidence")
            obligation.update(status="resolved", resolved_by=f"{stage}:{report_hash}:{obligation_id}",
                              judged_hash=obligation["remediation_hash"], resolved_at=util.now())
        else:
            if stage == "astra_challenge" and not any(
                    concern.get("blocking") and obligation_id in (concern.get("evidence_refs") or [])
                    for concern in value["concerns"]):
                raise ValueError(f"Rejecting remediation {obligation_id} needs a blocking concern citing it")
            obligation["status"] = "open"
    missing = sorted(set(pending) - decided)
    if missing:
        raise ValueError("Every remediation awaiting review needs one obligation_decisions entry: "
                         + ", ".join(missing))
    if stage == "astra_finalize":
        remaining = goals.open_obligations(state)
        if remaining:
            contract = value["contract"]
            if contract.get("initial_task", {}).get("kind") in ("implement", "validate"):
                raise ValueError("Unresolved obligations block an executable initial_task")
            surface_obligations(remaining, contract["open_blocking_questions"], "Final review")
