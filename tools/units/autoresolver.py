"""Read-only diagnosis and bounded repair planning; never writes application code.

Owns three stages: ``resolve`` (why did a reviewed build fail, and what bounded
rework fixes it), ``diagnose`` (an operational diagnosis that can only recommend a
retry or escalation) and ``investigate_stuck`` (autocode_stuck_job: why a stage stopped
converging, before the run pauses)."""

from __future__ import annotations

try:
    from .. import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ..autocode_run_state import RunState

try:
    from .. import autocode_counterexample as counterexample
    from .. import autocode_failures as failures
    from .. import autocode_goal_lifecycle as lifecycle
    from .. import autocode_goals as goals
    from .. import autocode_launch_inputs as launch_inputs
    from .. import autocode_providers
    from .. import autocode_quota_route as quota_route
    from .. import autocode_recovery_novelty as novelty
    from .. import autocode_resolver_human as human
    from .. import autocode_resolver_recommendation as recommendation
    from .. import autocode_resolver_recovery as resolver_recovery
    from .. import autocode_role_transport as role_transport
    from .. import autocode_route_permission as route_permission
    from .. import autocode_source_scope as source_scope
    from .. import autocode_stuck_job as stuck_job
    from .. import autocode_util as util
    from .. import autocode_verification_plan as verification_plan
    from .. import autocode_verify as verify
except ImportError:
    import autocode_counterexample as counterexample
    import autocode_failures as failures
    import autocode_goal_lifecycle as lifecycle
    import autocode_goals as goals
    import autocode_launch_inputs as launch_inputs
    import autocode_providers
    import autocode_quota_route as quota_route
    import autocode_recovery_novelty as novelty
    import autocode_resolver_human as human
    import autocode_resolver_recommendation as recommendation
    import autocode_resolver_recovery as resolver_recovery
    import autocode_role_transport as role_transport
    import autocode_route_permission as route_permission
    import autocode_source_scope as source_scope
    import autocode_stuck_job as stuck_job
    import autocode_util as util
    import autocode_verification_plan as verification_plan
    import autocode_verify as verify
import contextlib

from . import autoplanner
from .common import ModelRequest, execution_request

PROBE_TIMEOUT = 120  # seconds per probe; a probe checks one claim, it is not a test suite


def apply_job(stage, state: RunState, value, record, workspace, *, run_dir=None):
    """Autopilot hands a job stage's validated report here: the Investigator's diagnosis of a stuck stage."""
    if stage != stuck_job.STAGE:
        raise ValueError(f"Autoresolver cannot apply {stage}")
    clean_run, _ = launch_inputs.runners(
        state, workspace, run_dir or Path(record.get("output") or workspace).parent.parent, verify.scratch_run
    )
    # The runner shows the diagnosed cause: the probe runs in a scratch tree holding only the cited files,
    # and again with its counterexample made, where it must fail (#1769).
    probes = Path(record.get("output") or workspace).parent / "investigation-probe"

    def contained(workspace, directory, *, command, timeout, **inputs):
        if (state.get("stuck_investigation") or {}).get("mode") == "builder_failure":
            try:
                from .. import autocode_builder_failure as builder_failure
            except ImportError:
                import autocode_builder_failure as builder_failure
            command = builder_failure.readonly_probe(command)
        return clean_run(workspace, directory, command=command, timeout=timeout, **inputs)

    def probe(command, files):
        return contained(workspace, probes, command=command, files=files, timeout=PROBE_TIMEOUT)

    def against(command, edit, files):
        return counterexample.runner(contained, workspace, probes, timeout=PROBE_TIMEOUT, files=files)(command, edit)

    return stuck_job.apply(state, value, record, workspace, run_probe=probe, run_counterexample=against)


def wait_on_existing_diagnosis(state: RunState, decision, record, request, *, source_stage):
    """The Completion Owner blocks again on a task and source the AutoResolver already diagnosed.

    One diagnosis per task and source stays the rule. Refusing the second one by raising made the
    runner treat a valid review as invalid output: report repair could never fix it, and a run whose
    code was done could not move (live greenfield-greeting-cli and port-policy-go, 2026-09-29, after
    the user had answered the diagnosis's question and the source was unchanged). The review is
    accepted instead, and the run waits for the user with the existing diagnosis attached: the
    source is unchanged, so only a person, a changed setting or new code can move it. A pending
    request is retained through its own publication boundary instead of rejecting this review
    or creating another question. An answered request does not authorize another diagnosis.
    """
    if human.restage_pending(state):
        return
    diagnosis = ""
    output = request["diagnosis_output"]
    with contextlib.suppress(OSError, ValueError, AttributeError):
        diagnosis = str(json.loads(Path(output).read_text()).get("diagnosis") or "")
    asked = decision.get("user_request") or {}
    if (
        asked.get("kind", "none") == "none"
        or not str(asked.get("decision_needed", "")).strip()
        or not str(asked.get("impact", "")).strip()
    ):
        asked = {
            "kind": "blocker",
            "discovered": "The Completion Owner still does not accept this source after "
            "the AutoResolver's diagnosis, and the source has not changed.",
            "impact": "Another automatic round would review the same source and reach the same result.",
            "decision_needed": "How should the blocker in the existing diagnosis be resolved?",
            "options": [
                "Change the setting or source the diagnosis names, then resume",
                "Give feedback that changes the plan",
                "Leave the run paused",
            ],
            "proposed_delta": "",
        }
    evidence = {
        "diagnosis": diagnosis,
        "output": output,
        "repeated_on_unchanged_source": True,
        "hashes": dict(request.get("evidence_hashes") or {}),
    }
    if Path(output).is_file():
        evidence["hashes"][output] = util.file_hash(Path(output))
    origin = {
        "stage": source_stage,
        **{key: record[key] for key in ("output", "source_revision", "task_id") if key in record},
    }
    lifecycle.wait_for_user(state, asked, origin=origin, evidence=evidence, next_stage="review_completion")


def guard(state: RunState, workspace):
    goals.execution_guard(state)
    request = state.get("resolution_request") or {}
    output = request.get("source_output", request.get("review_output"))
    if (
        not cast(dict, state.get("current_task", {})).get("id")
        or request.get("contract_hash") != cast(dict, state["goal_contract"])["hash"]
        or request.get("task_id") != cast(dict, state.get("current_task", {})).get("id")
        or not output
        or output not in request.get("evidence_hashes", {})
        or request.get("source_revision") != source_scope.snapshot(workspace, state)["revision"]
    ):
        raise util.Paused("PAUSED_STALE_HANDOFF", "Repair diagnosis needs the current reviewed source and task")
    if request.get("diagnosis_output"):
        raise util.Paused(
            "PAUSED_RESOLVER", "The saved blocker already has a diagnosis; reconcile it before another resolver call"
        )
    for path, digest in request.get("evidence_hashes", {}).items():
        if not Path(path).is_file() or util.file_hash(path) != digest:
            raise util.Paused("PAUSED_STALE_HANDOFF", "Repair evidence changed; review again before resolving")


def prepare_stuck(state: RunState, state_path):
    """A fresh route and session every time, on a model different from the stuck stage's
    (stuck_job.route, or the pinned --investigator-model), so it does not inherit its reasoning."""
    if state["stuck_investigation"].get("mode") == "builder_failure":
        try:
            from .. import autocode_builder_failure as builder_failure
        except ImportError:
            import autocode_builder_failure as builder_failure
        builder_failure.guard(state, state["stuck_investigation"], state["workspace"])
    roles = state["settings"]["roles"]
    stuck_route = autoplanner.route_for(state, state["stuck_investigation"]["stage"])
    source = "plan_reviewer" if "plan_reviewer" in roles else "task_planner"
    selected = stuck_job.pinned_route(state["settings"])
    stuck_model = (roles.get(stuck_route) or {}).get("model", "")
    if selected is None and state["settings"].get("route_selection"):
        permitted = route_permission.investigator(state["settings"], stuck_model)
        if permitted is None:
            raise util.Paused(
                "PAUSED_INVALID_OUTPUT",
                "No independent permitted Investigator route remains; choose an Investigator model explicitly",
            )
        source, selected = permitted
    roles[stuck_job.ROUTE] = selected or stuck_job.route(roles, stuck_model, state["settings"].get("provider"))
    role_transport.inherit(state.get("settings"), stuck_job.ROUTE, source)
    state.setdefault("sessions", {}).pop(stuck_job.ROUTE, None)
    identities = state["settings"].setdefault("transport_identities", {})
    if roles[stuck_job.ROUTE].get("engine") == "opencode" and "opencode" not in identities:
        # A Codex run's first OpenCode stage (a pinned --investigator-model): record the transport
        # the way run creation does, so the per-stage billing and drift checks cover it.
        tool = autocode_providers.resolve(state["settings"].get("provider") or "opencode")
        identities["opencode"] = tool.local_settings(Path(state["workspace"]))
    prompt, metrics = stuck_job.prompt(
        state,
        state_path,
        autoplanner.workspace_inventory(state["workspace"], state["task"]),
        state["settings"].get("context_soft_tokens", 10000),
        autoplanner.engine_for(state["settings"], stuck_job.ROUTE),
    )
    return ModelRequest("task_planner", stuck_job.ROUTE, prompt, metrics, stuck_job.schema(state), False)


def prepare(state: RunState, stage, state_path, schema_dir, *, retry_authorization=None):
    if stage == stuck_job.STAGE:
        return prepare_stuck(state, state_path)
    if stage == "diagnose":
        return prepare_diagnosis(state, stage, state_path, schema_dir)
    if stage != "resolve":
        raise ValueError(f"Autoresolver cannot run {stage}")
    guard(state, Path(state["workspace"]))
    repeated = failures.repeated(
        state, {"stage": stage, "source_revision": cast(dict, state["resolution_request"])["source_revision"]}
    )
    if repeated and not failures.admitted_retry(repeated, retry_authorization):
        raise util.Paused(
            "PAUSED_REPEATED_FAILURE",
            "Resolver failure limit reached for this source; no additional diagnosis authorized",
        )
    # Inherit the planner model, not its conversation. A dedicated route keeps
    # resolver sessions separate from planning, completion and implementation.
    roles = state["settings"]["roles"]
    if "resolver" in roles:
        quota_route.sync_derived_role(state, "resolver")
    else:
        roles["resolver"] = copy.deepcopy(roles["task_planner"])
    role_transport.inherit(state.get("settings"), "resolver", "task_planner")
    request = execution_request(state, "review_completion", state_path, schema_dir, report_stage=stage)
    instruction, payload = request.prompt.split("CURRENT HANDOFF DATA\n", 1)
    data = json.loads(payload)
    data.update(
        stage=stage,
        resolution_request=state["resolution_request"],
        execution_engine=state["settings"]["roles"]["resolver"].get("engine", state["settings"].get("engine", "codex")),
    )
    schema = copy.deepcopy(request.schema)
    schema["properties"]["status"]["enum"] = ["REWORK", "BLOCKED"]
    schema["properties"]["diagnosis"] = goals.STRING
    schema["properties"]["recovery_change"] = copy.deepcopy(novelty.CHANGE_SCHEMA)
    schema["properties"]["user_request"]["properties"]["recommended_revision"] = copy.deepcopy(recommendation.SCHEMA)
    schema["properties"]["user_request"]["properties"]["correction"] = copy.deepcopy(CORRECTION_SCHEMA)
    schema["required"].append("diagnosis")
    prompt = (
        prompts.get("fragments/autoresolver/prepare-03.md")
        + verification_plan.EXPECTED_FAILURE_RULE
        + " "
        + verification_plan.GIT_STATUS_RULE
        + " "
        + verification_plan.SHELL_SYNTAX_RULE
        + prompts.get("fragments/autoresolver/prepare-02.md")
        + novelty.INSTRUCTION
        + instruction
        + prompts.get("fragments/autoresolver/prepare.md")
        + "CURRENT HANDOFF DATA\n"
        + json.dumps(data, indent=2)
    )
    metrics = {**request.metrics, "estimated_prompt_tokens": (len(prompt) + 3) // 4}
    return ModelRequest("task_planner", "resolver", prompt, metrics, schema, False)


# The one correction a Resolver blocker may name: the saved test or regression command is wrong.
# It only marks the blocker; the Resolver never supplies the commands. The status view then names
# the existing configure_resume action, where the operator sets them (#2973). Null is listed first: a
# scripted provider that fills a missing field with its first enum value means "no correction".
CORRECTIONS = ("verification_commands",)
CORRECTION_SCHEMA = {"type": ["string", "null"], "enum": [None, *CORRECTIONS]}


def validate(state: RunState, value, record, workspace):
    guard(state, workspace)
    if (
        record.get("changed_files")
        or record["source_revision"] != cast(dict, state["resolution_request"])["source_revision"]
    ):
        raise util.Paused("PAUSED_STALE_HANDOFF", "Resolver must leave the reviewed source unchanged")
    if record.get("rejected") or record.get("exit_code", 0) != 0 or not Path(record.get("output") or "").is_file():
        raise util.Paused("PAUSED_STALE_HANDOFF", "Resolver diagnosis requires its saved successful read-only output")
    if value.get("status") not in ("REWORK", "BLOCKED") or not value.get("diagnosis", "").strip():
        raise ValueError("Resolver requires a diagnosis and a REWORK or BLOCKED decision")
    request = value.get("user_request") or {}
    if request.get("correction") is not None and (
        value["status"] != "BLOCKED"
        or request.get("kind") != "blocker"
        or request.get("correction") not in CORRECTIONS
        or request.get("recommended_revision") is not None
    ):
        raise ValueError("A verification-command correction requires a diagnosed blocker and no criterion revision")
    if request.get("recommended_revision") is not None:
        if value["status"] != "BLOCKED" or request.get("kind") not in ("blocker", "contradiction", "infeasible"):
            raise ValueError("A recommended criterion revision requires a diagnosed contract blocker")
        recommendation.compose(cast(dict, state["goal_contract"])["body"], request["recommended_revision"])
    if value["status"] == "REWORK" and (
        not value.get("evidence") or value.get("next_task", {}).get("kind") not in ("implement", "validate")
    ):
        raise ValueError("Resolver must supply an evidence-backed implementation or validation repair")
    resolver_recovery.validate_decision(state, value, record)


# Operational diagnosis: a genuinely separate stage and schema from resolve.
# It is not bound to a reviewer REWORK verdict, never touches acceptance_criteria,
# and its output can only recommend "retry" (with guidance) or "escalate" -- never
# an implementation task, a contract change, or a completion claim. The runner
# revalidates the recommendation against the same bounded policy used to admit
# the diagnosis before any retry is authorized (see autocode_resolver_runtime).
DIAGNOSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["diagnosis", "recommendation"],
    "properties": {
        "diagnosis": goals.STRING,
        "recovery_change": copy.deepcopy(novelty.CHANGE_SCHEMA),
        "recommendation": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "rationale"],
            "properties": {
                "action": {"type": "string", "enum": ["retry", "escalate"]},
                "rationale": goals.STRING,
                "guidance": goals.STRING,
                "evidence_refs": {"type": "array", "items": goals.STRING},
            },
        },
    },
}


def diagnosis_guard(state: RunState, workspace):
    goals.execution_guard(state)
    request = state.get("diagnosis_request") or {}
    if (
        request.get("contract_hash") != cast(dict, state["goal_contract"])["hash"]
        or request.get("source_revision") != source_scope.snapshot(workspace, state)["revision"]
    ):
        raise util.Paused("PAUSED_STALE_HANDOFF", "Diagnosis needs the current source and approved contract")
    for path, digest in request.get("evidence_hashes", {}).items():
        if not Path(path).is_file() or util.file_hash(path) != digest:
            raise util.Paused("PAUSED_STALE_HANDOFF", "Diagnosis evidence changed; reconcile before diagnosing")


def prepare_diagnosis(state: RunState, stage, state_path, schema_dir):
    if stage != "diagnose":
        raise ValueError(f"Autoresolver cannot run {stage}")
    diagnosis_guard(state, Path(state["workspace"]))
    roles = state["settings"]["roles"]
    if "resolver" in roles:
        quota_route.sync_derived_role(state, "resolver")
    else:
        roles["resolver"] = copy.deepcopy(roles["task_planner"])
    role_transport.inherit(state.get("settings"), "resolver", "task_planner")
    # Reuse the Plan Reviewer's handoff context (current source, task, evidence
    # inventory) for input only; the output schema below is unrelated to and far
    # narrower than the reviewer decision schema that context call would imply.
    request = execution_request(
        state, "review_completion", state_path, schema_dir, report_stage=stage, schema_override=DIAGNOSIS_SCHEMA
    )
    instruction, payload = request.prompt.split("CURRENT HANDOFF DATA\n", 1)
    data = json.loads(payload)
    data.update(
        stage=stage,
        diagnosis_request=state["diagnosis_request"],
        execution_engine=state["settings"]["roles"]["resolver"].get("engine", state["settings"].get("engine", "codex")),
    )
    prompt = (
        prompts.get("fragments/autoresolver/prepare-diagnosis-02.md")
        + novelty.INSTRUCTION
        + instruction
        + prompts.get("fragments/autoresolver/prepare-diagnosis.md")
        + "CURRENT HANDOFF DATA\n"
        + json.dumps(data, indent=2)
    )
    metrics = {**request.metrics, "estimated_prompt_tokens": (len(prompt) + 3) // 4}
    return ModelRequest("task_planner", "resolver", prompt, metrics, DIAGNOSIS_SCHEMA, False)


def validate_diagnosis(state: RunState, value, record, workspace):
    diagnosis_guard(state, workspace)
    if record.get("changed_files") or record.get("source_revision") != state["diagnosis_request"]["source_revision"]:
        raise util.Paused("PAUSED_STALE_HANDOFF", "Diagnosis must leave the reviewed source unchanged")
    if not value.get("diagnosis", "").strip():
        raise ValueError("Diagnosis requires a nonempty explanation")
    recommendation = value.get("recommendation") or {}
    if recommendation.get("action") not in ("retry", "escalate") or not recommendation.get("rationale", "").strip():
        raise ValueError("Diagnosis recommendation requires a rationale and a retry-or-escalate action")


def preserve_review_criteria(state: RunState, value):
    """A focused diagnosis may omit or restate criteria, but cannot add or verify them.

    The approved wording stays. A one-character restatement used to reject the
    whole report, so a BLOCKED question about the proof environment never
    reached the operator (issue 624).
    """
    authoritative = state["acceptance_criteria"]
    by_id = {row["id"]: row for row in authoritative}
    seen = set()
    for row in value["acceptance_criteria"]:
        cid = row["id"]
        if cid in seen:
            raise util.Paused("PAUSED_INVALID_OUTPUT", "Duplicate acceptance IDs")
        seen.add(cid)
        if cid not in by_id:
            raise util.Paused("PAUSED_CRITERIA_CHANGE", "Repair cannot change approved acceptance criteria")
    # Preserve the last review's order, statuses and evidence, including omitted
    # criteria. Diagnosis supplies repair instructions, not a new review verdict.
    return {**value, "acceptance_criteria": copy.deepcopy(authoritative)}
