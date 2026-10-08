"""Cause identities and novelty admission, independent of controllers and models.

This policy grants no allowance or permission. Its caller supplies authenticated
evidence and still enforces the ordinary retry, scope and independent review gates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import ast
import io
import json
import re
import tokenize

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


CAUSES = frozenset({"setup", "runtime", "implementation", "evidence_gap", "product",
                    "permission", "provider_transient", "unresolved"})
CHANGE_SCHEMA = {
    "type": ["object", "null"], "additionalProperties": False,
    "required": ["hypothesis", "target", "before", "after", "expected_check", "expected_result", "evidence_refs", "question"],
    "properties": {**{key: {"type": "string"} for key in
        ("hypothesis", "target", "before", "after", "expected_check", "expected_result", "question")},
        "evidence_refs": {"type": "array", "items": {"type": "string"}}},
}
INSTRUCTION = (
    "Use recovery_change=null when there is no explicit bounded proposal. "
    "Recovery is not acceptance. Preserve original errors and exact packet artifacts. "
    "For a repeated incident supply recovery_change: a causal hypothesis, one approved relative target, "
    "unique exact before/after source excerpts, the exact failed command as expected_check, its expected_result, "
    "and pinned evidence_refs. Cite validation.output, validation.evidence_hashes keys, or an event:ID "
    "already present in the current Validator's checks; never invent references. Mere new sessions, changed wording, "
    "comments, and a source hash are not progress. "
    "A runner-attested changed input may instead target input:verified_dependency_delivery with the exact "
    "before/after input descriptors from incident packets and the original failed command; this never grants new controls. "
    "A new diagnosis also needs a specific unresolved causal question. "
    "Never weaken tests, change model pins, permissions, limits or the approved contract; "
    "use the existing human request for product/permission decisions. "
    "Automatic source novelty is currently proved for Python and JSON; other grammars remain unproven, "
    "not defective, and need an attested changed input or an explicit scoped operator retry. "
    "Fresh incident-relevant proof and independent acceptance are still required after any repair.")


def normalize(text, wrappers=()):
    """Remove only caller-proven volatile wrappers, never arbitrary paths/hex IDs.

    A wrapper is (exact_value, stable_label). Workspace roots retain their relative
    suffix, so tests/a.py and tests/b.py cannot become the same operation.
    """
    value = str(text)
    for original, label in sorted(wrappers, key=lambda row: len(row[0]), reverse=True):
        if original:
            value = re.sub(re.escape(str(original)) + r"(?=$|[/\s'\"),:;])", lambda _: label, value)
    return " ".join(value.split())


def classify(*, status=None, user_request=None, observation=None):
    """Only typed runner observations classify causes; model error prose does not."""
    kind = (user_request or {}).get("kind")
    if kind in ("permission", "goal_change"):
        return "permission" if kind == "permission" else "product"
    return {"PAUSED_TASK_PREFLIGHT": "setup", "PAUSED_PROVIDER_CAPACITY": "provider_transient",
            "PAUSED_RATE_LIMIT": "provider_transient", "PAUSED_PERMISSION": "permission",
            "PAUSED_PROVIDER_TIMEOUT": "runtime", "PAUSED_STALE_VALIDATION": "evidence_gap",
            "PAUSED_INVALID_OUTPUT": "evidence_gap", "PAUSED_COMPONENT_PLAN": "product"}.get(status) or {
            "runtime_correction": "runtime", "reproduced_implementation_failure": "implementation",
            "missing_proof": "evidence_gap"}.get(observation, "unresolved")


def failure_text(output, command, wrappers=()):
    """Identify unittest failures without volatile traceback presentation.

    Retain exact test IDs, exception messages/data and stack file/function names.
    Full output is separately retained in the packet, not replaced by this key.
    Unknown output formats remain exact apart from proven wrappers.
    """
    if "-m unittest" in command:
        blocks = re.split(r"(?m)^(FAIL|ERROR): (.+)$", output)
        failures = []
        for index in range(1, len(blocks), 3):
            body = blocks[index + 2].lstrip("\n-").split("\n----------------------------------------------------------------------", 1)[0]
            exception = re.search(r"(?m)^[\w.]+(?:Error|Exception|Failure|Interrupt|Exit):.*", body)
            if not exception:
                return normalize(output, wrappers)
            stack = re.findall(r'(?m)^\s*File "([^"\n]+)", line \d+, in ([^\n]+)', body)
            failures.append({"kind": blocks[index], "test": blocks[index + 1],
                "stack": [[normalize(path, wrappers), function] for path, function in stack],
                "exception": normalize(body[exception.start():], wrappers)})
        if failures:
            return util.digest(failures)
    return normalize(output, wrappers)


@dataclass(frozen=True)
class Incident:
    operation: str
    failure: str
    invariant: str
    affected_task: str
    cause: str = "unresolved"

    def __post_init__(self):
        if (self.cause not in CAUSES or any(not isinstance(v, str) or not v.strip()
                for v in (self.operation, self.failure, self.invariant, self.affected_task))):
            raise ValueError("Recovery incident needs an operation, failure, invariant and affected task")

    @property
    def id(self):
        # Classification may improve; it is not permission to rename an incident.
        return util.digest({k: v for k, v in asdict(self).items() if k != "cause"})


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    change_id: str | None = None


def source_identity(path, text):
    """A conservative identity for *attested* source, not a novelty claim itself."""
    if str(path).endswith(".py"):
        try:
            return util.digest(ast.dump(ast.parse(text), include_attributes=False))
        except SyntaxError:
            try:
                tokens = tokenize.generate_tokens(io.StringIO(text).readline)
                return util.digest([(t.type, t.string) for t in tokens if t.type not in
                                    (tokenize.COMMENT, tokenize.NL, tokenize.ENCODING, tokenize.ENDMARKER)])
            except (tokenize.TokenError, IndentationError):
                return None
    if str(path).endswith(".json"):
        try:
            return util.digest(json.loads(text))
        except ValueError:
            return None
    # Unsupported grammars are unknown, not automatically novel on a hash change.
    return None


def bounded_change(change, *, sources, allowed_paths, operations, wrappers=()):
    """Attest the bounds/check separately from supported grammar equivalence."""
    if not isinstance(change, dict):
        return None
    strings = ("hypothesis", "target", "before", "after", "expected_check", "expected_result")
    if any(not isinstance(change.get(k), str) or not change[k].strip() for k in strings):
        return None
    target = change["target"]
    if target not in allowed_paths or target not in sources:
        return None
    command = normalize(change["expected_check"], wrappers)
    if command not in operations:
        return None
    before, after = change["before"], change["after"]
    source = sources[target]
    if not isinstance(source, str) or before not in source or before == after:
        return None
    # A one-occurrence replacement makes the proposed change bounded/unambiguous.
    if source.count(before) != 1:
        return None
    return target, source, source.replace(before, after, 1), command


def change_identity(change, *, sources, allowed_paths, operations, wrappers=()):
    """Exact before/after source bounds plus non-cosmetic supported structure."""
    bounded = bounded_change(change, sources=sources, allowed_paths=allowed_paths, operations=operations, wrappers=wrappers)
    if bounded is None:
        return None
    target, original, proposed, command = bounded
    old = source_identity(target, original)
    new = source_identity(target, proposed)
    if old is None or new is None or old == new:
        return None
    return util.digest({"target": target, "before": old, "after": new, "check": command})


def decide(incident, prior, *, action, change_id=None, changed_input=None,
           expected_check=None, unresolved_question=None, explicit_grant=None,
           workers="stopped", known_correction=False):
    """A repeated incident needs a new experiment, not a fresh session or budget.

    ``prior`` contains dispatch receipts, not model messages. ``changed_input``
    is a caller-attested causal input transition, never a whole-source hash.
    ``explicit_grant`` is an existing, scoped one-use user receipt; this function
    cannot create it. All successful decisions still need fresh acceptance.
    """
    if action not in ("diagnosis", "repair"):
        raise ValueError("Unknown recovery action")
    if workers != "stopped":
        return Decision("hold", "Reconcile owned active or uncertain workers before recovery; do not restart them")
    if incident.cause in ("permission", "product"):
        return Decision("request", "A permission or product decision requires the existing human request route")
    same = [row for row in prior if incident.id in row.get("incident_ids", [])]
    if explicit_grant and not any(row.get("grant_id") == explicit_grant for row in same):
        return Decision(action, "explicit_retry", explicit_grant)
    if known_correction and action == "diagnosis":
        return Decision("hold", "Known cause requires its deterministic correction, not another paid diagnosis")
    if not same:
        if action == "diagnosis" and not unresolved_question:
            return Decision("hold", "Diagnosis needs a specific unresolved causal question")
        return Decision(action, "first_incident", change_id)
    if changed_input and expected_check and not any(row.get("change_id") == changed_input for row in same):
        if action == "diagnosis" and not unresolved_question:
            return Decision("hold", "An observed input correction does not need a paid diagnosis without a causal question")
        return Decision(action, "changed_input", changed_input)
    if change_id and expected_check and not any(row.get("change_id") == change_id for row in same):
        if action == "diagnosis" and not unresolved_question:
            return Decision("hold", "A bounded known repair does not need a paid diagnosis")
        return Decision(action, "bounded_change", change_id)
    return Decision("hold", "No causal progress for the same incident: retain the original errors and attempts; "
                    "supply a bounded source/input correction with a discriminating check, or use an explicit scoped retry")
