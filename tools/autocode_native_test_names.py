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
  is Go's hook for the test binary and TestXxx is Go's own placeholder; neither is a test to write. A
  subtest path (TestCacheExpiry/expired) is requested as the user wrote it, a quoted one with its spaces
  as the underscores Go runs it under ("TestA/empty input" is TestA/empty_input).
- The user wrote it as a test to add (``named``), in their own words, as for the brief-literal check
  (autocode_requirement_cues.scan_texts): the task, brief feedback and the user's answers, never a
  delegated default or model-written text. It must come right after "test", "tests" or "func" (a
  "function", "case", "named" or "called" may come between), as in "add the Go tests TestA, TestB and
  TestC" or "a test for Fixed named TestA", or start an item of a list whose lead-in names tests ("Add
  these tests:" then "- TestA: what it checks"). A name-first request such as "Add TestA, a real
  regression ... plus TestB and TestC" also names tests. A supplied func signature must take one
  pointer to T or qualifier.T and return nothing; a callable API such as TestConnection() error
  names no test. Without a signature, func needs test context: "test" or "tests" in its clause ("the
  test func TestA"), or, unless a return value or a production file follows the name ("a func named
  TestConnection that returns an error", "... to product.go"), "test", "tests", "regression" or a
  *_test.go file in its sentence, or a lead-in naming tests above its list item. "A func named
  TestConnection" alone is production code. In a list each name may carry a description ("TestA
  (empty input), TestB (one item)", or "TestA checks X, TestB checks Y; TestC ..."); the list ends at
  the sentence's end, at a description's first comma or semicolon that no name follows ("the test
  TestA, which must not break TestB"), and at a name inside a description ("TestA for the parser and
  make sure TestServer, TestClient still pass"; a file path such as TestData/golden.json is not a name
  there). A name further from the word, such as "the tests pass on TestNet" or "a test for TestHelper
  misuse", asks for nothing. So does one in a clause that negates or gives an example ("do not name the
  test TestFixed", "like the test TestReadAll"), or one offered with an alternative ("a test TestA or
  similar"). A later message of the user's that says "instead of", "rather than" or "not" right before a
  name withdraws it: a subtest path alone (so "TestA/empty and TestA/one, but not TestA/two" keeps the
  first two), a test function with the paths of it that earlier messages asked for ("Add the Go test
  TestA/empty instead of TestA" asks for TestA/empty).
- The runner's regression proof will run Go tests: an explicit ``go test`` regression command, else the
  framework autocode_verify detects in the workspace, chosen as autocode_regression.prove chooses it.
  Elsewhere TestParser is a Python or Java class, not a test the proof reports.
- The project's *_test.go files do not already declare it (or, for a subtest path, its function) as a
  top-level Go test function; subtest names live in strings, so one under an existing test asks for
  nothing. Comments, strings, references and production helpers do not declare tests. Declaration
  eligibility is a lexical inventory; Go compilation, build selection and execution supply the proof.
- The user has not settled it otherwise. The user's own edit of the plan (``USER_EDIT``) is never refused
  here, and the names the latest such edit leaves unaccounted for are no longer requested of the
  planners' later drafts. An edit of a design job's plan, which a follow-up building that design archives,
  settles nothing for the build.

Nothing is requested in a design-only job, or when a reproduced bug's diagnosis drives the proof (its
cases are named after their own ids, autocode_test_cases.diagnosis_cases).

The check (``problems``): each requested name is accounted for, either declared exactly, in the user's
spelling, right after the test: or guard: mark of one criterion, or named by an ordinary criterion whose
verification_method names that test and no other, for the Validator to check (a test the runner cannot
run to a pass, such as one that skips without a database). A requested test function is accounted for by
itself or any of its subtests (TestA/case); a requested subtest path by itself, a path under it or its
whole test function, which runs every subtest it has, never by a sibling path, which the runner binds
to that sibling's outcome only. A marked criterion that mentions, in its verification_method or its own
text, a requested name no marked criterion declares, while declaring another identifier (or none the
runner reads), is the prose alias of #498 (also when an ordinary criterion leaves that name to the
Validator; a criterion naming a subtest path is told that path, one naming only its function the first
requested path under it), and one that declares a respelling (test_fixed_returns_two, which the Go
matcher would bind to Test_fixed_returns_two) does not keep the requested name. Two criteria never
declare the same requested test. Criteria without a requested name keep the default
test_<criterion id>_... convention. The goal lifecycle calls ``check`` on every draft install and at
approval (validate_body with the draft's origin), so a refused draft is never installed and a saved one
cannot be approved, whatever a review accepted. Planning stages get ``rule``.
"""
from __future__ import annotations

import os
from pathlib import Path
import re

try:
    from . import autocode_requirement_cues as cues, autocode_test_cases as test_cases, autocode_verify as verify
    from . import autocode_contract_identity as identity
except ImportError:
    import autocode_requirement_cues as cues
    import autocode_test_cases as test_cases
    import autocode_verify as verify
    import autocode_contract_identity as identity

IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_])Test[A-Z0-9_][A-Za-z0-9_]*")
# A Go test name as one word of the brief: the function, or a subtest path under it (TestCacheExpiry/expired).
_TEST_WORD = re.compile(r"Test[A-Z0-9_][A-Za-z0-9_]*(?:/.+)?")
NOT_A_TEST = frozenset({"TestMain"})
PLACEHOLDER = re.compile(r"Test[Xx]+")
# The origin of a contract the user wrote with --edit-goal (autocode_run_actions); only the user makes one.
USER_EDIT = "user_cli_edit"

# The words a requested name follows, and the words that may come between them ("test function TestA").
CUES = frozenset({"test", "tests", "func"})
# "func" names a test only with a test's signature or test context (_func_test_request), so when it is the
# nearest cue it does not let "named"/"called" introduce one: "a func named TestConnection" is production code.
FUNC = "func"
NAMING = frozenset({"named", "called"})
# Test context in the sentence of a "func TestA" without a signature, besides a *_test.go file.
TEST_CONTEXT = frozenset({"test", "tests", "regression"})
# Right after such a name (after "that" or "which"), these make it a production function.
RETURNS = frozenset({"return", "returns", "returning"})
TARGETS = frozenset({"in", "to", "into"})
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
_TEST_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_])(?:Test[A-Z0-9_][A-Za-z0-9_]*(?:/[A-Za-z0-9_.-]*[A-Za-z0-9_])*"
                              r"|test_[A-Za-z0-9_]+)")
# Go runs a subtest under its name with each space made an underscore: "TestA/empty input" is TestA/empty_input.
_QUOTED_PATH = re.compile(r"([\"`“])(Test[A-Z0-9_][A-Za-z0-9_]*/[^\"`”\n]*)([\"`”])")
# A path ending in a file extension (TestData/golden.json) names a file, not a subtest, inside a description.
_FILE = re.compile(r"/[^/]*\.[A-Za-z]+$")
_SKIPPED_DIRECTORIES = frozenset({"node_modules"})


def _word(token: str) -> str:
    return token.lower() if token[:1].isalnum() or token[:1] == "_" else ""


def _test_name(token: str) -> str | None:
    """``token`` when it names a Go test: a test function (TestA) or a subtest path (TestA/empty); else None."""
    return token if _TEST_WORD.fullmatch(token) else None


def _function(name: str) -> str:
    """The test function of a requested name: TestA for TestA and for its subtest path TestA/empty."""
    return name.split("/", 1)[0]


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


def _separator(tokens, k) -> int | None:
    """Where the next list item starts when ``tokens[k]`` separates two ("," ";" "and" "&", ", and"), else None."""
    if tokens[k:k + 1] in ([","], [";"]):
        return k + 2 if _word(tokens[k + 1] if k + 1 < len(tokens) else "") in {"and", "plus"} else k + 1
    if k < len(tokens) and (_word(tokens[k]) in {"and", "plus"} or tokens[k] == "&"):
        return k + 1
    return None


def _past_description(tokens, k) -> int | None:
    """Where the next name of a list starts after the description from ``tokens[k]`` ("TestA checks X, TestB
    checks Y; TestC ..."), or None. The list ends at the sentence's end, at the description's first comma or
    semicolon that another name does not follow ("TestA, which must not break TestB"), and at a name inside
    the description ("TestA for the parser and make sure TestServer, TestClient still pass")."""
    while k < len(tokens) and tokens[k] not in SENTENCE_END - {":", ";"}:
        if _test_name(tokens[k]) and not _FILE.search(tokens[k]):
            return None
        after = _separator(tokens, k)
        if after is not None:
            after = _skip(tokens, after, QUOTES)
            if after < len(tokens) and _test_name(tokens[after]):
                return after
            if tokens[k] in (",", ";"):
                return None
        k += 1
    return None


def _apposition(tokens, k) -> bool:
    """A comma followed by an explicit test description: TestA, a real regression."""
    if tokens[k:k + 1] != [","]:
        return False
    k = _skip(tokens, k + 1, {"a", "an", "the", "real", "native", "go", "focused"})
    return k < len(tokens) and _word(tokens[k]) in {"test", "regression"}


def _names(tokens, k) -> list[tuple[int, str]]:
    """The Go test names listed from ``tokens[k]`` ("TestA (what it checks), TestB and TestC", or "TestA checks
    X, TestB checks Y"), with their positions; none when the list offers an alternative ("TestA or similar")."""
    found = []
    while True:
        k = _skip(tokens, k, QUOTES)
        name = _test_name(tokens[k]) if k < len(tokens) else None
        if not name:
            break
        found.append((k, name))
        k = _skip(tokens, k + 1, QUOTES)
        if k < len(tokens) and tokens[k] == "(":
            depth = 0
            while k < len(tokens):
                depth += {"(": 1, ")": -1}.get(tokens[k], 0)
                k += 1
                if not depth:
                    break
            k = _skip(tokens, k, QUOTES)
        if _apposition(tokens, k):
            after = _past_description(tokens, k + 1)
            if after is None:
                # An alternative to the described test is not an exact-name request.
                tail = tokens[k + 1:]
                end = next((j for j, token in enumerate(tail) if token in SENTENCE_END), len(tail))
                if any(j > 0 and tail[j - 1] == "," and _word(tail[j]) == "or" and
                       (_word(tail[j + 1]) in {"similar", "whatever", "equivalent", "another"} or
                        _test_name(tail[j + 1])) for j in range(end - 1)):
                    return []
                break
        else:
            after = _separator(tokens, k)
        if after is None:
            if k < len(tokens) and _word(tokens[k]) == "or":
                return []  # "TestA or similar"
            after = _past_description(tokens, k)
            if after is None:
                break
        k = after
    if k < len(tokens) and _word(tokens[k]) == "or":
        return []  # "TestA, TestB, or TestC"
    return [(at, name) for at, name in found
            if _function(name) not in NOT_A_TEST and not PLACEHOLDER.fullmatch(_function(name))]


def _cue(tokens, i) -> bool:
    word = _word(tokens[i])
    if word == "add":
        k = _skip(tokens, i + 1, QUOTES)
        return (k < len(tokens) and bool(_test_name(tokens[k]))
                and _apposition(tokens, _skip(tokens, k + 1, QUOTES)))
    if word in NAMING:
        cues = [w for w in _clause(tokens, i, SENTENCE_END) if w in CUES]
        return bool(cues) and cues[-1] != FUNC  # "a test for Fixed named TestA"; "a func named TestA" is func's
    return word in CUES


def _after_cue(tokens, i) -> int:
    """Where a name introduced by the cue at ``i`` starts: right after it, or after a colon that ends its
    sentence's lead-in ("Add native Go tests in product_test.go: TestA, TestB")."""
    k = _skip(tokens, i + 1, BETWEEN | QUOTES | {"(", ":"})
    if k < len(tokens) and _test_name(tokens[k]):
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


# Tokenize Go separately from prose: comments and literals cannot declare tests.
_GO_TOKEN = re.compile(r"//[^\n]*|/\*[\s\S]*?\*/|\"(?:\\.|[^\"\\])*\"|\x60[^\x60]*\x60|'(?:\\.|[^'\\])*'|[A-Za-z_][A-Za-z0-9_]*|[^\s]")
_GO_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _go_tokens(text):
    return [token for token in _GO_TOKEN.findall(text)
            if not token.startswith(("//", "/*", '"', chr(96), "'"))]


def _test_parameter(tokens) -> bool:
    """Go's test loader accepts one pointer to T or qualifier.T, optionally named."""
    if tokens[-1:] == [","]:
        tokens = tokens[:-1]
    if len(tokens) > 1 and _GO_WORD.fullmatch(tokens[0]) and tokens[1] == "*":
        tokens = tokens[1:]
    return (tokens == ["*", "T"] or
            (len(tokens) == 4 and tokens[0] == "*" and
             bool(_GO_WORD.fullmatch(tokens[1])) and tokens[2:] == [".", "T"]))


def _signature_end(tokens, k) -> int | None:
    if tokens[k + 1:k + 2] != ["("]:
        return None
    try:
        end = tokens.index(")", k + 2)
    except ValueError:
        return None
    return end + 1 if _test_parameter(tokens[k + 2:end]) else None


def _production(tokens, k) -> bool:
    """Whether the words right after the func name at ``k`` make it a production function: a return value
    ("TestConnection that returns an error") or a production file ("TestConnection to product.go")."""
    j = _skip(tokens, k + 1, QUOTES | {"that", "which"})
    word = _word(tokens[j]) if j < len(tokens) else ""
    if word in RETURNS:
        return True
    j = _skip(tokens, j + 1, QUOTES)
    target = _word(tokens[j]) if word in TARGETS and j < len(tokens) else ""
    return target.endswith(".go") and not target.endswith("_test.go")


def _func_test_context(tokens, k, listed) -> bool:
    """Whether "func TestA" without a signature names a test: "test" or "tests" in its clause ("the test func
    TestA"), or, unless the words after it make it production code, "test", "tests", "regression" or a
    *_test.go file in its sentence ("Add a func named TestA in a_test.go"), or a lead-in naming tests above
    its list item (``listed``)."""
    if any(word in {"test", "tests"} for word in _clause(tokens, k, CLAUSE_END)):
        return True
    if _production(tokens, k):
        return False
    stops = SENTENCE_END - {":"}
    end = k
    while end < len(tokens) and tokens[end] not in stops:
        end += 1
    words = [_word(token) for token in tokens[_start(tokens, k, stops):end]]
    return listed or any(word in TEST_CONTEXT or word.endswith("_test.go") for word in words)


def _func_test_request(tokens, k, listed=False) -> bool:
    if tokens[k + 1:k + 2] != ["("]:
        return _func_test_context(tokens, k, listed)
    try:
        end = tokens.index(")", k + 2)
    except ValueError:
        return False
    if not _test_parameter(_go_tokens("".join(tokens[k + 2:end]))):
        return False
    if tokens[end + 1:end + 3] == ["(", ")"]:
        end += 2  # Go permits an explicit empty result list.
    after = _skip(tokens, end + 1, QUOTES)
    # A supplied result type makes this an API, not a runnable Go test.
    return (after == len(tokens) or tokens[after] in {"{", ".", ";", "!", "?", ":"} or
            _word(tokens[after]) in {"and", "but", "then", "for", "to", "that", "which", "as", "in", "with"})


def _line_events(tokens, listed=False) -> list[tuple[int, str, bool]]:
    """(position, name, requested) for each name the line asks for or withdraws; ``listed`` when the line is an
    item under a lead-in that names tests."""
    events = [(k, token, False) for k, token in enumerate(tokens) if _test_name(token) and _withdrawn(tokens, k)]
    for i in range(len(tokens)):
        if _cue(tokens, i) and not _qualified(tokens, i):
            events += [(k, name, True) for k, name in _names(tokens, _after_cue(tokens, i))
                       if _word(tokens[i]) != FUNC or _func_test_request(tokens, k, listed)]
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
    text = _QUOTED_PATH.sub(lambda match: match[1] + re.sub(r"\s", "_", match[2]) + match[3], text)
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
        events += [(name, wanted) for _, name, wanted in _line_events(tokens, item and listing)]
        if not item and tokens[-1:] == [":"] and _leads_in(tokens):
            listing, listed = True, False
    return events


def named(texts) -> list[str]:
    """Go test names ``texts`` ask for as tests (a subtest path as written), in order, without repeats. A later
    withdrawal removes one: a subtest path's withdrawal that path only ("not TestA/two" leaves TestA/empty), a
    test function's the function and the paths of it that earlier texts asked for ("Add the Go test TestA/empty
    instead of TestA" narrows the request to TestA/empty)."""
    order, wanted = [], {}
    for text in texts:
        earlier = set(order)
        for name, request in _events(text):
            if request:
                if name not in order:
                    order.append(name)
                wanted[name] = True
            else:
                wanted.update((listed, False) for listed in order
                              if listed == name or (listed in earlier and _function(listed) == name))
    return [name for name in order if wanted[name]]


def go_identifiers(workspace) -> set[str]:
    """Eligible top-level test declarations in *_test.go, not mentions or production APIs.

    This inventories declarations; compilation, build selection and runtime proof stay with Go.
    """
    found = set()
    for directory, subdirectories, files in os.walk(workspace):
        subdirectories[:] = [name for name in subdirectories
                             if not name.startswith(".") and name not in _SKIPPED_DIRECTORIES]
        for name in files:
            if not name.endswith("_test.go"):
                continue
            try:
                tokens = _go_tokens(Path(directory, name).read_text(errors="replace"))
            except OSError:
                continue
            depth = 0
            for k, token in enumerate(tokens):
                if depth == 0 and token == "func" and k + 1 < len(tokens) and IDENTIFIER.fullmatch(tokens[k + 1]):
                    end = _signature_end(tokens, k + 1)
                    if end is not None and (tokens[end:end + 1] == ["{"] or tokens[end:end + 3] == ["(", ")", "{"]):
                        found.add(tokens[k + 1])
                depth += {"{": 1, "}": -1}.get(token, 0)
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
    """The body of the user's latest own edit of this job's plan, or None. A follow-up that builds a proposed
    design plans afresh (autocode_follow_up.plan_afresh) and archives the design job's contracts; an edit of
    those was not about this build's tests."""
    contracts = [*(state.get("contract_history") or []), state.get("goal_contract") or {}]
    contracts = [contract for contract in contracts if isinstance(contract, dict)]
    archived = {((turn.get("fresh_plan") or {}).get("contract")) for turn in state.get("turns") or []
                if isinstance(turn, dict)} - {None}
    ends = [index for index, contract in enumerate(contracts)
            if contract.get("revision") is not None and contract.get("hash") and identity.token(contract) in archived]
    edits = [contract for contract in contracts[max(ends, default=-1) + 1:] if contract.get("origin") == USER_EDIT]
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
    names = [name for name in names if _function(name) not in existing]
    edit = _user_edit(state)
    return names if edit is None else _accounted(edit, names)


def _criteria(body):
    """(id, mark or None, the text after the mark or the whole method, declared test name, the criterion's
    own text) per criterion."""
    for row in (body.get("acceptance_criteria") if isinstance(body, dict) else None) or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        method = str(row.get("verification_method") or "").strip()
        found = test_cases.mark(method)
        rest = method[len(found):].strip() if found else method
        yield (row["id"], found, rest, test_cases.declared_test_name(rest) if found else None,
               str(row.get("criterion") or ""))


def _declares(declared: str | None, name: str) -> bool:
    """Whether a criterion declaring ``declared`` proves the requested ``name``: a test function by itself or
    any of its subtests; a subtest path by itself, a path under it, or its whole test function, which runs
    every subtest it has. A sibling path does not: the runner binds it to that sibling's outcome only."""
    if not declared:
        return False
    if "/" not in name:
        return _function(declared) == name
    return declared in (name, _function(name)) or declared.startswith(name + "/")


def _respells(declared: str | None, name: str) -> bool:
    """Whether the Go matcher would bind ``declared`` to ``name`` only as one of its variant spellings."""
    function, name = _function(declared or ""), _function(name)
    return bool(function) and function != name and bool(
        test_cases.match_cases([{"id": "case", "test_name": function}], [name], framework="go")["case"])


def _mentions(text: str, name: str, *, exact=False) -> bool:
    """Whether ``text`` mentions ``name``: a test function alone or in any of its subtest paths; a subtest path
    itself or a path under it, or (unless ``exact``) its test function alone ("resolves to TestA"), never a
    sibling path."""
    pattern = re.escape(name) + "(?![A-Za-z0-9_])"
    if "/" in name and not exact:
        pattern = f"(?:{pattern}|{re.escape(_function(name))}(?![A-Za-z0-9_/]))"
    return bool(re.search("(?<![A-Za-z0-9_])" + pattern, text))


def _left_to_validator(method: str, name: str) -> bool:
    """Whether an ordinary criterion's verification_method names the requested test (as ``_declares`` reads
    a declaration) and no other test function."""
    tests = _TEST_IDENTIFIER.findall(method)
    return {_function(test) for test in tests} == {_function(name)} and any(_declares(test, name) for test in tests)


def _accounted(body, names: list[str]) -> list[str]:
    """The ``names`` that ``body`` declares after a mark, or leaves to the Validator by naming each in an
    ordinary criterion whose verification_method names that test and no other."""
    rows = list(_criteria(body))
    return [name for name in names
            if any(found and _declares(declared, name) for _, found, _, declared, _ in rows)
            or any(not found and _left_to_validator(rest, name) for _, found, rest, _, _ in rows)]


def problems(body, names: list[str]) -> list[str]:
    """Why ``body`` does not account for the requested Go tests ``names`` by name, one entry per fault;
    [] when it does."""
    if not names:
        return []
    rows = [row for row in _criteria(body) if row[1]]
    accounted = set(_accounted(body, names))
    # Only a marked criterion's declaration binds a name to the runner's proof; a mention elsewhere of a name the
    # Validator alone checks is still the prose alias of #498.
    declared_names = {name for name in names if any(_declares(declared, name) for _, _, _, declared, _ in rows)}
    errors, reported = [], set()
    for name in names:
        by_declaration = {}
        for criterion, _, _, declared, _ in rows:
            if _declares(declared, name):
                by_declaration.setdefault(declared, []).append(criterion)
        errors += [(f"{ids[0]} and {ids[1]} both" if len(ids) == 2 else ", ".join(ids) + " all")
                   + f" declare {declared}; each criterion needs its own test"
                   for declared, ids in by_declaration.items() if len(ids) > 1]
    for criterion, found, rest, declared, text in rows:
        # The prose alias may sit in the verification method or in the criterion's own text.
        declares = declared or ("no test name the runner reads (a name stands alone after the mark, or is followed "
                                "by \" — \" or a parenthesis)")
        told = set()  # one requested path per test function on a criterion, which can declare only one
        both = rest + "\n" + text
        # A path the criterion names itself comes before one it reaches only through the function's name.
        for name in sorted(names, key=lambda name: not _mentions(both, name, exact=True)):
            if name in declared_names or name in reported or _function(name) in told:
                continue
            if _respells(declared, name):
                errors.append(f"{criterion} declares {declared}, a respelling of {name}; keep the requested "
                              f"spelling (write \"{found} {name}\")")
            elif _mentions(both, name) and name in accounted:
                errors.append(f"{criterion} declares {declares} but refers to {name}, which only an ordinary "
                              f"criterion leaves to the Validator (write \"{found} {name}\", or drop the reference)")
            elif _mentions(both, name):
                errors.append(f"{criterion} declares {declares} but refers to {name} (write \"{found} {name}\")")
            else:
                continue
            reported.add(name)
            told.add(_function(name))
    unbound = [name for name in names if name not in accounted and name not in reported]
    if unbound:
        errors.append(f"no criterion declares {', '.join(unbound)}")
    return list(dict.fromkeys(errors))  # two subtest paths of one function find the same duplicate declaration


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
            "name there, after test: for new or fixed behavior or after guard: for behavior that must keep working "
            f"(\"test: {names[0]}\"), keeping the user's spelling and any subtest path they gave (never test_... or "
            "another respelling); an explanation may follow after \" — \". A requested test the runner cannot run "
            "to a pass here (one that skips without a database, say) goes instead on an ordinary criterion whose "
            "verification_method names it and no other test, for the Validator, and no test: or guard: criterion "
            "mentions it. This replaces the test_<criterion id>_... name for those criteria only; other criteria "
            "keep it. Never declare another identifier (such as test_ac1_...) and say in prose that it maps to, "
            "resolves to or stands for a requested name: the runner does not read that text and refuses such a "
            "draft, whatever a review says.\n")
