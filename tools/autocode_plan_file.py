"""A run's plan as a file a team can review in a pull request (#720).

``autocode plan --export PATH`` writes a concise overview followed by expandable contract and audit
evidence. The contract JSON is the plan: the contract body verbatim (criteria and how each is verified,
the ``test:``/``guard:`` names they cite, the brief's exact-output examples, milestones, limitations,
runner records), the goal hash and where it was exported from. The overview and full review are generated
from the contract so a reviewer can read it; edit the contract JSON, then regenerate the text
(``autocode plan --render``).

Formats 2 and 3 hold that JSON in a fenced block after ``CONTRACT_MARKER``. Format 3 corrects singular
scope wording; earlier formats retain their original text. Format 1's JSON front matter precedes all prose.
Keys, in order:

- ``autocode_plan``: the file format, ``FORMAT``.
- ``goal_hash``: the goal hash of the plan as exported (``goal_hash()``).
- ``revision``, ``approval_status``: the run's contract revision exported, and whether it was approved.
- ``contract``: the contract body, exactly as the run holds it, except for credential-shaped text.

The goal hash is the SHA-256 of the contract body (``autocode_util.digest``). Unlike the run's approval
token (``r<revision>:<contract hash>``, which also binds the run's task id and revision), it names what
the plan says and nothing else, so the same plan has the same goal hash in any run. Reading a file back
gives the body exactly as exported, so the goal hash survives the round trip. The approval token is not
written: a file meant for a pull request must not carry what approves a plan in a run.

A plan file is text that leaves the run directory, so credential-shaped text in the contract is masked
with the shared policy (``autocode_util.redact``, #712) before it is written. The exported
``goal_hash`` stays the run's, so such a file reads as differing from the plan it was exported from.

``read()`` names, rather than ignores, text that no longer matches its canonical contract, so what reviewers
read is what the file says. A change to ``render()`` that changes the text of an existing plan needs a
new ``FORMAT``, or files exported before it would read as stale.

Pure: imports only autocode_util.
"""

from __future__ import annotations

import dataclasses
import json
import re

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

FORMAT = 3
SUPPORTED_FORMATS = (1, 2, FORMAT)
FENCE = "---"
CONTRACT_MARKER = "<!-- autocode-plan-contract -->"
KEYS = ("autocode_plan", "goal_hash", "revision", "approval_status", "contract")
HASH = re.compile(r"[0-9a-f]{64}")
# The body's list sections in reading order, with the heading each is shown under.
SECTIONS = (
    ("deliverables", "Deliverables"),
    ("required_behaviors", "Required behaviors"),
    ("important_failure_cases", "Important failure cases"),
    ("scope_exclusions", "Out of scope"),
    ("constraints", "Constraints"),
    ("permission_boundaries", "Permissions"),
    ("end_to_end_flow", "End-to-end flow"),
    ("technical_approach", "Technical approach"),
)
# Keys rendered in full; any other key of the body is named under "Also in the front matter", including
# brief_acceptance, whose exact-output examples are shown but whose provenance records are not.
RENDERED = frozenset(
    {
        "intended_outcome",
        "intended_user",
        "task_kind",
        "acceptance_criteria",
        "milestones",
        "initial_task",
        "accepted_assumptions",
        "delegated_decisions",
        "open_blocking_questions",
        *(key for key, _ in SECTIONS),
    }
)


@dataclasses.dataclass(frozen=True)
class PlanFile:
    """A plan file read back: its contract body, what it was exported as, and whether its text is current."""

    body: dict
    exported_hash: str
    revision: int
    approval_status: str
    # The first line of the file (1-based) where the text differs from what its front matter renders,
    # or None when the text is current.
    stale_line: int | None
    format: int = 1

    @property
    def goal_hash(self) -> str:
        return goal_hash(self.body)


def goal_hash(body: dict) -> str:
    """The SHA-256 of a contract body: what the plan says, independent of the run and revision."""
    return util.digest(body)


def export(body: dict, *, revision: int, approved: bool, secret_values=()) -> str:
    """The whole file for a run's contract ``body`` at ``revision``; ``secret_values`` are masked too."""
    front = {
        "autocode_plan": FORMAT,
        "goal_hash": goal_hash(body),
        "revision": revision,
        "approval_status": "approved" if approved else "not approved",
        "contract": masked(body, secret_values),
    }
    return document(front)


def masked(value, secret_values=()):
    """``value`` with credential-shaped text, and the known ``secret_values``, masked in every string and key."""
    if isinstance(value, str):
        return util.redact(value, secret_values)
    if isinstance(value, list):
        return [masked(item, secret_values) for item in value]
    if isinstance(value, dict):
        copied = {}
        for key, item in value.items():
            safe = util.redact(key, secret_values)
            if safe in copied:
                raise ValueError("Masking credential-shaped text would merge two keys of the plan; it was not written")
            copied[safe] = masked(item, secret_values)
        return copied
    return value


def document(front: dict) -> str:
    """The versioned review document; legacy documents retain their exact rendering."""
    matter = json.dumps(front, indent=2, ensure_ascii=False)
    prefix, opening, closing, suffix = _layout(front)
    return f"{prefix}{opening}\n{matter}\n{closing}\n{suffix}"


def _layout(front: dict) -> tuple[str, str, str, str]:
    body, revision, status = front["contract"], front["revision"], front["approval_status"]
    if front["autocode_plan"] == 1:
        return "", FENCE, FENCE, render(body, exported_hash=front["goal_hash"], revision=revision, status=status)
    current = goal_hash(body)
    changed = current != front["goal_hash"]
    overview = summary_markdown(
        body,
        revision=revision,
        status="not approved" if changed else status,
        _format_two=front["autocode_plan"] == 2,
    )
    if changed:
        overview += (
            "\n\nThis content differs from the exported revision; its approval does not apply to this edited plan."
        )
    prefix = (
        "# Plan\n\n"
        + overview
        + "\n\n<details>\n<summary>Full contract and audit evidence</summary>\n\n"
        + CONTRACT_MARKER
        + "\n"
    )
    # Canonical strings can themselves contain code fences or HTML. A longer fence keeps the
    # complete JSON inert in a Markdown preview; its strings never become envelope delimiters.
    matter = json.dumps(front, indent=2, ensure_ascii=False)
    fence = "`" * max(3, max((len(run) for run in re.findall(r"`+", matter)), default=0) + 1)
    suffix = (
        "\n## Full plan\n\n"
        + review_markdown(body)
        + "\n\n## Export details\n\n"
        + f"Goal hash {_code(current)}; exported from plan revision {revision} ({status})"
        + (f" with goal hash {_code(front['goal_hash'])}; this plan differs from it" if changed else "")
        + ".\n\nThe contract JSON above is authoritative; this overview and full plan are generated from it. "
        "After editing the JSON, run `autocode plan --render FILE`; use `autocode plan --check FILE` "
        "to check that all displayed text matches. Exporting or editing this file does not approve a plan "
        "in AutoCode.\n\n</details>\n"
    )
    return prefix, fence + "json", fence, suffix


def read(text: str) -> PlanFile:
    """Read either plan-file format; ValueError names what AutoCode cannot read."""
    lines = text.removeprefix("\ufeff").replace("\r\n", "\n").split("\n")
    legacy = lines[0].rstrip() == FENCE
    if legacy:
        start, closing = 0, FENCE
    else:
        try:
            start = lines.index(CONTRACT_MARKER) + 1
        except ValueError:
            raise ValueError(
                "A plan file starts with a readable overview and canonical contract block, or legacy `---` front matter"
            ) from None
        opening = lines[start].rstrip() if start < len(lines) else ""
        if not re.fullmatch(r"`{3,}json", opening):
            raise ValueError("The canonical contract must be a fenced JSON block after its marker")
        closing = opening.removesuffix("json")
    end = next((index for index in range(start + 1, len(lines)) if lines[index].rstrip() == closing), None)
    if end is None:
        raise ValueError(
            "The front matter has no closing `---` line" if legacy else "The contract JSON has no closing fence"
        )
    try:
        front = json.loads("\n".join(lines[start + 1 : end]), object_pairs_hook=_unique, parse_constant=_constant)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"The front matter is not valid JSON at line {error.lineno + start + 1}: {error.msg}. "
            "Keep its quotes, commas and brackets"
        ) from None
    front = _front(front)
    if (front["autocode_plan"] == 1) != legacy:
        raise ValueError("Plan format 1 starts with front matter; later formats have an overview and marked JSON block")
    prefix, _, _, suffix = _layout(front)
    stale = _difference(lines[:start], prefix.split("\n")[:-1], offset=1)
    return PlanFile(
        body=front["contract"],
        exported_hash=front["goal_hash"],
        revision=front["revision"],
        approval_status=front["approval_status"],
        stale_line=stale or _difference(lines[end + 1 :], suffix.split("\n"), offset=end + 2),
        format=front["autocode_plan"],
    )


def rendered(text: str) -> str:
    """``text`` with the text under its front matter regenerated from it; the front matter keeps its values."""
    plan = read(text)
    return document(
        {
            "autocode_plan": plan.format,
            "goal_hash": plan.exported_hash,
            "revision": plan.revision,
            "approval_status": plan.approval_status,
            "contract": plan.body,
        }
    )


def _unique(pairs):
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError("The front matter names " + ", ".join(repeated) + " more than once; keep one of each")
    return dict(pairs)


def _constant(name):
    raise ValueError(f"The front matter holds {name}, which is not a JSON number")


def _front(front) -> dict:
    if not isinstance(front, dict):
        raise ValueError("The front matter must be one JSON object")
    if front.get("autocode_plan") not in SUPPORTED_FORMATS:
        raise ValueError(
            f"This file is plan format {front.get('autocode_plan')!r}; this AutoCode reads plan formats "
            + ", ".join(map(str, SUPPORTED_FORMATS))
        )
    unknown = sorted(set(front) - set(KEYS))
    missing = [key for key in KEYS if key not in front]
    if unknown or missing:
        raise ValueError(
            "The front matter holds exactly "
            + ", ".join(KEYS)
            + (f"; unknown: {', '.join(unknown)}" if unknown else "")
            + (f"; missing: {', '.join(missing)}" if missing else "")
        )
    if not isinstance(front["contract"], dict):
        raise ValueError("The front matter's contract must be a JSON object: the plan's contract body")
    _shape(front["contract"])
    if not isinstance(front["goal_hash"], str) or not HASH.fullmatch(front["goal_hash"]):
        raise ValueError("The front matter's goal_hash must be the 64-character SHA-256 written at export")
    if type(front["revision"]) is not int or front["revision"] < 1:
        raise ValueError("The front matter's revision must be the positive plan revision written at export")
    if front["approval_status"] not in ("approved", "not approved"):
        raise ValueError("The front matter's approval_status must be `approved` or `not approved`")
    return front


def _shape(body: dict) -> None:
    """Refuse a contract whose parts the text is rendered from have the wrong JSON type. Whether the plan
    itself is valid is the run's check, not the file's."""
    expected = {
        **{key: (str, "a string") for key in ("intended_outcome", "intended_user", "task_kind")},
        **{key: (list, "a list of strings") for key, _ in SECTIONS},
        **{
            key: (list, "a list of objects")
            for key in (
                "acceptance_criteria",
                "milestones",
                "accepted_assumptions",
                "delegated_decisions",
                "open_blocking_questions",
            )
        },
        "initial_task": (dict, "an object"),
        "brief_acceptance": (dict, "an object"),
    }
    for key, (kind, described) in expected.items():
        if key not in body:
            continue
        value = body[key]
        rows = value if isinstance(value, list) else []
        if (
            not isinstance(value, kind)
            or (described == "a list of strings" and not all(isinstance(row, str) for row in rows))
            or (described == "a list of objects" and not all(isinstance(row, dict) for row in rows))
        ):
            raise ValueError(f"The front matter's contract.{key} must be {described}")


def _difference(actual: list[str], expected: list[str], *, offset: int) -> int | None:
    """The file line of the first difference; trailing spaces and blank lines at the end never count."""

    def trimmed(lines):
        lines = [line.rstrip() for line in lines]
        while lines and not lines[-1]:
            lines.pop()
        return lines

    actual, expected = trimmed(actual), trimmed(expected)
    for index, (have, want) in enumerate(zip(actual, expected, strict=False)):
        if have != want:
            return offset + index
    return None if len(actual) == len(expected) else offset + min(len(actual), len(expected))


def render(body: dict, *, exported_hash: str, revision: int, status: str) -> str:
    """The plan as Markdown for a reviewer. Deterministic: read() compares a file's text with it."""
    current = goal_hash(body)
    out = ["", "# Plan", ""]
    if body.get("intended_outcome"):
        out += [_text(body["intended_outcome"]), ""]
    out += [
        f"Goal hash {_code(current)}"
        + (
            f", exported from plan revision {revision} ({status})."
            if current == exported_hash
            else f"; plan revision {revision} ({status}) was exported with goal hash {_code(exported_hash)}, "
            "and this plan differs from it."
        ),
        "",
        "The front matter above is the plan; this text is generated from it. After editing the front matter, "
        "run `autocode plan --render FILE` to regenerate this text. `autocode plan --check FILE` names a file "
        "whose text no longer matches its front matter.",
        "",
    ]
    if body.get("intended_user"):
        out += [f"For: {_text(body['intended_user'])}", ""]
    if body.get("task_kind"):
        out += [f"Job type: {body['task_kind']}", ""]
    out += _sections(body)
    out += _criteria(body.get("acceptance_criteria") or [])
    out += _milestones(body.get("milestones") or [])
    out += _first_task(body.get("initial_task"))
    out += _decisions(body.get("accepted_assumptions") or [], body.get("delegated_decisions") or [])
    out += _questions(body.get("open_blocking_questions") or [])
    out += _examples(body.get("brief_acceptance"))
    others = sorted(key for key in body if key not in RENDERED and body[key] not in (None, [], {}, ""))
    if others:
        out += [
            "## Also in the front matter",
            "",
            "Kept exactly as the run holds them: " + ", ".join(_code(key) for key in others) + ".",
            "",
        ]
    # Formatting can join text into a credential shape; the policy masks it here too.
    return util.redact("\n".join(out))


def summary_markdown(body: dict, *, revision: int, status: str, _format_two=False) -> str:
    """A bounded reading lead from canonical facts, never a second model-authored plan.

    The contract has no duration or execution-price estimate. Counts describe scope, not an
    invented estimate; no missing model output or provider receipt is interpreted as zero cost.
    The complete terms remain in review_markdown and the canonical export.
    """
    body = masked(body)

    def brief(value, limit=200):
        text = " ".join(str(value).split())
        return _review_text(text if len(text) <= limit else text[:limit].rstrip() + "…")

    def rows(heading, values, *, maximum=3, limit=200, ordered=False, empty=None):
        out = [f"## {heading}", ""]
        out += [
            (f"{index + 1}. " if ordered else "- ") + brief(value, limit)
            for index, value in enumerate(values[:maximum])
        ]
        if len(values) > maximum:
            out += [f"… {len(values) - maximum} more in the full plan."]
        if not values and empty:
            out += [empty]
        return out + [""]

    out = [f"Plan revision {revision} · {status}", ""]
    if body.get("intended_outcome"):
        out += [brief(body["intended_outcome"], 280), ""]
    approach = body.get("technical_approach") or []
    out += rows("Proposed approach", approach, empty="No technical approach recorded; see the full contract.")
    milestones = body.get("milestones") or []
    stages = [row.get("objective", "") for row in milestones] or body.get("end_to_end_flow") or []
    out += rows("Work stages", stages, maximum=4, limit=160, ordered=True, empty="No work stages recorded.")
    count = len(body.get("acceptance_criteria") or [])
    stages_word = "stage" if len(stages) == 1 and not _format_two else "stages"
    criteria_word = "acceptance criterion" if count == 1 and not _format_two else "acceptance criteria"
    out += [
        "## Effort and cost",
        "",
        f"Planned scope: {len(stages)} {stages_word}; {count} {criteria_word}. "
        "Duration and execution cost are not estimated. Model calls and retries affect actual usage.",
        "",
    ]
    qualifications = (
        ["Constraint: " + str(value) for value in (body.get("constraints") or [])[:1]]
        + ["Out of scope: " + str(value) for value in (body.get("scope_exclusions") or [])[:1]]
        + ["Assumption: " + str(row.get("text", "")) for row in (body.get("accepted_assumptions") or [])[:1]]
    )
    if qualifications:
        out += rows("Boundaries and uncertainty", qualifications, limit=160)
    questions = [row.get("question", "") for row in body.get("open_blocking_questions") or []]
    out += rows(
        "Decisions before approval",
        questions,
        maximum=2,
        limit=160,
        empty="No blocking questions recorded in the plan.",
    )
    out += [
        "This overview is condensed. The full plan below contains all terms, acceptance criteria and source evidence."
    ]
    return util.redact("\n".join(out)).strip()


def review_markdown(body: dict) -> str:
    """Material terms of a canonical plan for a person reviewing it, without file/runner metadata.

    The caller supplies the sealed body under its existing display-token guard. This text is display
    only: clients retain the structured approval fields and never infer authority from Markdown.
    Legacy file exports keep their original rendering, including their provenance instructions.
    """
    body = masked(body)
    out = []
    if body.get("intended_outcome"):
        out += [_review_text(body["intended_outcome"]), ""]
    if body.get("intended_user"):
        out += [f"For: {_review_text(body['intended_user'])}", ""]
    if body.get("task_kind"):
        out += [f"Job type: {_review_text(body['task_kind'])}", ""]
    out += _sections(body, review=True)
    out += _criteria(body.get("acceptance_criteria") or [], review=True)
    out += _milestones(body.get("milestones") or [], review=True)
    out += _first_task(body.get("initial_task"), review=True)
    out += _decisions(body.get("accepted_assumptions") or [], body.get("delegated_decisions") or [], review=True)
    out += _questions(body.get("open_blocking_questions") or [], review=True)
    out += _examples(body.get("brief_acceptance"), review=True)
    delivery = body.get("git_delivery")
    if isinstance(delivery, dict) and delivery.get("kind") == "atomic_commit":
        out += [
            "## Local commit",
            "",
            "Create one local atomic commit: " + _review_text(delivery.get("message", "")),
            "",
        ]
    return util.redact("\n".join(out)).strip()


def _sections(body, *, review=False) -> list[str]:
    out = []
    for key, heading in SECTIONS:
        if body.get(key):
            out += [f"## {heading}", "", *(_item(_review_text(row) if review else row) for row in body[key]), ""]
    return out


def _criteria(rows, *, review=False) -> list[str]:
    if not rows:
        return []
    out = ["## Acceptance criteria", ""]
    for row in rows:
        out += [f"### {row.get('id', '')}", "", (_review_text if review else _text)(row.get("criterion", "")), ""]
        method = str(row.get("verification_method", ""))
        if "\n" in method:
            out += ["Verified by:", "", *_block(method), ""]
        else:
            out += [f"Verified by: {_code(method)}", ""]
        if row.get("human_review"):
            out += ["A person reviews the result before the run completes.", ""]
        elif review:
            out += ["No separate person review is required.", ""]
        if review and row.get("current_behavior"):
            out += ["Current behavior:", "", _review_text(row["current_behavior"]), ""]
        supersedes = [value for value in _list(row.get("supersedes")) if isinstance(value, dict)]
        if review and supersedes:
            out += ["Tests this criterion replaces:", ""]
            out += [_item(_code(value.get("file", "")) + ": " + _code(value.get("test", ""))) for value in supersedes]
            out += [""]
        sources = [source for source in _list(row.get("sources")) if isinstance(source, dict)]
        if sources:
            out += ["Quoted from the request:", ""]
            out += [
                _item(
                    _review_text(source.get("quote", ""))
                    if review
                    else f"{source.get('source_id', '')}: {source.get('quote', '')}"
                )
                for source in sources
            ]
            out += [""]
    return out


def _milestones(rows, *, review=False) -> list[str]:
    if not rows:
        return []
    out = ["## Milestones", ""]
    for row in rows:
        out += [f"### {row.get('id', '')}", "", (_review_text if review else _text)(row.get("objective", "")), ""]
        out += [_item("Criteria: " + _names(row.get("acceptance_criteria")))]
        if "depends_on" in row:
            out += [_item("After: " + _names(row.get("depends_on")))]
        if "affected_paths" in row:
            out += [_item("May change: " + _names(row.get("affected_paths"), code=True))]
        out += [""]
    return out


def _first_task(task, *, review=False) -> list[str]:
    if not isinstance(task, dict):
        return []
    out = ["## First task", "", (_review_text if review else _text)(task.get("objective", "")), ""]
    out += [_item(f"Kind: {task.get('kind', '')}"), _item(f"Milestone: {task.get('milestone_id') or 'none'}")]
    out += [_item("Criteria: " + _names(task.get("acceptance_criteria")))]
    out += [_item("May change: " + _names(task.get("affected_paths"), code=True))]
    for requirement in _list(task.get("requirements")):
        out += [_item("Requirement: " + (_review_text(requirement) if review else str(requirement)))]
    for step in _list(task.get("validation_plan")):
        out += [_item("Check: " + _code(str(step)))]
    return out + [""]


def _decisions(assumed, delegated, *, review=False) -> list[str]:
    rows = [("Assumed", row) for row in assumed] + [("Delegated", row) for row in delegated]
    if not rows:
        return []
    out = ["## Decisions", ""]
    for label, row in rows:
        if review:
            basis = {
                "user_answer": "from your answer",
                "delegated": "under your delegation",
                "agent_proposed": "proposed in this plan",
            }.get(row.get("basis"), str(row.get("basis", "")).replace("_", " "))
            out += [_item(f"{label}" + (f" ({basis})" if basis else "") + ": " + _review_text(row.get("text", "")))]
            continue
        cited = f", answer {row['answer_id']}" if row.get("answer_id") else ""
        out += [_item(f"{label} ({row.get('basis', '')}{cited}): {row.get('text', '')}")]
    return out + [""]


def _questions(rows, *, review=False) -> list[str]:
    if not rows:
        return []
    out = ["## Open questions", "", "The plan cannot be approved while these are open.", ""]
    for row in rows:
        plain = _review_text if review else _text
        out += [f"### {row.get('id', '')}", "", plain(row.get("question", "")), "", f"Why: {plain(row.get('why', ''))}"]
        out += [""] + [_item(plain(option)) for option in _list(row.get("options"))]
        if row.get("proposed_default"):
            out += [_item("Proposed default: " + plain(row["proposed_default"]))]
        out += [""]
    return out


def _examples(acceptance, *, review=False) -> list[str]:
    """The brief's exact-output examples the runner checks on the delivered program."""
    manifest = acceptance.get("manifest") if isinstance(acceptance, dict) else None
    observations = manifest.get("observations") if isinstance(manifest, dict) else None
    rows = [row for row in _list(observations) if isinstance(row, dict)]
    out = []
    for row in rows:
        declaration = row.get("declaration")
        if not isinstance(declaration, dict):
            continue
        if review:
            argv = [str(declaration.get("program", "")), *map(str, _list(declaration.get("observe_argv")))]
            # Inline code collapses real newlines; JSON quoting keeps every whitespace character
            # visible and distinguishes one spaced argument from several arguments.
            out += [_item("Command arguments: " + _code(json.dumps(argv, ensure_ascii=False)))]
            out += [
                _item("Exact output: " + _code(json.dumps(str(declaration.get("literal", "")), ensure_ascii=False)))
            ]
            continue
        command = " ".join([str(declaration.get("program", "")), *map(str, _list(declaration.get("observe_argv")))])
        out += [_item(f"{_code(command.strip())} prints {_code(str(declaration.get('literal', '')))}")]
    if not out:
        return []
    label = "The delivered program must produce:" if review else "Checked on the delivered program:"
    return ["## Exact output from the request", "", label, "", *out, ""]


def _names(values, *, code=False) -> str:
    values = [str(value) for value in _list(values)]
    if not values:
        return "none"
    return ", ".join(_code(value) if code else value for value in values)


def _list(value) -> list:
    """A list as itself; a single value as a list of one; nothing as an empty list."""
    if isinstance(value, list):
        return value
    return [] if value in (None, "", {}) else [value]


def _text(value) -> str:
    return str(value).strip()


def _review_text(value) -> str:
    """Render canonical plain text literally without changing the versioned plan file."""
    return re.sub(r"([\\`*{}\[\]()#+.!_>~|<>-])", r"\\\1", _text(value))


def _item(value) -> str:
    """A list item; a later line of the value is indented under it."""
    return "- " + "\n  ".join(str(value).strip().split("\n"))


def _code(value: str) -> str:
    """An inline code span that holds ``value`` exactly, whatever backticks it contains."""
    value = str(value).replace("\n", " ")
    fence = "`" * (max((len(run) for run in re.findall(r"`+", value)), default=0) + 1)
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def _block(value: str) -> list[str]:
    fence = "`" * max(3, max((len(run) for run in re.findall(r"`+", value)), default=0) + 1)
    return [fence, *value.rstrip("\n").split("\n"), fence]
