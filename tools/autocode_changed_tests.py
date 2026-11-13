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
except ImportError:
    import autocode_cargo_tests as cargo_tests
    import autocode_go_tests as go_tests


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
