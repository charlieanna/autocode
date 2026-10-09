"""Independent behavior checks; candidate imports are read-only and source-local."""
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
workspace = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(workspace / "src"))
sys.path.insert(0, str(workspace))
checks = []


def check(name, fn):
    try:
        fn()
    except Exception as error:
        checks.append({"name": name, "ok": False, "detail": type(error).__name__ + ": " + str(error)})
    else:
        checks.append({"name": name, "ok": True})


def equal(actual, expected):
    assert actual == expected, (actual, expected)


# The source archive omits setuptools-scm's generated version module. Supply
# build metadata in memory so the actual candidate implementation imports offline.
import types
version = types.ModuleType("humanize._version")
version.__version__ = "0+arena"
sys.modules[version.__name__] = version
import humanize as module
assert Path(module.__file__).resolve().is_relative_to(workspace)


def large(sign):
    value = sign * (10**400 + 123)
    equal(module.intcomma(value), format(value, ",d"))


def integer_subclasses():
    class Integer(int):
        pass

    for sign in (1, -1):
        value = Integer(sign * (10**400 + 123))
        equal(module.intcomma(value), format(value, ",d"))


def ordinary():
    for value, expected in ((0, "0"), (12345, "12,345"), (-12345, "-12,345"),
                            ("12345", "12,345"), (1234.5, "1,234.5")):
        equal(module.intcomma(value), expected)
    equal(module.intcomma(1234.5, 2), "1,234.50")


def nonfinite():
    equal(module.intcomma(float("inf")), "+Inf")
    equal(module.intcomma(float("-inf")), "-Inf")
    equal(module.intcomma(float("nan")), "NaN")


check("large_positive", lambda: large(1))
check("large_negative", lambda: large(-1))
check("integer_subclasses", integer_subclasses)
check("ordinary_inputs", ordinary)
check("nonfinite", nonfinite)

print(json.dumps({"checks": checks}))
