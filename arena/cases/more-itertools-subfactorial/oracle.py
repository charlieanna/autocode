"""Independent behavior checks of a new function; candidate imports are read-only and source-local."""
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
workspace = Path(sys.argv[1]).resolve()
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


import more_itertools as module  # noqa: E402

assert Path(module.__file__).resolve().is_relative_to(workspace)

# OEIS A000166, the number of derangements of n elements, for n = 0..12.
A000166 = [1, 0, 1, 2, 9, 44, 265, 1854, 14833, 133496, 1334961, 14684570, 176214841]


def exact(n):
    # !n = n * !(n-1) + (-1)**n, the evaluator's own recurrence.
    value = 1
    for i in range(1, n + 1):
        value = i * value + (-1) ** i
    return value


def small():
    equal([module.subfactorial(n) for n in range(len(A000166))], A000166)


def counts_derangements():
    for n in range(7):
        equal(module.subfactorial(n), module.ilen(module.derangements(range(n))))


def large_is_exact():
    for n in (25, 60, 200):
        result = module.subfactorial(n)
        assert type(result) is int, type(result)
        equal(result, exact(n))


def negative():
    for n in (-1, -7):
        try:
            module.subfactorial(n)
        except ValueError:
            pass
        else:
            raise AssertionError("no ValueError for a negative n")


def exported():
    namespace = {}
    exec("from more_itertools import *", namespace)
    assert namespace.get("subfactorial") is module.subfactorial, "not exported by from more_itertools import *"


check("small_values", small)
check("counts_derangements", counts_derangements)
check("large_values_exact", large_is_exact)
check("negative_raises", negative)
check("exported", exported)

print(json.dumps({"checks": checks}))
