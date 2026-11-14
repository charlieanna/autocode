"""Tests described in plain English, and the runner's link from each one to the test that proves it.

Two places write them. An earlier runtime's bug-fix Investigator wrote ``test_cases``
(id, given, when, then) in its diagnosis, which a run saved past that stage still holds. A small feature's
Planner writes acceptance criteria as concrete examples and marks the ones a
test proves with a verification_method of ``test: test_<id>_...``, or of
``guard: test_<id>_...`` for behavior that already works and must keep working
(``contract_cases``; a guard is a preserve case, as in a bug diagnosis), or of the
Go test name the user asked for (autocode_native_test_names). Either way a person approves English, the Builder writes
one test per case named after its id, and the runner's proof
(autocode_regression) checks by name, with no model, that each case has a
test that passes with the change and did not before (``match_cases``).

In a plan with several milestones, a case is due once its milestone is: the
proof at a milestone checkpoint covers the current milestone (every member of a
parallel batch) and the milestones already accepted, never a later one
(``in_scope``). Criteria that belong to no milestone are due once every
milestone is, that is at final completion.

Pure functions over saved state. Imports nothing from the runner; milestone
progress is read from ``milestone_progress`` as autocode_milestones saves it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import re

try:
    from . import autocode_progressive_state as progressive_state
except ImportError:
    import autocode_progressive_state as progressive_state
from typing import Any

# A verification method may name the test and then say how to run it ("test: TestFoo (go test ...)").
# Go subtests keep hyphens and dots (Run_-_creates_..., Package.Case_Test). Stop before a parenthetical command.
_NAMED_TEST = re.compile(r"(test_[A-Za-z0-9_]+|Test[A-Za-z0-9_]+(?:/[A-Za-z0-9_.-]+)*)")


def _named_test(rest: str) -> str:
    match = _NAMED_TEST.match(str(rest or "").strip())
    return match.group(1) if match else ""


MARK = "test:"
# Behavior that already works and must keep working: its test passes before and after the change
# (a "preserve" case, autocode_regression.check_cases). Live review-then-fix plans (2026-09-29) had
# only "test:" for "a timeout before execution is still retried", which the reviewed change never
# broke, so the proof could not pass it: one run asked the user, one was sent back to rework.
GUARD_MARK = "guard:"


def proof(method) -> str:
    """A verification method with a test:/guard: mark reduced to the test it names.

    Whether a named test must fail first (test:) or pass before and after (guard:) is how the
    runner proves a criterion, not what the user agreed to. A planning revision may switch it
    without a user answer (autocode_goals.revision_guard); the proof still refuses a mislabeled
    one. A live review-then-fix plan (2026-09-29) could not move "a timeout before execution is
    still retried" from test: to guard:, as its Plan Reviewer asked, without asking the user.
    """
    text = str(method or "").strip()
    found = mark(text)
    return "tested: " + text[len(found) :].strip() if found else text


def mark(method) -> str | None:
    """MARK or GUARD_MARK when a verification method names a test the runner proves, else None."""
    lowered = str(method or "").strip().lower()
    return GUARD_MARK if lowered.startswith(GUARD_MARK) else MARK if lowered.startswith(MARK) else None


# "guard: run `ruby test/pager_test.rb`", then anything: a guard that is a command (autocode_command_guards).
# The word "run" tells it from a test's name or title, which a plan may also write in backticks.
_GUARD_COMMAND = re.compile(r"runs?\s+`([^`\n]+)`(?:\s.*)?", re.DOTALL | re.IGNORECASE)


def guard_command(method) -> str | None:
    """The command of a guard written as "run" and a command in backticks, for a project whose runner names
    no test (autocode_command_guards), or None. Such a criterion names no test, so it is not one of ``plan_cases``."""
    text = str(method or "").strip()
    if mark(text) != GUARD_MARK:
        return None
    found = _GUARD_COMMAND.fullmatch(text[len(GUARD_MARK) :].strip())
    return found.group(1).strip() or None if found else None


def design_only(state: RunState) -> bool:
    """A job recognized as design (autocode_workflows) delivers documents, never code or tests. A live run
    asked to "deliver only a design ... no application code" planned every criterion as a test and added
    tests/ (architecture-two-services, 2026-09-29); its criteria are checked by the Validator instead."""
    return (state.get("workflow") or {}).get("kind") == "design"


def declared_test_name(text: str) -> str | None:
    """Keep a declared identifier when an annotation follows a clear separator.

    The annotation explains the test; it must not silently replace its identity
    with the criterion ID. Placeholders and free-form methods keep the existing
    ID-based convention. Native Go names are valid explicit identifiers too.
    """
    match = re.fullmatch(
        r"(test_[A-Za-z0-9_]+|Test[A-Z0-9_][A-Za-z0-9_]*(?:/[A-Za-z0-9_.-]+)*)"
        r"(?:\s+(?:[—–-]\s+.+|\(.+\)))?",
        text,
        re.DOTALL,
    )
    return match[1] if match else None


# The runners whose tests need no test_ prefix. A Rust test is any function, so a plan names one the
# project already has as it is: "guard: exact_multiple", or by its path "guard: tests::exact_multiple".
NATIVE_RUNNERS = frozenset({"cargo"})
_NATIVE_NAME = re.compile(
    r"((?:[A-Za-z_][A-Za-z0-9_]*::)*[a-z_][a-z0-9_]*)(?:\s+(?:[—–-]\s+.+|\(.+\)))?",
    re.DOTALL,
)


def native_test_name(text: str) -> str | None:
    """A declared identifier that is no test_*/Test* name: one such a runner may know a test by.

    A lone word ("guard: manual") is prose, so the name needs an underscore or a path."""
    match = _NATIVE_NAME.fullmatch(text)
    return match[1] if match and ("_" in match[1] or "::" in match[1]) else None


def bound(cases: list[dict], framework) -> list[dict]:
    """``cases`` as ``framework``'s runner binds them: a case's ``native_name`` is its ``test_name`` where
    tests need no test_ prefix (NATIVE_RUNNERS). Every other runner keeps the criterion-id convention."""
    if framework not in NATIVE_RUNNERS:
        return list(cases)
    return [
        {**case, "test_name": case["native_name"]} if case.get("native_name") and not case.get("test_name") else case
        for case in cases
    ]


# The runners whose tests are named by a free-text title: test('counts a partial last page', ...). A plan
# names a test the project already has by that title, "guard: counts a partial last page", as the test file
# spells it. On real runs of a refactor and a bug fix in a node:test project, such guards were looked for as
# test_ac2_..., found nowhere, and each run copied its existing tests under those names after a failed proof.
#
# An RSpec example is named the same way, by what it says: `it "counts a partial last page"` in
# `describe ".page_count"` of `RSpec.describe Pager` has the id FILE::Pager.page_count counts a partial last
# page, RSpec's full description, and a plan names it by that or by its last words, which are the example's
# own. On real runs in an RSpec project (2026-11-13) a bug fix copied its two existing examples under
# test_ac2_... names after two failed proofs, as the node:test runs had.
TITLE_RUNNERS = frozenset({"node", "vitest", "jest", "rspec"})
_ANNOTATION = re.compile(r"\s+(?:[—–-]\s+.+|\(.+\))", re.DOTALL)
# FILE.rb::FULL DESCRIPTION (autocode_rspec_tests): the descriptions of the example's groups, then its own.
_RUBY_ID = re.compile(r"^[^:]*\.rb::(.+)$", re.DOTALL)


def _last_words(description: str) -> tuple[str, ...]:
    """An RSpec full description, then each shorter run of its last words: the id does not say where the
    groups' descriptions end and the example's own begins."""
    words = description.split(" ")
    return tuple(" ".join(words[at:]) for at in range(len(words)))


def _titles(test_id: str) -> tuple[str, ...]:
    """The titles a runner's id ends with: FILE::suite::title (node:test), FILE::suite > title (Vitest),
    FILE::groups title (RSpec)."""
    if found := _RUBY_ID.match(test_id):
        return _last_words(found.group(1))
    last = test_id.rsplit("::", 1)[-1]
    return (last, last.rsplit(" > ", 1)[-1])


def _unquoted(text: str) -> str:
    """``"a title" (note)`` as ``a title (note)``: a declared title may be quoted."""
    if text[:1] in ('"', "'", "`"):
        end = text.find(text[0], 1)
        if end > 0 and (not text[end + 1 :] or _ANNOTATION.fullmatch(text[end + 1 :])):
            return text[1:end] + text[end + 1 :]
    return text


def _declares(declared: str, title: str) -> bool:
    """Whether a declaration is exactly ``title``, alone or before an annotation ("title (tests/a.test.js)")."""
    return any(
        text == title or (text.startswith(title) and _ANNOTATION.fullmatch(text[len(title) :]) is not None)
        for text in (declared, _unquoted(declared))
    )


def titled(cases: list[dict], framework, test_ids) -> list[dict]:
    """``cases`` with a declared title bound as the ``test_name`` where ``framework`` names tests by title
    (TITLE_RUNNERS) and one of ``test_ids`` has exactly that title: the longest such title. A declaration
    that is no test's title is prose, and its case keeps the criterion-id convention."""
    if framework not in TITLE_RUNNERS:
        return list(cases)
    known = {title for test in test_ids for title in _titles(test)}
    result = []
    for case in cases:
        declared = case.get("title")
        found = [] if case.get("test_name") or not declared else [t for t in known if _declares(declared, t)]
        result.append({**case, "test_name": max(found, key=len)} if found else case)
    return result


def guard_names(cases: list[dict], framework) -> list[str]:
    """What the guards among ``cases`` name, for the proof to look for in the whole suite: each exact test
    name and, where ``framework`` names tests by title (TITLE_RUNNERS), each declared title.

    A title is bound to a test only once the suite has run (``titled``), so a plan whose guards were all
    titles named no guard here. With a new test file beside them the proof then never ran the suite on the
    original code, found the existing tests nowhere, and failed each guard as "no test named test_acN_"."""
    return sorted(
        {
            name
            for case in bound(cases, framework)
            if case.get("kind") == "preserve"
            for name in (case.get("test_name") or (framework in TITLE_RUNNERS and case.get("title")),)
            if name
        }
    )


def contract_cases(state: RunState, *, all_due: bool = False) -> list[dict]:
    """The approved plan's criteria marked ``test:`` or ``guard:`` that are due now (every one with ``all_due``,
    as at final completion), as ``plan_cases``. None in a design-only job: nothing there is proven by a test the
    Builder writes."""
    if design_only(state):
        return []
    due = None if all_due else in_scope(state)
    return [
        case for case in plan_cases((state.get("goal_contract") or {}).get("body")) if due is None or case["id"] in due
    ]


def plan_cases(body) -> list[dict]:
    """A plan body's criteria marked ``test:`` or ``guard:``, as cases: id, text, the test it names when that
    is a test name (else a ``native_name``, see ``bound``, and the declaration itself as a ``title``, see
    ``titled``), and kind "preserve" for a guard. A guard that is a command names no test and is left out
    (``guard_command``)."""
    cases = []
    for row in (body.get("acceptance_criteria") if isinstance(body, dict) else None) or []:
        found = mark(row.get("verification_method")) if isinstance(row, dict) else None
        if found and row.get("id") and not guard_command(row["verification_method"]):
            declared = str(row["verification_method"]).strip()[len(found) :].strip()
            test_name = declared_test_name(declared)
            native = None if test_name else native_test_name(declared)
            cases.append(
                {
                    "id": row["id"],
                    "text": row.get("criterion", ""),
                    **({"test_name": test_name} if test_name else {}),
                    **({"native_name": native} if native else {}),
                    **({"title": declared} if declared and not test_name else {}),
                    **({"kind": "preserve"} if found == GUARD_MARK else {}),
                }
            )
    return cases


def current_diagnosis(state: RunState) -> dict:
    """Retain historical diagnoses, but only use this turn's investigation as instructions."""
    found = state.get("investigation") or {}
    turns = state.get("turns") or []
    if not turns:
        return found
    boundary = turns[-1].get("stage_index")
    stages = state.get("stages") or []
    output = found.get("output")
    if type(boundary) is not int or not 0 <= boundary <= len(stages) or not output:
        return {}
    if any(row.get("output") == output for row in stages[:boundary]):
        return {}
    candidates = stages[boundary:] + [state.get("active_stage") or {}]
    if any(
        (row.get("original_stage") or row.get("stage")) == "investigate_bug"
        and row.get("output") == output
        and not row.get("rejected")
        for row in candidates
    ):
        return found
    return {}


def diagnosis_cases(state: RunState) -> list[dict]:
    """A reproduced bug's English test cases (an earlier runtime's Investigator), or [] (bugs planned without an
    investigation, older runs)."""
    found = current_diagnosis(state)
    return list(found.get("test_cases") or []) if found.get("outcome") == "reproduced" else []


def diagnosis_test_names(state: RunState) -> dict[str, str]:
    """Unique approved names already identifying a diagnosis case, without merging obligations.

    Bind by the existing identifier matcher, never by similarity of English examples.
    Conflicting kinds, combined identifiers and multiple names retain the legacy rule.
    """
    if (state.get("goal_contract") or {}).get("approval_status") != "approved" or not bugfix(state):
        return {}
    cases = diagnosis_cases(state)
    candidates: dict[str, set[str]] = {case["id"]: set() for case in cases}
    ambiguous: set[str] = set()
    for criterion in contract_cases(state, all_due=True):
        name = criterion.get("test_name")
        if not name:
            continue
        matched = match_cases(cases, [name])
        owners = [case for case in cases if matched[case["id"]]]
        if len(owners) != 1:
            ambiguous.update(case["id"] for case in owners)
            continue
        case = owners[0]
        if (case.get("kind") == "preserve") != (criterion.get("kind") == "preserve"):
            ambiguous.add(case["id"])
        else:
            candidates[case["id"]].add(name)
    return {cid: next(iter(names)) for cid, names in candidates.items() if len(names) == 1 and cid not in ambiguous}


def proof_cases(state: RunState, *, all_due: bool = False) -> list[dict]:
    """The cases the runner's regression proof (autocode_regression) requires a test for: a reproduced bug's
    diagnosis, else the plan's ``contract_cases``. The plan approval summary (autocode_approval_view) states
    the same cases with ``all_due``. Explicit approved test names can scope diagnosis cases to a checkpoint;
    incomplete mappings preserve the full requirement."""
    cases = diagnosis_cases(state)
    if not cases:
        return contract_cases(state, all_due=all_due)
    due = None if all_due else in_scope(state)
    if due is None or not due:
        return cases
    # Only the approved, explicit test names establish a case's milestone.
    # Legacy, incomplete or ambiguous mappings keep the whole proof required.
    bindings: dict[str, set[str]] = {case["id"]: set() for case in cases}
    for criterion in contract_cases(state, all_due=True):
        name = criterion.get("test_name")
        if not name:
            continue
        matched = match_cases(cases, [name], framework="go")
        owners = [case for case in cases if matched[case["id"]]]
        if len(owners) != 1:
            if owners:
                return cases
            continue
        case = owners[0]
        if (case.get("kind") == "preserve") != (criterion.get("kind") == "preserve"):
            return cases
        bindings[case["id"]].add(criterion["id"])
    if any(not owners for owners in bindings.values()):
        return cases
    return [case for case in cases if bindings[case["id"]] & due]


def in_scope(state: RunState) -> set[str] | None:
    """Criterion ids due at this point of a multi-milestone plan, or None when all are due.

    Due: the criteria of the current task's milestone (or batch members) and of milestones
    already accepted under this contract. All are due in a one-milestone plan, when the
    current task names no milestone, and once every milestone is current or accepted.
    """
    contract = state.get("goal_contract") or {}
    progressive = progressive_state.context(state)
    if progressive:
        # Contribution checks are due, but do not prove the broader product case.
        # Findings stay in the product ledger; this only selects contract tests.
        criteria = (contract.get("body") or {}).get("acceptance_criteria") or []
        outstanding = set(progressive.get("outstanding_criteria") or [])
        fully_due = {
            criterion
            for check in progressive.get("required_checks") or []
            if check.get("relation") == "fully_verify"
            for criterion in check.get("criterion_ids") or []
        }
        return {row["id"] for row in criteria if row.get("id") not in outstanding or row.get("id") in fully_due}
    milestones = (contract.get("body") or {}).get("milestones") or []
    task = state.get("current_task") or {}
    current = set(task.get("milestone_ids") or []) or ({task["milestone_id"]} if task.get("milestone_id") else set())
    if len(milestones) <= 1 or not current:
        return None
    accepted = {
        mid
        for row in (state.get("milestone_progress") or {}).values()
        if row.get("accepted") and row.get("contract_hash") == contract.get("hash")
        for mid in row.get("milestone_ids") or [row.get("id")]
    }
    reached = current | accepted
    if {milestone.get("id") for milestone in milestones} <= reached:
        return None
    return {
        criterion
        for milestone in milestones
        if milestone.get("id") in reached
        for criterion in milestone.get("acceptance_criteria") or []
    }


def case_text(case: dict) -> str:
    if "text" in case:
        return f"{case['id']}: {case['text']}"
    return f"{case['id']}: Given {case['given']}; when {case['when']}; then {case['then']}"


def case_test_name(case_id: str, test_name: str | None = None) -> str:
    return test_name or f"test_{case_id.lower()}_<what it checks>"


def _words(name: str) -> list[str]:
    """Lowercase words of an identifier: test_t1_x, TestT1X and test-t1-x all give test, t1, x."""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [word for word in re.split(r"[^a-z0-9]+", name.lower()) if word]


def _go_words(name: str) -> list[str]:
    """Go aliases retain whole words and numeric groups across case separators.

    To30 and to_30 are the same alias; To300 and version21 versus version2_1
    remain different. Other runners keep their exact identifier comparison.
    """
    return [part for word in _words(name) for part in re.findall(r"[a-z]+|[0-9]+", word)]


# FILE.rs::module::name, or a documentation test's "Doc-tests CRATE::FILE - item (line N)"
# (autocode_cargo_tests): a Rust test need not be named test_*, while its tests/ directory is.
_RUST_ID = re.compile(
    r"^(?:[^:]*\.rs::(?:.*::)?([A-Za-z_][A-Za-z0-9_]*)|Doc-tests \S+::\S+ - (?:\S*::)?([A-Za-z_][A-Za-z0-9_]*) \(line \d+\).*)$"
)


# FILE.js::suite::title (node:test) or FILE.ts::suite > title (Vitest): the file is never the test's name.
_JS_ID = re.compile(r"^[^:]*\.(?:[cm]?[jt]s|[jt]sx)::(.+)$", re.DOTALL)
_NAME = r"[A-Za-z_][A-Za-z0-9_]*"


def _test_function(test_id: str) -> str:
    """The test's own name inside a runner's id (module.Class.test_x, path::Class::test_x[param], ...).

    A JavaScript test is named by its title, which is free text: ``tests/pager.test.js::counts a partial
    last page`` is "counts a partial last page", and a title that begins with a ``test_`` name
    (``test_c1_adds two numbers``) is that name. Read from the whole id, every such test was "test",
    from its file's ``.test.js``: any changed file that says ``test(`` then named every test of the
    suite, so none of them could guard a change (live run, 2026-11-13). An RSpec example is named by its
    full description in the same way; read from the whole id it was that description's last word.
    """
    if found := _RUST_ID.match(test_id):
        return found.group(1) or found.group(2)
    if (script := _JS_ID.match(test_id)) or _RUBY_ID.match(test_id):
        title = _titles(test_id)[1 if script else 0]
        if re.fullmatch(_NAME, title):
            return title
        named = re.findall(r"(?<![A-Za-z0-9_])[Tt]est_[A-Za-z0-9_]+", title)
        return named[-1] if named else title
    names = re.findall(_NAME, re.sub(r"\[.*\]$", "", test_id))
    tests = [name for name in names if name.lower().startswith("test")]
    return tests[-1] if tests else (names[-1] if names else "")


def function_name(test_id: str) -> str:
    """The test's own function name inside a runner's id."""
    return _test_function(test_id)


def named_in(test_id: str, text: str) -> bool:
    """Whether ``text`` (a file's source) names the test's own function, or spells its free-text title."""
    name = _test_function(test_id)
    if not name:
        return False
    if not re.fullmatch(_NAME, name):
        if _RUBY_ID.match(test_id):
            # A spec file spells an example's own description, never RSpec's full one: its last words, quoted.
            return any(quote + words + quote in text for words in _last_words(name) for quote in "\"'")
        return name in text
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text) is not None


def match_cases(cases: list[dict], test_ids: list[str], *, framework=None) -> dict[str, list[str]]:
    """Match an approved exact test name, or a diagnosis case's id-based test name."""
    matched = {}
    for case in titled(bound(cases, framework), framework, test_ids):
        if case.get("test_name"):
            matched[case["id"]] = [
                test
                for test in test_ids
                if (
                    _test_function(test) == case["test_name"]
                    or test.rsplit("::", 1)[-1] == case["test_name"]
                    or (framework in NATIVE_RUNNERS and test.endswith("::" + case["test_name"]))
                    or (framework in TITLE_RUNNERS and case["test_name"] in _titles(test))
                    or (
                        framework == "go"
                        and case["test_name"].startswith("test_")
                        and re.match(r"^Test[A-Z0-9_]", _test_function(test))
                        and _go_words(_test_function(test)) == _go_words(case["test_name"])
                    )
                )
            ]
        else:
            # The documented lowercase spelling keeps M1A as m1a. Retain the
            # CamelCase spelling too, without accepting prefixes of either form.
            wants = (_words(case["id"]), _words(case["id"].lower()))
            matched[case["id"]] = [
                test
                for test in test_ids
                if any(
                    _words(_test_function(test))[i : i + len(want)] == want
                    for want in wants
                    for i in range(len(_words(_test_function(test))))
                )
            ]
    return matched


# A probe that is a bare `scheme:reference`: an event or tool ID rather than a command (#1769).
CITATION_PROBE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*:\S+$")

# The deliberately constructed counterexample a probe must tell its claim from (#1769): the smallest
# edit to one repository file that makes the claim false. The runner makes that edit in a scratch
# copy and re-runs the probe, which must then fail. Not in a report's required list, so reports
# saved before it still validate; a probe without one is refused where the job asks for it.
COUNTEREXAMPLE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["file", "find", "replace"],
    "properties": {"file": {"type": "string"}, "find": {"type": "string"}, "replace": {"type": "string"}},
}


def apply_counterexample(text: str, counterexample: dict) -> str:
    """``text`` with the counterexample's ``find`` replaced by its ``replace``; ``find`` must occur once."""
    find = counterexample.get("find") or ""
    found = text.count(find) if find else 0
    if found != 1:
        raise ValueError(
            f"the counterexample's find text occurs {found} times in {counterexample.get('file')!r}; "
            "it must be copied exactly from that file and occur once"
        )
    return text.replace(find, counterexample.get("replace") or "", 1)


def _counterexample_error(row: dict, what: str, key: str) -> str:
    """Why a probed row's counterexample cannot be run, or "" when it can."""
    counter = row.get("counterexample") or {}
    if not isinstance(counter, dict) or not str(counter.get("file") or "").strip() or not counter.get("find"):
        return (
            f"A probed {what} needs a counterexample: the smallest edit to one repository file "
            f"(file, find, replace) that makes it false, so the runner can check that the probe fails "
            f"there. A probe that passes whatever the code says shows nothing: {row.get(key)!r}"
        )
    if counter.get("find") == counter.get("replace"):
        return f"A {what}'s counterexample must change its file; find and replace are the same: {row.get(key)!r}"
    return ""


# A probe that runs a test written only in the stage's own scratch workspace finds no such test in the
# runner's copy and fails there; in a live etcd run both repairs then dropped the reproduction
# (docs/bugs/2026-11-02-retry-loops.md).
PROBE_COPY_NOTE = (
    " The runner's copy holds the repository's files only, not files you created or edited in your own "
    "scratch workspace: a probe that runs a new test must write that test itself (for example with a "
    "heredoc in the probe command) or run a test the repository already has."
)


def run_probes(
    rows: list[dict], run_probe, *, what: str = "claim", key: str = "claim", run_counterexample=None
) -> list[dict]:
    """Run every row's ``probe`` (``run_probe(command)``, a scratch run); each must exit 0.

    A probed row needs its ``example`` in plain English. Returns the receipts of the rows shown;
    raises ValueError naming the rows whose probe did not exit 0. ``what`` names the rows in
    messages (claim, concern, conflict) and ``key`` is the field that identifies a row.

    With ``run_counterexample(command, counterexample)`` (a scratch run with the row's
    ``counterexample`` edit made), every probed row needs a counterexample and its probe must
    exit non-zero there: a probe that exits 0 whatever the code says, such as
    ``python3 -c 'assert 2 + 2 == 4'``, does not tell the claim from its opposite and is refused
    rather than shown as a check that ran (#1769).
    """
    shown: list[Any] = []
    failed: list[Any] = []
    vacuous: list[Any] = []
    for row in rows:
        probe = str(row.get("probe") or "").strip()
        if not probe:
            continue
        if CITATION_PROBE.match(probe):
            # A report-repair prompt states the schema, where a probe is just a string, and a model that
            # guesses writes its own event ID there. Running that costs a full repair call over a shell
            # error the model cannot read back as a rule (#1769).
            raise ValueError(
                f"{probe!r} is a reference, not a command: for a {what}, give the shell command that "
                f"exits 0 exactly when it holds, run from the repository root."
            )
        if not str(row.get("example") or "").strip():
            raise ValueError(f"A probed {what} needs its example in plain English: {row.get(key)!r}")
        if run_counterexample is not None:
            error = _counterexample_error(row, what, key)
            if error:
                raise ValueError(error)
        run = run_probe(probe)
        receipt = {
            key: row.get(key),
            "probe": probe,
            "exit_code": run.get("exit_code"),
            "tail": (run.get("tail") or run.get("error") or "")[-600:],
        }
        if run.get("exit_code") != 0 or run.get("error"):
            failed.append(receipt)
            continue
        if run_counterexample is not None:
            counter = row["counterexample"]
            against = run_counterexample(probe, counter)
            receipt["counterexample"] = {
                "file": str(counter.get("file") or "").strip(),
                "exit_code": against.get("exit_code"),
                "tail": (against.get("tail") or against.get("error") or "")[-600:],
            }
            if against.get("error") or against.get("timed_out") or against.get("exit_code") in (0, None):
                vacuous.append(receipt)
                continue
        shown.append(receipt)
    if failed:
        raise ValueError(
            f"These {what}s' probes did not exit 0 on the code as it is, so they are not shown: "
            + "; ".join(
                f"{row[key]!r} ({row['probe']}: exit {row['exit_code']}) {row['tail'][-200:]}" for row in failed
            )
            + PROBE_COPY_NOTE
        )
    if vacuous:
        raise ValueError(
            f"These {what}s' probes did not fail on their counterexample, so they do not tell the {what} "
            f"from its opposite and are not shown as checked. Give a probe that exits 0 on the code and "
            f"non-zero once the counterexample edit is made, or leave example and probe empty for a {what} "
            f"shown by its source alone: "
            + "; ".join(
                f"{row[key]!r} ({row['probe']} on {row['counterexample']['file']} changed: "
                f"exit {row['counterexample']['exit_code']}) {row['counterexample']['tail'][-200:]}"
                for row in vacuous
            )
        )
    return shown


NAMED_PROOF_NOTE = prompts.get("fragments/test-cases/named-proof-note.md")

BUILDER_PROOF_NOTE = prompts.get("fragments/test-cases/builder-proof-note.md")

BUILDER_NOTE = prompts.get("fragments/test-cases/builder-note.md") + NAMED_PROOF_NOTE + BUILDER_PROOF_NOTE


# A live Arena bug fix (Boltons #474, 2026-10-05) used the plan's AC ids for its
# tests, while the proof still required the Investigator's T ids. Give the
# Builder the same cases the runner proves, including each case's before-state.
DIAGNOSIS_BUILDER_NOTE = prompts.get("fragments/test-cases/diagnosis-builder-note.md")


# Issue #299: a seam the fix adds cannot compile on the unfixed code, and a log line the fix adds proves nothing.
BUGFIX_TEST_NOTE = prompts.get("fragments/test-cases/bugfix-test-note.md")


def bugfix(state: RunState) -> bool:
    """The approved contract is a bug fix, whose tests must run and fail on the unfixed code (autocode_regression)."""
    return ((state.get("goal_contract") or {}).get("body") or {}).get("task_kind") == "bugfix"


def builder_note(state: RunState) -> str:
    fix_note = BUGFIX_TEST_NOTE if bugfix(state) else ""
    diagnosis = diagnosis_cases(state)
    if diagnosis:
        names = diagnosis_test_names(state)
        rows = []
        for case in diagnosis:
            before = (
                prompts.get("fragments/test-cases/builder-note-02.md")
                if case.get("kind") == "preserve"
                else prompts.get("fragments/test-cases/builder-note-03.md")
            )
            rows.append(
                f"- {case_text(case)}; test name: {case_test_name(case['id'], names.get(case['id']))}; {before}."
            )
        return DIAGNOSIS_BUILDER_NOTE + "\n".join(rows) + "\n" + NAMED_PROOF_NOTE + BUILDER_PROOF_NOTE + fix_note
    if contract_cases(state):
        return BUILDER_NOTE + fix_note
    return fix_note
