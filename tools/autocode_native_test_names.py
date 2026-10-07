"""Go test names the user's brief asks for, and the runner's check that the plan proves each one by that name.

A live Go run (#498, 2026-10-05) was told to add the native tests TestFixedReturnsTwo,
TestFixedPreservesExisting and TestFixedPreservesCrash. Its draft declared test_ac1_fixed_returns_two
and so on after test:/guard:, and a revision added prose saying each one "resolves to" the requested
test. The runner proves a criterion only by the identifier right after the mark
(autocode_test_cases.declared_test_name and match_cases), so none of the three criteria could bind a
test, yet the final Plan Reviewer offered the draft for approval. Prose is not a binding, and the
matcher is not widened to read it: the plan must declare the requested name itself.

A name is requested (``requested``) when all of these hold:

- It has the form of a Go test function (Test, then an upper-case letter, digit or underscore). TestMain
  is Go's hook for the test binary and TestXxx is Go's own placeholder; neither is a test to write.
- The user wrote it as a test to add (``named``), in their own words, as for the brief-literal check
  (autocode_requirement_cues.scan_texts): the task, brief feedback and the user's answers, never a
  delegated default or model-written text. It must come right after "test", "tests" or "func" (a
  "function", "case", "named" or "called" may come between), as in "add the Go tests TestA, TestB and
  TestC" or "a test for Fixed named TestA", or start an item of a list whose lead-in names tests ("Add
  these tests:" then "- TestA: what it checks"). A name further from the word, such as "the tests pass
  on TestNet" or "a test for TestHelper misuse", asks for nothing. So does one in a clause that negates
  or gives an example ("do not name the test TestFixed", "like the test TestReadAll"), or one offered
  with an alternative ("a test TestA or similar"). A later message of the user's that says "instead of",
  "rather than" or "not" right before a name withdraws it.
- The runner's regression proof will run Go tests: an explicit ``go test`` regression command, else the
  framework autocode_verify detects in the workspace, chosen as autocode_regression.prove chooses it.
  Elsewhere TestParser is a Python or Java class, not a test the proof reports.
- The project's Go files do not already contain it. A brief that mentions an existing test or helper
  ("the test TestRetention fails", "keep the test TestX passing") names the suite's own code, which the
  proof already protects; it is not a new case.
- The user has not settled it otherwise. The user's own edit of the plan (``USER_EDIT``) is never refused
  here, and the names the latest such edit leaves unaccounted for are no longer requested of the
  planners' later drafts.

Nothing is requested in a design-only job, or when a reproduced bug's diagnosis drives the proof (its
cases are named after their own ids, autocode_test_cases.diagnosis_cases).

The check (``problems``): each requested name is accounted for, either declared exactly, in the user's
spelling, right after the test: or guard: mark of one criterion (a subtest, TestA/case, counts), or
named by an ordinary criterion that names no other test, for the Validator to check (a test the runner
cannot run to a pass, such as one that skips without a database). A marked criterion that mentions a
requested name no marked criterion declares, while declaring another identifier, is the prose alias of #498
(also when an ordinary criterion leaves that name to the Validator), and one that declares a
respelling (test_fixed_returns_two, which the Go matcher would bind to Test_fixed_returns_two) does not
keep the requested name. Two criteria never declare the same requested test. Criteria without a
requested name keep the default test_<criterion id>_... convention. The goal lifecycle calls ``check``
on every draft install and at approval (validate_body with the draft's origin), so a refused draft is
never installed and a saved one cannot be approved, whatever a review accepted. Planning stages get
``rule``.
"""
from __future__ import annotations

import os
from pathlib import Path
import re

try:
    from . import autocode_requirement_cues as cues, autocode_test_cases as test_cases, autocode_verify as verify
except ImportError:
    import autocode_requirement_cues as cues
    import autocode_test_cases as test_cases
    import autocode_verify as verify

IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_])Test[A-Z0-9_][A-Za-z0-9_]*")
NOT_A_TEST = frozenset({"TestMain"})
PLACEHOLDER = re.compile(r"Test[Xx]+")
# The origin of a contract the user wrote with --edit-goal (autocode_run_actions); only the user makes one.
USER_EDIT = "user_cli_edit"

# The words a requested name follows, and the words that may come between them ("test function TestA").
CUES = frozenset({"test", "tests", "func"})
NAMING = frozenset({"named", "called"})
BETWEEN = frozenset({"function", "functions", "case", "cases"}) | NAMING
# A clause with one of these before the cue negates the request or gives an example. "Don't forget" and
# "not only" still ask; "I'd like" and "we would like" want.
NEGATORS = frozenset({"not", "never", "without", "instead", "rather"})
STILL_ASKS = frozenset({"forget", "only", "just"})
EXAMPLE = frozenset({"eg", "example", "instance"})
HEDGES = EXAMPLE | {"like", "such", "similar"}
WANTS = frozenset({"would", "d", "i", "we", "you", "they", "also"})
COORDINATORS = frozenset({"and", "but", "then"})
SENTENCE_END = frozenset({".", ";", "!", "?", ":"})
CLAUSE_END = SENTENCE_END | {","}
QUOTES = frozenset("`\"'*“”‘’")
BULLET = re.compile(r"\s*(?:[-*•+]|\d+[.)])\s+")
# A word keeps inner dots, slashes and hyphens (product_test.go, testing.T), so those end no sentence.
_TOKEN = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_./-]*[A-Za-z0-9_])?|[^\sA-Za-z0-9_]")
_CONTRACTION = re.compile(r"n['’]t\b", re.I)
_ABBREVIATION = re.compile(r"\b([ei])\.([ge])\.", re.I)
_TEST_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_])(?:Test[A-Z0-9_][A-Za-z0-9_]*|test_[A-Za-z0-9_]+)")
_SKIPPED_DIRECTORIES = frozenset({"node_modules"})


def _word(token: str) -> str:
    return token.lower() if token[:1].isalnum() or token[:1] == "_" else ""


def _skip(tokens, k, allowed) -> int:
    while k < len(tokens) and (tokens[k] in allowed or _word(tokens[k]) in allowed):
        k += 1
    return k


def _start(tokens, end, stops) -> int:
    """Where the stretch before ``end`` begins: just after the nearest of ``stops``."""
    start = end
    while start > 0 and tokens[start - 1] not in stops:
        start -= 1
    return start


def _clause(tokens, end, stops) -> list[str]:
    """The words before ``end`` back to the nearest of ``stops``."""
    return [word for word in map(_word, tokens[_start(tokens, end, stops):end]) if word]


def _qualified(tokens, cue) -> bool:
    """Whether the clause before ``cue`` negates the request or gives an example. A clause restarts after
    "and", "but" or "then": "return 2 instead of 0 and add the tests TestA and TestB" asks for both."""
    words = _clause(tokens, cue, CLAUSE_END)
    restart = max((index + 1 for index, word in enumerate(words) if word in COORDINATORS), default=0)
    words = words[restart:]
    start = _start(tokens, cue, CLAUSE_END)
    if tokens[start - 1:start] == [","] and _word(tokens[start - 2] if start > 1 else "") in EXAMPLE:
        return True  # "For example, the test TestA fails today" describes; it does not ask
    for index, word in enumerate(words):
        after = words[index + 1] if index + 1 < len(words) else ""
        before = words[index - 1] if index else ""
        if word in NEGATORS and after not in STILL_ASKS:
            return True
        if word in HEDGES and not (word == "like" and before in WANTS):
            return True
    return False


def _names(tokens, k) -> list[tuple[int, str]]:
    """The Go test names listed from ``tokens[k]`` ("TestA (what it checks), TestB and TestC"), with their
    positions; none when the list offers an alternative ("TestA or similar")."""
    found = []
    while True:
        k = _skip(tokens, k, QUOTES)
        if k >= len(tokens) or not IDENTIFIER.fullmatch(tokens[k]):
            break
        found.append((k, tokens[k]))
        k = _skip(tokens, k + 1, QUOTES)
        if k < len(tokens) and tokens[k] == "(":
            depth = 0
            while k < len(tokens):
                depth += {"(": 1, ")": -1}.get(tokens[k], 0)
                k += 1
                if not depth:
                    break
            k = _skip(tokens, k, QUOTES)
        if tokens[k:k + 1] == [","]:
            k += 2 if _word(tokens[k + 1] if k + 1 < len(tokens) else "") == "and" else 1
        elif k < len(tokens) and (_word(tokens[k]) == "and" or tokens[k] == "&"):
            k += 1
        else:
            break
    if k < len(tokens) and _word(tokens[k]) == "or":
        return []
    return [(at, name) for at, name in found if name not in NOT_A_TEST and not PLACEHOLDER.fullmatch(name)]


def _cue(tokens, i) -> bool:
    word = _word(tokens[i])
    return word in CUES or (word in NAMING and any(w in CUES for w in _clause(tokens, i, SENTENCE_END)))


def _after_cue(tokens, i) -> int:
    """Where a name introduced by the cue at ``i`` starts: right after it, or after a colon that ends its
    sentence's lead-in ("Add native Go tests in product_test.go: TestA, TestB")."""
    k = _skip(tokens, i + 1, BETWEEN | QUOTES | {"(", ":"})
    if k < len(tokens) and IDENTIFIER.fullmatch(tokens[k]):
        return k
    while k < len(tokens) and tokens[k] not in SENTENCE_END:
        k += 1
    return _skip(tokens, k + 1, QUOTES) if tokens[k:k + 1] == [":"] else len(tokens)


def _withdrawn(tokens, k) -> bool:
    """Whether the name at ``k`` follows "instead of", "rather than" or "not" ("the test" and quotes aside)."""
    words, j = [], k - 1
    while j >= 0 and len(words) < 2:
        word = _word(tokens[j])
        if word and (words or word not in CUES | {"the"}):
            words.insert(0, word)
        j -= 1
    return words[-1:] == ["not"] or words in (["instead", "of"], ["rather", "than"])


def _line_events(tokens) -> list[tuple[int, str, bool]]:
    """(position, name, requested) for each name the line asks for or withdraws."""
    events = [(k, token, False) for k, token in enumerate(tokens)
              if IDENTIFIER.fullmatch(token) and _withdrawn(tokens, k)]
    for i in range(len(tokens)):
        if _cue(tokens, i) and not _qualified(tokens, i):
            events += [(k, name, True) for k, name in _names(tokens, _after_cue(tokens, i))]
    return sorted(set(events))


def _leads_in(tokens) -> bool:
    """Whether a line ending in a colon introduces a list of tests ("Add these Go tests in x_test.go:")."""
    end = len(tokens) - 1
    return any(_cue(tokens, i) and not _qualified(tokens, i) for i in range(_start(tokens, end, SENTENCE_END), end))


def _lines(text: str) -> list[tuple[bool, str]]:
    """(is a list item, text) for each logical line: wrapped prose joined, each list item its own line
    (a bulleted or numbered line, or one starting with a test name under a lead-in or another item), and ""
    for a paragraph break."""
    lines = []
    for raw in text.splitlines():
        if not raw.strip():
            lines.append((False, ""))
            continue
        bullet = BULLET.match(raw)
        content = raw[bullet.end():].strip() if bullet else raw.strip()
        previous = lines[-1] if lines else (False, "")
        bare = (not bullet and previous[1] and (previous[0] or previous[1].endswith(":"))
                and IDENTIFIER.match(content.lstrip("".join(QUOTES))))
        if bullet or bare:
            lines.append((True, content))
        elif previous[1]:
            lines[-1] = (previous[0], previous[1] + " " + content)
        else:
            lines.append((False, content))
    return lines


def _events(text: str) -> list[tuple[str, bool]]:
    """(name, requested) in the order ``text`` asks for or withdraws each name."""
    text = _ABBREVIATION.sub(lambda match: match[1] + match[2], _CONTRACTION.sub(" not", text or ""))
    events, listing, listed = [], False, False
    for item, line in _lines(text):
        if not line:
            listing = listing and not listed
            continue
        tokens = _TOKEN.findall(line)
        if item and listing:
            names = _names(tokens, 0)
            events += [(name, True) for _, name in names]
            listed = listed or bool(names)
        elif not item:
            listing = listed = False
        events += [(name, wanted) for _, name, wanted in _line_events(tokens)]
        if not item and tokens[-1:] == [":"] and _leads_in(tokens):
            listing, listed = True, False
    return events


def named(texts) -> list[str]:
    """Go test names ``texts`` ask for as tests, in order, without repeats; a later withdrawal removes one."""
    order, wanted = [], {}
    for text in texts:
        for name, request in _events(text):
            if request and name not in order:
                order.append(name)
            wanted[name] = request
    return [name for name in order if wanted[name]]


def go_identifiers(workspace) -> set[str]:
    """Every Test-prefixed identifier already written in the workspace's Go files."""
    found = set()
    for directory, subdirectories, files in os.walk(workspace):
        subdirectories[:] = [name for name in subdirectories
                             if not name.startswith(".") and name not in _SKIPPED_DIRECTORIES]
        for name in files:
            if name.endswith(".go"):
                try:
                    found.update(IDENTIFIER.findall(Path(directory, name).read_text(errors="replace")))
                except OSError:
                    continue
    return found


def proof_framework(state) -> str | None:
    """The framework the regression proof will run, chosen as autocode_regression.prove chooses it."""
    options = (state.get("settings") or {}).get("regression") or {}
    command = options.get("test_command")
    framework = verify.command_framework(command) if command else None
    workspace = state.get("workspace")
    if framework is None and workspace and Path(workspace).is_dir():
        python = options.get("python") or verify.python_for(state.get("project_workspace") or workspace)
        framework = verify.detect_framework(workspace, python=python)
    return framework.name if framework else None


def _user_edit(state) -> dict | None:
    """The body of the user's latest own edit of the plan, or None."""
    contracts = [*(state.get("contract_history") or []), state.get("goal_contract") or {}]
    edits = [contract for contract in contracts if isinstance(contract, dict) and contract.get("origin") == USER_EDIT]
    return edits[-1].get("body") if edits else None


def requested(state) -> list[str]:
    """The Go test names the user asked for that the plan must declare (see the module docstring)."""
    if test_cases.design_only(state) or test_cases.diagnosis_cases(state):
        return []
    names = named(cues.scan_texts(state))
    if not names or proof_framework(state) != "go":
        return []
    workspace = state.get("workspace")
    existing = go_identifiers(workspace) if workspace and Path(workspace).is_dir() else set()
    names = [name for name in names if name not in existing]
    edit = _user_edit(state)
    return names if edit is None else _accounted(edit, names)


def _criteria(body):
    """(id, mark or None, the text after the mark or the whole method, declared test name) per criterion."""
    for row in (body.get("acceptance_criteria") if isinstance(body, dict) else None) or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        method = str(row.get("verification_method") or "").strip()
        found = test_cases.mark(method)
        rest = method[len(found):].strip() if found else method
        yield row["id"], found, rest, test_cases.declared_test_name(rest) if found else None


def _declares(declared: str | None, name: str) -> bool:
    """Whether a criterion declaring ``declared`` is proven by the test ``name`` itself (or its subtest)."""
    return bool(declared) and declared.split("/", 1)[0] == name


def _respells(declared: str | None, name: str) -> bool:
    """Whether the Go matcher would bind ``declared`` to ``name`` only as one of its variant spellings."""
    function = (declared or "").split("/", 1)[0]
    return bool(function) and function != name and bool(
        test_cases.match_cases([{"id": "case", "test_name": function}], [name], framework="go")["case"])


def _mentions(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text))


def _accounted(body, names: list[str]) -> list[str]:
    """The ``names`` that ``body`` declares after a mark, or leaves to the Validator by naming them in an
    ordinary criterion that names no other test."""
    rows = list(_criteria(body))
    return [name for name in names
            if any(found and _declares(declared, name) for _, found, _, declared in rows)
            or any(not found and _mentions(rest, name) and set(_TEST_IDENTIFIER.findall(rest)) <= set(names)
                   for _, found, rest, _ in rows)]


def problems(body, names: list[str]) -> list[str]:
    """Why ``body`` does not account for the requested Go tests ``names`` by name, one entry per fault;
    [] when it does."""
    if not names:
        return []
    rows = [row for row in _criteria(body) if row[1]]
    accounted = set(_accounted(body, names))
    # Only a marked criterion's declaration binds a name to the runner's proof; a mention elsewhere of a name the
    # Validator alone checks is still the prose alias of #498.
    declared_names = {name for name in names if any(_declares(declared, name) for *_, declared in rows)}
    errors, reported = [], set()
    for name in names:
        by_declaration = {}
        for criterion, _, _, declared in rows:
            if _declares(declared, name):
                by_declaration.setdefault(declared, []).append(criterion)
        errors += [(f"{ids[0]} and {ids[1]} both" if len(ids) == 2 else ", ".join(ids) + " all")
                   + f" declare {declared}; each criterion needs its own test"
                   for declared, ids in by_declaration.items() if len(ids) > 1]
    for criterion, found, rest, declared in rows:
        for name in names:
            if name in declared_names or name in reported:
                continue
            if _respells(declared, name):
                errors.append(f"{criterion} declares {declared}, a respelling of {name}; keep the requested "
                              f"spelling (write \"{found} {name}\")")
            elif _mentions(rest, name) and name in accounted:
                errors.append(f"{criterion} declares {declared or 'no test name'} but refers to {name}, which only "
                              f"an ordinary criterion leaves to the Validator (write \"{found} {name}\", or drop "
                              "the reference)")
            elif _mentions(rest, name):
                errors.append(f"{criterion} declares {declared or 'no test name'} but refers to {name} "
                              f"(write \"{found} {name}\")")
            else:
                continue
            reported.add(name)
    unbound = [name for name in names if name not in accounted and name not in reported]
    if unbound:
        errors.append(f"no criterion declares {', '.join(unbound)}")
    return errors


def check(state, body, *, origin=None) -> None:
    """Refuse a contract body that does not account for the Go tests the user asked for. The user's own
    edit (``USER_EDIT``) is theirs to decide and is never refused here."""
    if origin == USER_EDIT:
        return
    errors = problems(body, requested(state))
    if errors:
        raise ValueError(
            "The plan does not prove the Go tests the user asked for by name: " + "; ".join(errors) + ". The runner "
            "proves a criterion only by the identifier right after test: or guard:; text saying one name maps to, "
            "resolves to or stands for another binds nothing. Put each requested name, spelled as the user wrote "
            "it, right there on the one criterion it proves: test: for new or fixed behavior, guard: for behavior "
            "that must keep working. A requested test the runner cannot run to a pass here (one that skips "
            "without a database, say) goes on an ordinary criterion whose verification_method names it and no "
            "other test, for the Validator")


def rule(names: list[str]) -> str:
    """The planning instruction naming the Go tests the runner will require, so a draft need not be sent back."""
    listed = ", ".join(names)
    return ("\nNATIVE TEST NAMES: the user asked for the Go tests " + listed + ". The runner proves a criterion only "
            "by the identifier right after test: or guard:, so on the one criterion each proves write that exact "
            f"name there (\"test: {names[0]}\" for new or fixed behavior, \"guard: {names[0]}\" for behavior that "
            "must keep working), keeping the user's spelling (never test_... or another respelling); an explanation "
            "may follow after \" — \". A requested test the runner cannot run to a pass here (one that skips "
            "without a database, say) goes instead on an ordinary criterion whose verification_method names it "
            "and no other test, for the Validator, and no test: or guard: criterion mentions it. This replaces the test_<criterion id>_... name for those "
            "criteria only; other criteria keep it. Never declare another identifier (such as test_ac1_...) and "
            "say in prose that it maps to, resolves to or stands for a requested name: the runner does not read "
            "that text and refuses such a draft, whatever a review says.\n")
