"""Three jobs in one conversation (issue #185): each leaves its report, the design follows the
discussion's decision, the build follows the design, and the code passes hidden tests that run
separate worker processes against one cache directory.

The hidden tests use only the service's entry point, with METADATA_CACHE_DIR provisioned as the
deploy configuration says, so any design that follows the decision can pass them. Whether the build
follows THIS design is checked against the names the design fixes: every app/ module it names exists
and every public callable it names is defined in app/ (or is a builtin or comes from a
standard-library module the code imports, such as `flock(...)` from fcntl). A name with a leading
underscore is a private helper, which the build may split or name differently, and a callable the
seed already provides (seed_callables) is the seed's, which the design may keep or remove.
"""
import ast
import builtins
import importlib
import math
import re
import sys

from harness.oracle import Check, hidden_tests, load_json, python_tests, run_checks, scratch_copy, tail

NOTE = "docs/decisions/metadata-cache.json"
SPAN = re.compile(r"`([^`\n]+)`")
MODULE = re.compile(r"^app/[\w/]+\.py$")
SIGNATURE = re.compile(r"^([A-Za-z_]\w*)\(([^()]*)\)$")
IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")
# Citing the decision in the design's own words: "shared-file", "a shared file cache", "a shared,
# host-local file-backed cache" (a live design that followed it said only the last).
SHARED_FILE = re.compile(r"\bshared(?:-|[\s,]+(?:[\w-]+[\s,]+){0,2})file", re.I)
# The methods functools.lru_cache adds to the function it wraps (the seed's metadata()).
LRU_METHODS = {"cache_clear", "cache_info", "cache_parameters"}
# A design's rejected options are not what it decides; their names and words do not count.
SET_ASIDE = re.compile(r"^#{1,6}\s*(rejected|alternatives?|considered|not chosen)\b", re.I | re.M)


def check(project, scenario, run=None):
    checks = []
    note, error = load_json(project / NOTE)
    checks.append(Check("decision_note_kept", isinstance(note, dict) and note.get("recommendation") == "shared-file",
                        error or f"recommendation={(note or {}).get('recommendation')!r}"))
    design_path = design_document(project, run)
    whole = (project / design_path).read_text() if design_path else ""
    text = decided(whole)
    checks.append(Check("design_document_written", bool(whole.strip()), design_path or "no design under docs/design/"))
    follows = ((NOTE in text or SHARED_FILE.search(text)) and "METADATA_CACHE_DIR" in text
               and re.search(r"os\.replace|\brename|\batomic", text, re.I))
    checks.append(Check("design_follows_decision", bool(follows),
                        "" if follows else "outside its rejected options, the design must cite the decision "
                        "(note or shared-file), use the shared METADATA_CACHE_DIR and say how a write becomes "
                        "visible atomically"))
    freshness = freshness_policy(text)
    missing_policy = [name for name, satisfied in freshness.items() if not satisfied]
    checks.append(Check("design_settles_freshness", not missing_policy,
                        "missing freshness decisions: " + ", ".join(missing_policy) if missing_policy else ""))
    modules, signatures = named_interface(text)
    defined, params = definitions(project)
    seeded = seed_callables(scenario.seed)
    missing = [m for m in modules if not (project / m).is_file()] + [
        n for n in signatures if n not in defined and n not in seeded]
    differs = [f"{name}({', '.join(want)}) vs {[list(p) for p in params.get(name, ())]}"
               for name, want in signatures.items() if want is not None and name in params
               and tuple(want) not in params[name]]
    # With a run record, the build turn must also have changed app/: a design that keeps the seed's
    # interface names only what is already there (a live build turn that wrote nothing passed this).
    built = [path for path in ((run or {}).get("turns") or [{}, {}, {}])[-1].get("changed_files") or []
             if path.startswith("app/")] if run and len(run.get("turns") or []) == 3 else None
    checks.append(Check("build_follows_design", bool(modules and signatures) and not missing and not differs
                        and built != [],
                        f"design names modules {modules} and callables {sorted(signatures)}; "
                        f"missing from app/: {missing}; other parameters: {differs}"
                        + ("; the build turn changed nothing under app/" if built == [] else "")))
    with scratch_copy(project) as copy:
        suite = python_tests(copy)
        checks.append(Check("project_tests_pass", suite.returncode == 0, tail(suite)))
        hidden = hidden_tests(copy, scenario.dir / "hidden")
        checks.append(Check("hidden_tests_pass", hidden.returncode == 0, tail(hidden)))
    checks += conversation_checks(run)
    return checks


def design_document(project, run):
    """The design turn 2 wrote; without a run record, the one design document besides the README.
    The folder's README is an index either way: a design turn may add its design to it."""
    if run and len(run.get("turns") or []) == 3:
        written = [path for path in run["turns"][1].get("changed_files") or []
                   if path.startswith("docs/design/") and path.endswith(".md") and not path.endswith("/README.md")]
        return written[0] if len(written) == 1 else ""
    folder = project / "docs" / "design"
    found = sorted(path.relative_to(project).as_posix() for path in folder.glob("*.md")
                   if path.name != "README.md") if folder.is_dir() else []
    return found[0] if len(found) == 1 else ""


def decided(text):
    """The design without the sections that set options aside (Rejected, Alternatives, ...)."""
    kept, skipping = [], False
    for line in text.splitlines():
        if re.match(r"^#{1,6}\s", line):
            skipping = bool(SET_ASIDE.match(line))
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def freshness_policy(text):
    """Explicit decisions, outside rejected options; report each absent obligation."""
    patterns = {
        "ttl": r"\bttl\b|ttl_seconds|time.to.live",
        "caller_configurable": r"caller.configur|caller.{0,40}(?:controls?|chooses?|sets?|supplies?).{0,40}\bttl(?:_seconds)?\b"
                               r"|(?:configur\w*|parameter|argument).{0,100}\bttl(?:_seconds)?\b"
                               r"|\bttl(?:_seconds)?\b.{0,100}(?:configur\w*|parameter|argument)",
        "default_3600": r"default.{0,40}(?:\b3,?600\b|\b(?:one|1).hour\b)"
                        r"|(?:\b3,?600\b|\b(?:one|1).hour\b).{0,40}default"
                        r"|\bttl\s*:\s*(?:float|int)\s*=\s*3600",
        "injectable_clock": r"inject\w*.{0,40}\bclock\b|\bclock\b.{0,60}Callable\["
                            r"|`clock`.{0,60}(?:zero.argument|callable)",
        "expiry_at_equality": r"(?:>=|greater than or equal|at (?:least|or after)).{0,40}(?:ttl|time.to.live)"
                              r"|younger than.{0,40}(?:ttl|time.to.live)"
                              r"|(?:age|elapsed|clock).{0,120}<(?!=).{0,40}(?:ttl|time.to.live)",
        "no_stale": r"(?:never|no|not).{0,40}stale|stale.{0,40}(?:never|not|forbidden)",
        "single_refresh": r"\bfetch(?:es|ing)?(?:\([^)]*\))?`?\s+(?:new\s+)?(?:metadata\s+)?(?:exactly\s+|only\s+)?once\b",
        "atomic_publication": r"os\.replace|\brename|\batomic",
        "worker_reuse": r"workers?.{0,100}(?:reus\w*|shared)|(?:reus\w*|shared).{0,100}workers?",
        "upstream_errors": r"(?:propagat\w*|rais\w*|return\w*).{0,100}(?:upstream|fetch|HTTP).{0,40}errors?"
                           r"|(?:upstream|fetch|HTTP).{0,80}(?:errors?|fail\w*).{0,100}(?:propagat\w*|rais\w*|return\w*)",
    }
    text = decided(text)
    policy = {name: bool(re.search(pattern, text, re.I | re.S)) for name, pattern in patterns.items()}
    for span in SPAN.findall(text):
        try:
            function = ast.parse("def " + span.strip() + ": pass").body[0]
        except SyntaxError:
            continue
        if isinstance(function, ast.FunctionDef) and any(arg.arg in ("ttl", "ttl_seconds") for arg in
                [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]):
            policy["caller_configurable"] = True
    # Positive clauses cannot override an explicit contradictory policy elsewhere in the design.
    if re.search(r"\b(?:exclud\w*|except|not(?:\s+at)?)\s+(?:at\s+)?equality\b"
                 r"|\b(?:fresh|hit|reuse)\b[^.\n]{0,100}<=\s*`?ttl"
                 r"|\b(?:expir\w*|stale)\b[^.\n]{0,100}>(?!=)\s*`?ttl", text, re.I):
        policy["expiry_at_equality"] = False
    if re.search(r"\bttl(?:_seconds)?\b[^.\n]{0,60}\b(?:fixed|hard.coded|not configurable)\b"
                 r"|\b(?:fixed|hard.coded)\b[^.\n]{0,60}\bttl(?:_seconds)?\b"
                 r"|\bcallers?[^.\n]{0,40}(?:cannot|can't|may not)[^.\n]{0,40}(?:override|configure|set|change)", text, re.I):
        policy["caller_configurable"] = False
    return policy


def named_interface(text):
    """The app/ modules and the callables the design names in code spans: {name: parameters or None}.

    `Name(a, b)` with plain parameter names is a signature the code must have; `name()` or a call with
    arguments (`Name(os.environ.get(...), 3600)`) only names the callable. `_name(...)` is a private
    helper and fixes nothing: a live design listed its helpers as example names, and the build merged
    two of them. An argument declared Callable is an injected callback, not another exported
    function; a separately declared typed function with that name still binds the build."""
    spans = SPAN.findall(text)
    modules = sorted({span for span in spans if MODULE.match(span)})
    callbacks = set()
    declarations = {}
    for span in spans:
        try:
            function = ast.parse("def " + span.strip() + ": pass").body[0]
        except SyntaxError:
            continue
        if not isinstance(function, ast.FunctionDef):
            continue
        positional = [*function.args.posonlyargs, *function.args.args]
        arguments = [*positional, *function.args.kwonlyargs]
        if function.returns or function.args.kwonlyargs or function.args.defaults or any(arg.annotation for arg in arguments):
            declarations[function.name] = [arg.arg for arg in positional]
        for argument in arguments:
            if ((argument.annotation and "Callable" in ast.unparse(argument.annotation))
                    or re.search(r"`" + re.escape(argument.arg) + r"`.{0,60}(?:zero.argument|callable|callback)", text, re.I)):
                callbacks.add(argument.arg)
    signatures = {name: args for name, args in declarations.items() if not name.startswith("_")}
    for span in spans:
        match = SIGNATURE.match(span.strip())
        if not match or match.group(1).startswith("_") or match.group(1) in callbacks:
            continue
        names = [part.strip() for part in match.group(2).split(",") if part.strip()]
        exact = bool(names) and all(IDENTIFIER.match(name) for name in names)
        if exact or match.group(1) not in signatures:
            signatures[match.group(1)] = names if exact else signatures.get(match.group(1))
    return modules, signatures


def seed_callables(seed):
    """Callables the seed already provides: what definitions() finds there, and the methods of an
    lru_cache-wrapped function. They are the seed's, not the design's: a design may keep or remove
    them, and live designs that removed the seed's cache named it (`functools.lru_cache(maxsize=256)`,
    "`cache_clear()`: Removed") while their builds did just that."""
    names, _ = definitions(seed)
    for path in (seed / "app").rglob("*.py") if (seed / "app").is_dir() else []:
        if "lru_cache" in path.read_text():
            names |= LRU_METHODS
    return names


def definitions(project):
    """Names the code can call (app/ definitions, builtins, its standard-library imports), and the
    positional parameters of each app/ function or class (its __init__), self and cls left out.
    The math module's names count too: a design's formula (`ceil(3600 / TTL_seconds)`) is
    arithmetic, not interface, and a live build that computed it another way failed on it."""
    names, params = set(dir(builtins)) | set(dir(math)), {}
    for path in (project / "app").rglob("*.py") if (project / "app").is_dir() else []:
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                function = node if not isinstance(node, ast.ClassDef) else next(
                    (item for item in node.body if isinstance(item, ast.FunctionDef) and item.name == "__init__"), None)
                if function is not None:
                    args = [arg.arg for arg in [*function.args.posonlyargs, *function.args.args]]
                    params.setdefault(node.name, set()).add(tuple(a for a in args if a not in ("self", "cls")))
            elif isinstance(node, (ast.Import, ast.ImportFrom)) and not getattr(node, "level", 0):
                modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for module in modules:
                    if module.split(".")[0] in sys.stdlib_module_names:
                        try:
                            names |= set(dir(importlib.import_module(module)))
                        except ImportError:
                            pass
    return names, params


def conversation_checks(run):
    if run is None:
        return []
    turns = run.get("turns") or []
    checks = [Check("three_turns_in_one_run", len(turns) == 3, f"{len(turns)} turns recorded")]
    if len(turns) != 3:
        return checks
    discuss, design, build = turns

    def named(prefix, rows):
        return [Check(f"{prefix}_{row.name}", row.ok, row.detail) for row in rows]

    def changed_only(turn, *allowed):
        changed = turn.get("changed_files") or []
        stray = [path for path in changed if not path.startswith(allowed)]
        return Check("changed_only_its_report", bool(changed) and not stray,
                     f"changed {changed}, allowed {list(allowed)}")

    checks += named("discuss_turn", [*run_checks(discuss, workflow="discuss", no_build=True, max_questions=3),
                                     changed_only(discuss, "docs/decisions/")])
    # A new design is produced by the build pipeline, and "Build it." builds that design as
    # approved: the user still approves each turn's plan.
    checks += named("design_turn", [*run_checks(design, workflow="design", plan_approved=True),
                                    changed_only(design, "docs/design/")])
    checks += named("build_turn", [*run_checks(build, workflow="build", no_requirements=True, plan_approved=True,
                                               max_questions=0), changed_only(build, "app/", "tests/")])
    checks.append(Check("build_turn_checked_the_design", "check_design" in (build.get("model_stages") or []),
                        f"turn 3 model stages: {build.get('model_stages')}"))
    return checks
