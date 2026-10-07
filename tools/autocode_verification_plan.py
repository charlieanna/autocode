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
    from . import autocode_toolchain_requirements as toolchain_requirements
    from . import autocode_verification_schedule as schedule, autocode_brief_obligations as brief_obligations, autocode_risk_obligations as risk_obligations
except ImportError:
    import autocode_verification_expectations as expectations
    import autocode_toolchain_requirements as toolchain_requirements
    import autocode_verification_schedule as schedule, autocode_brief_obligations as brief_obligations, autocode_risk_obligations as risk_obligations

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


# Every command in backticks in a check plan is replayed as a check that must exit 0 unless its exit status is
# declared (assertion_commands). A live repair task's validation plan named usage errors in backticks and said
# "check that each exits 2", so its Validator could never pass (2026-10-06).
EXPECTED_FAILURE_RULE = (
    "Every command a check plan names in backticks (an acceptance criterion's verification_method, a task's "
    "validation_plan step) is replayed by the runner as a check that must exit 0. Check a command that must fail "
    "(a usage error, a refused input) inside a test, or name it as \"run `X` and assert exit N\" right after the "
    "commands (N/M with one status per command for several), or wrap it so it exits 0 exactly when it fails as it "
    "should, for example sh -c 'X; test $? -eq 2'.")

# Clean replay refuses a Validator check that runs git status (autocode_check_replay.WORKTREE_STATE). Live design
# plans told the Validator to run one anyway, in the contract and in the Completion Reviewer's and Resolver's
# tasks, and the replay refused every report that obeyed (#185, 2026-10-07). This pattern is narrower than the
# replay's because it reads prose: git, its global options (-C PATH, --no-pager), then the status subcommand, also
# as a Python argument list ('git','status'); never "git diff --stat ... app/status.py" or "the exit status". It
# cannot tell an instruction from a prohibition ("do not run git status"), so the rule says not to name it at all.
_ARG_SEP = r"""(?:\s+|['"]\s*,\s*['"])"""
# -c VALUE and -C PATH take only their own branch: were -c also a plain option, a run of them would have 2**n parses.
GIT_STATUS = re.compile(rf"""\bgit(?:{_ARG_SEP}(?:-c{_ARG_SEP}[^\s'",]+|-(?!c{_ARG_SEP})-?[a-z][\w-]*(?:=[^\s'",]+)?))*"""
                        rf"""{_ARG_SEP}status\b""", re.IGNORECASE)
GIT_STATUS_RULE = (
    "Never name git status in a check plan (an acceptance criterion's verification_method, a task's validation_plan "
    "or requirements), not even to forbid it: it reads the working tree's Git state, not the product. The runner "
    "refuses a Validator check that runs it (the Validator is told so) and refuses a plan that names it. Check the "
    "delivered files and behavior instead. Scope needs no such check: the runner pauses a Builder that changes a "
    "file outside its task's affected_paths (when the task names them), and rejects a workflow job's change "
    "outside the paths that job may write.")


def task_rows(task, name):
    """A task's rows the Validator follows, labelled for refuse_git_status: validation_plan, then requirements."""
    task = task or {}
    return [(f"{name}.{field}", text) for field in ("validation_plan", "requirements")
            for text in task.get(field) or []]


def refuse_git_status(rows):
    """Refuse a new plan's (where, text) row that names git status, with GIT_STATUS_RULE as the reason.

    Callers pass only what an author has just written (a draft contract, a Completion Reviewer's or Resolver's
    next_task, a new progressive proposal), so the author gets a report repair. An approved contract, an assigned
    task or a saved progressive plan is never checked here again: a run saved before this rule keeps them.
    """
    named = []
    for where, text in rows:
        text = str(text)
        try:
            parts = [text, *commands(text)]
        except ValueError:  # a malformed exit declaration is refused where commands are checked, not here
            parts = [text]
        if any(GIT_STATUS.search(part) for part in parts):
            named.append(f"{where} `{text if len(text) <= 240 else text[:237] + '...'}`")
    # Every row at once: live plans named it in two or more, and a repair that fixed only the one named would
    # spend the report's repairs one row at a time.
    if named:
        raise ValueError("; ".join(named) + (" name" if len(named) > 1 else " names") + " git status. "
                         + GIT_STATUS_RULE)


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
    methods += toolchain_requirements.initial_validation(state)
    methods += [row.get("verification_method", "") for row in body.get("acceptance_criteria") or []
                if not row.get("human_review") and (not ids or row.get("id") in ids)]
    return list(dict.fromkeys(command for method in methods for command in commands(method)))


def launch_commands(state, *, progressive_context=None):
    """All declared commands whose tools must work before a contained stage."""
    result = approved_commands(state, progressive_context=progressive_context)
    regression = state.get('settings', {}).get('regression') or {}
    result.extend(regression[key] for key in ('test_command', 'regression_command')
                  if regression.get(key))
    return list(dict.fromkeys(result))


def obligations(state, *, progressive_context=None):
    """Project the plan before approval without inventing executable coverage.

    Selectors and natural-language claims are declarations, not collected test
    IDs. Only a subsequent runner receipt can attest inventory/environment.
    This projection never changes an approved command or execution obligation.
    """
    contract = state.get("goal_contract") or {}
    body = contract.get("body") or {}
    task = state.get("current_task") or body.get("initial_task") or {}
    selected = set(task.get("acceptance_criteria") or [])
    criteria = body.get("acceptance_criteria") or []
    checks = []
    for row in criteria:
        method = row.get("verification_method", "")
        checks.append({"criterion_ids": [row["id"]], "criterion_identity": hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "commands": commands(method) if not row.get("human_review") else [],
            "due_in_current_task": not selected or row["id"] in selected,
            "purpose": "human_review" if row.get("human_review") else "approved_acceptance",
            "claim": row.get("criterion"), "method": method,
            "collected_test_ids": None, "environment": None,
            "uncertainty": ["Test inventory and execution conditions require runner collection"]})
    for method in task.get("validation_plan") or []:
        checks.append({"criterion_ids": sorted(selected), "commands": commands(method),
                       "purpose": "approved_task_check", "claim": method,
                       "collected_test_ids": None, "environment": None,
                       "uncertainty": ["Exact CID-to-test coverage is not declared by a command string"]})
    for check in checks:
        check["collection_recipe"] = [{"command": command, "collector": schedule.collection_kind(command),
                                       "minimum_tests": 1 if schedule.collection_kind(command) else None,
                                       "require_complete_ids_for_reuse": True} for command in check["commands"]]
        check["environment"] = "runner_clean_copy"
    recipe_complete = bool(checks) and all(check["commands"] and all(
        row["collector"] for row in check["collection_recipe"]) for check in checks)
    original_brief = {"supported_syntax": "Explicit Python CLI one-per-line output format with successful exit",
                      "inventory": brief_obligations.inventory(state),
                      "protected_manifest": body.get(brief_obligations.KEY),
                      "scope": "Current criterion slice during build; every observation on the current source before completion"}
    lifecycle_risks = {"supported_protocols": "Source-declared Python lease fencing and transactional outbox recovery; "
                                              "a three-interpreter race when the source states atomicity under contention",
                       "inventory": risk_obligations.inventory(state), "protected_manifest": body.get(risk_obligations.KEY),
                       "scope": "Only disclosed API lifecycle promises; current slice then whole-product clean replay"}
    return {"contract_hash": contract.get("hash"), "checks": checks, "original_brief": original_brief,
            "lifecycle_risks": lifecycle_risks,
            "required_commands": approved_commands({**state, "current_task": task}, progressive_context=progressive_context),
            "required_commands_scope": "current_task",
            "phases": ["builder_feedback", "runner_regression", "independent_clean_replay",
                       "visual_acceptance", "mandatory_final_execution"],
            "environment_recipe": {"runner_clean_copy": {
                "cwd": "repository root in a fresh source copy", "source": "full current source snapshot",
                "environment": "credential-scrubbed inherited environment; CI=1; PYTHONDONTWRITEBYTECODE=1",
                "dependencies": "project dependencies; virtualenv may be linked from the main checkout",
                "fixtures": "source and copied generated/vendor inputs; explicit seed variables are identity-bound",
                "reuse": "only named Python collectors with a fully hashed virtualenv; global runtimes execute fresh",
                "parallel": False, "attested": False}},
            "plan_recipe_complete": recipe_complete, "execution_inventory_complete": False,
            "policy": "No phase substitutes for another. Approved commands still execute. Reuse is limited "
                      "to completed runner proof of the identical check in the same validation obligation.",
            "complete": False, "uncertainty": ["No preapproval runner-collected test inventory or environment "
                                               "attestation is implied by this declaration"]}


def repetitions(state, *, progressive_context=None):
    """Explicit repeated invocations remain obligations, not cache duplicates."""
    body = (state.get("goal_contract") or {}).get("body") or {}
    task = state.get("current_task") or {}
    selected = set(task.get("acceptance_criteria") or [])
    methods = list(task.get("validation_plan") or []) + [row.get("verification_method", "")
        for row in body.get("acceptance_criteria") or []
        if not row.get("human_review") and (not selected or row.get("id") in selected)]
    if progressive_context:
        methods += [row["method"] for row in progressive_context.get("required_checks", [])]
    else:
        methods += toolchain_requirements.initial_validation(state)
    result = {}
    for method in methods:
        extracted = commands(method)
        # Count repeated snippets in one instruction, not the same command
        # cited by two criteria. No fuzzy command equivalence is used.
        count = 2 if re.search(r"\b(?:twice|two times)\b", re.sub(r"`[^`]*`", "", method), re.I) else 1
        for command in extracted:
            result[command] = max(result.get(command, 1), extracted.count(command), count)
    return result


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
