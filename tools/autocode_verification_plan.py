"""Executable commands and prerequisites from an approved verification plan.

Natural-language methods stay with the Validator. Commands are plain shell
commands or backtick snippets explicitly requested for execution. Quoted
documentation examples are not commands. Depends only on the standard library
and the exit-expectation helper.
"""
from pathlib import Path, PurePosixPath
import hashlib
import json
import re
import shlex

try:
    from . import autocode_verification_expectations as expectations
except ImportError:
    import autocode_verification_expectations as expectations

# Plain text (no backticks) is a command only when all of it is one: prose after a command makes the whole
# method prose, left to the Validator. Live bugfix-trivial runs (Claude models, 2026-09-30) approved
# "python3 -m unittest -v passes; Validator reads the diff" and "Run python3 -m unittest -v via capture and
# read the diff"; the runner replayed each sentence as a shell command, it could never pass, and the run paused.
PROSE = re.compile(r"[;,]|(?:^|\s)(?:and|or|then|via|passes|pass|reads?|should|must|the|with|using|while|which|"
                   r"that|from|in|on|at|for|of|each|after|before|confirms?|verif(?:y|ies)|inspects?|shows?|prints?|outputs?|returns?)(?=\s|$)", re.IGNORECASE)
# A sentence rather than a command: a word ending in a colon ("from repo root: 2 tests OK") or a closing period after a
# word ("... tests OK."). A live parallel-diamond plan wrote "Run python3 -m unittest integration.test_check from repo
# root: 2 tests OK." and the runner replayed it whole; unittest read "from", "repo" and "OK." as modules (2026-10-01).
# A lone "." argument ("-t .") and a path ending in dots are not matched.
SENTENCE = re.compile(r"\w:(?:\s|$)|[A-Za-z0-9_)]\.$")
# An unquoted "(" that does not open $(...) is shell syntax no plain command uses: a note such as "(all 10 pass)".
# A live to-do run approved "python3 -m unittest test_todo -v (all 10 pass)" as a check, the replay ran it whole
# (Syntax error: "(" unexpected) and rejected every Validator report until the run stopped (2026-10-04).
NOTE = re.compile(r"(?<!\$)\(")
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
# `python3 -c doing a topological sort` is prose: the code after -c is one quoted argument, and unquoted code
# followed by more words is a sentence. A live architecture run approved it as AC3's method, the runner replayed
# it as a command (NameError), and the run paused (2026-09-30); another wrote `python3 -c: Kahn topological sort`.
UNQUOTED_CODE = re.compile(r"\s-c:|\s-c\s+[^\s'\"]\S*\s+\S")

RUNNERS = frozenset({"pytest", "npm", "npx", "yarn", "pnpm", "go", "cargo", "ruby", "bundle",
                     "node", "deno", "bun", "uv", "make", "cmake", "ctest", "dotnet", "mvn", "gradle",
                     "sh", "bash"})


def executable(text):
    text = text.strip()
    try:
        words = shlex.split(text)
    except ValueError:
        return False
    if not words:
        return False
    name = PurePosixPath(words[0]).name
    if name == "autocode":
        return words[1:2] == ["visual-check"]  # a check, not permission to launch another task run
    if name in ("sh", "bash") or re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", name):
        return not UNQUOTED_CODE.search(text)
    return name in RUNNERS


def commands(method):
    text = str(method).strip()
    snippets = list(re.finditer(r"`([^`]+)`", text))
    if snippets:
        result, previous = [], 0
        for snippet in snippets:
            prefix = text[previous:snippet.start()].strip()
            requested = (not prefix and not result) or bool(re.search(
                r"\b(?:runs?|executes?|invokes?)\s*$", prefix, re.IGNORECASE))
            requested |= bool(result) and prefix.lower() in ("and", ",", ", and")
            command = snippet.group(1).strip()
            if requested and executable(command):
                result.append(command)
            previous = snippet.end()
        return expectations.assertion_commands(text, result)
    if text.lower().startswith("run "):
        text = text[4:].strip()
    bare = QUOTED.sub("", text)
    return [text] if executable(text) and not any(p.search(bare) for p in (PROSE, SENTENCE, NOTE)) else []


def approved_commands(state, *, progressive_context=None):
    """Select ordinary checks or the authoritative, normalized cumulative checklist.

    The caller supplies progressive_state.context(state); proposals and tentative
    future checks are never read here. Invalid required checks fail closed.
    """
    body = (state.get("goal_contract") or {}).get("body") or {}
    task = state.get("current_task") or {}
    ids = set(task.get("acceptance_criteria") or [])
    methods = list(task.get("validation_plan") or [])
    if progressive_context:
        required = progressive_context.get("required_checks")
        if not isinstance(required, list) or not required:
            raise ValueError("Progressive verification requires a nonempty cumulative required_checks set")
        for check in required:
            extracted = commands(check.get("method", "")) if isinstance(check, dict) else []
            if not extracted:
                raise ValueError(f"Progressive required check has no executable command: {check!r}")
            methods.append(check["method"])
        methods += [check["method"] for check in product_checks(body, required)]
        return list(dict.fromkeys(command for method in methods for command in commands(method)))
    methods += [row.get("verification_method", "") for row in body.get("acceptance_criteria") or []
                if not row.get("human_review") and (not ids or row.get("id") in ids)]
    return list(dict.fromkeys(command for method in methods for command in commands(method)))


def product_checks(body, required_checks):
    """Retain prescribed product commands once their full-verification target is due.

    Contribution-only slices do not bring future product commands forward.
    Explicit original methods cannot disappear merely because a slice declares
    a different demonstration. Prose methods remain with the independent
    Validator, as on the ordinary path; no command is guessed from them.
    """
    due = {criterion for check in required_checks if check.get("relation") == "fully_verify"
           for criterion in check.get("criterion_ids", [])}
    checks = []
    for criterion in body.get("acceptance_criteria", []):
        method = criterion.get("verification_method", "")
        if criterion["id"] not in due or criterion.get("human_review") or not commands(method):
            continue
        represented = {command for check in required_checks if criterion["id"] in check.get("criterion_ids", [])
                       for command in commands(check.get("method", ""))}
        if set(commands(method)) <= represented:
            continue
        identity = hashlib.sha256(json.dumps(criterion, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        checks.append({"id": "contract-" + identity[:24], "method": method, "relation": "fully_verify",
                       "criterion_ids": [criterion["id"]], "origin": "product_contract"})
    return checks


def package_markers(command):
    """Markers required by unittest discovery with an explicit top-level directory."""
    try:
        words = shlex.split(command)
    except ValueError:
        return []
    if not any(words[i:i + 2] == ["-m", "unittest"] and "discover" in words[i + 2:]
               for i in range(len(words) - 2)):
        return []
    options = {"start": ".", "top": None}
    for i, word in enumerate(words):
        for key, flags in (("start", ("-s", "--start-directory")), ("top", ("-t", "--top-level-directory"))):
            if word in flags and i + 1 < len(words):
                options[key] = words[i + 1]
            for flag in flags:
                if word.startswith(flag + "="):
                    options[key] = word[len(flag) + 1:]
                elif len(flag) == 2 and word.startswith(flag) and word != flag:
                    options[key] = word[2:]
    if options["top"] is None:
        return []
    start, top = PurePosixPath(options["start"]), PurePosixPath(options["top"])
    if start.is_absolute() or top.is_absolute() or ".." in start.parts or ".." in top.parts:
        return []
    try:
        relative = start.relative_to(top)
    except ValueError:
        return []
    return [str(top.joinpath(*relative.parts[:i], "__init__.py")) for i in range(1, len(relative.parts) + 1)]


def require_scaffolding(workspace, paths, methods):
    """Refuse an impossible assignment before approval; never expand its scope."""
    if not workspace:
        return
    for method in methods:
        for command in commands(method):
            for marker in package_markers(command):
                if (Path(workspace) / marker).is_file():
                    continue
                if any(marker == root.rstrip("/") or marker.startswith(root.rstrip("/") + "/") for root in paths):
                    continue
                raise ValueError(f"Verification command `{command}` requires {marker}, which does not exist and "
                                 "is outside affected_paths. Assign the package marker explicitly or use a test "
                                 "command compatible with the approved scope before asking for approval.")
