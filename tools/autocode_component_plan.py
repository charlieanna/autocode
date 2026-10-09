"""Pure admission for the supported generated component brief, not generic test roots.

The original task and any caller-saved root bind ownership. Model constraints and
workspace names confer no authority. Flow checks cover explicit Docker/container,
Compose/smoke and gateway-proxy contradictions, not arbitrary natural language.
"""
from __future__ import annotations

import re
import shlex

try:
    from .autocode_test_root import normalize
except ImportError:
    from autocode_test_root import normalize


_HEADER = re.compile(r"\AImplement the ([A-Za-z0-9][A-Za-z0-9._-]{0,63}) component of a larger system:")
_OWNERSHIP = re.compile(r"Own only the directory ([^;]+); do not create or edit any file outside it\.")
_OPERATIONS = {
    "Docker build": r"(?:run |execute )docker(?: image)? build\b|build (?:the |a )?(?:docker |container )image\b",
    "container run": r"(?:run |execute )docker run\b|(?:the )?container (?:starts?|runs?)\b|(?:start|run) (?:the |a )?container\b",
    "Compose smoke": r"(?:run |execute )docker compose (?:up|build)\b|(?:run|execute) (?:the )?(?:compose|cross-service) smoke\b",
    "gateway proxy": r"(?:the )?gateway(?: \([^)]*\))? (?:proxies|proxying)\b|(?:test|exercise|run) (?:the )?gateway proxy\b",
}
_CLI = {"Docker build": r"docker(?: image)? build", "container run": r"docker run",
        "Compose smoke": r"docker compose (?:up|build)"}
_QUOTED = r'''`[^`]*`|'[^']*'|"(?:\\.|[^"\\])*"'''
_SEPARATORS = r";|(?<=[.!?])\s+|,\s+|\b(?:but|however|yet|then)\b"
_AFFIRMATIVE = "(?:" + "|".join(_OPERATIONS.values()) + ")"
_DEFERRED = re.compile(
    r"\A\s*,?\s*(?:deferred (?:to|until) (?:the )?(?:later |separate )?integration\b|"
    r"(?:during|at) (?:(?:later|separate) integration|integration time)\b|"
    r"(?:exercised|verified|tested|executed) at integration time\b|"
    r"(?:these calls|requests|to the real store) at integration time\b)|"
    r"\(\s*(?:exercised|verified|tested|executed) at integration time\b", re.I)
_PROHIBITED = {
    "Docker build": r"\b(?:no|do not|cannot|must not) (?:run )?docker(?: image)? build\b|"
                    r"\b(?:do not|cannot|must not) build (?:the |a )?(?:docker|container) image\b|"
                    r"\bdocker(?: image)? build(?:s|ing)? (?:is|are) (?:prohibited|forbidden|not allowed)\b|"
                    r"\b(?:image|images) (?:is|are) not built in this run\b",
    "container run": r"\b(?:no|do not|cannot|must not) (?:start|run) (?:the |a )?container\b",
    "Compose smoke": r"\b(?:no|do not|cannot|must not) (?:run )?docker compose\b",
    "gateway proxy": r"\b(?:no|do not|cannot|must not) (?:run |execute )?gateway proxy(?:ing)?\b",
}
_OUTPUT_SUFFIX = re.compile(r"\.(?:py|pyi|js|jsx|ts|tsx|html|css|scss|json|ya?ml|toml|ini|cfg|sh|sql|md|txt|png|jpe?g|svg|go|rs|java|c|h|cpp|swift)$", re.I)
_COMPONENT_PATH = re.compile(r"^(?:(?:[A-Za-z]:)?[/\\]+|\.{1,2}/|:\([^)]*\))?components[/\\]")
_ROOT_PATH = re.compile(r"^(?:[/\\]|[A-Za-z]:[/\\]|\.{1,2}/|~[/\\])")
_OUTPUT_NAMES = {"Dockerfile", "Makefile", "README", "LICENSE", "tests", "src", "docs",
                 ".gitignore", ".dockerignore", ".env", ".npmrc", ".editorconfig"}


def _split(text, separator):
    """Split supported flow punctuation/conjunctions, never quoted arguments."""
    start, parts = 0, []
    for match in re.finditer(_QUOTED + "|(?P<separator>" + separator + ")", text, re.I):
        if match.lastgroup == "separator":
            parts.append(text[start:match.start()])
            start = match.end()
    return [*parts, text[start:]]


def _literal(clause):
    """A single lowercase Docker CLI, not a descriptive predicate or shell parser.

    Build has one shell-token context or named option syntax; run has arguments;
    Compose up/build can be bare. A quoted span ends after its closing backtick.
    This recognizes intent, not option validity, arbitrary prose, shell sequences
    or executable success.
    """
    for operation, cue in _CLI.items():
        occurrence = re.match(r"\s*`?(?:" + cue + r")\b", clause)
        if not occurrence:
            continue
        command = clause.lstrip()
        end = occurrence.end()
        if command.startswith('`'):
            closing = command.find('`', 1)
            if closing < 0:
                continue
            end = len(clause) - len(command) + closing + 1
            command = command[1:closing]
        else:
            command = _split(command, r"\(\s*(?:exercised|verified|tested|executed) at integration time\b")[0]
        try:
            arguments = shlex.split(command)[len(occurrence[0].strip(' `').split()):]
        except ValueError:
            continue
        if operation == 'Docker build' and not (len(arguments) == 1
                or any(re.match(r"--?[A-Za-z]", arg) for arg in arguments)) or operation == 'container run' and not arguments:
            continue
        return operation, end
    return None


def effective_root(task: str, test_root):
    """Read caller ownership without rewriting saved settings or approval bindings.

    Shipped component TaskRuns saved the generated task but no test_root. Only its
    exact, single ownership declaration supplies that missing root. An explicit
    saved root must still agree; generic tasks gain no inferred scope.
    """
    if not re.match(r"\AImplement the .*? component of a larger system\b", task, re.S):
        return test_root
    header = _HEADER.match(task)
    owned = _OWNERSHIP.findall(task)
    if (not header or ".." in header[1] or len(owned) != 1
            or len(re.findall(r"\bImplement the \S+ component of a larger system:", task)) != 1
            or task.count("Own only the directory") != 1
            or owned[0] != f"components/{header[1]}/"):
        raise ValueError("Component plan: malformed or ambiguous generated ownership declaration")
    prefix = owned[0][:-1]
    if test_root is None:
        return prefix
    try:
        root = normalize(test_root) if isinstance(test_root, str) else None
    except ValueError:
        root = None
    if root != prefix:
        raise ValueError("Component plan: generated component ownership disagrees with caller test_root")
    return root


def recovery(task: str, test_root) -> dict:
    """Classify repair from immutable caller inputs, never model text or stop reasons."""
    try:
        effective_root(task, test_root)
    except ValueError:
        return {'new_run_required': True, 'edit_required': False, 'action': 'fresh_run', 'feedback_action': None,
                'recovery_hint': 'Preserve this stopped run and start a fresh component run with a valid original caller '
                                 'ownership declaration and matching caller test root, using TaskRun.start. '
                                 'The saved caller binding is immutable; editing the plan or requesting a new draft '
                                 'cannot repair it. The fresh plan still needs approval.'}
    return {'edit_required': True, 'action': '--edit-goal FILE', 'feedback_action': '--feedback TEXT',
            'recovery_hint': 'Correct the component plan or request a new draft with planning feedback. '
                             'A corrected draft needs fresh approval; unchanged resume cannot repair this contract.'}


def validate(task: str, test_root, body: dict) -> None:
    """Raise ValueError without mutation for an inadmissible generated component plan.

    Only the generated header activates policy. Its single exact ownership sentence
    must match the component id and any saved caller root. Paths are literal, never
    selectors. Deliverables classify confident filesystem outputs, not abstract
    labels. Flow checks cover supported CLI/imperative contradictions and typed
    operation-wide permission bans, not arbitrary prose intent.
    """
    root = effective_root(task, test_root)
    if not re.match(r"\AImplement the .*? component of a larger system\b", task, re.S):
        return
    prefix = root
    paths = [(f"milestone {row.get('id')}", path)
             for row in body.get("milestones", []) for path in row.get("affected_paths", [])]
    paths += [("initial_task", path) for path in body.get("initial_task", {}).get("affected_paths", [])]
    for item in body.get("deliverables", []):
        if not isinstance(item, str):
            continue
        text = item.strip()
        token = not re.search(r"\s", text)
        component_path = _COMPONENT_PATH.match(text)
        if (_ROOT_PATH.match(text) or (token and text.endswith(('/', '\\')))
                or (token and (_OUTPUT_SUFFIX.search(text) or text in _OUTPUT_NAMES
                        or any(char in text for char in "*?[]\\")))
                or (component_path and (token or _OUTPUT_SUFFIX.search(text)))):
            paths.append(("deliverables", item))
    for location, path in paths:
        try:
            literal = normalize(path) if isinstance(path, str) else None
        except ValueError:
            literal = None
        if literal is None or not (literal == prefix or literal.startswith(prefix + "/")):
            raise ValueError(f"Component plan: {location} affected/output path {path!r} is not literal ownership under {prefix}/")

    for step in body.get("end_to_end_flow", []):
        clauses = _split(step, _SEPARATORS)
        for index, clause in enumerate(clauses):
            if index:
                clause = re.sub(r"^\s*and\s+(?=" + _AFFIRMATIVE + ")", "", clause, flags=re.I)
            literal = _literal(clause)
            parts = ([clause] if (literal and not clause.lstrip().startswith('`'))
                     or re.match(r"\s*(?:run|execute) docker\b", clause, re.I)
                     or (not literal and not re.match(r"\s*" + _AFFIRMATIVE, clause, re.I))
                     else _split(clause, r"\s+and\s+(?=" + _AFFIRMATIVE + ")"))
            for member, part in enumerate(parts):
                literal = _literal(part)
                matches = ([literal] if literal else [(operation, match.end())
                    for operation, cue in _OPERATIONS.items()
                    if (match := re.match(r"\s*(?:" + cue + r")", part, re.I))])
                for operation, end in matches:
                    context = re.sub(_QUOTED, ' ', part[end:])
                    if literal and part.lstrip().startswith('`') and re.match(
                            r"\s+(?:(?:is|are) not|will not be) (?:required|executed|run|built)\b", context, re.I):
                        continue
                    adjacent = (clauses[index + 1] if member == len(parts) - 1 and index + 1 < len(clauses) else '')
                    if _DEFERRED.search(context) or _DEFERRED.match(adjacent):
                        raise ValueError(f"Component plan: mandatory end_to_end_flow {operation} is explicitly deferred to integration")
                    for boundary in body.get("permission_boundaries", []):
                        ban = r"(?:" + _PROHIBITED[operation] + r")(?=\s*(?:[.;,:]|$|here\b|in this run\b))"
                        if re.search(ban, boundary, re.I):
                            if operation == "Docker build" and not re.search(r"\b(?:docker|container)\b", boundary, re.I):
                                continue
                            raise ValueError(f"Component plan: mandatory end_to_end_flow {operation} is explicitly prohibited")
