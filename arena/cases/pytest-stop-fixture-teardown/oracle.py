"""Offline public-behavior oracle for pytest's early-stop fixture lifecycle.

Each child imports pytest and _pytest only from the supplied source tree. Child
projects, caches and reports live in temporary directories, never in candidate.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap

sys.dont_write_bytecode = True
workspace = Path(sys.argv[1]).resolve()
checks = []


def check(name, fn):
    try:
        fn()
    except Exception as error:
        checks.append({"name": name, "ok": False, "detail": str(error)})
    else:
        checks.append({"name": name, "ok": True})


def equal(actual, expected):
    assert actual == expected, (actual, expected)


def run(source, options=(), plugin=""):
    with tempfile.TemporaryDirectory(prefix="arena-pytest-lifecycle-") as directory:
        root = Path(directory)
        (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (root / "test_lifecycle.py").write_text(textwrap.dedent(source), encoding="utf-8")
        (root / "conftest.py").write_text(textwrap.dedent('''
            import json
            import pathlib
            import pytest
            import _pytest
            import os
            import warnings
            events = []
            reports = []
            nextitems = []
            flag_checks = []

            def pytest_runtest_logreport(report):
                reports.append({"when": report.when, "outcome": report.outcome,
                                "nodeid": report.nodeid, "longrepr": str(report.longrepr)})

            @pytest.hookimpl(tryfirst=True)
            def pytest_runtest_teardown(item, nextitem):
                nextitems.append([item.name, None if nextitem is None else nextitem.name])

            @pytest.hookimpl(trylast=True)
            def pytest_sessionfinish(session, exitstatus):
                if os.environ.get("ARENA_CHECK_STICKY"):
                    attribute = os.environ["ARENA_CHECK_STICKY"]
                    before = getattr(session, attribute)
                    with warnings.catch_warnings(record=True) as caught:
                        warnings.simplefilter("always")
                        setattr(session, attribute, False)
                    flag_checks.append([attribute, bool(before),
                                        bool(getattr(session, attribute)),
                                        [{"category": w.category.__name__,
                                          "message": str(w.message)} for w in caught]])
                payload = {"events": events, "reports": reports,
                           "nextitems": nextitems, "flags": flag_checks,
                           "pytest": pytest.__file__, "internal": _pytest.__file__}
                pathlib.Path("observed.json").write_text(json.dumps(payload), encoding="utf-8")
        ''') + textwrap.dedent(plugin), encoding="utf-8")
        env = dict(os.environ)
        env.update({"PYTHONPATH": str(workspace / "src"),
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                    "PYTHONDONTWRITEBYTECODE": "1"})
        env.pop("PYTEST_PLUGINS", None)
        env.pop("PYTEST_ADDOPTS", None)
        env.pop("ARENA_CHECK_STICKY", None)
        opts = list(options)
        for opt in tuple(opts):
            if opt.startswith("sticky:"):
                env["ARENA_CHECK_STICKY"] = opt.split(":", 1)[1]
                opts.remove(opt)
        # Git archives omit setuptools-scm's generated version module, which
        # assertion rewriting imports directly before plugins can initialize.
        # Supply metadata only; all runtime behavior remains candidate-owned.
        bootstrap = '''
import pathlib, runpy, sys, types
source = pathlib.Path(sys.path[0])
version = types.ModuleType("_pytest._version")
version.version = version.__version__ = "8.0.0.dev0"
version.version_tuple = version.__version_tuple__ = (8, 0, 0, "dev0")
sys.modules.setdefault("_pytest._version", version)
import _pytest
_pytest._version = version
sys.argv[0] = "pytest"
runpy.run_module("pytest", run_name="__main__")
'''
        # Cacheprovider owns --stepwise. Its cache is confined to this temporary
        # child project and is removed when this block exits.
        result = subprocess.run([sys.executable, "-B", "-c", bootstrap, "-q",
                                 "-c", "pytest.ini", *opts],
                                cwd=root, env=env, capture_output=True, text=True, timeout=30)
        observed = root / "observed.json"
        assert observed.is_file(), result.stdout[-2000:] + result.stderr[-2000:]
        payload = json.loads(observed.read_text(encoding="utf-8"))
        for field in ("pytest", "internal"):
            assert Path(payload[field]).resolve().is_relative_to(workspace), payload[field]
        payload["returncode"] = result.returncode
        payload["output"] = result.stdout + result.stderr
        return payload


def early_stop(scope, option):
    p = run(f'''
        import pytest
        from conftest import events
        @pytest.fixture(scope={scope!r})
        def shared():
            events.append("setup")
            yield
            events.append("teardown")
            raise ValueError("ARENA_HIGH_SCOPE_TEARDOWN")
        def test_fail(shared):
            events.append("first")
            assert False, "ARENA_TEST_FAILURE"
        def test_later(shared):
            events.append("later")
    ''', [option])
    equal(p["events"], ["setup", "first", "teardown"])
    equal(p["nextitems"], [["test_fail", None]])
    failed = [r for r in p["reports"] if r["outcome"] == "failed"]
    equal([(r["when"], r["nodeid"]) for r in failed],
          [("call", "test_lifecycle.py::test_fail"), ("teardown", "test_lifecycle.py::test_fail")])
    assert "ARENA_HIGH_SCOPE_TEARDOWN" in failed[1]["longrepr"]
    equal(p["returncode"], 1 if option == "--maxfail=1" else 2)
    assert "INTERNALERROR" not in p["output"]


def sticky(attribute, option):
    p = run('''
        def test_fail():
            assert False
        def test_later():
            pass
    ''', [option, "sticky:" + attribute])
    equal(len(p["flags"]), 1)
    name, before, after, recorded_warnings = p["flags"][0]
    equal([name, before, after], [attribute, True, True])

    def explains_ignored_reset(warning):
        message = warning["message"].lower()
        names_flag = any(term in message for term in (attribute, "session", "flag"))
        names_reset = any(term in message for term in ("reset", "clear", "unset", "false"))
        names_refusal = any(term in message for term in
                            ("ignor", "cannot", "can't", "not allowed", "refus", "prevent", "reject"))
        return names_flag and names_reset and names_refusal

    assert any(explains_ignored_reset(warning) for warning in recorded_warnings), recorded_warnings


def setup_failure():
    p = run('''
        import pytest
        from conftest import events
        @pytest.fixture(scope="session")
        def shared():
            events.append("setup_shared")
            yield
            events.append("teardown_shared")
            raise LookupError("ARENA_SETUP_TEARDOWN")
        @pytest.fixture
        def broken(shared):
            raise RuntimeError("ARENA_SETUP_FAILURE")
        def test_first(broken):
            events.append("must_not_run")
        def test_later(shared):
            events.append("later")
    ''', ["--maxfail=1"])
    equal(p["events"], ["setup_shared", "teardown_shared"])
    equal(p["nextitems"], [["test_first", None]])
    failures = [r for r in p["reports"] if r["outcome"] == "failed"]
    equal([r["when"] for r in failures], ["setup", "teardown"])
    assert "ARENA_SETUP_FAILURE" in failures[0]["longrepr"]
    assert "ARENA_SETUP_TEARDOWN" in failures[1]["longrepr"]
    equal(p["returncode"], 1)


def all_finalizers():
    p = run('''
        import pytest
        from conftest import events
        @pytest.fixture(scope="session")
        def outer():
            events.append("outer_setup")
            yield
            events.append("outer_teardown")
            raise ValueError("ARENA_OUTER_ERROR")
        @pytest.fixture(scope="module")
        def inner(outer):
            events.append("inner_setup")
            yield
            events.append("inner_teardown")
            raise RuntimeError("ARENA_INNER_ERROR")
        def test_fail(inner):
            assert False
        def test_later(inner):
            events.append("later")
    ''', ["--maxfail=1"])
    equal(p["events"], ["outer_setup", "inner_setup", "inner_teardown", "outer_teardown"])
    failures = [r for r in p["reports"] if r["when"] == "teardown" and r["outcome"] == "failed"]
    equal(len(failures), 1)
    assert "ARENA_OUTER_ERROR" in failures[0]["longrepr"]
    assert "ARENA_INNER_ERROR" in failures[0]["longrepr"]
    equal(p["returncode"], 1)


def normal_reuse():
    p = run('''
        import pytest
        from conftest import events
        @pytest.fixture(scope="module")
        def shared():
            events.append("setup")
            yield
            events.append("teardown")
        def test_first(shared):
            events.append("first")
        def test_later(shared):
            events.append("later")
    ''')
    equal(p["events"], ["setup", "first", "later", "teardown"])
    equal(p["nextitems"], [["test_first", "test_later"], ["test_later", None]])
    equal(p["returncode"], 0)
    equal([r["when"] for r in p["reports"] if r["outcome"] == "passed"],
          ["setup", "call", "teardown"] * 2)


def same_fixture_finalizers(option, *, setup_failure=False):
    # Separate fixtures exercise SetupState, but cannot catch FixtureDef.finish
    # dropping all but the first error from callbacks on the same fixture.
    p = run('''
        import pytest
        from conftest import events
        @pytest.fixture(scope="session")
        def shared(request):
            def first():
                events.append("first_cleanup")
                raise RuntimeError("ARENA_SAME_FIXTURE_FIRST")
            def second():
                events.append("second_cleanup")
                raise RuntimeError("ARENA_SAME_FIXTURE_SECOND")
            request.addfinalizer(first)
            request.addfinalizer(second)
        @pytest.fixture(scope="module")
        def inner(shared):
            yield
            events.append("inner_cleanup")
            raise ValueError("ARENA_SAME_FIXTURE_INNER")
        @pytest.fixture
        def prepared(inner):
            if SETUP_FAILURE:
                raise LookupError("ARENA_SAME_FIXTURE_SETUP")
        def test_first(prepared):
            events.append("call")
            assert False, "ARENA_SAME_FIXTURE_CALL"
        def test_later(inner):
            events.append("later")
    '''.replace("SETUP_FAILURE", repr(setup_failure)), [option])
    equal(p["events"], ([] if setup_failure else ["call"]) +
          ["inner_cleanup", "second_cleanup", "first_cleanup"])
    equal(p["nextitems"], [["test_first", None]])
    failures = [r for r in p["reports"] if r["outcome"] == "failed"]
    equal([(r["when"], r["nodeid"]) for r in failures],
          [("setup" if setup_failure else "call", "test_lifecycle.py::test_first"),
           ("teardown", "test_lifecycle.py::test_first")])
    # Match rendered exceptions, not literals merely present in traceback source.
    for error in ("RuntimeError: ARENA_SAME_FIXTURE_FIRST",
                  "RuntimeError: ARENA_SAME_FIXTURE_SECOND",
                  "ValueError: ARENA_SAME_FIXTURE_INNER"):
        assert error in failures[1]["longrepr"], error
    equal(p["returncode"], 1 if option == "--maxfail=1" else 2)
    assert "INTERNALERROR" not in p["output"]


for scope in ("module", "session"):
    for flag, label in (("--maxfail=1", "maxfail"), ("--stepwise", "stepwise")):
        check(label + "_" + scope, lambda scope=scope, flag=flag: early_stop(scope, flag))
check("setup_error_cleanup", setup_failure)
check("all_finalizers_reported", all_finalizers)
check("same_fixture_maxfail", lambda: same_fixture_finalizers("--maxfail=1"))
check("same_fixture_stepwise", lambda: same_fixture_finalizers("--stepwise"))
check("same_fixture_setup", lambda: same_fixture_finalizers("--maxfail=1", setup_failure=True))
check("shouldfail_sticky", lambda: sticky("shouldfail", "--maxfail=1"))
check("shouldstop_sticky", lambda: sticky("shouldstop", "--stepwise"))
check("normal_fixture_reuse", normal_reuse)
print(json.dumps({"checks": checks}))
