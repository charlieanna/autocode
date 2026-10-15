"""A run's plan as a file a team can review in a pull request (#720).

``autocode plan --export PATH`` writes the current contract of a saved run as Markdown with a front
matter. The front matter is the plan: the contract body verbatim (criteria and how each is verified,
the ``test:``/``guard:`` names they cite, the brief's exact-output examples, milestones, limitations,
runner records), the goal hash and where it was exported from. The text under it is generated from
the front matter so a reviewer can read the plan; edit the front matter, then regenerate the text
(``autocode plan --render``).

The front matter is JSON between two ``---`` lines. JSON is YAML 1.2, so a YAML front-matter reader
reads it, and AutoCode reads and writes it without a YAML library (its only dependency is psutil).
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
with the shared policy (``autocode_util.redact``, #712) before it is written. The front matter's
``goal_hash`` stays the run's, so such a file reads as differing from the plan it was exported from.

``read()`` names, rather than ignores, text that no longer matches its front matter, so what reviewers
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

FORMAT = 1
FENCE = "---"
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
    """The front matter, then the text generated from it."""
    matter = json.dumps(front, indent=2, ensure_ascii=False)
    return f"{FENCE}\n{matter}\n{FENCE}\n" + render(
        front["contract"], exported_hash=front["goal_hash"], revision=front["revision"], status=front["approval_status"]
    )


def read(text: str) -> PlanFile:
    """Read a plan file; ValueError names what is wrong with one AutoCode cannot read."""
    lines = text.removeprefix("\ufeff").replace("\r\n", "\n").split("\n")
    if lines[0].rstrip() != FENCE:
        raise ValueError(
            "A plan file starts with its front matter: a `---` line, the plan as JSON, then another `---` line"
        )
    end = next((index for index in range(1, len(lines)) if lines[index].rstrip() == FENCE), None)
    if end is None:
        raise ValueError("The front matter has no closing `---` line")
    try:
        front = json.loads("\n".join(lines[1:end]), object_pairs_hook=_unique, parse_constant=_constant)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"The front matter is not valid JSON at line {error.lineno + 1}: {error.msg}. AutoCode writes and reads "
            "it as JSON (which YAML readers also accept): keep its quotes, commas and brackets"
        ) from None
    front = _front(front)
    expected = render(
        front["contract"], exported_hash=front["goal_hash"], revision=front["revision"], status=front["approval_status"]
    ).split("\n")
    return PlanFile(
        body=front["contract"],
        exported_hash=front["goal_hash"],
        revision=front["revision"],
        approval_status=front["approval_status"],
        stale_line=_difference(lines[end + 1 :], expected, offset=end + 2),
    )


def rendered(text: str) -> str:
    """``text`` with the text under its front matter regenerated from it; the front matter keeps its values."""
    plan = read(text)
    return document(
        {
            "autocode_plan": FORMAT,
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
    if front.get("autocode_plan") != FORMAT:
        raise ValueError(
            f"This file is plan format {front.get('autocode_plan')!r}; this AutoCode reads plan format {FORMAT}"
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
    for key, heading in SECTIONS:
        if body.get(key):
            out += [f"## {heading}", "", *(_item(row) for row in body[key]), ""]
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


def _criteria(rows) -> list[str]:
    if not rows:
        return []
    out = ["## Acceptance criteria", ""]
    for row in rows:
        out += [f"### {row.get('id', '')}", "", _text(row.get("criterion", "")), ""]
        method = str(row.get("verification_method", ""))
        if "\n" in method:
            out += ["Verified by:", "", *_block(method), ""]
        else:
            out += [f"Verified by: {_code(method)}", ""]
        if row.get("human_review"):
            out += ["A person reviews the result before the run completes.", ""]
        sources = [source for source in _list(row.get("sources")) if isinstance(source, dict)]
        if sources:
            out += ["Quoted from the request:", ""]
            out += [_item(f"{source.get('source_id', '')}: {source.get('quote', '')}") for source in sources]
            out += [""]
    return out


def _milestones(rows) -> list[str]:
    if not rows:
        return []
    out = ["## Milestones", ""]
    for row in rows:
        out += [f"### {row.get('id', '')}", "", _text(row.get("objective", "")), ""]
        out += [_item("Criteria: " + _names(row.get("acceptance_criteria")))]
        if "depends_on" in row:
            out += [_item("After: " + _names(row.get("depends_on")))]
        if "affected_paths" in row:
            out += [_item("May change: " + _names(row.get("affected_paths"), code=True))]
        out += [""]
    return out


def _first_task(task) -> list[str]:
    if not isinstance(task, dict):
        return []
    out = ["## First task", "", _text(task.get("objective", "")), ""]
    out += [_item(f"Kind: {task.get('kind', '')}"), _item(f"Milestone: {task.get('milestone_id') or 'none'}")]
    out += [_item("Criteria: " + _names(task.get("acceptance_criteria")))]
    out += [_item("May change: " + _names(task.get("affected_paths"), code=True))]
    for requirement in _list(task.get("requirements")):
        out += [_item("Requirement: " + str(requirement))]
    for step in _list(task.get("validation_plan")):
        out += [_item("Check: " + _code(str(step)))]
    return out + [""]


def _decisions(assumed, delegated) -> list[str]:
    rows = [("Assumed", row) for row in assumed] + [("Delegated", row) for row in delegated]
    if not rows:
        return []
    out = ["## Decisions", ""]
    for label, row in rows:
        cited = f", answer {row['answer_id']}" if row.get("answer_id") else ""
        out += [_item(f"{label} ({row.get('basis', '')}{cited}): {row.get('text', '')}")]
    return out + [""]


def _questions(rows) -> list[str]:
    if not rows:
        return []
    out = ["## Open questions", "", "The plan cannot be approved while these are open.", ""]
    for row in rows:
        out += [f"### {row.get('id', '')}", "", _text(row.get("question", "")), "", f"Why: {_text(row.get('why', ''))}"]
        out += [""] + [_item(str(option)) for option in _list(row.get("options"))]
        if row.get("proposed_default"):
            out += [_item("Proposed default: " + str(row["proposed_default"]))]
        out += [""]
    return out


def _examples(acceptance) -> list[str]:
    """The brief's exact-output examples the runner checks on the delivered program."""
    manifest = acceptance.get("manifest") if isinstance(acceptance, dict) else None
    observations = manifest.get("observations") if isinstance(manifest, dict) else None
    rows = [row for row in _list(observations) if isinstance(row, dict)]
    out = []
    for row in rows:
        declaration = row.get("declaration")
        if not isinstance(declaration, dict):
            continue
        command = " ".join([str(declaration.get("program", "")), *map(str, _list(declaration.get("observe_argv")))])
        out += [_item(f"{_code(command.strip())} prints {_code(str(declaration.get('literal', '')))}")]
    if not out:
        return []
    return ["## Exact output from the request", "", "Checked on the delivered program:", "", *out, ""]


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
