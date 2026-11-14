"""Which named tests a change itself adds or edits, for runners whose original code cannot build with them.

A feature's new test usually calls something the original code lacks. In Go and Rust the whole test
package then fails to build there, so no test ran on the original code and every passing test looks
new (#2505). Only a test the change adds or edits can show the new behavior; the judgment keeps an
unchanged one that passed in the original suite (autocode_verify_judgment.judge_regression).
"""

from __future__ import annotations

try:
    from . import autocode_cargo_tests as cargo_tests
    from . import autocode_go_tests as go_tests
    from . import autocode_rust_inline_tests as rust_inline_tests
except ImportError:
    import autocode_cargo_tests as cargo_tests
    import autocode_go_tests as go_tests
    import autocode_rust_inline_tests as rust_inline_tests


def inline_tests(workspace, base, tests, sources, changes) -> tuple[list[str], list[str]]:
    """Promote modified/added ``.rs`` files with changed inline ``#[cfg(test)]`` blocks to runnable tests."""
    return rust_inline_tests.partition_tests(workspace, base, tests, sources, changes)


def seam_words(path: str, before: str, after: str, proof_seam) -> tuple[set[str], set[str]]:
    """Product identifiers added by ``path`` plus inline test identifiers for seam detection."""
    if path.endswith(".rs") and rust_inline_tests.test_modules(after.encode()):
        before_prod = (
            rust_inline_tests.product_bytes(before.encode()).decode("utf-8", errors="replace") if before else ""
        )
        after_prod = rust_inline_tests.product_bytes(after.encode()).decode("utf-8", errors="replace")
        test_text = rust_inline_tests.inline_test_text(after.encode())
        return proof_seam.added_names(path, before_prod, after_prod), proof_seam.words(test_text)
    return proof_seam.added_names(path, before, after), set()


def changed_filter(run, git):
    """Whether a test id names a test the proof run's change adds or edits, or None to keep every test.

    ``run`` gives the proof's ``framework``, ``workspace``, ``base``, ``changes`` (path -> status) and
    ``runnable_tests`` (its changed test files); ``git(workspace, *args, check=False)`` returns Git's output.
    """
    name = run.framework.name if run.framework is not None else None

    def base_text(path):
        return git(run.workspace, "show", f"{run.base}:{path}", check=False)

    if name == "go":
        return go_tests.changed_filter(run.workspace, run.changes, base_text)
    if name == "cargo":
        tests = {path: run.changes[path] for path in run.runnable_tests}
        return cargo_tests.changed_filter(run.workspace, tests, base_text)
    return None
