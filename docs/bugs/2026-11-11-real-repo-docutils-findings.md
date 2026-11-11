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

## 2. Every test outside a top-level `test/` is ambiguous (open)

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

Either change alone makes both files TEST (checked by patching each rule on the delivered project).
Both touch how protected oracles are chosen: a nested `test/` may be a product package, and a parse
failure may be newer syntax than AutoCode's interpreter reads, so neither is changed here. The
scenario records this as its `known_failure` until the policy is decided. A run stopped here is a
machinery stop, which AGENTS.md counts as a defect, not a person's question.
