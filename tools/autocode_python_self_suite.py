"""Recognize literal Python commands selecting the test that launches them (#3447).

This is a source check, not a Python interpreter or a sandbox. Only known subprocess
calls, literal arguments and a few ordinary file-relative path expressions are read.
Unknown dynamic commands/cwd/selectors are left alone; guidance forbids self-suite
wrappers even when this bounded check cannot recognize their construction.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import re
import shlex
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _Reference:
    name: str


UNKNOWN = object()
SPAWNS = {"subprocess." + name for name in ("run", "call", "check_call", "check_output", "Popen")}
SHELL_SPAWNS = {"os.system", "subprocess.getoutput", "subprocess.getstatusoutput"}


def _value(node, names):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        value = names.get(node.id, UNKNOWN)
        return "python" if value == _Reference("sys.executable") else value
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [_value(item, names) for item in node.elts]
        return values if all(item is not UNKNOWN for item in values) else UNKNOWN
    if isinstance(node, ast.Attribute):
        owner = _value(node.value, names)
        if isinstance(owner, _Reference):
            name = owner.name + "." + node.attr
            return "python" if name == "sys.executable" else _Reference(name)
        if isinstance(owner, Path) and node.attr == "parent":
            return owner.parent
    if isinstance(node, ast.BinOp):
        left, right = _value(node.left, names), _value(node.right, names)
        if isinstance(node.op, ast.Add) and type(left) is type(right) and isinstance(left, (str, list)):
            return left + right
        if isinstance(node.op, ast.Div) and isinstance(left, Path) and isinstance(right, str):
            return left / right
    if isinstance(node, ast.Call):
        args = [_value(item, names) for item in node.args]
        if node.keywords or any(item is UNKNOWN for item in args):
            return UNKNOWN
        function = _value(node.func, names)
        if isinstance(function, _Reference):
            name = function.name
            if name in ("pathlib.Path", "str", "os.fspath") and len(args) == 1 and isinstance(args[0], (str, Path)):
                return Path(args[0]) if name == "pathlib.Path" else str(args[0])
            if (
                name in ("os.path.abspath", "os.path.realpath", "os.path.dirname")
                and len(args) == 1
                and isinstance(args[0], (str, Path))
            ):
                path = Path(args[0])
                return str(path.parent if name.endswith("dirname") else (names["__cwd__"] / path).resolve())
            if name == "os.path.join" and args and all(isinstance(item, (str, Path)) for item in args):
                return str(Path(args[0]).joinpath(*args[1:]))
        if isinstance(node.func, ast.Attribute) and not args:
            owner = _value(node.func.value, names)
            if isinstance(owner, Path) and node.func.attr in ("resolve", "absolute", "as_posix"):
                return owner.as_posix() if node.func.attr == "as_posix" else (names["__cwd__"] / owner).resolve()
    return UNKNOWN


def _owned(root, file):
    path = root / file
    if Path(file).is_absolute() or ".." in Path(file).parts or path.suffix != ".py":
        return None
    return path.resolve() if path.resolve().is_relative_to(root) and path.is_file() else None


def files_for_ids(root, test_ids):
    """Resolve only actual Python module/file prefixes inside this command's tree."""
    root = Path(root).resolve()
    found = {}
    for test in test_ids:
        prefix = test.split("::", 1)[0]
        candidates = [prefix] if prefix.endswith(".py") else []
        parts = prefix.split(".")
        candidates.extend("/".join(parts[:end]) + ".py" for end in range(len(parts), 0, -1))
        compatible = []
        for file in dict.fromkeys(candidates):
            path = _owned(root, file)
            if not path:
                continue
            # A module shadows a same-named namespace directory; a real package
            # shadows its sibling .py file. A fixture named after a class does
            # not become that class's authoritative source.
            shadowed = any(
                parent != Path(".")
                and (root / parent).with_suffix(".py").is_file()
                and not (root / parent / "__init__.py").is_file()
                for parent in Path(file).parents
            )
            if not shadowed and not path.with_suffix("").joinpath("__init__.py").is_file():
                compatible.append(file)
        if len(compatible) == 1:
            found[test] = compatible[0]
    return found


def _includes(argv, cwd, file, context, script_main, collection):
    if not isinstance(argv, list) or not argv or not all(isinstance(word, str) for word in argv):
        return False
    words = list(argv)
    program = Path(words.pop(0)).name
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", program):
        while words and words[0] in ("-B", "-u"):
            words.pop(0)
        if words and not words[0].startswith("-"):
            return (
                script_main and (cwd / words[0]).resolve() == file and all(word in ("-v", "-q") for word in words[1:])
            )
        if len(words) < 2 or words.pop(0) != "-m":
            return False
        runner = words.pop(0)
    else:
        runner = program
    if runner not in ("unittest", "pytest"):
        return False
    if runner == "unittest" and collection != "unittest":
        return False  # unittest does not collect bare pytest functions/classes.
    # Filtering and custom configuration can exclude the caller. Do not guess.
    safe = {"-v", "--verbose", "-q", "--quiet", "-b", "--buffer", "-f", "--failfast"}
    words = [word for word in words if word not in safe]
    if runner == "unittest" and words[:1] == ["discover"]:
        start, pattern = cwd, "test*.py"
        rest = words[1:]
        while rest:
            option = rest.pop(0)
            if option not in ("-s", "--start-directory", "-p", "--pattern", "-t", "--top-level-directory") or not rest:
                return False
            value = rest.pop(0)
            if option in ("-s", "--start-directory"):
                start = (cwd / value).resolve()
            elif option in ("-p", "--pattern"):
                pattern = value
        if not file.is_relative_to(start) or not fnmatch.fnmatchcase(file.name, pattern):
            return False
        return all(
            (parent / "__init__.py").is_file()
            for parent in file.parent.parents
            if parent.is_relative_to(start) and parent != start
        ) and (file.parent == start or (file.parent / "__init__.py").is_file())
    if not words or any(word.startswith("-") for word in words):
        return False
    try:
        module = ".".join(file.relative_to(cwd).with_suffix("").parts)
    except ValueError:
        module = ""
    caller = ".".join((module, *context))
    for target in words:
        if runner == "unittest":
            if target.endswith(".py") and (cwd / target).resolve() == file:
                return True
            if module and (caller == target or target == module or caller.startswith(target + ".")):
                return True
        else:
            path, *selectors = target.split("::")
            if (cwd / path).resolve() == file and (not selectors or list(context[: len(selectors)]) == selectors):
                return True
    return False


def self_inclusions(source, file, root):
    """Known collected test contexts and their self-selecting commands; no helper callgraph."""
    root, file = Path(root).resolve(), Path(file).resolve()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}  # The ordinary runner owns parse/collection failures.

    def expressions(node):
        # Nested statement bodies have their own sequential bindings below.
        def direct(expression):
            if isinstance(
                expression,
                (
                    ast.Lambda,
                    ast.Compare,
                    ast.BoolOp,
                    ast.IfExp,
                    ast.GeneratorExp,
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                ),
            ):
                return  # Lazy/conditional evaluation is not proof that a call runs.
            yield expression
            for child in ast.iter_child_nodes(expression):
                if not isinstance(child, ast.stmt):
                    yield from direct(child)

        for _, value in ast.iter_fields(node):
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, ast.AST) and not isinstance(item, ast.stmt):
                    yield from direct(item)

    def assigned(node):
        return {
            item.id
            for item in ast.walk(node)
            if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del))
        }

    # Function globals are looked up at call time, not definition time. A
    # rebound module alias/constant is not a proven input to a function body.
    bindings = {"__file__": 1, "str": 1}

    def module_bindings(body, conditional=False):
        for node in body:
            bound = set()
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                bound = {alias.asname or alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound = {node.name}
            elif not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound = assigned(node)
                targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
                for target in targets:
                    while isinstance(target, (ast.Attribute, ast.Subscript)):
                        target = target.value
                    if isinstance(target, ast.Name):
                        bound.add(target.id)
            for name in bound:
                bindings[name] = bindings.get(name, 0) + (2 if conditional else 1)
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for field in ("body", "orelse", "finalbody"):
                    nested = getattr(node, field, None)
                    if isinstance(nested, list):
                        module_bindings(nested, True)
                for handler in getattr(node, "handlers", []):
                    if handler.name:
                        bindings[handler.name] = 2
                    module_bindings(handler.body, True)

    module_bindings(tree.body)
    unstable = {name for name, count in bindings.items() if count > 1}
    custom_collection = "load_tests" in bindings
    module_test_override = "__test__" in bindings

    def entrypoint(node, names):
        return (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
            and len(node.test.ops) == 1
            and isinstance(node.test.ops[0], ast.Eq)
            and len(node.test.comparators) == 1
            and _value(node.test.comparators[0], names) == "__main__"
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Call)
            and _value(node.body[0].value.func, names) == _Reference("unittest.main")
        )

    # Only the ordinary direct unittest script entry point proves a self-file
    # invocation runs this suite; a script's unrelated worker mode does not.
    imports = {"__cwd__": root}
    script_main = False
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split(".")[0]] = _Reference(alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports[alias.asname or alias.name] = _Reference((node.module or "") + "." + alias.name)
        else:
            script_main |= entrypoint(node, imports)
            for name in assigned(node):
                imports.pop(name, None)

    found = {}

    def scan(
        body, names, context=(), class_globals=None, collection=None, direct_definitions=True, unknown_definitions=()
    ):
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    # ``from . import helper`` has no module name.
                    original = ((node.module or "") + "." if isinstance(node, ast.ImportFrom) else "") + alias.name
                    names[alias.asname or alias.name.split(".")[0]] = _Reference(original)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if (
                    not direct_definitions
                    or node.name in unknown_definitions
                    or node.decorator_list
                    or isinstance(node, ast.AsyncFunctionDef)
                ):
                    continue  # Replacement/skip/async collection needs execution, not guessing.
                if not isinstance(node, ast.ClassDef) and any(
                    isinstance(item, (ast.Yield, ast.YieldFrom)) for item in ast.walk(node)
                ):
                    continue  # Named-test collection does not execute a generator body.
                is_class = isinstance(node, ast.ClassDef)
                if not context and (node.name in unstable or module_test_override):
                    continue
                if is_class:
                    if context or node.keywords:
                        continue  # Nested/helper classes are not proven module-level test classes.
                    class_assignments = set().union(
                        *(
                            assigned(child)
                            for child in node.body
                            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                        )
                    )
                    definitions = Counter(
                        child.name for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    )
                    class_unknown = class_assignments | {name for name, count in definitions.items() if count > 1}
                    if "__test__" in class_assignments:
                        continue
                    if any(name in definitions for name in ("__init__", "__new__")):
                        continue
                    mode = (
                        "unittest"
                        if len(node.bases) == 1 and _value(node.bases[0], names) == _Reference("unittest.TestCase")
                        else "pytest"
                        if not node.bases
                        and node.name.startswith("Test")
                        and not any(
                            isinstance(child, ast.FunctionDef) and child.name in ("__init__", "__new__")
                            for child in node.body
                        )
                        else None
                    )
                    if mode == "unittest" and custom_collection:
                        continue
                else:
                    mode = collection if context else "pytest"
                    if (
                        not node.name.startswith("test")
                        or (mode == "pytest" and not node.name.startswith("test_"))
                        or mode is None
                    ):
                        continue  # An uncalled helper cannot launch a collected test.
                # A method closes over function scopes, never a class namespace.
                inner = dict(class_globals if class_globals is not None and not is_class else names)
                if not isinstance(node, ast.ClassDef):
                    for name in unstable:
                        inner.pop(name, None)
                    for name in assigned(node):
                        inner.pop(name, None)
                    for argument in (
                        *node.args.posonlyargs,
                        *node.args.args,
                        *node.args.kwonlyargs,
                        node.args.vararg,
                        node.args.kwarg,
                    ):
                        if argument is None:
                            continue
                        inner.pop(argument.arg, None)
                scan(
                    node.body,
                    inner,
                    (*context, node.name),
                    names if is_class else None,
                    mode,
                    is_class,
                    class_unknown if is_class else (),
                )
                names[node.name] = UNKNOWN
            else:
                for call in (item for item in expressions(node) if isinstance(item, ast.Call)):
                    function = _value(call.func, names)
                    if not isinstance(function, _Reference) or function.name not in SPAWNS | SHELL_SPAWNS:
                        continue
                    if any(item.arg is None for item in call.keywords):
                        continue  # Expanded options may select a different fixture cwd.
                    if len(call.args) > 1:
                        continue  # Positional Popen options can replace executable/cwd.
                    kwargs = {item.arg: _value(item.value, names) for item in call.keywords}
                    argv = _value(call.args[0], names) if call.args else kwargs.get("args", UNKNOWN)
                    shell = kwargs.get("shell", False)
                    if "executable" in kwargs or (call.args and "args" in kwargs):
                        continue
                    if shell is not True and shell is not False or shell is True and not isinstance(argv, str):
                        continue  # A shell argv-list is not the equivalent direct Python argv.
                    cwd = kwargs.get("cwd", root)
                    if not isinstance(cwd, (str, Path)):
                        continue
                    cwd = (root / cwd).resolve()
                    if isinstance(argv, str) and (kwargs.get("shell") is True or function.name in SHELL_SPAWNS):
                        try:
                            argv = shlex.split(argv)
                        except ValueError:
                            continue
                        if any(word in (";", "&&", "||", "|") for word in argv):
                            continue
                    if (
                        collection
                        and (len(context) == 2 or (len(context) == 1 and collection == "pytest"))
                        and _includes(argv, cwd, file, context, script_main, collection)
                    ):
                        found.setdefault(
                            context, {"command": shlex.join(argv), "collection": collection, "script_main": script_main}
                        )
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            names[target.id] = _value(node.value, names)
                        elif isinstance(target, (ast.Attribute, ast.Subscript)):
                            base = target
                            while isinstance(base, (ast.Attribute, ast.Subscript)):
                                base = base.value
                            if isinstance(base, ast.Name):
                                names.pop(base.id, None)
                # Conditions are not executed and their assignments cannot authorize another branch.
                for field in ("body", "orelse", "finalbody"):
                    nested = getattr(node, field, None)
                    if isinstance(nested, list) and not collection:
                        scan(nested, dict(names), context, class_globals, collection, False, unknown_definitions)
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    for name in assigned(node):
                        names.pop(name, None)
                if isinstance(node, (ast.Return, ast.Raise)):
                    break

    scan(tree.body, {"__file__": str(file), "__cwd__": root, "str": _Reference("str")})
    return found


def self_inclusion(source, file, root):
    value = next(iter(self_inclusions(source, file, root).values()), None)
    return value["command"] if value else None


def reason(file, command):
    return (
        f"{file} launches its own Python test suite ({command[:160]}). A self-suite wrapper repeats "
        "the tests inside each proof invocation and cannot be its named proof; assert product behavior "
        "directly, and verify aggregate suite delivery from the runner's actual current proof. "
        "Keep the approved criteria and test identities; an invalid verification strategy needs plan review."
    )


def named_refusals(root, test_ids):
    root = Path(root).resolve()
    cached, refused = {}, {}
    for test, file in files_for_ids(root, test_ids).items():
        if file not in cached:
            cached[file] = self_inclusions((root / file).read_text(errors="replace"), root / file, root)
        module = ".".join(Path(file).with_suffix("").parts)
        prefix, *selectors = test.split("::")
        if prefix == file:
            context = selectors
        elif prefix.startswith(module + "."):
            context = prefix[len(module) + 1 :].split(".") + selectors
        else:
            context = selectors if prefix == module else []
        context = tuple(part.split("[", 1)[0] for part in context)
        if value := cached[file].get(context):
            refused[test] = reason(file, value["command"])
    return refused


def selected_refusals(root, files, argv, cwd):
    """Negative facts only when this exact outer command selects the wrapper."""
    root, cwd = Path(root).resolve(), Path(cwd).resolve()
    found = []
    for file in files:
        path = _owned(root, file)
        if not path:
            continue
        source = path.read_bytes()
        for context, value in self_inclusions(source.decode(errors="replace"), path, cwd).items():
            if _includes(argv, cwd, path, context, value["script_main"], value["collection"]):
                found.append(
                    {
                        "file": file,
                        "context": list(context),
                        "source_sha256": hashlib.sha256(source).hexdigest(),
                        "inner_command": value["command"],
                    }
                )
    return found


def refusals(root, files):
    root = Path(root).resolve()
    found = {}
    for file in files:
        path = _owned(root, file)
        if path:
            command = self_inclusion(path.read_text(errors="replace"), path, root)
            if command:
                found[file] = reason(file, command)
    return found
