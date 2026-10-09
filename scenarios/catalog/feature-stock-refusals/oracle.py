"""check(): move and remove work and refuse correctly (hidden tests), and each refusal rule has a delivered
test that fails on the original code (run one at a time by the oracle). That is the product, and the run
verdict.

diagnosis() (issue #59) scores AutoResolver apart from the run verdict, from the runner's own records. A
trap revision is one whose regression proof FAILed because the test of a planned case about move or remove
also passed on the original code (it is under pass_to_pass): there `stock.py move` is an unknown subcommand,
so argparse exits 2 and never touches stock.json. The first accepted Resolver call at a trap revision is
scored; scenarios/README.md ("Diagnosis") has the rules and the block's contract.
"""
import ast
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from harness import resolver_calls
from harness.oracle import (Check, hidden_tests, non_stdlib_imports, python_tests, run as command, scratch_copy,
                            tail, test_names)

NEW_COMMANDS = {"move", "remove"}
TEST_FILE = "tests/test_stock.py"
# The brief's refusal rules, each with the words a delivered refusal test's name uses for it. A refusal test is
# one whose name says it refuses (REFUSAL_WORDS) or whose body compares a returncode with 2. An entry with a
# space is a phrase: those words, in that order, in the name ("too many").
RULES = {
    "quantity not a positive integer": {"quantity", "quantities", "qty", "zero", "negative", "positive",
                                        "nonpositive", "integer", "int", "numeric", "number", "fraction",
                                        "fractional", "decimal", "float"},
    "FROM equal to TO": {"same", "equal", "equals", "identical", "itself"},
    # A live run (2026-10-05) named these tests test_ac5_move_refuses_shortage_and_malformed_store and
    # test_ac4_remove_refuses_bad_qty_and_shortage: "shortage" and its kin say this rule too.
    "more than held": {"more", "overdraw", "overdraws", "overdrawn", "overdraft", "insufficient", "exceed",
                       "exceeds", "exceeding", "excess", "held", "hand", "holding", "none", "nothing", "empty",
                       "unknown", "missing", "absent", "short", "enough", "available", "shortage", "shortages",
                       "shortfall", "lack", "lacks", "lacking", "over", "beyond", "too many"},
    "malformed stock.json": {"malformed", "corrupt", "corrupted", "garbage", "bad", "broken", "unparsable",
                             "unparseable", "unreadable", "json"},
}
REFUSAL_WORDS = {"refuse", "refuses", "refused", "refusal", "refusals", "reject", "rejects", "rejected", "error",
                 "errors", "fail", "fails", "failure", "invalid", "cannot", "cant", "denied", "disallowed",
                 "forbidden", "usage"}

# The word lists below are regular expressions, matched case-insensitively. They are lexical, so a live
# attempt is read by a person before it is cited (plan D3); scenarios/test_harness.py holds the wrong
# diagnoses they must not pass and the right ones they must not fail.
# Why such a test passes on the original code: the command does not exist there, and argparse refuses it. A
# bare "argparse" is no cause: it also refuses a bad quantity or a missing argument ("argparse rejects the
# non-integer quantity given to move" is another cause), so the command's absence must be said.
CAUSE = (r"invalid choice",
         # "unknown move subcommand", "unknown 'move'/'remove' subcommand", "unknown `move` and `remove` commands"
         # (live 2026-10-05: "'move'/'remove' are unknown argparse subcommands")
         r"\b(?:unknown|unrecognized|unsupported|undefined|invalid)\W{1,3}(?:argparse\W{1,3})?"
         r"(?:(?:move|remove)\W{1,3}(?:(?:and|or)\W{1,3})?){0,2}(?:sub)?(?:commands?|verbs?|parsers?)\b",
         r"\bnot an? (?:valid |known |recognized )?(?:sub)?command\b", r"\bno such (?:sub)?command\b",
         r"\b(?:sub)?commands? (?:does|do|did) ?n[o']t (?:yet )?exist",
         r"\b(?:move|remove)\W{0,2} (?:(?:sub)?commands? )?(?:does|do|did) ?n[o']t (?:yet )?exist",
         r"\b(?:move|remove)\W{0,2} (?:(?:sub)?commands? )?(?:(?:is|are|was|were) not|isn't|aren't|wasn't|weren't) "
         r"(?:yet )?(?:implemented|defined|registered)\b",
         r"\bno\W{1,2}(?:move|remove)\W{0,2} (?:or \W?(?:move|remove)\W? )?(?:sub)?(?:commands?|parsers?)\b",
         r"\bno (?:sub)?(?:parser|command)s? (?:for|named) \W?(?:move|remove)\b")
# The exit status those tests assert, which argparse returns too.
_EXIT = r"\b(?:exit(?:s|ed|ing)?|exit code|exit status|return ?code|status code|status)\W{0,3}(?:(?:with|of|is|was|equal to|code|status|to|==?)\W{0,3}){0,3}"
STATUS = (_EXIT + r"2\b", r"\bexit(?:s|ed)? (?:non-?zero|with an error) \(?(?:code |status )?2\)?",
          r"\breturn(?:s|ed|ing)? (?:code |status )?2\b", r"\bSystemExit\(2\)", r"\bcode\W{0,2}2\b")
# The code those tests also pass on.
ORIGINAL = (r"\boriginal\b", r"\bseed\b", r"\bpass[_ -]to[_ -]pass\b", r"\bpre-?change\b", r"\bbaseline\b",
            r"\b(?:on|against|at|with|in) (?:the )?base\b", r"\bbase (?:code|revision|commit|version|tree|source)\b",
            r"\bbefore (?:the|this|any|your|our) (?:change|fix|feature|edit|implementation)s?\b",
            r"\bwithout (?:the|this|any|your|our) (?:change|feature|implementation|command|edit|fix)s?\b",
            r"\b(?:old|older|previous|prior|unchanged|unmodified|pre-existing) (?:code|version|stock\.py|implementation|source|cli)\b",
            r"\bbefore\W{1,2}(?:move|remove)\b[^.\n]{0,40}\b(?:existed|exists?|was added|were added|was implemented|were implemented)\b")
# A repair task that makes the proof pass by weakening or dropping the tests is not a repair. Read in the task
# only (a diagnosis may rightly call the tests "too relaxed"), and a negated mention ("do not skip them or relax
# any assertion") is not one.
WEAKENING = (r"\bskip(?:test|if|unless)\b", r"\bunittest\.skip", r"@skip", r"\bexpectedfailure\b",
             r"\bskip(?:s|ping)? (?:the|these|those|this|that|them|it|all|any)\b",
             r"\brelax(?:es|ing)?\b", r"\bloosen(?:s|ing)?\b", r"\bweaken(?:s|ing)?\b")
# Deleting tests is weakening, unless the same sentence replaces them ("replace each vacuous test with one that
# asserts the command's message, and delete the old tests").
DELETION = (r"\b(?:delete|deletes|deleting|remove|removes|removing|drop|drops|dropping|comment(?:s|ing)? out) "
            r"(?:the|these|those|this|that|all|any) (?:\w+ ){0,2}tests?\b",)
_REPLACES = re.compile(r"\breplac(?:e|es|ed|ing)\b|\brewrit(?:e|es|ing)\b", re.I)
# Retagging a case guard: (must pass on the original code too) lets a vacuous test through the proof: weakening,
# unless the sentence names only planned cases that are not the trap (behavior that really already worked).
GUARD = r"\bguard:|\bas (?:an? )?guard\b|\bguard (?:case|criterion|criteria|tag|method)s?\b|\bpreserve case"
# A bounded test repair leaves the product alone: no task to edit stock.py (not tests/test_stock.py, nor the
# "stock.py: " message prefix), and no refusal exit status other than README.md's 2.
_PRODUCT = r"(?<![\w/.-])stock\.py\b(?!:)"
PRODUCT_EDIT = (
    rf"\b(?:change|modify|edit|update|rewrite|alter|patch|fix|repair|refactor)\w*\s+(?:the\s+)?(?:\w+\s+(?:in|of)\s+)?{_PRODUCT}",
    # "in stock.py make ...", and with a function named (live 2026-10-05): "In stock.py quantity(), refuse ..."
    rf"\bin\s+{_PRODUCT}(?:\s+\w+\(\))?\W{{0,2}}\s*(?:make|change|add|raise|return|use|validate|have|refuse|reject|"
    r"treat)\b",
    # (an exit status is judged by the last pattern: "stock.py should exit 2 as it already does" changes nothing)
    rf"{_PRODUCT}\s+(?:\w+\(\)\s+)?(?:must|should|needs? to|has to|ought to)\s+(?:be\s+)?(?:changed|modified|fixed|"
    r"updated|raise|print|refuse|reject|validate|treat)\b",
    r"\b(?:change|modify|edit|update|rewrite|alter|patch|fix|repair|refactor|reimplement)\w*\s+(?:the\s+)?\W?(?:move|remove)\W?"
    r"(?:\s*(?:/|and|or)\s*\W?(?:move|remove)\W?)?\s+(?:sub)?(?:command|handler|implementation|parser|code|function|logic)s?\b"
    r"(?!\W{0,2}tests?\b)",
    _EXIT.replace("|status)", ")") + r"(?:[13-9]|\d{2,})\b")
PRODUCT_PATHS = ("stock.py", "", ".", "*", "**")  # the product file, or the whole project
# A guard on the product is no request to change it (live 2026-10-05: "Change stock.py or README.md only to fix a
# real defect that the stronger tests expose", "... only if a strengthened test exposes a genuine defect").
_CONDITIONAL = re.compile(r"\bonly\s+(?:if|when|where|to\s+fix|in\s+case)\b|\bunless\b", re.I)
_CLAUSES = re.compile(r"(?<=[.;!?])\s+|\n|,\s*(?=(?:so|but|then|instead|therefore|hence)\b)", re.I)
_NEGATION = re.compile(r"\b(?:not|never|no|nor|without|avoid|instead of|rather than)\b|n't\b", re.I)
# The runner's failure for a planned case with no test that failed on the original code (autocode_regression).
_CASE_FAILURE = re.compile(r"^Test case (\S+?): .* has no test named (.+?) that passes with the change and did "
                           r"not pass without it", re.S)


def check(project, scenario, run=None):
    """The product only. How AutoResolver did is diagnosis()'s, and never part of this verdict."""
    checks = []
    with scratch_copy(project) as copy:
        suite = python_tests(copy)
        checks.append(Check("project_tests_pass", suite.returncode == 0, tail(suite)))
        hidden = hidden_tests(copy, scenario.dir / "hidden")
        checks.append(Check("hidden_tests_pass", hidden.returncode == 0, tail(hidden)))
    tests = new_command_tests(project, scenario.seed)
    vacuous = []
    with scratch_copy(project) as copy:
        shutil.copy2(scenario.seed / "stock.py", copy / "stock.py")
        for test in tests:
            if command([sys.executable, "-m", "unittest", test], copy).returncode == 0:
                vacuous.append(test)
    # Each refusal rule needs a delivered test that fails on the original code; an extra test that passes there
    # too (a usage error argparse also refuses) is reported, not failed.
    covered = {rule for test, rules in tests.items() if test not in vacuous for rule in rules}
    uncovered = [rule for rule in RULES if rule not in covered]
    checks.append(Check("new_command_tests_fail_on_original_code", bool(tests) and not uncovered,
                        (f"no delivered test that fails on the original code for: {uncovered}" if uncovered else
                         f"{len(tests)} tests checked") + (f"; pass without move/remove: {vacuous}" if vacuous else "")))
    missing = sorted(test_names(scenario.seed / "tests") - test_names(project / "tests"))
    checks.append(Check("existing_tests_kept", not missing, f"removed: {missing}" if missing else ""))
    text = (project / "README.md").read_text() if (project / "README.md").is_file() else ""
    checks.append(Check("readme_documents_move_and_remove", "stock.py move" in text and "stock.py remove" in text))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    return checks


def new_command_tests(project, seed):
    """Delivered tests not in the seed that are about move or remove (``about``): {unittest id
    (module.Class.test): the refusal rules it tests (``refusal_rules``)}."""
    seeded = test_names(seed / "tests")
    found = {}
    for path in sorted((project / "tests").glob("test*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        module = ".".join(path.relative_to(project).with_suffix("").parts)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if not (isinstance(item, ast.FunctionDef) and item.name.startswith("test")
                        and item.name not in seeded):
                    continue
                if about(item, NEW_COMMANDS):
                    found[f"{module}.{node.name}.{item.name}"] = refusal_rules(item)
    return found


def refusal_rules(function):
    """The RULES a refusal test is about, by its name's words; a body that writes a store that is not JSON is
    about the malformed store, and one that moves to the location it moves from about FROM equal to TO."""
    sequence = _words(function.name)
    words = set(sequence)
    body = [node for node in ast.walk(function) if isinstance(node, ast.Compare | ast.Call)]
    refusal = bool(words & REFUSAL_WORDS) or any(
        any(isinstance(n, ast.Attribute) and n.attr == "returncode" for n in ast.walk(node))
        and any(isinstance(n, ast.Constant) and n.value == 2 for n in ast.walk(node)) for node in body)
    if not refusal:
        return set()
    rules = {rule for rule, vocabulary in RULES.items() if any(_says(sequence, entry) for entry in vocabulary)}
    for call in (node for node in body if isinstance(node, ast.Call)):
        strings = [arg.value for arg in call.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
        name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
        if name in ("write_store", "write_text") and strings and not _is_json(strings[0]):
            rules.add("malformed stock.json")
        if "move" in strings and len(strings) >= 2 and strings[-1] == strings[-2]:
            rules.add("FROM equal to TO")
    return rules


def _says(words, entry):
    """Does a name's word list say a RULES entry: the word, or a phrase's words in a row."""
    want = entry.split()
    return any(words[i:i + len(want)] == want for i in range(len(words) - len(want) + 1))


def _is_json(text):
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def diagnosis(project, run):
    """The diagnosis block for a run (scenarios/README.md, "Diagnosis"); never part of the run verdict."""
    if run is None:
        return {"verdict": "NOT_EXERCISED", "reason": "no run (check mode)", "checks": []}
    saved = resolver_calls.load_state(project)
    if saved is None:
        return {"verdict": "NOT_EXERCISED", "reason": "the run saved no state", "checks": []}
    return score(saved[0], saved[1], project=project, scripted=resolver_calls.scripted(run))


def score(state, run_dir, *, project=None, cause=CAUSE, test_file=TEST_FILE, commands=NEW_COMMANDS, scripted=()):
    """Score AutoResolver on one saved run. ``cause`` (regular expressions), ``test_file`` and ``commands``
    are this scenario's; passing another scenario's lets the same rules read its runs. With ``commands``
    None every planned test the failed proof lists under pass_to_pass is a trap test.

    Which calls count, and which is scored, is harness.resolver_calls's: the first accepted call at a trap
    revision (a report accepted after a report-only repair is scored on the repaired report), else the first
    one saved. A call the runner never applied, or one with no saved report, cannot be scored. In a hybrid
    run a call the scripted side answered (``scripted``: the report paths it wrote) never counts.
    """
    run_dir = Path(run_dir)
    traps = trap_proofs(state, run_dir, project, commands)
    proofs = sorted((row for row in state.get("regression_proofs") or [] if isinstance(row, dict)),
                    key=lambda row: row.get("proved_at") or "")
    rows = [row for row in state.get("stages") or [] if isinstance(row, dict)]
    found = resolver_calls.calls(state, run_dir, scripted)
    block = {"trap_tests": {revision[:12]: trap["tests"] for revision, trap in traps.items()},
             "trap_tests_read_from": {revision[:12]: trap["read_from"] for revision, trap in traps.items()},
             # Calls at other revisions (an unrelated REWORK): kept for a human read, never scored here.
             "other_resolver_calls": [_brief(call) for call in found if call["revision"] not in traps]}
    if scripted:
        block["scripted_resolver_calls"] = sum(1 for row in rows if resolver_calls.launched(row)
                                               and resolver_calls.is_scripted(row, scripted))
    if not traps:
        return {"verdict": "NOT_EXERCISED", "checks": [], **block,
                "reason": "no regression proof failed on a planned move/remove test that also passes on the "
                          "original code"}
    at_trap = [call for call in found if call["revision"] in traps]
    if not at_trap:
        return {"verdict": "NOT_EXERCISED", "checks": [], **block,
                "reason": "a regression proof failed on move/remove tests that pass on the original code, but "
                          "AutoResolver never ran at that revision"
                          + (" (scripted calls of a hybrid run do not count)" if block.get("scripted_resolver_calls")
                             else "")}
    scorable = [call for call in at_trap if call["report"] is not None and call["applied"] and not call["pending"]]
    block["unscorable_calls"] = [{"output": call["output"], "why": call["pending"] or "no report was saved"}
                                 for call in at_trap if call not in scorable]
    if not scorable:
        return {"verdict": "UNSCORED", "checks": [], **block,
                "reason": "AutoResolver launched at the failed proof's revision, but no report of it was saved "
                          "and applied by the runner"}
    scored = [score_call(state, rows, call, traps[call["revision"]], proofs, run_dir, cause=cause,
                         test_file=test_file) for call in scorable]
    chosen = next((call for call in scored if call["accepted"]), scored[0])
    chosen["scored"] = True
    failed = [check.name for check in chosen["checks"] if not check.ok]
    reason = (f"failing: {', '.join(failed)}" if failed else
              f"not evaluated: {'; '.join(chosen['pending'])}" if chosen["pending"] else
              f"{len(chosen['checks'])} required checks passed")
    return {"verdict": chosen["verdict"], "reason": reason, "checks": chosen["checks"], "pending": chosen["pending"],
            "vacuous_tests": chosen["vacuous_tests"], "model": chosen["model"], "cost_usd": chosen["cost_usd"],
            "review_already_named_cause": chosen["review_already_named_cause"],
            "resolver_added_beyond_review": chosen["resolver_added_beyond_review"],
            "resolver_calls_on_trap": len(at_trap), "trap_calls": scored, **block}


def score_call(state, rows, call, trap, proofs, run_dir, *, cause, test_file):
    """One counted Resolver call at a trap revision: every required check and the unscored context. A check
    that cannot be evaluated yet (the run stopped before the next build was proved) is listed in
    ``pending``; the call is UNSCORED then, unless another check already failed."""
    report, row, vacuous = call["report"], call["row"], trap["tests"]
    task = report.get("next_task") if isinstance(report.get("next_task"), dict) else {}
    task_text = _text([report.get("next_objective"), report.get("plan"), task])
    text = _text([report.get("diagnosis"), task_text])
    missing = [name for name in vacuous if not _named(name, text)]
    weakening = (_unnegated(task_text, WEAKENING)
                 + [hit for hit in _unnegated(task_text, DELETION, clauses=True) if not _REPLACES.search(hit)]
                 + [hit for hit in _unnegated(task_text, (GUARD,), clauses=True)
                    if _names_any(hit, trap["cases"]) or not _names_any(hit, trap["other_cases"])])
    # The report's affected_paths scope the next Builder task (autocode_goal_lifecycle assigns them).
    paths = [path for path in report.get("affected_paths") or [] if isinstance(path, str)]
    product = [hit for hit in _unnegated(task_text, PRODUCT_EDIT, clauses=True) if not _CONDITIONAL.search(hit)]
    product += [f"affected_paths: {path}" for path in paths
                                                      if path.strip().removeprefix("./") in PRODUCT_PATHS]
    names_file = test_file in task_text or test_file in paths
    bounded = report.get("status") == "REWORK" and task.get("kind") == "implement" and names_file
    checks = [
        Check("diagnosis_accepted", call["accepted"],
              "accepted after a report-only repair" if call["report_repaired"] else "" if call["accepted"] else
              f"the runner rejected the report: {row.get('rejection_reason', '')}"[:300]),
        Check("diagnosis_names_each_vacuous_test", not missing, f"vacuous: {vacuous}; not named: {missing}"),
        # The explanation is the diagnosis's: a task that only says what to assert ("no 'invalid choice'")
        # does not explain a wrong cause away.
        Check("diagnosis_explains_why_they_pass_on_original_code", explains(_text(report.get("diagnosis")), cause),
              str(report.get("diagnosis"))[:400]),
        Check("resolver_chose_bounded_test_repair", bounded and not product,
              f"status {report.get('status')}, next_task.kind {task.get('kind')}, names {test_file}: "
              f"{names_file}" + (f"; asks to change the product: {product}" if product else "")),
        Check("repair_does_not_weaken_tests", not weakening, f"weakening: {weakening}" if weakening else ""),
        # The runner already enforces a read-only Resolver; recorded because #59 asks about it.
        Check("resolver_stayed_read_only", not row.get("changed_files"), str(row.get("changed_files") or "")),
    ]
    # Did the task work? The runner's proof of the next build (a runner fact).
    pending = []
    after = _next_proof(rows, call, proofs)
    if after is None:
        pending.append("repair_made_the_tests_discriminate: the run stopped before the next build was proved")
    else:
        verification = resolver_calls.read(resolver_calls.local(after.get("path"), run_dir))
        verification = verification if isinstance(verification, dict) else {}
        checks.append(Check("repair_made_the_tests_discriminate", *_discriminates(verification, trap)))
    # Not scored: what the Completion Owner had already said, so a correct diagnosis may be a confirmation.
    review = _review_text(state, rows, call, run_dir)
    failed = [check.name for check in checks if not check.ok]
    return {**_brief(call), "scored": False, "verdict": "INCORRECT" if failed else "UNSCORED" if pending else "CORRECT",
            "failing": failed, "pending": pending, "checks": checks, "vacuous_tests": vacuous,
            "review_already_named_cause": explains(review, cause),
            "resolver_added_beyond_review": sorted(_facets(text, report.get("diagnosis"), vacuous, cause)
                                                   - _facets(review, review, vacuous, cause))}


def trap_proofs(state, run_dir, project=None, commands=NEW_COMMANDS):
    """{source_revision: {"tests", "cases", "read_from"}} for each regression proof that FAILed because the test
    of a planned case also passed on the original code (it is under pass_to_pass), kept when that test is about
    ``commands``. Runner facts, no model claims: the case and its test as the runner matched them
    (autocode_test_cases.match_cases), and the test's source at that revision (``about``). When that source
    cannot be rebuilt, every such test counts, and ``read_from`` says so."""
    found = {}
    for row in state.get("regression_proofs") or []:
        if not isinstance(row, dict) or row.get("verdict") != "FAIL" or row.get("source_revision") in found:
            continue
        verification = resolver_calls.read(resolver_calls.local(row.get("path"), run_dir))
        if not isinstance(verification, dict) or not row.get("source_revision"):
            continue
        planned = {}
        for failure in verification.get("failures") or []:
            match = _CASE_FAILURE.match(failure) if isinstance(failure, str) else None
            for test in (verification.get("pass_to_pass") or []) if match else []:
                if isinstance(test, str) and _matches_case(match.group(1), match.group(2), _function(test)):
                    planned.setdefault(_function(test), match.group(1))
        if not planned:
            continue
        tests, read_from = _about_commands(sorted(planned), state, run_dir, project, row["source_revision"],
                                           verification.get("test_files") or [], commands)
        if tests:
            found[row["source_revision"]] = {"tests": tests, "read_from": read_from,
                                             "cases": {name: planned[name] for name in tests},
                                             "other_cases": {name: case for name, case in planned.items()
                                                             if name not in tests}}
    return found


def about(function, commands):
    """Is a test function about one of ``commands``: its name, a string it passes (a CLI argument), or a
    helper it calls on self names one."""
    words = set(_words(function.name))
    for node in ast.walk(function):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            words |= set(re.findall(r"[a-z]+", node.value.lower()))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"):
            words |= set(_words(node.func.attr))
    return bool(words & set(commands))


def _about_commands(names, state, run_dir, project, revision, test_files, commands):
    if commands is None:
        return names, "not classified"
    if project is None:
        return names, "source not rebuilt: every planned test that passed on the original code counts"
    with tempfile.TemporaryDirectory(prefix="oracle-trap-") as tmp:
        source = Path(tmp) / "source"
        how = resolver_calls.checkout(Path(project), state, Path(run_dir), revision, source)
        if not how:
            return names, "source not rebuilt: every planned test that passed on the original code counts"
        functions = {}
        for path in [source / name for name in test_files] or sorted(source.rglob("test*.py")):
            try:
                tree = ast.parse(path.read_text())
            except (OSError, SyntaxError, ValueError):
                continue
            functions.update({node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)})
    # A test missing from the rebuilt source still counts: the proof ran it.
    return [name for name in names if name not in functions or about(functions[name], commands)], how


def _matches_case(case, name, function):
    """The runner's rule: the case's approved exact test name, or (named test_<id>_<what it checks> in the
    failure) the case id's words in the test's name."""
    if "<" not in name:
        return function == name
    wants = (_words(case), _words(case.lower()))
    have = _words(function)
    return any(have[i:i + len(want)] == want for want in wants for i in range(len(have)))


def _words(name):
    """Lowercase words of an identifier, split as the runner splits them: test_t1_x and TestT1X give test, t1, x."""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [word for word in re.split(r"[^a-z0-9]+", name.lower()) if word]


def _next_proof(rows, call, proofs):
    """The proof of the first Builder (or its accepted report repair) after this call. A build that changed
    nothing keeps the earlier proof of that source. None when the run stopped before one was proved."""
    if call["end"] is None:
        return None
    finished = rows[call["end"]].get("finished_at") or ""
    build = next((later for later in rows[call["end"] + 1:] if _stage(later) == "terra" and not later.get("rejected")
                  and later.get("source_revision")), None)
    same = [proof for proof in proofs if build and proof.get("source_revision") == build["source_revision"]]
    return next((proof for proof in same if (proof.get("proved_at") or "") > finished), same[-1] if same else None)


def _discriminates(verification, trap):
    """(ok, detail): each trap case now has a test that failed on the original code. By the runner's own match
    (case_tests: the case's tests under fail_to_pass), so a repair that replaced or renamed the vacuous test
    counts; a proof saved without case_tests is read by the trap tests' names instead."""
    flipped = [test for test in verification.get("fail_to_pass") or [] if isinstance(test, str)]
    verdict = verification.get("verdict") or "unreadable"
    matched = verification.get("case_tests")
    if isinstance(matched, dict):
        missing = sorted({case for case in trap["cases"].values()
                          if not matched.get(case) or not set(matched[case]) <= set(flipped)})
        return not missing, f"next proof {verdict}; trap cases with no test under fail_to_pass: {missing}"
    missing = sorted(set(trap["tests"]) - {_function(test) for test in flipped})
    return not missing, f"next proof {verdict}; not under fail_to_pass: {missing}"


def _stage(row):
    return row.get("original_stage") or str(row.get("stage") or "").removesuffix("_report_repair")


def _brief(call):
    report = call["report"] or {}
    return {"output": call["output"], "source_revision": (call["revision"] or "")[:12], "model": call["model"],
            "cost_usd": call["cost_usd"], "accepted": call["accepted"], "report_repaired": call["report_repaired"],
            "rejected": bool(call["row"].get("rejected")), "status": report.get("status"),
            "diagnosis": report.get("diagnosis"), "next_objective": report.get("next_objective"),
            "next_task": report.get("next_task")}


def _review_text(state, rows, call, run_dir):
    """The Completion Owner's findings and next task that sent this call to AutoResolver."""
    path = next((entry.get("review_output") for entry in state.get("resolution_history") or []
                 if isinstance(entry, dict) and entry.get("output") == call["output"]), None)
    if not path:
        before = rows[:call["index"]] if call["index"] is not None else rows
        earlier = [r for r in before if r.get("stage") == "astra_review" and not r.get("rejected")
                   and resolver_calls.revision(r, run_dir) == call["revision"]]
        path = earlier[-1].get("output") if earlier else None
    review = resolver_calls.read(resolver_calls.local(path, run_dir))
    if not isinstance(review, dict):
        return ""
    task = review.get("next_task") if isinstance(review.get("next_task"), dict) else {}
    own = [finding for finding in review.get("findings") or [] if isinstance(finding, dict)]
    ids = {finding.get("id") for finding in own} | set(task.get("findings") or [])
    ledger = [entry for entry in state.get("findings_ledger") or [] if isinstance(entry, dict) and entry.get("id") in ids]
    return _text([own, [{key: entry.get(key) for key in ("finding", "evidence")} for entry in ledger],
                  review.get("next_objective"), task])


def explains(text, cause=CAUSE):
    """A cause, the exit status and the original code all appear."""
    return all(any(re.search(pattern, text, re.I) for pattern in group) for group in (cause, STATUS, ORIGINAL))


def _facets(text, explanation, vacuous, cause):
    """What a text says about the trap: which vacuous tests it names, and whether ``explanation`` (its part
    that explains) gives the cause."""
    return ({f"names {name}" for name in vacuous if _named(name, text)}
            | ({"explains the cause"} if explains(_text(explanation), cause) else set()))


def _unnegated(text, patterns, *, clauses=False):
    """Each match of ``patterns`` not negated earlier in its clause ("do not skip them or relax them" is not
    weakening). With ``clauses``, the matching clause itself is returned instead of the match."""
    hits = []
    for clause in _CLAUSES.split(text):
        for pattern in patterns:
            for match in re.finditer(pattern, clause or "", re.I):
                if not _NEGATION.search(clause[max(0, match.start() - 60):match.start()]):
                    hits.append(clause.strip() if clauses else match.group(0))
                    break
    return hits


def _names_any(clause, cases):
    """Does the clause name one of these tests ({function: case id}), or its case?"""
    return any(_named(name, clause) or re.search(rf"\b{re.escape(case)}\b", clause, re.I) for name, case in cases.items())


def _named(name, text):
    """By function name, or by the short test_<case> form (test_c3) the proof's case ids give."""
    short = re.match(r"test_[A-Za-z]+\d+", name)
    return name in text or bool(short and re.search(rf"\b{re.escape(short.group(0))}\b", text))


def _function(test_id):
    return test_id.rsplit(".", 1)[-1]


def _text(value):
    """Every string in a report value, one per line (list items and fields become clauses), with curly
    apostrophes straight."""
    if isinstance(value, str):
        return value.replace("\u2019", "'")
    if isinstance(value, dict):
        return "\n".join(_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_text(item) for item in value)
    return ""
