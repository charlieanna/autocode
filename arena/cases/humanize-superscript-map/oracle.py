"""Independent checks of a behavior-preserving refactor; candidate imports are read-only and source-local."""
import ast
import inspect
import json
import sys
import textwrap
import types
from pathlib import Path

sys.dont_write_bytecode = True
workspace = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(workspace / "src"))
checks = []


def check(name, fn):
    try:
        fn()
    except Exception as error:
        checks.append({"name": name, "ok": False, "detail": type(error).__name__ + ": " + str(error)})
    else:
        checks.append({"name": name, "ok": True})


# The source archive omits the generated version module; supply it in memory.
version = types.ModuleType("humanize._version")
version.__version__ = "0+arena"
sys.modules.setdefault(version.__name__, version)

import humanize.number as number  # noqa: E402

assert Path(number.__file__).resolve().is_relative_to(workspace)

SUPERSCRIPTS = {
    "0": "\u2070", "1": "\u00b9", "2": "\u00b2", "3": "\u00b3", "4": "\u2074", "5": "\u2075",
    "6": "\u2076", "7": "\u2077", "8": "\u2078", "9": "\u2079", "-": "\u207b",
}

# scientific(value, precision) as the original code formats it, recorded from the pinned baseline.
UNCHANGED = [
    (500, 0, "5 x 10²"),
    (500, 2, "5.00 x 10²"),
    (500, 5, "5.00000 x 10²"),
    (0.3, 0, "3 x 10⁻¹"),
    (0.3, 2, "3.00 x 10⁻¹"),
    (0.3, 5, "3.00000 x 10⁻¹"),
    ('20000', 0, "2 x 10⁴"),
    ('20000', 2, "2.00 x 10⁴"),
    ('20000', 5, "2.00000 x 10⁴"),
    (1000000, 0, "1 x 10⁶"),
    (1000000, 2, "1.00 x 10⁶"),
    (1000000, 5, "1.00000 x 10⁶"),
    (-1e-05, 0, "-1 x 10⁻⁵"),
    (-1e-05, 2, "-1.00 x 10⁻⁵"),
    (-1e-05, 5, "-1.00000 x 10⁻⁵"),
    (1e-25, 0, "1 x 10⁻²⁵"),
    (1e-25, 2, "1.00 x 10⁻²⁵"),
    (1e-25, 5, "1.00000 x 10⁻²⁵"),
    (-12345.678, 0, "-1 x 10⁴"),
    (-12345.678, 2, "-1.23 x 10⁴"),
    (-12345.678, 5, "-1.23457 x 10⁴"),
    ('1.5e300', 0, "2 x 10³⁰⁰"),
    ('1.5e300', 2, "1.50 x 10³⁰⁰"),
    ('1.5e300', 5, "1.50000 x 10³⁰⁰"),
    (float("nan"), 0, "NaN"),
    (float("nan"), 2, "NaN"),
    (float("nan"), 5, "NaN"),
    (float("inf"), 0, "+Inf"),
    (float("inf"), 2, "+Inf"),
    (float("inf"), 5, "+Inf"),
    (float("-inf"), 0, "-Inf"),
    (float("-inf"), 2, "-Inf"),
    (float("-inf"), 5, "-Inf"),
    ('foo', 0, "foo"),
    ('foo', 2, "foo"),
    ('foo', 5, "foo"),
    (None, 0, "None"),
    (None, 2, "None"),
    (None, 5, "None"),
    (0, 0, "0 x 10⁰"),
    (0, 2, "0.00 x 10⁰"),
    (0, 5, "0.00000 x 10⁰"),
    (7, 0, "7 x 10⁰"),
    (7, 2, "7.00 x 10⁰"),
    (7, 5, "7.00000 x 10⁰")
]


def unchanged():
    for value, precision, expected in UNCHANGED:
        try:
            actual = number.scientific(value, precision)
        except Exception as error:
            actual = "!" + type(error).__name__
        assert actual == expected, (value, precision, actual, expected)


def module_constant():
    mapping = number._SUPERSCRIPT_MAP
    assert dict(mapping) == SUPERSCRIPTS, mapping


def one_mapping():
    # The docstring's examples may show superscripts; the code may not spell out its own table.
    function = ast.parse(textwrap.dedent(inspect.getsource(number.scientific))).body[0]
    body = function.body[1:] if ast.get_docstring(function) is not None else function.body
    found = {
        char
        for statement in body
        for node in ast.walk(statement)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        for char in SUPERSCRIPTS.values()
        if char in node.value
    }
    assert not found, "scientific() still spells out superscript digits: " + "".join(sorted(found))


check("scientific_unchanged", unchanged)
check("module_constant", module_constant)
check("scientific_uses_constant", one_mapping)

print(json.dumps({"checks": checks}))
