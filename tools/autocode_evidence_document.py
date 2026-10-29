"""Canonical, versioned evidence document and its deterministic Markdown rendering.

This is a report of supplied public facts, never a verifier or completion gate.
"""

import html
import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path

try:
    from . import autocode_approval_provenance as approval_provenance
    from . import autocode_roles as roles
    from . import autocode_util as util
except ImportError:
    import autocode_approval_provenance as approval_provenance
    import autocode_roles as roles
    import autocode_util as util

SCHEMA_PATH = Path(__file__).parent / "autocode-schemas/evidence-report.schema.json"
# The fields of a status-view approvals row the report copies beside its kind and provenance (#722).
APPROVAL_FIELDS = ("at", "actor", "contract_token", "criterion", "pause_status", "source")
POLICY_ACTOR = "workflow_policy"  # an approval no person typed (autocode_workflows)
APPROVER_UNVERIFIED = (
    "Who approved is recorded as given, not verified: the Git identity is what the checkout's Git configuration "
    "held and a name given with --as is the person's own claim"
)
# The reports that list their child runs' approvals, each row naming its ``child`` (autocode_evidence_aggregate),
# and what each says it leaves out: the decisions of its own that record no approver, and the approvals of the run
# it was planned in, which that run's own report lists (#722).
COORDINATOR_APPROVALS_LEFT_OUT = {
    "program": "The program agreement (`autocode program approve`) records no approver, so it is not listed, and "
    "neither are the approvals of a plan run it was derived from (`autocode program derive`).",
    "components": "Approvals of the architecture this run was built from are not listed.",
}
DIGEST = re.compile(r"[0-9a-f]{64}")
# The fields of the status view's approved_contract.plan_file the report copies (#720).
PLAN_FILE_FIELDS = ("path", "goal_hash", "revision", "changed")


def mapping(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def select(row, keys):
    return {key: deepcopy(row.get(key)) for key in keys}


def accounting_facts(accounting, *, role_context=None):
    """Copy exactly the usage facts rendered by a completed checkpoint report.

    Native request/log bookkeeping and status inspection metadata are not report
    facts. The caller supplies its already computed public accounting projection.
    """
    accounting = mapping(accounting)
    attempts = []
    for row in rows(accounting.get("attempts")):
        attempt = select(
            row,
            (
                "identity",
                "stage",
                "engine",
                "model",
                "source_revision",
                "started_at",
                "finished_at",
                "duration_seconds",
                "runner_owned",
                "rejected",
                "interrupted",
                "timed_out",
                "exit_code",
                "tokens",
                "reported_cost_usd",
                "historical_estimated_cost_usd",
                "usage_basis",
                "request_coverage_complete",
            ),
        )
        if "prompts_hash" in row:
            attempt["prompts_hash"] = deepcopy(row["prompts_hash"])
        attempt["role"] = roles.screen_name(row.get("stage") or "", role_context) or row.get("role") or "Runner"
        attempts.append(attempt)
    return {
        "attempts": attempts,
        "usage": select(accounting, ("tokens", "provider_requests", "cost")),
        "incomplete": not bool(accounting) or bool(accounting.get("issues")),
    }


def _fact(value):
    return None if value is None else str(value)


def approval_facts(approvals):
    """The status view's ``approvals`` rows as report facts (#722): each row's fields and provenance, as text.

    Who approved is copied as recorded; nothing here verifies it. A row without provenance (saved before AutoCode
    recorded it, or approved under the workflow policy) keeps ``provenance`` null.
    """
    facts = []
    for row in rows(approvals):
        recorded = row.get("provenance")
        facts.append(
            {
                "kind": str(row.get("kind")),
                **{key: _fact(row.get(key)) for key in APPROVAL_FIELDS},
                "provenance": (
                    {key: _fact(recorded.get(key)) for key in approval_provenance.FIELDS}
                    if isinstance(recorded, dict)
                    else None
                ),
            }
        )
    return facts


def _digest(value):
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def plan_identity(approved, contract_token):
    """The approved plan's goal hash and the plan file it was loaded from, as report facts (#720).

    ``approved`` is the status view's ``approved_contract``. Empty unless it is the plan the report is bound to
    (its token is the report's contract token) and carries a goal hash, so a report built from other facts, or
    written before the view gave one, has neither field. ``plan_file`` is null for a plan not loaded with
    --goal-file and for a citation that is not one autocode_plan_file_origin.citation writes.
    """
    approved = mapping(approved)
    if not contract_token or approved.get("token") != contract_token or not _digest(approved.get("goal_hash")):
        return {}
    cited = approved.get("plan_file")
    well_formed = (
        isinstance(cited, dict)
        and isinstance(cited.get("path"), str)
        and _digest(cited.get("goal_hash"))
        and type(cited.get("revision")) is int
        and isinstance(cited.get("changed"), list)
        and all(isinstance(key, str) for key in cited["changed"])
    )
    return {"goal_hash": approved["goal_hash"], "plan_file": select(cited, PLAN_FILE_FIELDS) if well_formed else None}


def build(view, *, run_identity, completed_at, provenance, binding, kind="task", children=(), role_context=None):
    if (view.get("replay_progress") or {}).get("checks") == "recorded" or (view.get("replay_result") or {}).get(
        "simulated"
    ):
        raise ValueError("Archival replay cannot publish current canonical proof")
    evidence, verification = mapping(view.get("evidence")), mapping(view.get("verification"))
    accounting = accounting_facts(mapping(view.get("usage")).get("accounting"), role_context=role_context)
    coverage = {row.get("id"): row for row in rows(verification.get("coverage"))}
    criteria = []
    for row in rows(evidence.get("acceptance")):
        detail = coverage.get(row.get("id"), {})
        criteria.append(
            {
                **select(row, ("id", "criterion", "status", "validator_status", "human_reviewed", "evidence")),
                **select(detail, ("verification_method", "human_review", "evidence_refs")),
            }
        )
    checks = [
        select(
            row,
            ("command", "exit_code", "timed_out", "duration_seconds", "output", "output_sha256", "purpose", "results"),
        )
        for row in rows(mapping(evidence.get("check_replay")).get("checks"))
    ]
    unverified = []
    for row in criteria:
        if row["validator_status"] != "PASS":
            unverified.append(f"{row['id']}: Validator {row['validator_status'] or 'not recorded'}")
        if row["human_review"] and not row["human_reviewed"]:
            unverified.append(f"{row['id']}: human acceptance not recorded")
    if not checks:
        unverified.append("No independent check replay recorded; job completion does not invent code verification")
    for check in checks:
        if check["timed_out"] or (check["exit_code"] != 0 and mapping(check["results"]).get("ok") is not True):
            unverified.append("Check failed or unavailable: " + str(check["command"]))
    proof = mapping(evidence.get("regression_proof"))
    if proof.get("verdict") and proof["verdict"] != "PASS":
        unverified.append("Regression proof verdict: " + str(proof["verdict"]))
    unverified += ["Regression proof unverified: " + str(gap) for gap in proof.get("unverified") or []]
    changed_code = mapping(proof.get("changed_code_evidence"))
    if changed_code and changed_code.get("status") != "PASS":
        unverified.append("Changed-code evidence gate: " + str(changed_code.get("reason") or "unverified"))
    if accounting["incomplete"]:
        unverified.append("Reported usage is incomplete or unavailable")
    if provenance.get("kind") in ("fake", "mixed", "unknown"):
        unverified.append("Model effectiveness is not established by fake, mixed or undeclared provider evidence")
    # Only the task status view lists approvals; a report built from other facts (verification, program,
    # components) has no such record and omits the field rather than claiming there were none. So does the report
    # of a workflow with no approval step (review, discuss), which review-pr may post on someone else's pull request.
    # A program or components report then gains its child runs' (autocode_evidence_aggregate.child_approvals).
    approvals = (
        approval_facts(view["approvals"])
        if "approvals" in view and approval_provenance.asks_for_approval(view.get("workflow"))
        else None
    )
    if any(row["provenance"] for row in approvals or ()):
        unverified.append(APPROVER_UNVERIFIED)
    unrecorded = sum(1 for row in approvals or () if row["provenance"] is None and row["actor"] != POLICY_ACTOR)
    if unrecorded:
        unverified.append(
            f"Who approved was not recorded for {unrecorded} of the listed approval records, "
            "saved before AutoCode recorded it"
        )
    started_at = evidence.get("created_at")
    elapsed = None
    if started_at and completed_at:
        try:
            duration = (datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)).total_seconds()
            elapsed = duration if duration >= 0 else None
        except (TypeError, ValueError):
            pass
    document = {
        "version": 1,
        "kind": kind,
        "binding": binding,
        "run": {
            "id": str(run_identity),
            "status": view.get("status") or "",
            "workflow": view.get("workflow"),
            "completed_at": completed_at,
            "turn": view.get("turn"),
            "started_at": started_at,
            "elapsed_seconds": elapsed,
            "prompts_hash": view.get("prompts_hash"),
            **(
                {"interaction_timing": deepcopy(evidence["interaction_timing"])}
                if evidence.get("interaction_timing")
                else {}
            ),
        },
        "provenance": deepcopy(provenance),
        "plan": {
            "contract_token": verification.get("contract_token"),
            "criteria_revision": verification.get("criteria_revision"),
            "task_id": verification.get("task_id"),
            "intended_outcome": evidence.get("outcome"),
            **plan_identity(view.get("approved_contract"), verification.get("contract_token")),
        },
        "revision": {
            "base_commit": evidence.get("base_commit"),
            "source_revision": evidence.get("validator_source_revision"),
            "meaning": "Content snapshot checked before delivery; a later delivery commit is not a tested Git SHA",
        },
        "criteria": criteria,
        "checks": checks,
        "workflow_result": deepcopy(evidence.get("workflow_result")),
        "regression_proof": _with_survivors(evidence),
        "test_cases": deepcopy(evidence.get("test_cases") or []),
        "findings": deepcopy(evidence.get("findings") or []),
        "attempts": accounting["attempts"],
        "usage": accounting["usage"],
        "unverified": unverified,
        "children": deepcopy(list(children)),
        **(
            {"changed_code_repairs": deepcopy(verification["changed_code_repairs"])}
            if verification.get("changed_code_repairs")
            else {}
        ),
        **({"approvals": approvals} if approvals is not None else {}),
    }
    validate(document)
    return document


def _with_survivors(evidence):
    """The view's regression proof, plus the mutation check's survivors when there are any (#699, report only)."""
    proof = deepcopy(evidence.get("regression_proof"))
    survivors = mapping(mapping(evidence.get("mutation_check")).get("survivors"))
    if isinstance(proof, dict) and survivors:
        proof["mutation_survivors"] = deepcopy(survivors)
    return proof


def validate(document):
    util.validate_schema(document, json.loads(SCHEMA_PATH.read_text()))
    # The local schema validator handles types; enforce digest syntax here too.
    for row in [document["run"], *document["attempts"]]:
        value = row.get("prompts_hash")
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)):
            raise ValueError("Prompt identity must be a SHA-256 digest or unknown")
    plan = document["plan"]
    if "plan_file" in plan and "goal_hash" not in plan:
        raise ValueError("A plan file is cited only beside the approved plan's goal hash")
    hashes = [plan["goal_hash"]] if "goal_hash" in plan else []
    if isinstance(plan.get("plan_file"), dict):
        hashes.append(plan["plan_file"]["goal_hash"])
    if not all(_digest(value) for value in hashes):
        raise ValueError("A plan's goal hash must be a SHA-256 digest")
    # Each approval a program or components report lists names the child run whose report lists it; no other
    # report's approval names one, so each renders in exactly one table.
    coordinated = document["kind"] in COORDINATOR_APPROVALS_LEFT_OUT
    named = {child["id"] for child in document["children"]}
    for row in document.get("approvals", ()):
        if coordinated != ("child" in row) or (coordinated and row["child"] not in named):
            raise ValueError("Only a program or components report names an approval's child run, one it lists")
    # Also reject non-JSON numbers (NaN/Infinity) in nested public accounting.
    json.dumps(document, allow_nan=False)


REDACTION_NOTICE = (
    "Text matching the shared credential policy was masked. Artifact hashes still "
    "identify the original raw evidence bytes."
)
_AUTH_FIELDS = frozenset(
    (
        "binding",
        "contract_token",
        "criteria_revision",
        "source_revision",
        "base_commit",
        "prompts_hash",
        "goal_hash",
        "proof_context",
        "gap_id",
    )
)


def sanitize(value):
    """Copy export facts through the shared policy without rebinding raw proof."""

    def visit(item):
        if isinstance(item, str):
            return util.redact(item)
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, dict):
            copied = {}
            for key, child in item.items():
                safe_key, safe_child = util.redact(key), visit(child)
                if safe_key in copied:
                    raise ValueError("Credential masking would merge distinct evidence keys")
                if isinstance(key, str) and (key in _AUTH_FIELDS or key.endswith("_sha256")) and safe_child != child:
                    raise ValueError("Credential masking would change an authentication identifier")
                copied[safe_key] = safe_child
            return copied
        return item

    result = visit(value)
    formatted = _render(result)
    if (result != value or util.redact(formatted) != formatted) and REDACTION_NOTICE not in result["unverified"]:
        result["unverified"].append(REDACTION_NOTICE)
    return result


def render(document):
    """Mask formatting-introduced matches before the canonical Markdown is bound."""
    return util.redact(_render(document))


def text(value):
    value = "not recorded" if value is None else str(value)
    return (
        html.escape(value, quote=False)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("`", "\\`")
        .replace("\r", " ")
        .replace("\n", "<br>")
    )


def code(value):
    """``value`` as one inline code span. A code span's content is literal (no emphasis, HTML or escapes), so a
    path such as ``pkg/__init__.py`` or code such as ``a*b`` shows as written: only the fence must be longer
    than any backtick run inside, with a space against a backtick at either end."""
    value = " ".join(("not recorded" if value is None else str(value)).split())
    fence = "`" * (max(map(len, re.findall("`+", value)), default=0) + 1)
    pad = " " if value[:1] == "`" or value[-1:] == "`" else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def _survivor(row):
    """A surviving mutant from its fields (the row's ``text`` says the same in prose, for the JSON)."""
    where = f"{row.get('path')}:{row.get('line')}"
    return (
        f"{code(row.get('test'))} passes with {code(where)} changed "
        f"({code(row.get('before'))} {text('->')} {code(row.get('after'))})"
    )


APPROVAL_DECISIONS = {
    "plan": "Plan {contract_token} approved",
    "review": "Review of {criterion} approved",
    "edit": "Edited plan {contract_token} saved",
    "resume": "Resumed from {pause_status}",
    "completion": "Completion accepted",
}


def _approval_cells(row):
    """One approval row's table cells, saying plainly when no person's identity was recorded."""
    decision = APPROVAL_DECISIONS.get(row["kind"], "{kind}").format(
        **{key: "not recorded" if value is None else value for key, value in row.items()}
    )
    recorded = row["provenance"]
    if recorded is None:
        if row["actor"] == POLICY_ACTOR:
            return (
                row["at"],
                decision,
                "none: approved under the workflow policy, not by a person",
                *["not applicable"] * 3,
            )
        return (row["at"], decision, "not recorded: saved before AutoCode recorded who approved", *[None] * 3)
    name, email, claim = recorded["git_user_name"], recorded["git_user_email"], recorded["claimed_as"]
    who = " ".join(part for part in (name, f"<{email}>" if email else None) if part) or "no Git user name or email set"
    claimed = f"{claim} (a claim, not verified)" if claim else "none given"
    return (row["at"], decision, who, recorded["host"], recorded["command"], claimed)


APPROVALS_UNVERIFIED = (
    "None of it is verified: the Git identity is what the checkout's Git configuration held, and a name given "
    "with `--as` is the person's own claim."
)


def _child_approval_lines(document):
    """A program or components report's Approvals section (#722): the approvals each child run's own report lists,
    child by child in the order of its children, each row naming its child."""
    lines = [
        "## Approvals",
        "",
        "Who approved in each child run, as that run's own evidence report lists it: child by child, oldest first.",
        APPROVALS_UNVERIFIED,
        COORDINATOR_APPROVALS_LEFT_OUT[document["kind"]],
        "",
    ]
    if not document["approvals"]:
        return [*lines, "None recorded: no child run's report lists an approval, plan edit or resume.", ""]
    lines += [
        "| Child | When | Decision | Git identity | Host | Command | Name given with --as |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        "| " + " | ".join(text(cell) for cell in (row["child"], *_approval_cells(row))) + " |"
        for row in document["approvals"]
    ]
    return [*lines, ""]


def _approval_lines(document):
    """The Approvals section (#722), or [] for a report without approvals: one written before AutoCode listed
    them, so an older report's Markdown still renders byte for byte from its JSON, or a review or discussion's."""
    if "approvals" not in document:
        return []
    if document["kind"] in COORDINATOR_APPROVALS_LEFT_OUT:
        return _child_approval_lines(document)
    # Only a report that lists a completion acceptance names it here: every report written before AutoCode listed
    # one keeps its sentence, so its Markdown still renders byte for byte from its JSON.
    accepted = any(row["kind"] == "completion" for row in document["approvals"])
    lines = [
        "## Approvals",
        "",
        "Who approved the plan and each review, edited the plan, resumed the run from a pause or accepted its "
        "completion, oldest first."
        if accepted
        else "Who approved the plan and each review, edited the plan or resumed the run from a pause, oldest first.",
        APPROVALS_UNVERIFIED,
        "",
    ]
    if not document["approvals"]:
        return [
            *lines,
            "None recorded: this run saved no plan approval, review approval, plan edit or resume from a pause.",
            "",
        ]
    lines += [
        "| When | Decision | Git identity | Host | Command | Name given with --as |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines += ["| " + " | ".join(text(cell) for cell in _approval_cells(row)) + " |" for row in document["approvals"]]
    return [*lines, ""]


def _plan_lines(plan):
    """The approved plan's goal hash and the plan file it was loaded from (#720), or [] for a report without
    them (one written before AutoCode cited them), so an older report's Markdown still renders byte for byte.

    The approved plan is the file's when their goal hashes are equal. Otherwise ``changed`` names what differs
    from the revision loaded from it; when it names nothing, only the runner's records of the plan's review do
    (autocode_goal_file.approval_lines says the same at the approval stop)."""
    if "goal_hash" not in plan:
        return []
    lines = [
        f"Plan goal hash: {text(plan['goal_hash'])} (what `autocode plan --check FILE` prints for a file holding "
        "exactly this plan)."
    ]
    cited = plan.get("plan_file")
    if not cited:
        return lines
    if cited["goal_hash"] == plan["goal_hash"]:
        relation = "is that file's plan, unchanged"
    elif not cited["changed"]:
        relation = "differs from that file's plan only in the runner's records of the plan's review"
    else:
        relation = "differs from that file's plan in " + text(", ".join(cited["changed"]))
    return [
        *lines,
        f"Plan file: {code(cited['path'])} (goal hash {text(cited['goal_hash'])}), loaded as plan revision "
        f"{text(cited['revision'])}; the approved plan {relation}.",
    ]


def _changed_code_lines(proof, repairs):
    """Render recorded gate facts; absent fields preserve historical Markdown."""
    assessment = mapping(proof.get("changed_code_evidence"))
    if not assessment and not repairs:
        return []
    lines = ["", "## Changed-code evidence gate", ""]
    if assessment:
        lines += [
            "Runner gate: **" + text(assessment.get("status")) + "**. " + text(assessment.get("reason")),
            "Each changed executable line needs behavioral evidence or an independently accepted Tester reason. "
            "Unresolved obligations block completion; missing tools do not count as a pass.",
            f"Source: {text(assessment.get('source_revision'))}. Proof context: {text(assessment.get('proof_context'))}.",
            "Deterministic fallback: " + text(", ".join(assessment.get("fallback") or []) or "none") + ".",
            "",
            "| Gap | File:line (SHA-256) | Status | Execution / mutation | Fallback | Reason / evidence |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows(assessment.get("obligations")):
            observations = (
                f"executed={row.get('executed')}; mutation demonstrated={row.get('mutation_demonstrated')}; "
                + ", ".join(row.get("mutation_outcomes") or [])
            )
            reason = row.get("reason") or "not recorded"
            refs = list(row.get("evidence") or [])
            accepted = mapping(row.get("exception")) if row.get("status") == "excepted" else {}
            if accepted:
                reason = "Independently accepted Tester reason: " + str(accepted.get("reason") or reason)
                if accepted.get("reason_kind"):
                    reason += " (" + str(accepted["reason_kind"]) + ")"
                refs += accepted.get("evidence") or []
                reviewer = mapping(accepted.get("reviewer"))
                if reviewer:
                    reason += "; reviewer record: " + str(reviewer.get("output") or "not recorded")
            cells = (
                row.get("gap_id"),
                f"{row.get('path')}:{row.get('line')} ({row.get('sha256')})",
                row.get("status"),
                observations,
                row.get("fallback"),
                reason + "; evidence: " + (", ".join(dict.fromkeys(refs)) or "not recorded"),
            )
            lines.append("| " + " | ".join(text(cell) for cell in cells) + " |")
        if not assessment.get("obligations"):
            lines.append("No line obligations recorded; the gate verdict above still applies.")
    else:
        lines.append("Current changed-code assessment not recorded.")
    if repairs:
        lines += [
            "",
            "### Recorded changed-code repair decisions",
            "",
            "These historical decisions retain their own source and proof context; they do not satisfy current gaps.",
        ]
        for row in rows(repairs):
            action = row.get("action")
            stage = {"builder": "terra", "investigator": "investigate_stuck"}.get(action)
            label = roles.screen_name(stage) if stage else action
            prior = mapping(row.get("assessment"))
            lines += [
                f"- {text(row.get('at'))}: {text(label)}; source {text(row.get('source_revision'))}; "
                f"proof context {text(prior.get('proof_context'))}; task {text(row.get('task_id'))}; "
                f"plan r{text(row.get('contract_revision'))}:{text(row.get('contract_hash'))}.",
                "  Review record: " + text(row.get("review_output")) + ". " + text(prior.get("reason")),
                "  Unresolved gaps: "
                + text(
                    ", ".join(
                        str(gap.get("gap_id"))
                        for gap in rows(prior.get("obligations"))
                        if gap.get("status") == "reason_required"
                    )
                    or "none recorded"
                )
                + ".",
            ]
            investigation = mapping(row.get("investigation"))
            if investigation:
                lines.append(
                    "  Investigator diagnosis: "
                    + text(investigation.get("diagnosis"))
                    + "; guidance: "
                    + text(investigation.get("guidance"))
                    + "; evidence: "
                    + text(", ".join(investigation.get("evidence_refs") or []))
                    + "; record: "
                    + text(investigation.get("output"))
                    + " ("
                    + text(investigation.get("sha256"))
                    + ")."
                )
    return lines


def _render(document):
    validate(document)
    run, revision, plan = document["run"], document["revision"], document["plan"]
    provenance = document["provenance"]
    lines = [
        "## AutoCode evidence",
        "",
        f"Run **{text(run['id'])}**: {text(run['status'])} ({text(run['workflow'])}).",
        f"Provider evidence: **{text(provenance['kind'])}**; {text(provenance['basis'])}. {text(provenance['declaration'])}.",
        "This record reports what was checked. Review the diff; completion is evidence, not proof of correctness.",
        "",
        "## Intended outcome",
        "",
        text(plan["intended_outcome"]),
        "",
        f"Agreed plan: {text(plan['contract_token'])}. Completed: {text(run['completed_at'])}.",
        *_plan_lines(plan),
        f"Elapsed wall time (including input waits): {text(run['elapsed_seconds'])} seconds.",
        *([f"Launch prompt set (SHA-256): {text(run['prompts_hash'])}."] if "prompts_hash" in run else []),
        f"Checked source snapshot: {text(revision['source_revision'])}; base commit: {text(revision['base_commit'])}.",
        revision["meaning"],
        "",
        *_approval_lines(document),
        "## Acceptance criteria",
        "",
        "| ID | Criterion | Result | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    timing = run.get("interaction_timing")
    if timing:
        position = lines.index("## Acceptance criteria")
        lines[position:position] = [
            "## First interaction",
            "",
            f"Launched: {text(timing['launched_at'])}. Wall time includes waits for human input.",
            "Builder measures successful provider supervisor or parallel Builder worker launch; it does not attest first model response.",
            "",
            "| Milestone | First observed at | Seconds from launch |",
            "| --- | --- | ---: |",
            *[
                f"| {label} | {text(timing[f'first_{event}_at'])} | {text(timing[f'first_{event}_seconds'])} |"
                for event, label in (
                    ("question", "Question shown"),
                    ("plan", "Plan shown for approval"),
                    ("builder", "Builder dispatched"),
                )
            ],
            "",
        ]
    job = document.get("workflow_result")
    if job:
        lines[lines.index("## Acceptance criteria") : lines.index("## Acceptance criteria")] = [
            "## Workflow result",
            "",
            f"Outcome: {text(job['outcome'])}. {text(job['summary'])}.",
            f"Artifact: {text(job['artifact'])} ({text(job['artifact_sha256'])}).",
            "",
        ]
    for row in document["criteria"]:
        status = f"{row['status'] or 'no outcome recorded'}, validator: {row['validator_status'] or 'unchecked'}"
        if row["human_reviewed"]:
            status += ", accepted by a person"
        lines.append(f"| {text(row['id'])} | {text(row['criterion'])} | {text(status)} | {text(row['evidence'])} |")
    if not document["criteria"]:
        lines.append("None recorded.")
    lines += [
        "",
        "## Independent checks",
        "",
        "Artifact references below are local files, not publicly hosted evidence.",
        "",
        "| Command | Exit | Timed out | Seconds | Artifact (SHA-256) | Results |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in document["checks"]:
        result = json.dumps(row["results"], sort_keys=True, allow_nan=False) if row["results"] is not None else None
        lines.append(
            f"| {text(row['command'])} | {text(row['exit_code'])} | {text(row['timed_out'])} | {text(row['duration_seconds'])} | {text(row['output'])} ({text(row['output_sha256'])}) | {text(result)} |"
        )
    if not document["checks"]:
        lines.append("None recorded.")
    review_mutations = mapping(mapping(document.get("workflow_result")).get("mutation_check"))
    if review_mutations:
        lines += ["", "## Review test mutation check", "", "Report only; this does not change the review verdict."]
        lines.append(
            "Subject: passing tests from the reviewed patch, separate from the reviewer's failing finding tests."
        )
        if review_mutations.get("note"):
            lines.append(text(review_mutations["note"]))
        if not review_mutations.get("collected"):
            lines.append("No mutation result was collected.")
        for test, found in sorted(mapping(review_mutations.get("survivors")).items()):
            lines += [f"- Surviving mutant, {text(test)}: {_survivor(row)}" for row in rows(found)]
    proof = mapping(document["regression_proof"])
    if document["test_cases"]:
        lines += [
            "",
            "## Regression tests in plain English",
            "",
            "| Case | Given | When | Then | Test |",
            "| --- | --- | --- | --- | --- |",
        ]
        for case in document["test_cases"]:
            names = mapping(proof.get("case_tests")).get(case.get("id")) or []
            lines.append(
                "| "
                + " | ".join(text(case.get(key)) for key in ("id", "given", "when", "then"))
                + " | "
                + text(", ".join(names) or "not proven")
                + " |"
            )
    lines += ["", "## Regression proof", "", "Runner verdict: " + text(proof.get("verdict")) + "."]
    if proof.get("fail_to_pass"):
        lines.append("Fail-before/pass-after tests: " + text(", ".join(proof["fail_to_pass"])))
    for key, names in sorted(mapping(proof.get("case_tests")).items()):
        lines.append(f"- {text(key)}: {text(', '.join(names))}")
    for key, found in sorted(mapping(proof.get("mutation_survivors")).items()):
        lines += [f"- Surviving mutant, {text(key)}: {_survivor(row)}" for row in rows(found)]
    for label, command in sorted(mapping(proof.get("commands")).items()):
        lines.append(f"- {text(label)} command: {text(command)}")
    lines += ["- Failure: " + text(failure) for failure in proof.get("failures") or []]
    lines += ["- Unverified: " + text(gap) for gap in proof.get("unverified") or []]
    lines += _changed_code_lines(proof, document.get("changed_code_repairs") or [])
    lines += ["", "## Review findings", ""]
    lines += [
        f"- {text(row.get('id'))} [{text(row.get('status'))}, {text(row.get('severity'))}]: {text(row.get('finding'))}"
        for row in document["findings"]
    ] or ["None recorded."]
    lines += [
        "",
        "## Roles and models",
        "",
        "| Job | Engine | Model | Prompt set (SHA-256) | Seconds | Result |"
        if "prompts_hash" in run
        else "| Job | Engine | Model | Seconds | Result |",
        "| --- | --- | --- | --- | --- | --- |" if "prompts_hash" in run else "| --- | --- | --- | --- | --- |",
    ]
    for row in document["attempts"]:
        result = "interrupted" if row["interrupted"] else "rejected" if row["rejected"] else f"exit {row['exit_code']}"
        prompt_cell = f" {text(row.get('prompts_hash'))} |" if "prompts_hash" in run else ""
        lines.append(
            f"| {text(row['role'])} | {text(row['engine'])} | {text(row['model'])} |{prompt_cell} {text(row['duration_seconds'])} | {text(result)} |"
        )
    lines += [
        "",
        "## Reported usage",
        "",
        text(json.dumps(document["usage"], sort_keys=True, allow_nan=False)),
        "",
        "Usage is the accounting snapshot captured at this completed checkpoint; it does not attest all later raw log bytes.",
        "Reported cost, historical estimates and unknown quantities remain separate; this is not a subscription invoice.",
        "",
        "## Not verified",
        "",
    ]
    lines += ["- " + text(reason) for reason in document["unverified"]] or [
        "No additional gap recorded; this does not replace review."
    ]
    if document["children"]:
        lines += ["", "## Child evidence", ""]
        lines += [
            f"- {text(row['id'])}: {text(row['json_path'])} ({text(row['json_sha256'])})"
            for row in document["children"]
        ]
    return "\n".join(lines) + "\n"
