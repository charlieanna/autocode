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


import more_itertools as module
assert Path(module.__file__).resolve().is_relative_to(workspace)


class FalsyError(Exception):
    def __bool__(self):
        return False


class NoRepr:
    def __repr__(self):
        raise RuntimeError("candidate must not format these items")


def custom(fn, values, keyword):
    expected = FalsyError("custom")
    try:
        fn(values, **{keyword: expected})
    except Exception as actual:
        assert actual is expected, type(actual).__name__
    else:
        raise AssertionError("custom exception was not raised")


def no_repr(fn):
    expected = OverflowError("custom")
    try:
        fn([NoRepr(), NoRepr()], too_long=expected)
    except Exception as actual:
        assert actual is expected, type(actual).__name__
    else:
        raise AssertionError("custom exception was not raised")


def ordinary():
    equal(module.one([7]), 7)
    equal(module.only([7]), 7)
    equal(module.only([], default=9), 9)
    for fn, values in ((module.one, []), (module.one, [1, 2]), (module.only, [1, 2])):
        try:
            fn(values)
        except ValueError:
            pass
        else:
            raise AssertionError("default ValueError missing")


check("one_empty_custom", lambda: custom(module.one, [], "too_short"))
check("one_many_custom", lambda: custom(module.one, [1, 2], "too_long"))
check("only_many_custom", lambda: custom(module.only, [1, 2], "too_long"))
check("one_no_repr", lambda: no_repr(module.one))
check("only_no_repr", lambda: no_repr(module.only))
check("ordinary_inputs", ordinary)

print(json.dumps({"checks": checks}))
