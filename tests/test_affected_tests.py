"""Conservative affected-test selection for changed-code mutation checks (#2942)."""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import autocode_affected_tests as affected_tests  # noqa: E402


class AffectedTestsSelectionTests(unittest.TestCase):
    def _tree(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory(prefix="affected-tests-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for rel, body in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(body), encoding="utf-8")
        return root

    def test_multi_module_narrowing_under_unittest_discover_and_direct_and_pytest(self):
        root = self._tree(
            {
                "calc.py": """\
                    def limit(n):
                        if n > 10:
                            return 10
                        return n
                    """,
                "billing.py": """\
                    def total(items):
                        return sum(items)
                    """,
                "tests/__init__.py": "",
                "tests/test_calc.py": """\
                    import unittest
                    from calc import limit


                    class CalcTests(unittest.TestCase):
                        def test_limit(self):
                            self.assertEqual(10, limit(20))
                    """,
                "tests/test_billing.py": """\
                    import unittest
                    from billing import total


                    class BillingTests(unittest.TestCase):
                        def test_total(self):
                            self.assertEqual(5, total([2, 3]))
                    """,
            }
        )
        ut_tests = [
            "tests.test_calc.CalcTests.test_limit",
            "tests.test_billing.BillingTests.test_total",
        ]
        for cmd in (
            f"{sys.executable} -m unittest -v",
            f"{sys.executable} -m unittest discover -v",
        ):
            with self.subTest(command=cmd):
                sel = affected_tests.select("unittest", cmd, ut_tests, root, ["calc.py", "billing.py"])
                by_site, summary = affected_tests.plan_for_mutants(sel, [("calc.py", 2), ("billing.py", 2)])
                self.assertEqual(["tests.test_calc.CalcTests.test_limit"], by_site[("calc.py", 2)]["tests"])
                self.assertEqual(affected_tests.RULE_SYMBOL, by_site[("calc.py", 2)]["rule"])
                self.assertEqual(["tests.test_billing.BillingTests.test_total"], by_site[("billing.py", 2)]["tests"])
                self.assertEqual(2, summary["narrowed_mutants"])
                self.assertIsNone(summary["fallback"])

        pt_tests = [
            "tests/test_calc.py::CalcTests::test_limit",
            "tests/test_billing.py::BillingTests::test_total",
        ]
        sel_pt = affected_tests.select(
            "pytest", f"{sys.executable} -m pytest", pt_tests, root, ["calc.py", "billing.py"]
        )
        by_site_pt, summary_pt = affected_tests.plan_for_mutants(sel_pt, [("calc.py", 2), ("billing.py", 2)])
        self.assertEqual(["tests/test_calc.py::CalcTests::test_limit"], by_site_pt[("calc.py", 2)]["tests"])
        self.assertEqual(["tests/test_billing.py::BillingTests::test_total"], by_site_pt[("billing.py", 2)]["tests"])
        self.assertIn("test_limit", by_site_pt[("calc.py", 2)]["command"])
        self.assertNotIn("test_total", by_site_pt[("calc.py", 2)]["command"])
        self.assertEqual(2, summary_pt["narrowed_mutants"])

    def test_intra_module_symbol_closure_aliases_helpers_fixtures_and_setup(self):
        root = self._tree(
            {
                "controller.py": """\
                    def core(x):
                        return x + 1


                    def add(x):
                        return core(x)


                    def sub(x):
                        return x - 1
                    """,
                "tests/test_controller.py": """\
                    import unittest
                    from controller import add as add_fn, sub


                    def helper_calls_add(v):
                        return add_fn(v)


                    class ControllerTests(unittest.TestCase):
                        def test_add_via_alias_helper(self):
                            self.assertEqual(3, helper_calls_add(2))

                        def test_sub_only(self):
                            self.assertEqual(1, sub(2))
                    """,
            }
        )
        tests = [
            "tests.test_controller.ControllerTests.test_add_via_alias_helper",
            "tests.test_controller.ControllerTests.test_sub_only",
        ]
        sel = affected_tests.select(
            "unittest", f"{sys.executable} -m unittest discover -v", tests, root, ["controller.py"]
        )
        by_site, _ = affected_tests.plan_for_mutants(sel, [("controller.py", 2), ("controller.py", 10)])
        # `core` (line 2) reaches `add` via intra-module call closure and `add_fn` alias -> test_add_via_alias_helper only
        self.assertEqual(
            ["tests.test_controller.ControllerTests.test_add_via_alias_helper"],
            by_site[("controller.py", 2)]["tests"],
        )
        self.assertEqual(affected_tests.RULE_SYMBOL, by_site[("controller.py", 2)]["rule"])
        # `sub` (line 10) -> test_sub_only only
        self.assertEqual(
            ["tests.test_controller.ControllerTests.test_sub_only"], by_site[("controller.py", 10)]["tests"]
        )

        # setUp referencing `core` / `add_fn` must conservatively pull all tests in the TestCase
        root_setup = self._tree(
            {
                "controller.py": (root / "controller.py").read_text(),
                "tests/test_controller.py": """\
                    import unittest
                    from controller import add as add_fn, sub


                    class ControllerTests(unittest.TestCase):
                        def setUp(self):
                            self.base = add_fn(0)

                        def test_add_one(self):
                            self.assertEqual(2, add_fn(1))

                        def test_sub_only(self):
                            self.assertEqual(1, sub(2))
                    """,
            }
        )
        sel_setup = affected_tests.select(
            "unittest",
            f"{sys.executable} -m unittest discover -v",
            [
                "tests.test_controller.ControllerTests.test_add_one",
                "tests.test_controller.ControllerTests.test_sub_only",
            ],
            root_setup,
            ["controller.py"],
        )
        sites_setup, _ = affected_tests.plan_for_mutants(sel_setup, [("controller.py", 2)])
        self.assertEqual(2, len(sites_setup[("controller.py", 2)]["tests"]))

        # pytest fixture referencing `add` pulls tests (via fixture active refs)
        root_pt = self._tree(
            {
                "controller.py": (root / "controller.py").read_text(),
                "tests/test_pt.py": """\
                    import pytest
                    from controller import add, sub


                    @pytest.fixture
                    def seeded():
                        return add(1)


                    def test_alpha(seeded):
                        assert seeded == 2


                    def test_beta():
                        assert sub(2) == 1
                    """,
            }
        )
        pt_ids = ["tests/test_pt.py::test_alpha", "tests/test_pt.py::test_beta"]
        sel_pt = affected_tests.select("pytest", f"{sys.executable} -m pytest", pt_ids, root_pt, ["controller.py"])
        sites_pt, _ = affected_tests.plan_for_mutants(sel_pt, [("controller.py", 2), ("controller.py", 10)])
        self.assertEqual(pt_ids, sites_pt[("controller.py", 2)]["tests"])
        self.assertEqual(["tests/test_pt.py::test_beta"], sites_pt[("controller.py", 10)]["tests"])

    def test_conservative_retention_on_dynamic_and_structural_edges(self):
        base_controller = """\
            def fn_a(x):
                return x + 1


            def fn_b(x):
                return x + 2
            """
        # 1. Decorator / default at import time on fn_a runs when controller imports
        dec_root = self._tree(
            {
                "controller.py": """\
                    def _tag(f):
                        return f


                    @_tag
                    def fn_a(x):
                        return x + 1


                    def fn_b(x):
                        return x + 2
                    """,
                "tests/test_a.py": """\
                    import unittest
                    from controller import fn_a


                    class ATests(unittest.TestCase):
                        def test_a(self):
                            self.assertEqual(2, fn_a(1))
                    """,
                "tests/test_b.py": """\
                    import unittest
                    from controller import fn_b


                    class BTests(unittest.TestCase):
                        def test_b(self):
                            self.assertEqual(3, fn_b(1))
                    """,
            }
        )
        ids = ["tests.test_a.ATests.test_a", "tests.test_b.BTests.test_b"]
        # Decorator line 5 (on `@_tag def fn_a`) runs on import -> keeps both tests
        by_site, _ = affected_tests.plan_for_mutants(
            affected_tests.select(
                "unittest", f"{sys.executable} -m unittest discover -v", ids, dec_root, ["controller.py"]
            ),
            [("controller.py", 5), ("controller.py", 1)],
        )
        self.assertEqual(ids, sorted(by_site[("controller.py", 5)]["tests"]))
        self.assertEqual(ids, sorted(by_site[("controller.py", 1)]["tests"]))

        # 2. getattr / star import / subprocess / custom TestCase base / conftest / ancestor __init__ / syntax error
        cases = {
            "getattr": {
                "controller.py": base_controller + "\nVAR = getattr(__import__('builtins'), 'len')\n",
                "tests/test_a.py": "import unittest\nfrom controller import fn_a\nclass ATests(unittest.TestCase):\n    def test_a(self): self.assertEqual(2, fn_a(1))\n",
                "tests/test_b.py": "import unittest\nfrom controller import fn_b\nclass BTests(unittest.TestCase):\n    def test_b(self): self.assertEqual(3, fn_b(1))\n",
            },
            "star_import": {
                "controller.py": base_controller,
                "tests/test_a.py": "import unittest\nfrom controller import fn_a\nclass ATests(unittest.TestCase):\n    def test_a(self): self.assertEqual(2, fn_a(1))\n",
                "tests/test_b.py": "import unittest\nfrom controller import *\nclass BTests(unittest.TestCase):\n    def test_b(self): self.assertEqual(3, fn_b(1))\n",
            },
            "subprocess": {
                "controller.py": base_controller,
                "tests/test_a.py": "import unittest\nfrom controller import fn_a\nclass ATests(unittest.TestCase):\n    def test_a(self): self.assertEqual(2, fn_a(1))\n",
                "tests/test_b.py": "import subprocess, unittest\nclass BTests(unittest.TestCase):\n    def test_b(self): pass\n",
            },
            "custom_base": {
                "controller.py": base_controller,
                "tests/helpers.py": "import unittest\nfrom controller import fn_a\nclass BaseCase(unittest.TestCase):\n    def setUp(self): self.v = fn_a(1)\n",
                "tests/test_a.py": "import unittest\nfrom controller import fn_a\nclass ATests(unittest.TestCase):\n    def test_a(self): self.assertEqual(2, fn_a(1))\n",
                "tests/test_b.py": "from tests.helpers import BaseCase\nfrom controller import fn_b\nclass BTests(BaseCase):\n    def test_b(self): self.assertEqual(3, fn_b(1))\n",
            },
            "conftest_plugin_and_init": {
                "controller.py": base_controller,
                "tests/__init__.py": "from controller import fn_a\n",
                "tests/test_a.py": "import unittest\nfrom controller import fn_a\nclass ATests(unittest.TestCase):\n    def test_a(self): self.assertEqual(2, fn_a(1))\n",
                "tests/test_b.py": "import unittest\nfrom controller import fn_b\nclass BTests(unittest.TestCase):\n    def test_b(self): self.assertEqual(3, fn_b(1))\n",
            },
            "syntax_error_in_other_test": {
                "controller.py": base_controller,
                "tests/test_a.py": "import unittest\nfrom controller import fn_a\nclass ATests(unittest.TestCase):\n    def test_a(self): self.assertEqual(2, fn_a(1))\n",
                "tests/test_b.py": "def broken(:\n",
            },
        }
        for label, files in cases.items():
            with self.subTest(case=label):
                root = self._tree(files)
                sel = affected_tests.select(
                    "unittest", f"{sys.executable} -m unittest discover -v", ids, root, ["controller.py"]
                )
                sites, _ = affected_tests.plan_for_mutants(sel, [("controller.py", 2)])
                self.assertEqual(ids, sorted(sites[("controller.py", 2)]["tests"]), label)

        # Verify pytest ancestor conftest.py pulls in tests even without direct import in test file
        pt_conftest_root = self._tree(
            {
                "controller.py": base_controller,
                "tests/conftest.py": "from controller import fn_a\n",
                "tests/test_a.py": "def test_a():\n    assert True\n",
                "tests/test_b.py": "from controller import fn_b\ndef test_b():\n    assert fn_b(1) == 3\n",
            }
        )
        pt_ids = ["tests/test_a.py::test_a", "tests/test_b.py::test_b"]
        sel_cf = affected_tests.select(
            "pytest", f"{sys.executable} -m pytest", pt_ids, pt_conftest_root, ["controller.py"]
        )
        sites_cf, _ = affected_tests.plan_for_mutants(sel_cf, [("controller.py", 2)])
        self.assertEqual(pt_ids, sorted(sites_cf[("controller.py", 2)]["tests"]))

    def test_pytest_and_unittest_discover_substring_closure(self):
        root = self._tree(
            {
                "calc.py": """\
                    def add(x):
                        return x + 1


                    def sub(x):
                        return x - 1
                    """,
                "tests/test_calc.py": """\
                    import unittest
                    from calc import add, sub


                    class CalcTests(unittest.TestCase):
                        def test_add(self):
                            self.assertEqual(2, add(1))

                        def test_add_negative(self):
                            self.assertEqual(1, sub(2))
                    """,
            }
        )
        # Under pytest `-k test_add` or unittest discover `-k test_add`, selecting test_add also executes test_add_negative
        pt_ids = ["tests/test_calc.py::CalcTests::test_add", "tests/test_calc.py::CalcTests::test_add_negative"]
        sel_pt = affected_tests.select("pytest", f"{sys.executable} -m pytest", pt_ids, root, ["calc.py"])
        sites_pt, _ = affected_tests.plan_for_mutants(sel_pt, [("calc.py", 2)])
        self.assertEqual(sorted(pt_ids), sorted(sites_pt[("calc.py", 2)]["tests"]))
        self.assertEqual(affected_tests.RULE_ALL, sites_pt[("calc.py", 2)]["rule"])

        ut_ids = ["tests.test_calc.CalcTests.test_add", "tests.test_calc.CalcTests.test_add_negative"]
        sel_ut = affected_tests.select(
            "unittest", f"{sys.executable} -m unittest discover -v", ut_ids, root, ["calc.py"]
        )
        sites_ut, _ = affected_tests.plan_for_mutants(sel_ut, [("calc.py", 2)])
        # Wait: selectors for unittest discover use full dotted ID `tests.test_calc.CalcTests.test_add`,
        # which DOES NOT match `tests.test_calc.CalcTests.test_add_negative` unless substring overlaps.
        # Verify dotted selector `test_add` IS a substring of `test_add_negative`!
        self.assertEqual(sorted(ut_ids), sorted(sites_ut[("calc.py", 2)]["tests"]))
        self.assertEqual(affected_tests.RULE_ALL, sites_ut[("calc.py", 2)]["rule"])

    def test_a_module_imported_through_any_sys_path_entry_selects_its_tests(self):
        """However a directory reaches sys.path, the test importing a module from it is selected.

        The local compose component imported ``server`` after inserting its own directory into
        sys.path; no test was selected, so every changed line was recorded as unobserved."""
        body = "def f():\n    return 1\n"
        case = "class T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(1, f())\n"
        unrelated = (
            "import unittest\nclass U(unittest.TestCase):\n    def test_u(self):\n        self.assertEqual(2, 2)\n"
        )
        insert = "import os, sys\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
        layouts = {
            "test inserts its parent, hyphenated directory": (
                "components/link-service/server.py",
                "components/link-service/tests/test_s.py",
                "components.link-service.tests.test_s",
                insert + "import unittest\nimport server\nf = server.f\n",
                "python -m unittest discover -v",
            ),
            "conftest appends the directory": (
                "app/lib.py",
                "tests/test_s.py",
                "tests.test_s",
                "import unittest\nfrom lib import f\n",
                "python -m pytest -v",
            ),
            "PYTHONPATH names the directory": (
                "app/lib.py",
                "tests/test_s.py",
                "tests.test_s",
                "import unittest\nfrom lib import f\n",
                "PYTHONPATH=app python -m unittest discover -v",
            ),
            "src package": (
                "src/pkg/lib.py",
                "tests/test_s.py",
                "tests.test_s",
                "import unittest\nfrom pkg.lib import f\n",
                "python -m unittest discover -v",
            ),
        }
        for name, (module, test_file, dotted, header, command) in layouts.items():
            with self.subTest(name):
                files = {
                    module: body,
                    test_file: header + case,
                    "tests/test_unrelated.py": unrelated,
                    "tests/conftest.py": "import sys, pathlib\n"
                    "sys.path.append(str(pathlib.Path(__file__).parent.parent / 'app'))\n",
                }
                for directory in {str(Path(test_file).parent), "tests"}:
                    files.setdefault(f"{directory}/__init__.py", "")
                root = self._tree(files)
                pytest = "pytest" in command
                wanted = f"{test_file}::T::test_a" if pytest else f"{dotted}.T.test_a"
                other = "tests/test_unrelated.py::U::test_u" if pytest else "tests.test_unrelated.U.test_u"
                selection = affected_tests.select(
                    "pytest" if pytest else "unittest", command, [wanted, other], root, [module]
                )
                sites, summary = affected_tests.plan_for_mutants(selection, [(module, 2)])
                self.assertIn(wanted, sites[(module, 2)]["tests"])
                self.assertEqual(0, summary["unobserved_mutants"])

    def test_a_definition_no_test_names_keeps_the_tests_that_import_its_module(self):
        # A live FastAPI run (2026-11-25): `@app.post("/alert")` registered the handler and every test reached
        # it through `TestClient(app)`, never by name, so symbol narrowing left its 26 mutants with no test and
        # they were recorded as survivors without running. A registry, a base class or an entry table does the
        # same, so the case is any definition reached without its name, in either runner.
        registries = {
            "decorator": (
                "HANDLERS = {}\n\n\ndef route(name):\n    def register(function):\n"
                "        HANDLERS[name] = function\n        return function\n\n    return register\n\n\n"
                "def dispatch(name, value):\n    return HANDLERS[name](value)\n\n\n"
                '@route("cap")\ndef cap(n):\n    if n > 10:\n        return 9\n    return n\n',
                'self.assertEqual(9, dispatch("cap", 11))',
            ),
            "subclass registry": (
                "class Handler:\n    known = {}\n\n    def __init_subclass__(cls, **kwargs):\n"
                "        super().__init_subclass__(**kwargs)\n        Handler.known[cls.__name__] = cls\n\n\n"
                "def dispatch(name, value):\n    return Handler.known[name]().run(value)\n\n\n"
                "class Cap(Handler):\n    def run(self, n):\n        if n > 10:\n            return 9\n"
                "        return n\n",
                'self.assertEqual(9, dispatch("Cap", 11))',
            ),
        }
        for name, (module, assertion) in registries.items():
            for runner, command, ids in (
                (
                    "unittest",
                    f"{sys.executable} -m unittest discover -v",
                    ["tests.test_app.AppTests.test_cap", "tests.test_other.OtherTests.test_other"],
                ),
                (
                    "pytest",
                    f"{sys.executable} -m pytest tests -q",
                    ["tests/test_app.py::AppTests::test_cap", "tests/test_other.py::OtherTests::test_other"],
                ),
            ):
                with self.subTest(name, runner=runner):
                    root = self._tree(
                        {
                            "app.py": module,
                            "other.py": "def other():\n    return 1\n",
                            "tests/__init__.py": "",
                            "tests/test_app.py": "import unittest\nfrom app import dispatch\n\n\n"
                            f"class AppTests(unittest.TestCase):\n    def test_cap(self):\n        {assertion}\n",
                            "tests/test_other.py": "import unittest\nfrom other import other\n\n\n"
                            "class OtherTests(unittest.TestCase):\n    def test_other(self):\n"
                            "        self.assertEqual(1, other())\n",
                        }
                    )
                    line = next(
                        number for number, text in enumerate(module.splitlines(), 1) if text.strip() == "if n > 10:"
                    )
                    selection = affected_tests.select(runner, command, ids, root, ["app.py"])
                    sites, summary = affected_tests.plan_for_mutants(selection, [("app.py", line)])
                    self.assertEqual([ids[0]], sites[("app.py", line)]["tests"])
                    self.assertEqual(affected_tests.RULE_IMPORT, sites[("app.py", line)]["rule"])
                    self.assertEqual(0, summary["unobserved_mutants"])

    def test_unobserved_file_and_fallbacks(self):
        root = self._tree(
            {
                "calc.py": "def f():\n    return 1\n",
                "unused.py": "def g():\n    return 2\n",
                "tests/test_calc.py": """\
                    import unittest
                    from calc import f


                    class CalcTests(unittest.TestCase):
                        def test_f(self):
                            self.assertEqual(1, f())

                        def test_const(self):
                            self.assertEqual(2, 2)
                    """,
            }
        )
        tests = ["tests.test_calc.CalcTests.test_f", "tests.test_calc.CalcTests.test_const"]
        sel = affected_tests.select("unittest", f"{sys.executable} -m unittest -v", tests, root, ["unused.py"])
        sites, summary = affected_tests.plan_for_mutants(sel, [("unused.py", 2)])
        self.assertEqual([], sites[("unused.py", 2)]["tests"])
        self.assertEqual(affected_tests.RULE_IMPORT, sites[("unused.py", 2)]["rule"])
        self.assertIsNone(sites[("unused.py", 2)]["command"])
        self.assertEqual(1, summary["unobserved_mutants"])

        # Unmapped test IDs fallback (never raises KeyError in plan_for_mutants)
        unmapped_sel = affected_tests.select(
            "unittest",
            f"{sys.executable} -m unittest -v",
            ["completely.unknown.Case.test_a", "completely.unknown.Case.test_b"],
            root,
            ["calc.py"],
        )
        self.assertEqual("test_files_unmapped", unmapped_sel["fallback"])
        sites_u, summary_u = affected_tests.plan_for_mutants(unmapped_sel, [("calc.py", 2)])
        self.assertEqual(2, len(sites_u[("calc.py", 2)]["tests"]))
        self.assertEqual("test_files_unmapped", summary_u["fallback"])
        self.assertEqual(1, summary_u["full_selection_mutants"])

        # Single selected test fallback & unsupported runner fallback
        single_sel = affected_tests.select(
            "unittest", f"{sys.executable} -m unittest -v", ["tests.test_calc.CalcTests.test_f"], root, ["calc.py"]
        )
        self.assertEqual("single_selected_test", single_sel["fallback"])
        sites_s, summary_s = affected_tests.plan_for_mutants(single_sel, [("calc.py", 2)])
        self.assertEqual("single_selected_test", summary_s["fallback"])
        self.assertEqual(1, summary_s["full_selection_mutants"])

        unsup_sel = affected_tests.select("go", "go test ./...", ["a", "b"], root, ["calc.py"])
        self.assertEqual("unsupported_runner", unsup_sel["fallback"])
        _, summary_go = affected_tests.plan_for_mutants(unsup_sel, [("calc.py", 2)])
        self.assertEqual("unsupported_runner", summary_go["fallback"])


if __name__ == "__main__":
    unittest.main()
