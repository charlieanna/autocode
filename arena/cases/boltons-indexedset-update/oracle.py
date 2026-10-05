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


import boltons.setutils as module
assert Path(module.__file__).resolve().is_relative_to(workspace)
IndexedSet = module.IndexedSet


def multiple(kind):
    values = IndexedSet([1])
    equal(values.update(kind([2, 1, 3]), kind([]), kind([3, 4, 2])), None)
    equal(list(values), [1, 2, 3, 4])


def tuple_members():
    values = IndexedSet()
    values.update([(1, 2)], [(3, 4), (1, 2)])
    equal(list(values), [(1, 2), (3, 4)])


def ordinary():
    values = IndexedSet([1])
    equal(values.update(), None)
    values.update([2, 1])
    equal(list(values), [1, 2])


for name, kind in (("lists", list), ("tuples", tuple), ("iterators", iter)):
    check(name, lambda kind=kind: multiple(kind))
check("tuple_members", tuple_members)
check("empty_and_single", ordinary)

print(json.dumps({"checks": checks}))
