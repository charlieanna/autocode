"""Prove a change's tests in another project of the repository with that project's own suite.

The regression proof runs the change's tests with one suite. A change that adds a field to an API in a
Python package and the line that shows it in a Node package beside it has tests in both, and no suite runs
both: the plan was refused when it was drafted, the Planner could not satisfy it, and the person was asked
how to proceed (a real run, 2026-11-14). Without that refusal the proof would have run only the tests its
own suite collects, and the other project's new tests would never have been shown to fail on the original
code.

``prove`` runs autocode_verify.verify once more for each suite autocode_companion_suites.test_suites names,
with that project's suite: its own baseline, the project's changed tests on the original code and with the
change, their repeats, and the project's whole suite before and after, exactly as a run of that project
alone is proven. Nothing is judged here by another rule. Its failures and unverified findings join the
proof's and only ever lower its verdict, its receipts join the proof's checks as
``companion_tests:DIR:CHECK``, and what it returns lets autocode_regression.check_cases rest a criterion on
a test the other suite ran.
"""

from __future__ import annotations

from pathlib import Path

try:
    from . import autocode_companion_suites as companion_suites
    from . import autocode_package_scope as package_scope
    from . import autocode_verify as verify
except ImportError:
    import autocode_companion_suites as companion_suites
    import autocode_package_scope as package_scope
    import autocode_verify as verify


def context(suites) -> dict:
    """What the proof's execution context records of these suites: a changed suite or file is another proof."""
    return {
        companion_suites.label(suite["project"]): {"framework": suite["framework"].to_dict(), "files": suite["files"]}
        for suite in suites
    }


def _said(found, key, already, where) -> list:
    """A companion proof's findings the proof does not already hold, each naming its project.

    Findings about the whole change (no product code changed, an existing test removed) are the same in
    every proof of it and are reported once, by the proof's own suite."""
    return [
        f"In {where}: {text}" if isinstance(text, str) else text for text in found.get(key) or [] if text not in already
    ]


def prove(suites, proof, result, *, workspace, base, out, baseline, arguments, timeout) -> list[dict]:
    """Prove each suite's changed tests (see the module docstring); returns what each shows, for check_cases.

    ``baseline`` holds autocode_verify.baseline's keyword arguments and ``arguments`` verify's, as the
    proof's own run got them, without a framework, a suite or a candidate tree to keep. Each returned dict
    has the changed tests that did not pass on the original code and pass with the change (``fail_to_pass``),
    those that pass on both (``proved``), the rest of the suite that passes on both (``passing``), the
    suite's failures with the change (``failing``), whether the runs were complete, and where and how they
    ran.
    """
    others: list[dict] = []
    failures: list = []
    unverified: list = []
    for suite in suites:
        project, framework = suite["project"], suite["framework"]
        at, where = companion_suites.label(project), companion_suites.where(project)
        evidence = Path(out) / "companion-tests" / (project.replace("/", "-") or "root")
        base_suite = verify.baseline(
            workspace, base, evidence, framework=framework, suite_command=framework.suite, timeout=timeout, **baseline
        )
        found = verify.verify(
            workspace, base, evidence, framework=framework, base_suite=base_suite, timeout=timeout, **arguments
        )
        for label, receipt in found["checks"].items():
            result["checks"][f"companion_tests:{at}:{label}"] = receipt
        failures += _said(found, "failures", result.get("failures") or [], where)
        unverified += _said(found, "unverified", result.get("unverified") or [], where)
        proof["notes"] = [*(proof.get("notes") or []), *_said(found, "notes", proof.get("notes") or [], where)]
        proof["review_reasons"] = [
            *(proof.get("review_reasons") or []),
            *_said(found, "review_reasons", proof.get("review_reasons") or [], where),
        ]
        commands = found.get("commands") or {}
        command = commands.get("regression") or commands.get("suite") or framework.suite
        others.append(
            {
                "fail_to_pass": list(found.get("fail_to_pass") or []),
                "proved": list(found.get("pass_to_pass") or []),
                "passing": list(found.get("suite_pass_to_pass") or []),
                "failing": list(found.get("failed_on_candidate") or []),
                "complete": found["verdict"] != verify.UNVERIFIED,
                "framework": framework.name,
                "where": where,
                "command": command,
                "guards": [],
                "ids_root": package_scope.ids_root(workspace, project, command),
            }
        )
    proof["failures"] = [*(proof.get("failures") or []), *failures]
    proof["unverified"] = [*(proof.get("unverified") or []), *unverified]
    proof["verdict"] = companion_suites.verdict(
        proof["verdict"], failures, unverified, fail=verify.FAIL, unverified_verdict=verify.UNVERIFIED
    )
    return others
