"""Deterministic first-order mutants of changed Python lines (#699): pure, bytes in and mutants out."""

import ast
import unittest
from unittest import mock

import autocode_mutants as mutants

WINDOW = b'def in_window(n, lo, hi):\n    """Half-open window."""\n    if lo > hi:\n        raise ValueError("empty")\n    return lo <= n < hi\n'


def every_line(source):
    return range(1, len(mutants.NEWLINE.findall(source)) + 2)


def shown(made):
    return [(mutant.line, mutant.operator, mutant.before, mutant.after) for mutant in made]


class OperatorTests(unittest.TestCase):
    def test_each_operator_changes_one_site_on_the_changed_lines(self):
        made, notes = mutants.python_mutants("app.py", WINDOW, range(3, 6), limit=20)
        self.assertEqual([], notes)
        self.assertEqual(
            [
                (3, "negate_condition", "lo > hi", "not (lo > hi)"),
                (3, "swap_bound", ">", ">="),
                (4, "drop_branch", 'raise ValueError("empty")', "pass"),
                (5, "swap_bound", "<=", "<"),
                (5, "swap_bound", "<", "<="),
                (5, "constant_return", "lo <= n < hi", "None"),
            ],
            shown(made),
        )
        self.assertEqual(WINDOW.replace(b"if lo > hi", b"if lo >= hi"), mutants.apply(WINDOW, made[1]))
        self.assertEqual(WINDOW.replace(b'raise ValueError("empty")', b"pass"), mutants.apply(WINDOW, made[2]))

    def test_a_literal_bound_to_a_name_becomes_another_constant(self):
        # A line whose whole behavior is the value it binds has no condition, branch or return to
        # mutate; the constant itself is its site. None and Ellipsis have no other constant to become.
        source = b"TEXT = 'hello'\nLIMIT: int = 3\nFLAG = True\nNOTHING = None\nRATIO = 0.0\nMARK = ...\n"
        made, notes = mutants.python_mutants("settings.py", source, every_line(source), limit=20)
        self.assertEqual([], notes)
        self.assertEqual(
            [
                (1, "constant_assignment", "'hello'", '""'),
                (2, "constant_assignment", "3", "0"),
                (3, "constant_assignment", "True", "False"),
                (5, "constant_assignment", "0.0", "1"),
            ],
            shown(made),
        )
        self.assertEqual(source.replace(b"'hello'", b'""'), mutants.apply(source, made[0]))
        self.assertEqual([], mutants.python_mutants("settings.py", source, [4, 6], limit=20)[0])

    def test_unchanged_lines_are_never_mutated(self):
        made, _ = mutants.python_mutants("app.py", WINDOW, [5], limit=20)
        self.assertEqual({5}, {mutant.line for mutant in made})
        self.assertEqual(([], []), mutants.python_mutants("app.py", WINDOW, []))

    def test_tuple_assignment_swaps_the_first_two_names_on_a_changed_line(self):
        source = b"def week_key(day):\n    year,  week, _ = day.isocalendar()  # ISO\n    return year, week\n"
        made, notes = mutants.python_mutants("weeks.py", source, [2])
        self.assertEqual([], notes)
        self.assertEqual([(2, "swap_tuple_assignment", "year, week", "week, year")], shown(made))
        self.assertEqual(source.replace(b"year,  week", b"week,  year"), mutants.apply(source, made[0]))
        self.assertEqual(["constant_return"], [m.operator for m in mutants.python_mutants("weeks.py", source, [3])[0]])

    def test_tuple_assignment_skips_non_simple_or_split_targets(self):
        for source in (
            b"a, a = pair\n",
            b"a, *rest = values\n",
            b"a, obj.b = pair\n",
            b"(a,\n b) = pair\n",
            b"a, b = c, d = pair\n",
        ):
            with self.subTest(source=source):
                made, notes = mutants.python_mutants("values.py", source, every_line(source))
                self.assertEqual([], notes)
                self.assertNotIn("swap_tuple_assignment", [mutant.operator for mutant in made])
                self.assertEqual({"none_assignment"}, {mutant.operator for mutant in made})

    def test_an_integer_bound_shifts_by_one_but_a_bool_is_not_a_bound(self):
        made, _ = mutants.python_mutants("limits.py", b"ok = n > 52\nflag = n < True\n", [1, 2], limit=20)
        self.assertIn((1, "shift_bound", "52", "53"), shown(made))
        self.assertNotIn("shift_bound", [mutant.operator for mutant in made if mutant.line == 2])

    def test_a_returned_value_becomes_a_constant_of_its_kind(self):
        source = b"def a():\n    return True\n\n\ndef b():\n    return 7\n\n\ndef c():\n    return 'text'\n\n\ndef d():\n    return 0\n"
        made, _ = mutants.python_mutants("values.py", source, every_line(source), limit=20)
        self.assertEqual(
            [(2, "True", "False"), (6, "7", "0"), (10, "'text'", '""'), (14, "0", "1")],
            [(mutant.line, mutant.before, mutant.after) for mutant in made],
        )
        none, _ = mutants.python_mutants("none.py", b"def f():\n    return None\n", [1, 2], limit=20)
        self.assertEqual([], none)

    def test_a_dropped_else_block_is_reported_at_its_own_first_line(self):
        source = b"if a:\n    x = 1\nelse:\n    x = 2\n    y = 3\n"
        made, _ = mutants.python_mutants("branch.py", source, [4, 5], limit=20)
        self.assertEqual(
            [
                (4, "drop_branch", "x = 2 y = 3", "pass"),
                (4, "constant_assignment", "2", "0"),
                (5, "constant_assignment", "3", "0"),
            ],
            shown(made),
        )
        self.assertEqual(5, made[0].end_line)
        self.assertEqual(b"if a:\n    x = 1\nelse:\n    pass\n\n", mutants.apply(source, made[0]))

    def test_guards_f_strings_annotations_and_inert_branches_are_left_alone(self):
        source = (
            b"from typing import TYPE_CHECKING\n"
            b"if TYPE_CHECKING:\n"
            b"    import os\n"
            b"msg = f'{a < 2}'\n"
            b"def f(n: int = 3) -> int:\n"
            b"    if n:\n"
            b"        '''nothing'''\n"
            b"    return n\n"
            b"if __name__ == '__main__':\n"
            b"    f(1)\n"
        )
        made, notes = mutants.python_mutants("guards.py", source, every_line(source), limit=20)
        self.assertEqual([], notes)
        self.assertEqual([(6, "negate_condition", "n", "not (n)"), (8, "constant_return", "n", "None")], shown(made))

    def test_only_an_ordering_comparison_has_a_bound_to_shift(self):
        source = b"if n == 0:\n    pass\nok = n != 3 and n is not 4 and n in (5,)\nr = 0 < n == 7\n"
        made, _ = mutants.python_mutants("eq.py", source, [1, 3, 4], limit=20)
        self.assertEqual([(4, "0", "1")], [(m.line, m.before, m.after) for m in made if m.operator == "shift_bound"])

    def test_a_keyword_right_before_the_site_keeps_a_space_after_it(self):
        source = b"def f(a, b):\n    return(a, b)\n\n\ndef g():\n    return-1\n\n\nif-x:\n    pass\n"
        made, notes = mutants.python_mutants("kw.py", source, [2, 6, 9], limit=20)
        self.assertEqual([], notes)
        self.assertEqual(
            [
                (2, "constant_return", "(a, b)", "None"),
                (6, "constant_return", "-1", "None"),
                (9, "negate_condition", "-x", "not (-x)"),
            ],
            shown(made),
        )
        self.assertEqual(
            [b"    return None", b"    return None", b"if not (-x):"],
            [mutants.apply(source, mutant).splitlines()[mutant.line - 1] for mutant in made],
        )

    def test_an_else_holding_only_an_if_is_dropped_but_an_elif_is_not(self):
        nested = b"if a:\n    x = 1\nelse:\n    if b:\n        y = 2\n"
        made, _ = mutants.python_mutants("else.py", nested, [4, 5], limit=20)
        self.assertIn((4, "drop_branch", "if b: y = 2", "pass"), shown(made))
        dropped = next(m for m in made if (m.line, m.operator) == (4, "drop_branch"))
        self.assertEqual(b"if a:\n    x = 1\nelse:\n    pass\n\n", mutants.apply(nested, dropped))
        chained = b"if a:\n    x = 1\nelif b:\n    y = 2\n"
        made, _ = mutants.python_mutants("elif.py", chained, [3, 4], limit=20)
        self.assertEqual([(4, "y = 2")], [(m.line, m.before) for m in made if m.operator == "drop_branch"])
        named = b"if a:\n    x = 1\nelse:\n    elif_x = 2\n"
        made, _ = mutants.python_mutants("named.py", named, [3, 4], limit=20)
        self.assertIn((4, "drop_branch", "elif_x = 2", "pass"), shown(made))

    def test_a_decorated_definition_is_dropped_from_its_first_decorator(self):
        source = b"if x:\n    @cache\n    @dec(1)\n    def f():\n        return 1\nelse:\n    @dec\n    class C:\n        pass\n"
        made, notes = mutants.python_mutants("dec.py", source, range(1, 10), limit=20)
        self.assertEqual([], notes)
        dropped = [m for m in made if m.operator == "drop_branch"]
        self.assertEqual([(2, 5), (7, 9)], [(m.line, m.end_line) for m in dropped])
        self.assertEqual(
            b"if x:\n    pass\n\n\n\nelse:\n    @dec\n    class C:\n        pass\n", mutants.apply(source, dropped[0])
        )

    def test_a_comment_between_the_operands_is_not_taken_for_the_operator(self):
        source = b"ok = (a  # < not this one\n      < b)\n"
        made, _ = mutants.python_mutants("comment.py", source, [1, 2], limit=20)
        # Line 1 has no site of its own to start, so the statement's value gets one there (#2815).
        self.assertEqual(
            [(1, "none_assignment", "a # < not this one < b", "(None )"), (2, "swap_bound", "<", "<=")], shown(made)
        )
        self.assertEqual(b"ok = (a  # < not this one\n      <= b)\n", mutants.apply(source, made[1]))

    def test_a_line_with_only_a_call_a_computed_value_or_a_loop_gets_a_statement_site(self):
        # #2815: these lines had no site, so each changed one needed a written reason. A call made for its
        # effect is dropped, a computed value becomes None and a loop runs over nothing.
        source = (
            b"def f(items, log):\n"
            b"    log.write(items)\n"
            b"    total = sum(items)\n"
            b"    names: list = sorted(items)\n"
            b"    for item in items:\n"
            b"        log.write(item)\n"
            b"    for nothing in ():\n"
            b"        pass\n"
            b"    count = 3\n"
            b"    return total, names\n"
        )
        made, notes = mutants.python_mutants("effects.py", source, range(2, 10), limit=20)
        self.assertEqual([], notes)
        self.assertEqual(
            [
                (2, "drop_call", "log.write(items)", "None"),
                (3, "none_assignment", "sum(items)", "None"),
                (4, "none_assignment", "sorted(items)", "None"),
                (5, "skip_loop", "items", "()"),
                (6, "drop_call", "log.write(item)", "None"),
                (9, "constant_assignment", "3", "0"),
            ],
            shown(made),
        )
        self.assertEqual(source.replace(b"for item in items", b"for item in ()"), mutants.apply(source, made[3]))

    def test_every_changed_statement_line_of_a_live_run_gets_a_site(self):
        # #2815: on a live ladder-24 run (claude-tiers, 2026-11-07) these kinds of changed line had no
        # mutant, so each needed a written reason: an import, an except clause, a re-raise, the context of
        # a with, an argument or a dictionary entry on a line of its own, an augmented assignment, and
        # every statement of an if body after its first. Each now has a site of its own.
        source = (
            b"import json\n"
            b"from os import path as p\n"
            b"def load(name, env, seen):\n"
            b"    try:\n"
            b"        with open(name) as f:\n"
            b"            data = json.load(p.join(f))\n"
            b"    except ValueError as e:\n"
            b"        raise\n"
            b"    if env:\n"
            b"        key = env[0]\n"
            b"        seen += 1\n"
            b"    out = dict(\n"
            b"        mode='w',\n"
            b"        delete=False,\n"
            b"    )\n"
            b"    return {\n"
            b"        'data': data,\n"
            b"        'key': key,\n"
            b"    }\n"
        )
        made, notes = mutants.python_mutants("live.py", source, every_line(source), limit=100)
        self.assertEqual([], notes)
        ops = {(mutant.line, mutant.operator) for mutant in made}
        self.assertTrue(
            {
                (1, "drop_import"),
                (2, "drop_import"),
                (5, "none_context"),
                (6, "none_assignment"),
                (7, "never_catch"),
                (8, "drop_raise"),
                (10, "drop_branch"),
                (11, "none_update"),
                (13, "none_keyword"),
                (14, "none_keyword"),
                (17, "none_entry"),
                (18, "none_entry"),
            }
            <= ops,
            sorted(ops),
        )
        changes = {mutant.operator: (mutant.before, mutant.after) for mutant in made}
        # Dropping an import counts only when a function reads its name: one read while the module loads
        # fails collection, not a test, and an unused one changes nothing.
        eager = b"import json\nimport os\nfrom a import b\nLOADS = json.loads\ndef f():\n    return os.sep\n"
        self.assertEqual(
            [(2, "drop_import", "import os", "pass")],
            [row for row in shown(mutants.python_mutants("eager.py", eager, [1, 2, 3], limit=20)[0])],
        )
        local = b"def f():\n    import os\n    return os.sep\ndef g():\n    import sys\n"
        self.assertEqual(
            [(2, "drop_import", "import os", "pass")],
            [row for row in shown(mutants.python_mutants("local.py", local, [2, 5], limit=20)[0]) if row[0] != 3],
        )
        self.assertEqual(("ValueError", "()"), changes["never_catch"])
        self.assertEqual(("raise", "pass"), changes["drop_raise"])
        self.assertEqual(("1", "None"), changes["none_update"])
        self.assertIn(("False", "True"), {(m.before, m.after) for m in made if m.operator == "none_keyword"})
        self.assertEqual(source.replace(b"import json", b"pass"), mutants.apply(source, made[0]))
        for mutant in made:
            with self.subTest(mutant=mutant.id):
                result = mutants.apply(source, mutant)
                compile(result, "<mutant>", "exec", dont_inherit=True)
                self.assertEqual(len(mutants.NEWLINE.findall(source)), len(mutants.NEWLINE.findall(result)))

    def test_a_statement_site_never_doubles_a_line_that_has_another_site(self):
        source = b"ok = a < 2\nhits = [x for x in y if x > 1]\nf(a if a < 2 else b)\nfor x in g(a > 1):\n    pass\n"
        made, _ = mutants.python_mutants("both.py", source, every_line(source), limit=50)
        self.assertEqual(set(), {mutant.operator for mutant in made} & mutants.STATEMENT_OPERATORS)
        self.assertEqual({1, 2, 3, 4}, {mutant.line for mutant in made})

    def test_code_under_a_main_or_type_checking_guard_gets_no_statement_site(self):
        source = (
            b"from typing import TYPE_CHECKING\n"
            b"if TYPE_CHECKING:\n"
            b"    thing = load()\n"
            b"if __name__ == '__main__':\n"
            b"    main()\n"
            b"run()\n"
        )
        made, _ = mutants.python_mutants("guarded.py", source, every_line(source), limit=50)
        self.assertEqual([(6, "drop_call", "run()", "None")], shown(made))

    def test_no_none_binding_where_the_module_would_then_fail_to_load(self):
        # A None the module's own import reads fails test collection, so that mutant could only end
        # not_run. Seen in review-then-fix: _errors runs on import and DEFAULT reads TRANSIENT.
        source = (
            b"def _errors(*groups):\n"
            b"    merged = []\n"
            b"    for group in groups:\n"
            b"        merged.extend(group)\n"
            b"    return tuple(merged)\n"
            b"\n"
            b"\n"
            b"def lookup(name):\n"
            b"    found = TABLE.get(name)\n"
            b"    return found\n"
            b"\n"
            b"\n"
            b'TRANSIENT = ("BUSY",)\n'
            b"DEFAULT = _errors(TRANSIENT)\n"
            b"TABLE = dict(a=DEFAULT)\n"
            b"LATER = dict(b=1)\n"
            b"count = len(TABLE)\n"
            b"count = len(TABLE) + count\n"
        )
        made, notes = mutants.python_mutants("load.py", source, every_line(source), limit=50)
        self.assertEqual([], notes)
        bound = {(mutant.line, mutant.before) for mutant in made if mutant.operator == "none_assignment"}
        # merged runs on import; TRANSIENT, DEFAULT, TABLE and the first count are read later while loading.
        # lookup's binding and the names nothing reads on import keep theirs.
        self.assertEqual({(9, "TABLE.get(name)"), (16, "dict(b=1)"), (18, "len(TABLE) + count")}, bound)
        # Its other operators keep their sites: skipping a load-time loop leaves a value a test can check.
        self.assertIn((3, "skip_loop", "groups", "()"), shown(made))
        self.assertEqual(
            len(mutants.NEWLINE.findall(source)), len(mutants.NEWLINE.findall(mutants.apply(source, made[0])))
        )

    def test_the_statement_operators_leave_code_that_runs_on_import_alone(self):
        # never_catch on an optional import, or None for a module-level with, update, raise or a keyword
        # that load-time code reads, fails collection, not a test. The same lines in a function keep theirs.
        source = (
            b"try:\n"
            b"    import fast\n"
            b"except ImportError:\n"
            b"    fast = None\n"
            b"with open('x') as f:\n"
            b"    TEXT = f.read()\n"
            b"COUNT = 1\n"
            b"COUNT += 1\n"
            b"if COUNT > 9:\n"
            b"    raise SystemExit\n"
            b"TABLE = dict(\n"
            b"    size=COUNT,\n"
            b")\n"
            b"WIDTH = TABLE['size']\n"
            b"LATER = dict(\n"
            b"    b=1,\n"
            b")\n"
            b"def run():\n"
            b"    try:\n"
            b"        count = 0\n"
            b"    except ValueError:\n"
            b"        raise\n"
            b"    with open('y') as g:\n"
            b"        count += 1\n"
            b"    return dict(\n"
            b"        size=count,\n"
            b"    )\n"
        )
        made, notes = mutants.python_mutants("load.py", source, every_line(source), limit=100)
        self.assertEqual([], notes)
        new = {"never_catch", "none_context", "none_update", "drop_raise", "none_keyword"}
        ops = {(mutant.line, mutant.operator) for mutant in made if mutant.operator in new}
        self.assertEqual(
            {(16, "none_keyword"), (21, "never_catch"), (22, "drop_raise"), (23, "none_context")}
            | {(24, "none_update"), (26, "none_keyword")},
            ops,
        )


class ValidityTests(unittest.TestCase):
    SOURCES = {
        "bom": b"\xef\xbb\xbfx = 1 if a < 2 else 0\n",
        "crlf": b"def f(a):\r\n    if a < 2:\r\n        return a\r\n    return 0\r\n",
        "form feed": b"x = 1\n\x0c\ndef f(a):\n    return a < 2\n",
        "multibyte": "s = 'é'; t = 1 if a < 2 else 0\n".encode(),
        "multi-line return": b"def f(a):\n    return g(\n        a,\n    ); y = 1\n",
        "chained": b"ok = 0 < a <= 10\n",
        "elif": b"if a < 1:\n    x = 1\nelif a < 2:\n    x = 2\nelse:\n    x = 3\n",
        "while": b"while i < n:\n    i += 1\n",
        "match guard": b"match p:\n    case [x] if x > 1:\n        y = x\n",
        "comprehension": b"z = [x for x in y if x >= 3]\n",
        "multi-line call": b"log(\n    a,\n    b,\n)\n",
        "multi-line value": b"z = dict(\n    a=1,\n)\n",
        "multi-line loop": b"for x in g(\n    a,\n):\n    log(x)\n",
        "awaited call": b"async def f():\n    await g()\n",
    }

    def test_every_mutant_parses_keeps_the_line_count_and_differs_from_the_original(self):
        for name, source in self.SOURCES.items():
            made, notes = mutants.python_mutants("sample.py", source, every_line(source), limit=50)
            with self.subTest(source=name):
                self.assertTrue(made)
                self.assertEqual([], notes)
                for mutant in made:
                    result = mutants.apply(source, mutant)
                    body = result[3:] if result.startswith(mutants.BOM) else result
                    self.assertNotEqual(
                        ast.dump(ast.parse(source[3:] if name == "bom" else source)), ast.dump(ast.parse(body))
                    )
                    self.assertEqual(len(mutants.NEWLINE.findall(source)), len(mutants.NEWLINE.findall(result)))
                    self.assertEqual(source.startswith(mutants.BOM), result.startswith(mutants.BOM))

    def test_a_mutant_that_parses_but_does_not_compile_is_skipped_and_the_next_one_runs(self):
        # Dropping `x = 0` leaves inner's `nonlocal x` with nothing to bind: the module would not import,
        # and the mutant would be counted as killed. It is skipped and the next site takes its place.
        source = (
            b"def outer(a):\n    if a:\n        x = 0\n    def inner():\n        nonlocal x\n"
            b"        return x\n    return 5\n"
        )
        made, notes = mutants.python_mutants("scope.py", source, [3, 7], limit=1)
        self.assertEqual([(7, "constant_return", "5", "0")], shown(made))
        self.assertEqual("scope.py:3: drop_branch does not compile; skipped", notes[0])

    def test_a_site_that_cannot_be_built_or_checked_is_skipped_and_the_others_still_run(self):
        deep = " + ".join(f"p{index}" for index in range(600)).encode()
        source = b"def f(n):\n    if n > 2:\n        s = " + deep + b"\n        return s\n    return n < 7\n"
        made, notes = mutants.python_mutants("deep.py", source, [2, 5], limit=20)
        self.assertTrue(made)
        self.assertEqual({5}, {mutant.line for mutant in made})
        self.assertIn("deep.py:2: negate_condition could not be checked (RecursionError); skipped", notes)
        huge = b"ok = n < 0x" + b"f" * 4000 + b"\nz = a < 2\n"
        made, notes = mutants.python_mutants("huge.py", huge, [1, 2], limit=20)
        self.assertEqual([(2, "swap_bound", "<", "<="), (2, "shift_bound", "2", "3")], shown(made))
        self.assertIn("huge.py:1: a mutation site could not be built (ValueError); skipped", notes)

    def test_every_rejection_names_its_reason(self):
        def check(start, end, replacement, operator="swap_bound", detail=0, source=b"ok = a < 2\nz = 1\n"):
            tree = ast.parse(source)
            parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            target = tree.body[0] if operator == "drop_branch" else tree.body[0].value
            site = mutants._Site(operator, start, end, replacement, target, detail)
            return mutants._check(tree, source, site, parents)

        # The whole file's line breaks count: `pass` plus the block's lone CR, spliced before a line
        # feed, makes one CRLF of two breaks, though the replacement keeps the block's own count.
        merged = b"if a:\n    x = 1\r    y = 2\nz = 3\n"
        self.assertEqual("would change the line count", check(10, 25, b"pass\r", "drop_branch", "body", merged)[0])

        self.assertEqual("would change the line count", check(7, 8, b"<\n")[0])
        self.assertEqual("does not parse", check(7, 8, b"<<<")[0])
        self.assertEqual("does not compile", check(0, 10, b"return a")[0])
        self.assertEqual("changes more than its statement", check(9, 10, b"2; y = 3")[0])
        self.assertEqual("does not make the intended change", check(7, 8, b">")[0])
        self.assertEqual("does not make the intended change", check(7, 8, b"<")[0])
        problem, key = check(7, 8, b"<=")
        self.assertIsNone(problem)
        self.assertEqual(key, check(5, 10, b"a <= 2")[1])  # a different splice, the same mutant

    def test_two_splices_that_make_the_same_mutant_give_one(self):
        def sites(tree, *_):
            compare = tree.body[0].value
            return [
                mutants._Site("swap_bound", 7, 8, b"<=", compare, 0),
                mutants._Site("swap_bound", 5, 10, b"a <= 2", compare, 0),
            ]

        with mock.patch.object(mutants, "_sites", side_effect=sites):
            made, notes = mutants.python_mutants("same.py", b"ok = a < 2\n", [1], limit=20)
        self.assertEqual([("a < 2", "a <= 2")], [(mutant.before, mutant.after) for mutant in made])
        self.assertEqual([], notes)

    def test_a_multi_line_return_keeps_every_later_line_number(self):
        source = self.SOURCES["multi-line return"]
        mutant, bound = mutants.python_mutants("sample.py", source, [2, 3, 4], limit=50)[0]
        self.assertEqual((2, 4), (mutant.line, mutant.end_line))
        self.assertEqual((4, "constant_assignment", "1", "0"), shown([bound])[0])
        self.assertEqual(b"def f(a):\n    return (None\n\n); y = 1\n", mutants.apply(source, mutant))

    def test_the_same_bytes_give_the_same_mutants(self):
        first = mutants.python_mutants("app.py", WINDOW, range(1, 6))
        second = mutants.python_mutants("app.py", bytes(WINDOW), list(reversed(range(1, 6))))
        self.assertEqual(first, second)
        self.assertEqual("app.py:5:swap_bound:2", first[0][4].id)


class LimitTests(unittest.TestCase):
    def test_the_limit_spreads_over_operators_and_lines_and_says_so(self):
        source = (
            b"".join(
                f"def f{index}(n):\n    if n > {index}:\n        n = 0\n    return n\n".encode() for index in range(10)
            )
            + b"a, b = values\n"
        )
        for limit in (6, 7):
            with self.subTest(limit=limit):
                made, notes = mutants.python_mutants("many.py", source, every_line(source), limit=limit)
                self.assertEqual(limit, len(made))
                self.assertEqual(set(mutants.OPERATORS[:limit]), {mutant.operator for mutant in made})
                self.assertEqual(limit, len({mutant.line for mutant in made}))
                self.assertEqual(sorted(made, key=lambda mutant: mutant.line), made)
                self.assertEqual([f"many.py: 61 mutation sites on the changed lines; {limit} were chosen"], notes)
        self.assertEqual(mutants.PER_FILE_LIMIT, len(mutants.python_mutants("many.py", source, every_line(source))[0]))


class SkipTests(unittest.TestCase):
    def test_files_that_cannot_be_mutated_safely_say_why(self):
        cases = {
            "stub.pyi": (b"def f(n: int) -> bool: ...\n", "a type stub has no runtime behavior"),
            "broken.py": (b"def f(:\n", "does not parse"),
            "latin.py": (b"# -*- coding: latin-1 -*-\ns = '\xe9'\nok = a < 2\n", "declares the iso-8859-1 encoding"),
            "cr.py": (b"if a:\n    x = 1\r    y = 2\nok = a < 2\n", "a carriage return without a line feed"),
            "head.py": (b"s = '\xff'\nok = a < 2\n", "its encoding cannot be read"),
            "bytes.py": (b"ok = a < 2\nx = 1\ns = '\xff'\n", "is not valid UTF-8"),
            "notes.txt": (b"a < 2\n", "not a Python source file"),
        }
        for path, (source, reason) in cases.items():
            with self.subTest(path=path):
                made, notes = mutants.python_mutants(path, source, [1, 2, 3])
                self.assertEqual([], made)
                self.assertEqual(1, len(notes))
                self.assertIn(reason, notes[0])
                self.assertTrue(notes[0].startswith(path + ":"))


class EncodingTests(unittest.TestCase):
    def test_every_spelling_of_utf_8_and_ascii_is_mutated(self):
        for cookie in (b"utf-8", b"utf8", b"UTF8", b"U8", b"utf_8", b"ascii", b"us-ascii"):
            with self.subTest(cookie=cookie):
                made, notes = mutants.python_mutants("c.py", b"# -*- coding: " + cookie + b" -*-\nok = a < 2\n", [2])
                self.assertEqual([], notes)
                self.assertEqual([(2, "swap_bound", "<", "<="), (2, "shift_bound", "2", "3")], shown(made))


class ApplyTests(unittest.TestCase):
    def test_a_mutant_refuses_bytes_other_than_its_own_file(self):
        mutant = mutants.python_mutants("app.py", WINDOW, [5])[0][0]
        with self.assertRaisesRegex(ValueError, "not the file its mutants were made from"):
            mutants.apply(WINDOW.replace(b"Half", b"Open"), mutant)

    def test_changed_lines_come_from_diff_coverage_hunks(self):
        rows = [
            {"path": "app.py", "hunks": [{"start": 3, "end": 5}, {"start": 9, "end": 9}]},
            {"path": "other.py", "hunks": [{"start": 1, "end": 1}]},
        ]
        self.assertEqual({"app.py": frozenset({3, 4, 5, 9}), "other.py": frozenset({1})}, mutants.changed_lines(rows))
        self.assertEqual({}, mutants.changed_lines(None))


if __name__ == "__main__":
    unittest.main()
