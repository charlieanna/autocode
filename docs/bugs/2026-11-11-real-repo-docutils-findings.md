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
again. The scenario's `known_failure` is removed
(`tests.test_python_test_roles.NestedProjectTestTreeTests`).

## 3. An unchanged file of unknown role stopped the proof (fixed)

With the suite classified, the scripted run stopped at the same `PAUSED_INVALID_OUTPUT`, now for
`sandbox/enhancement-proposals/settings-API/test_cf_parser.py`, a file nobody changed. The regression
proof listed the original tests for the companion suites (#2889) by asking the role of every file in
the repository with the call that refuses an ambiguous changed path, and seven unchanged `sandbox/`
test files still have no provable role. The proof now counts the original tests the way the protected
inventory does: a test-like path stays a test unless it is proven original product, so an unknown one
stays protected and only a changed file of unknown role stops a run
(`tests.test_test_cases.FeatureProofTests.test_an_unchanged_file_of_unknown_role_does_not_stop_the_proof`).
