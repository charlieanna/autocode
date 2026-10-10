"""Local checkpoint, evidence and context utilities for the existing autocode loop.

No provider calls, credentials, external memory or alternate workflow state.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import contextlib
import copy
import json
import os
import re
import shlex
import subprocess
import tomllib
from pathlib import Path

# Re-export shared helpers for existing callers and test patches; retain the compact facade imports.
# isort: off
# fmt: off
try:
    from .autocode_baseline import BASELINE_POLICY  # noqa: F401 - compatibility API
    from .autocode_legacy_process import assert_no_legacy_process, duplicate_runner_command  # noqa: F401 - compatibility API
    from .autocode_report_schema import review_generation_schema, review_validation_schema, hydrate_review_report  # noqa: F401 - compatibility API
    from . import autocode_output_filter as output_filter, autocode_request_usage as request_usage  # noqa: F401 - compatibility API
    from . import autocode_evidence_snapshot as evidence_snapshot  # noqa: F401 - compatibility API
    from .autocode_util import (Paused, atomic_json, changed_paths, criteria_definition, digest, file_hash,  # noqa: F401 - compatibility API
                                model_output_schema, now, read, run_lock, snapshot, validate_schema, workspace_lock)  # noqa: F401 - compatibility API
    from . import autocode_receipts as receipts, autocode_usage as token_usage, autocode_provider_error_lines as provider_error_lines  # noqa: F401 - compatibility API
    from . import autocode_event_matching as event_matching, autocode_event_metrics as event_summary, autocode_provider_refusal as provider_refusal  # noqa: F401 - compatibility API
    from .autocode_event_matching import same_command  # noqa: F401 - compatibility API
except ImportError:
    from autocode_baseline import BASELINE_POLICY  # noqa: F401 - compatibility API
    from autocode_legacy_process import assert_no_legacy_process, duplicate_runner_command  # noqa: F401 - compatibility API
    from autocode_report_schema import review_generation_schema, review_validation_schema, hydrate_review_report  # noqa: F401 - compatibility API
    import autocode_output_filter as output_filter
    import autocode_request_usage as request_usage  # noqa: F401 - compatibility API
    import autocode_evidence_snapshot as evidence_snapshot  # noqa: F401 - compatibility API
    from autocode_util import (Paused, atomic_json, changed_paths, criteria_definition, digest, file_hash,  # noqa: F401 - compatibility API
                               model_output_schema, now, read, run_lock, snapshot, validate_schema, workspace_lock)  # noqa: F401 - compatibility API
    import autocode_receipts as receipts
    import autocode_usage as token_usage
    import autocode_provider_error_lines as provider_error_lines  # noqa: F401 - compatibility API
    import autocode_event_matching as event_matching
    import autocode_event_metrics as event_summary
    import autocode_provider_refusal as provider_refusal  # noqa: F401 - compatibility API
    from autocode_event_matching import same_command  # noqa: F401 - compatibility API
# fmt: on
# isort: on
def events(path):
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except ValueError:
            continue
    if any(
        row.get("sessionID") and row.get("type") in ("step_start", "step_finish", "tool_use", "text", "error")
        for row in rows
    ):
        try:
            from . import autocode_opencode
        except ImportError:
            import autocode_opencode
        return autocode_opencode.normalized_events(rows)
    return rows


def event_metrics(path):
    rows = events(path)
    return event_summary.summarize(rows, request_usage.read(path), token_usage.reported_cost(rows))


def terminal_failure_reason(path):
    """Expose recognized transport failures without interpreting model prose."""
    for row in events(path):
        error = row.get("error") if row.get("type") == "turn.failed" else None
        if isinstance(error, dict) and error.get("code") in ("output_token_limit", "incomplete_turn"):
            return error.get("message")
    return None


def failure_status(path):
    # Inspect actual provider errors, not arbitrary tool logs mentioning errors.
    failures = [e for e in events(path) if e.get("type") in ("turn.failed", "error")]
    # A command-line tool can end with the failed request as plain text instead (#562).
    text = "\n".join([json.dumps(failures), *provider_error_lines.trailing(path)]).lower()
    # A content-filter refusal is about this model, not the work: the same model is likely to refuse again.
    if provider_refusal.refusal(failures):
        return provider_refusal.STATUS
    # Model capacity is transient, unlike account rate limits and quota; the runner may retry it under a small budget.
    if re.search(r"model is at capacity|model capacity exceeded|model_overloaded|resource_exhausted", text):
        return "PAUSED_PROVIDER_CAPACITY"
    # A used-up plan can be a 429 that never says quota (Z.AI: "Weekly/Monthly Limit Exhausted"); a rate limit is not.
    if any(
        x in text for x in ("quota", "budget", "usage limit", "usage_limit", "insufficient_credit", "add credits")
    ) or re.search(r"(?<!rate )limit (exhausted|will reset at \d{4}-)|\b(daily|weekly|monthly) limit\b", text):
        return "PAUSED_BUDGET"
    if any(x in text for x in ("rate_limit", "rate limit", "429")):
        return "PAUSED_RATE_LIMIT"
    return "PAUSED_PROVIDER_UNCERTAIN"


def compact_output(text, *, enabled=True, command=None, exit_code=None):
    return output_filter.compact_output(text, enabled=enabled, command=command, exit_code=exit_code)


def summarize_events(path, destination):
    commands = []
    for e in events(path):
        item = e.get("item", {})
        if e.get("type") == "item.completed" and item.get("type") == "command_execution":
            commands.append(
                {
                    "command": item.get("command"),
                    "exit_code": item.get("exit_code"),
                    "event_id": item.get("id"),
                    "full_log": str(path),
                    "output": compact_output(item.get("aggregated_output", "")),
                }
            )
    atomic_json(destination, {"commands": commands, "full_log": str(path)})


def implementation_evidence_paths(refs, events_path):
    """Resolve actual executed-event references to their preserved event log.

    File references retain containment checks; events identify one completed command.
    """
    resolved = []
    completed = None
    for ref in refs:
        if ref.startswith("event:"):
            if completed is None:
                completed = [
                    e["item"]
                    for e in events(events_path)
                    if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "command_execution"
                ]
            matches = [item for item in completed if item.get("id") == ref[len("event:") :]]
            if len(matches) != 1 or type(matches[0].get("exit_code")) is not int:
                raise ValueError(f"Implementation evidence references a missing executed event: {ref}")
            resolved.append(str(events_path))
        else:
            resolved.append(ref)
    return list(dict.fromkeys(resolved))


def evidence_hashes(refs, workspace, run_dir):
    found = {}
    for ref in refs:
        path = Path(ref.split("#", 1)[0].strip())
        path = path if path.is_absolute() else Path(workspace) / path
        path = path.resolve()
        if not path.is_relative_to(Path(workspace).resolve()):
            raise ValueError(f"Evidence outside project: {ref}")
        if not path.is_file():
            raise ValueError(f"Missing evidence: {ref} (cite a project file path, not a description of what you read)")
        path = evidence_snapshot.stable_path(path, run_dir)
        found[str(path)] = file_hash(path)
    if not found:
        raise ValueError("Evidence references are empty")
    return found


def verify_checks(checks, workspace, event_path, *, receipt_only=False, capture_context=None):
    """Verify checks against executed events or attempt-bound, output-hashed receipts.
    Fill only absent exit codes from unique evidence."""
    rows = [] if receipt_only else events(event_path)
    tool_outputs = [
        e["item"]
        for e in rows
        if e.get("type") == "item.completed" and e.get("item", {}).get("type") in ("command_execution", "tool_output")
    ]
    normalized = copy.deepcopy(checks)
    for check in normalized:
        if (
            not isinstance(check, dict)
            or not isinstance(check.get("command"), str)
            or not isinstance(check.get("evidence_ref"), str)
        ):
            raise ValueError("Check needs a command and evidence reference")
        missing_exit = check.get("exit_code") is None
        if not missing_exit and type(check["exit_code"]) is not int:
            raise ValueError("Check exit code must be an integer")
        if check["evidence_ref"].startswith("event:"):
            if receipt_only:
                raise ValueError("Report-file providers require capture receipts, not event references")
            event_id = check["evidence_ref"].split(":", 1)[1]
            matches = [
                e["item"]
                for e in rows
                if e.get("type") == "item.completed"
                and e.get("item", {}).get("id") == event_id
                and e["item"].get("type") == "command_execution"
            ]
            if len(matches) == 1 and isinstance(matches[0].get("command"), str):
                executed = matches[0]["command"]
                same = same_command(executed, check["command"])
                if not same and event_matching.workspace_wrapped_command(executed, check["command"], workspace):
                    same = True
                    check["command"] = executed
                if (
                    same
                    and type(matches[0].get("exit_code")) is int
                    and (missing_exit or matches[0]["exit_code"] == check["exit_code"])
                ):
                    check["exit_code"] = matches[0]["exit_code"]
                    continue
            # A model sees no event IDs or exit codes, so a check may cite a bare "event:": it binds to the LATEST
            # run of its exact command, exit-less runs included, whose exit must be known and match (EVD-06).
            if not matches:
                alternates = [
                    e["item"]
                    for e in rows
                    if e.get("type") == "item.completed"
                    and e.get("item", {}).get("type") in ("command_execution", "tool_output")
                    and isinstance(e["item"].get("command"), str)
                    and (
                        same_command(e["item"]["command"], check["command"])
                        or event_matching.workspace_wrapped_command(e["item"]["command"], check["command"], workspace)
                    )
                ]
                latest = alternates[-1] if alternates else {}
                if (
                    type(latest.get("exit_code")) is int
                    and isinstance(latest.get("id"), str)
                    and latest["id"]
                    and (missing_exit or latest["exit_code"] == check["exit_code"])
                ):
                    check["evidence_ref"] = "event:" + latest["id"]
                    check["exit_code"] = latest["exit_code"]
                    check["command"] = latest["command"]
                    continue
            raise ValueError("Check is not supported by an exact executed Validator event")
        path = Path(check["evidence_ref"])
        path = path if path.is_absolute() else Path(workspace) / path
        if not path.resolve().is_relative_to(Path(workspace).resolve() / ".autocode"):
            raise ValueError("Executed check receipt must be captured under project .autocode")
        try:
            receipt = read(path)
        except OSError as error:
            raise ValueError(
                f"Cannot read check receipt {path}: {error}. Cite the exact captured receipt path."
            ) from error
        if (
            not isinstance(receipt.get("command"), list)
            or not all(isinstance(part, str) for part in receipt["command"])
            or type(receipt.get("exit_code")) is not int
            or not receipts.adopt_command(check, receipt["command"])
            or (not missing_exit and receipt["exit_code"] != check["exit_code"])
        ):
            raise ValueError(receipts.mismatch(check, receipt))
        raw = Path(receipt["full_output"])
        if (
            not raw.resolve().is_relative_to(Path(workspace).resolve() / ".autocode")
            or file_hash(raw) != receipt["full_output_sha256"]
        ):
            raise ValueError("Full check output missing or changed")
        if receipt_only:
            if not capture_context or receipt.get("capture_context") != capture_context:
                raise ValueError("Capture receipt does not belong to this validation attempt")
            check["exit_code"] = receipt["exit_code"]
            continue
        # capture prints one JSON object. Match structurally even if event text
        # adds shell notices. No word-search for PASS/COMPLETE is used.
        matched = False
        for item in tool_outputs:
            if item["type"] == "tool_output":
                try:
                    invocation = shlex.split(item.get("command", ""))
                    marker = invocation.index("capture")
                    output_flag = invocation.index("--output", marker + 1)
                    cited = Path(invocation[output_flag + 1])
                    cited = cited if cited.is_absolute() else Path(workspace) / cited
                    if cited.resolve() != path.resolve():
                        continue
                except (ValueError, IndexError):
                    continue
            for line in item.get("aggregated_output", "").splitlines():
                with contextlib.suppress(ValueError):
                    matched |= json.loads(line) == receipt
        if not matched:
            raise ValueError("No independently executed Validator tool event matches receipt")
        check["exit_code"] = receipt["exit_code"]
    # Failed or ambiguous normalization must not partly repair the caller's report.
    for original, derived in zip(checks, normalized, strict=False):
        original.update(derived)


def local_settings():
    """Read only nonsecret settings; never open auth.json or emit auth headers."""
    config_path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    keys = ("model", "model_reasoning_effort", "model_provider", "openai_base_url")
    settings = {k: config.get(k) for k in keys}
    auth = subprocess.run(["codex", "login", "status"], capture_output=True, text=True)
    message = auth.stdout + auth.stderr
    settings["auth_mode"] = "ChatGPT" if "using ChatGPT" in message else "unknown"
    settings["environment_auth_present"] = any(k in os.environ for k in ("OPENAI_API_KEY", "CODEX_API_KEY"))
    settings["environment_base_url_present"] = "OPENAI_BASE_URL" in os.environ
    return settings


def transport_drift(current, checkpoint, roles):
    """True only when local-setting drift can affect this run's launches.
    Auth, provider and base-url drift always matters. Top-level model/effort
    defaults matter only for roles that launch without explicit overrides
    (e.g. a desktop app flipping its saved default must not pause such runs)."""
    for key in (
        "auth_mode",
        "environment_auth_present",
        "environment_base_url_present",
        "model_provider",
        "openai_base_url",
    ):
        if current.get(key) != checkpoint.get(key):
            return True
    if any(not role.get("model") for role in roles.values()) and current.get("model") != checkpoint.get("model"):
        return True
    return bool(
        any(not role.get("reasoning_effort") for role in roles.values())
        and current.get("model_reasoning_effort") != checkpoint.get("model_reasoning_effort")
    )


def transport_arguments(settings):
    adapter = settings.get("headroom", {})
    if not adapter.get("enabled", False):
        return []
    # Installed-version launch flags and this custom ChatGPT/local upstream path
    # have not passed a transport smoke test. Fail closed; never reroute or bill
    # against an API key merely because the flag was toggled.
    raise Paused(
        "PAUSED_TRANSPORT_UNVERIFIED",
        "Headroom disabled: installed-version/auth/stream/tool/schema smoke verification is absent",
    )


STABLE = {
    "astra_plan": prompts.get("task-plan.md"),
    "astra_review": prompts.get("completion-review.md"),
    "terra": prompts.get("builder.md"),
    "sol": prompts.get("validator.md"),
}
ASTRA_DECISIONS = prompts.get("fragments/support/astra-decisions.md")
MILESTONE_POLICY = prompts.get("fragments/support/milestone-policy.md")
COMMON = prompts.get("fragments/support/common.md")


def migrate_v1(state, run_dir, workspace, settings, schemas):
    """Reconcile saved role finals, including a completed role missing from history.

    A partially emitted/failed turn is uncertain; never automatically replay it.
    Legacy validation lacks source revision pins, so it cannot authorize completion.
    """
    state = json.loads(json.dumps(state))
    state.update(
        version=2,
        settings=settings,
        stages=state.get("stages", []),
        next_stage="astra_plan",
        acceptance_criteria=[],
        criteria_revision=None,
        unresolved_findings=[],
    )
    role_order = {"astra": 0, "terra": 1, "sol": 2}
    paths = (Path(run_dir) / "iterations").glob("*/*.jsonl")
    for path in sorted(paths, key=lambda p: (p.parent.name, role_order.get(p.stem, 3))):
        role = path.stem
        if role not in ("astra", "terra", "sol"):
            continue
        iteration = int(path.parent.name)
        rows = events(path)
        final = path.with_suffix(".json")
        complete = any(e.get("type") == "turn.completed" for e in rows)
        # Preserve earlier failed launcher attempts as history, not pending work.
        if not complete or not final.exists():
            if iteration == state["iteration"]:
                state.update(status="PAUSED_UNCERTAIN_STAGE", stop_reason=f"Reconcile unfinished legacy {role}: {path}")
                state["next_stage"] = "astra_review" if role == "astra" else role
                state["uncertain_artifacts"] = str(path)
            continue
        value = read(final)
        validate_schema(value, read(schemas / f"{role}-{'decision' if role == 'astra' else 'report'}.schema.json"))
        thread = next((e.get("thread_id") for e in rows if e.get("type") == "thread.started"), None)
        if thread:
            state.setdefault("sessions", {})[role] = thread
        stage = (
            "astra_plan"
            if role == "astra" and not state["acceptance_criteria"]
            else "astra_review"
            if role == "astra"
            else role
        )
        state["stages"].append(
            {
                "stage": stage,
                "iteration": iteration,
                "output": str(final),
                "events": str(path),
                "completed_at": now(),
                "legacy": True,
                "metrics": event_metrics(path),
            }
        )
        state.setdefault("evidence_locations", []).append(str(final))
        if role == "astra":
            state["acceptance_criteria"] = value["acceptance_criteria"]
            state["criteria_revision"] = digest(criteria_definition(value["acceptance_criteria"]))
            state["next_action"] = value["next_objective"]
            state["plan"] = [value["next_objective"]]
            state["next_stage"] = "terra" if value["status"] == "CONTINUE" else "sol"
        elif role == "terra":
            state["implementation"] = value
            state["changed_files"] = value["changed_files"]
            state["next_stage"] = "sol"
        else:
            state["validation"] = {**value, "source_revision": None, "criteria_revision": None}
            state["unresolved_findings"] = value["findings"]
            state["next_stage"] = "astra_review"
    state["migration"] = {"from": 1, "at": now(), "legacy_validation_requires_freshness_review": True}
    if state.get("status") == "TASK_COMPLETE":
        state.update(status="PAUSED_LEGACY_COMPLETION_UNVERIFIED", next_stage="sol")
    return state
