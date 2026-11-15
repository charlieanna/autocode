"""Stable-first stage prompt layout and byte measurement (#3347).

Utility layer: partitions stage handoffs into contract-pure stable blocks, scope-bound
stable blocks, and changing per-turn blocks with deterministic key ordering inside one
single JSON object so providers maximize prompt-cache prefix hits without breaking
``CURRENT HANDOFF DATA`` consumers or mutating runner authority.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

try:
    from . import autocode_prompt_bound as prompt_bound
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompt_bound as prompt_bound
    import autocode_prompts as prompts

STABLE_MARKER = "\nSTABLE HANDOFF DATA\n"
CURRENT_MARKER = "\nCURRENT HANDOFF DATA\n"

# Keys that depend only on requirements, contract, workspace, and stage configuration.
# Must match tools/providers/claude_stream.py:SYSTEM_HANDOFF_KEYS exactly.
CONTRACT_PURE_KEYS: tuple[str, ...] = (
    "acceptance_criteria",
    "approved_design",
    "architecture_runtime_contract",
    "baseline_compare_command",
    "capture_command",
    "context_policy",
    "contract_author_rules",
    "coordinated_member",
    "criteria_revision",
    "design_readiness",
    "design_readiness_instruction",
    "execution_engine",
    "execution_limits",
    "figma_file",
    "goal_contract",
    "linked_issues",
    "linked_issues_note",
    "output_transport",
    "package",
    "project_memory",
    "project_notes",
    "regression_reference",
    "reply_budget",
    "requirements",
    "requirements_reply_budget",
    "retained_contract",
    "retained_criteria",
    "saved_answers",
    "source_catalog",
    "source_inventory",
    "source_sentences",
    "stage",
    "state_file",
    "task",
    "tool_containment",
    "visual_capture_command",
    "workspace",
    "write_scope",
)

# Keys bound to the active task/milestone scope that remain fixed across rework retries.
SCOPE_BOUND_STABLE_KEYS: tuple[str, ...] = (
    "current_milestone",
    "current_task",
    "design_manifest",
    "protected_tests",
    "verification_obligations",
    "workflow",
)

STABLE_HANDOFF_KEYS: tuple[str, ...] = CONTRACT_PURE_KEYS + SCOPE_BOUND_STABLE_KEYS

_CONTRACT_PURE_SET = frozenset(CONTRACT_PURE_KEYS)
_SCOPE_BOUND_SET = frozenset(SCOPE_BOUND_STABLE_KEYS)


def is_legacy_layout(state: Mapping[str, Any]) -> bool:
    """Keep byte-exact legacy prompt layout for runs pinned to an earlier prompt set."""
    settings = state.get("settings")
    if isinstance(settings, Mapping) and settings.get("legacy_prompt_layout") is True:
        return True
    return bool("prompts_hash" in state and state.get("prompts_hash") != prompts.HASH)


def sort_nested(value: Any) -> Any:
    """Recursively sort mapping keys while preserving sequence order."""
    if isinstance(value, Mapping):
        return {str(k): sort_nested(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [sort_nested(item) for item in value]
    if isinstance(value, tuple):
        return [sort_nested(item) for item in value]
    return value


def order_handoff(
    handoff: Mapping[str, Any],
    *,
    contract_only: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Partition ``handoff`` into deterministic ``(stable, current)`` dicts.

    Inside ``stable``, contract-pure keys appear sorted before scope-bound keys so
    the earliest prefix remains invariant even when only ``current_task`` changes.
    """
    b1: dict[str, Any] = {}
    b2: dict[str, Any] = {}
    rest: dict[str, Any] = {}
    for key, value in sorted(handoff.items(), key=lambda item: str(item[0])):
        key_str = str(key)
        sorted_val = sort_nested(value)
        if key_str in _CONTRACT_PURE_SET:
            b1[key_str] = sorted_val
        elif not contract_only and key_str in _SCOPE_BOUND_SET:
            b2[key_str] = sorted_val
        else:
            rest[key_str] = sorted_val
    stable = {**b1, **b2}
    return stable, rest


def serialize_handoff(handoff: Mapping[str, Any], *, legacy: bool = False) -> str:
    """Serialize handoff into a single stable-first JSON object under ``CURRENT HANDOFF DATA``."""
    if legacy:
        return CURRENT_MARKER + json.dumps(dict(handoff))
    stable, current = order_handoff(handoff)
    ordered = {**stable, **current}
    return CURRENT_MARKER + json.dumps(ordered, separators=(",", ":"))


def build_stage_prompt(instructions: str, handoff: Mapping[str, Any], *, legacy: bool = False) -> str:
    """Assemble Block A instructions plus serialized stable-first handoff data."""
    return instructions + serialize_handoff(handoff, legacy=legacy)


def _decode_json_value(text: str) -> tuple[Any, int] | None:
    """Return decoded first JSON value and its end offset (relative to ``text``), if valid."""
    stripped = text.lstrip()
    if not stripped:
        return None
    lead = len(text) - len(stripped)
    try:
        value, end = json.JSONDecoder().raw_decode(stripped)
    except ValueError:
        return None
    return value, lead + end


def measure_prompt(prompt: str) -> dict[str, int]:
    """Return exact UTF-8 byte breakdown: instruction, stable handoff, changing handoff."""
    prompt_bytes = len(prompt.encode("utf-8"))
    stable_handoff_bytes = 0
    changing_handoff_bytes = 0

    stable_idx = prompt.find(STABLE_MARKER)
    current_idx = prompt.find(CURRENT_MARKER)

    if stable_idx != -1 and (current_idx == -1 or stable_idx < current_idx):
        after_stable = prompt[stable_idx + len(STABLE_MARKER) :]
        decoded_stable = _decode_json_value(after_stable)
        if decoded_stable is not None:
            _, span = decoded_stable
            stable_handoff_bytes = len((STABLE_MARKER + after_stable[:span]).encode("utf-8"))
            search_tail = after_stable[span:]
            sub_current = search_tail.find(CURRENT_MARKER)
            if sub_current != -1:
                after_current = search_tail[sub_current + len(CURRENT_MARKER) :]
                decoded_cur = _decode_json_value(after_current)
                cur_len = decoded_cur[1] if decoded_cur is not None else len(after_current)
                changing_handoff_bytes = len((CURRENT_MARKER + after_current[:cur_len]).encode("utf-8"))
            instruction_bytes = max(0, prompt_bytes - stable_handoff_bytes - changing_handoff_bytes)
            return {
                "prompt_bytes": prompt_bytes,
                "instruction_bytes": instruction_bytes,
                "stable_handoff_bytes": stable_handoff_bytes,
                "changing_handoff_bytes": changing_handoff_bytes,
            }

    if current_idx != -1:
        before_and_marker = prompt[: current_idx + len(CURRENT_MARKER)]
        instruction_bytes = len(before_and_marker.encode("utf-8"))
        after_current = prompt[current_idx + len(CURRENT_MARKER) :]
        stripped_after = after_current.lstrip()
        decoded_cur = _decode_json_value(after_current)
        if decoded_cur is not None and isinstance(decoded_cur[0], dict):
            handoff_dict, _ = decoded_cur
            stable, current = order_handoff(handoff_dict)
            if stable:
                stable_json = json.dumps(stable, separators=(",", ":"))
                stable_prefix = stable_json if not current else stable_json[:-1]
                if stripped_after.startswith(stable_prefix):
                    stable_handoff_bytes = len(stable_prefix.encode("utf-8"))
        changing_handoff_bytes = max(0, prompt_bytes - instruction_bytes - stable_handoff_bytes)
        return {
            "prompt_bytes": prompt_bytes,
            "instruction_bytes": instruction_bytes,
            "stable_handoff_bytes": stable_handoff_bytes,
            "changing_handoff_bytes": changing_handoff_bytes,
        }

    return {
        "prompt_bytes": prompt_bytes,
        "instruction_bytes": prompt_bytes,
        "stable_handoff_bytes": 0,
        "changing_handoff_bytes": 0,
    }


def refresh_metrics(prompt: str, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return ``base`` updated with exact prompt byte splits, token estimate, and top fields."""
    byte_metrics = measure_prompt(prompt)
    return {
        **(dict(base) if base else {}),
        **byte_metrics,
        "estimated_prompt_tokens": (byte_metrics["prompt_bytes"] + 3) // 4,
        "top_handoff_fields": prompt_bound.top_fields(prompt, limit=3),
    }
