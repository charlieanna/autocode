# First findings of the mid-size real-repository case (#2944)

The fourth reliability case, `bugfix-realrepo-docutils-number-lines`, seeds the whole Docutils
repository (GitHub mirror at `54f4d615a7608d6b0bee7dec43a5be1543d4e456`: 2,984 files, 191,000 lines
of Python; the `docutils/` project holds 94,000 of them and a suite of about 2,300 cases, and
`sandbox/` holds 97,000 lines of old experiments). Its oracle tells upstream's fix from both broken
variants (`scenarios/run.py check`). The scripted run (`scenarios/run.py run
bugfix-realrepo-docutils-number-lines --fake`, master `19bc40c` plus this change, Linux cloud
container, 2026-11-11) delivered a fix the oracle accepts (12/12 checks) and still did not complete.
Neither stop is reachable from the small fixtures.

## 1. Start crashed on undecodable Python files (fixed)

`autocode` exited 2 before Requirements with `'utf-8' codec can't decode byte 0xe5`. The Python
test-role inventory (`tools/autocode_test_role_inventory.py`, `_text`) decoded every `.py` file in
its declared encoding, and four `sandbox/` files hold Latin-1 bytes with no coding line. Python 3
cannot import such a file either. `_text` now keeps the bytes as surrogate escapes when the encoding
cannot decode them or the coding cookie names no codec, so the file is an unparseable source, as
the role policy already handles (`tests.test_python_test_roles.UndecodableSourceTests`).

## 2. Every test outside a top-level `test/` is ambiguous (fixed)

With that fixed, the run stops at `PAUSED_INVALID_OUTPUT`: "Python product/test ownership is
ambiguous for changed path docutils/test/test_parsers/test_rst/test_directives/
test_include_number_lines.py". The original `test_include.py` beside it is ambiguous too. Two rules
in `tools/autocode_python_test_roles.py` combine:

- `_test_tree` treats `tests/` at any depth as a test tree but `test/` only at the repository root,
  and the packaging ownership is read only from a root `pyproject.toml`; Docutils' project and
  suite are one directory down.
- `_imports` reports an unparseable source as uncertain, and one uncertain file anywhere makes the
  whole reach uncertain, so a declared test outside a test tree is never classified TEST. The
  `sandbox/` has 98 Python 2 files that do not parse.

A run stopped here is a machinery stop, which AGENTS.md counts as a defect, not a person's question.

The second rule alone cannot be relaxed: Docutils' own product loads parsers, readers, writers and
languages by computed name (`importlib.import_module(name)`, five modules), which keeps runtime reach
uncertain with or without `sandbox/`, and a parse failure may be newer syntax than AutoCode's
interpreter reads. The first rule now counts `test/` and `spec/` as a test tree where they would be a
top-level import name: at the repository root, as before, or directly under a directory that is not a
Python package (no `__init__.py`). `docutils/test/` sits beside the `docutils/docutils/` package under
a plain project directory, so it is a test tree; a `test/` inside a package, such as Django's shipped
`django/test/`, still is not, and packaging ownership still wins over the tree. On the pinned Docutils
tree the change moves 158 test modules from ambiguous to test (in `docutils/test/`, `docutils/tools/test/`
and `sandbox/`) and changes no other role. The role policy is version 4, so a proof from version 3 is run
again. (`tests.test_python_test_roles.NestedProjectTestTreeTests`).

## 3. An unchanged file of unknown role stopped the proof (fixed)

With the suite classified, the scripted run stopped at the same `PAUSED_INVALID_OUTPUT`, now for
`sandbox/enhancement-proposals/settings-API/test_cf_parser.py`, a file nobody changed. The regression
proof listed the original tests for the companion suites (#2889) by asking the role of every file in
the repository with the call that refuses an ambiguous changed path, and seven unchanged `sandbox/`
test files still have no provable role. The proof now counts the original tests the way the protected
inventory does: a test-like path stays a test unless it is proven original product, so an unknown one
stays protected and only a changed file of unknown role stops a run
(`tests.test_test_cases.FeatureProofTests.test_an_unchanged_file_of_unknown_role_does_not_stop_the_proof`).

## 4. The seed commit dropped files Docutils tracks in ignored directories (fixed)

Next, the Tester's citation of `cd docutils/test && python3 alltests.py` was refused: AutoCode re-ran it in
a clean copy of the source and 31 cases failed with `No such file or directory:
'functional/output/latex_docinfo.tex'`. Docutils tracks `test/functional/output/README.rst` (and eleven
other files) inside directories its `.gitignore` ignores, so every checkout has the directory its suite
writes into. The harness committed the seed with `git add -A`, which skips ignored files, so the scenario
project held 2,972 of the commit's 2,984 files and AutoCode's clean copies had no `output/` directory.
The harness now commits every seed file (`git add -A --force`, `scenarios/harness/project.py`); no
catalog seed directory held an ignored file, so only pinned real-repository seeds change
(`scenarios.test_pinned_seed`). AutoCode was right to refuse: the check did not pass from a clean
checkout of the project it was given.

## 5. The root suite collected `sandbox/` (fixed)

With those fixed, the scripted run (`--out` outside this checkout, so pytest's root is not this
repository's own `pyproject.toml`) reached a regression proof that was `UNVERIFIED`: AutoCode ran the suite
it detected at the repository root, which collected `sandbox/`'s Python 2 test modules, and they failed to
collect on the original code and with the change alike, so "the base suite has collection errors;
preservation is unproven". The owner chose, on 2026-11-12, to prove such a change with the project's own
suite: a repository root with no manifest is no project, so tests inside a directory with its own
manifest run with that directory's suite even when the root's suite could also run them
(`autocode_package_scope.for_tests`, `tests.test_package_scope.SideBySideProjects`). Code changed outside
that project is still checked by the root's suite as a companion (#2889).

## 6. A module unittest skips at import was not read (fixed)

Proven with `docutils/`'s own suite on a host whose Python has no pytest (`python3 -m unittest discover
-v`), the results were still incomplete: Docutils skips two modules when an optional package is missing,
and unittest reports each as `test.test_parsers.test_recommonmark
(unittest.loader.ModuleSkipped.test.test_parsers.test_recommonmark) ... skipped`, which the result reader
did not recognize, so 466 named outcomes against "Ran 468" left the base suite incomplete. The reader now
reads that line as a skipped test (`tests.test_python_tests.UnittestResultParserTests`).

## 7. Upstream's test left two changed lines untested (fixed in the reference)

The changed-code gate then asked for evidence for two lines of upstream's fix in `body.py`, the option
table entry and `startline = 1`: their mutants survived upstream's regression test, which never runs the
"code" directive or an included `:code:` without a start value. The gate was right (AutoCode delivers no
untested code), so the scenario's reference adds those two cases to the delivered test; the oracle's
controls are unchanged.

With all of this, the scripted run completes: `TASK_COMPLETE`, oracle 14/14
(`scenarios/run.py run bugfix-realrepo-docutils-number-lines --fake`, Linux cloud container, no pytest
on the host, 2026-11-12), and the scenario has no known failure.

One smaller finding remains: one `sandbox/` module with module-level `def test_*` functions makes
AutoCode choose pytest for a suite detected at the repository root, even when pytest cannot be imported.
Now that `docutils/` has its own suite this no longer reaches the case.
