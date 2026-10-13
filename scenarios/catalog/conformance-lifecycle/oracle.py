"""The greeting fix (checks as in completion-rework-direct), delivered by a client that used only the status view.

In check mode (``run is None``) only the product is judged. With a run, the client's journal (what each
view named and what the client did, harness/conformance.py) and the run's own stage record show that
every step of #1012's lifecycle happened, in order, through the interface.
"""

from harness import catalog
from harness.oracle import Check


def _product_checks(project):
    sibling = catalog.load("completion-rework-direct")
    return [row for row in sibling.oracle()(project, sibling) if not row.name.startswith("seed_")] + [
        Check("seed_tests_unchanged", *_seed_tests(project, sibling))
    ]


def _seed_tests(project, sibling):
    path, seed = project / "test_greet.py", sibling.seed / "test_greet.py"
    return path.is_file() and path.read_bytes() == seed.read_bytes(), "test_greet.py byte for byte"


def _index(rows, predicate, start=0):
    return next((i for i, row in enumerate(rows) if i >= start and predicate(row)), None)


def check(project, scenario, run=None):
    checks = _product_checks(project)
    if run is None:
        return checks
    view = run.get("view") or {}
    journal = run.get("conformance") or []
    turns = run.get("turns") or []
    first = (turns[0] if turns else run).get("stages") or []
    took = [row for row in journal if row["did"] == "took"]
    unnamed = [
        row
        for row in took
        if not (
            (row["source"] == "next_command" and row["next_command"])
            or (row["source"] == "needs.kind" and row["needs"] == "continue" and not row["next_command"])
            or (row["source"] == "needs.retry_action" and row["needs"] == "answer")
        )
    ]
    checks.append(
        Check(
            "every_step_named_by_the_view",
            bool(took) and not unnamed,
            f"{len(took)} actions taken; not named by the view: {unnamed}",
        )
    )
    review, revise, build = (
        _index(first, lambda s, n=name: s == n) for name in ("astra_challenge", "glm_revise", "terra")
    )
    checks.append(
        Check(
            "plan_review_findings_then_revision",
            None not in (review, revise, build) and review < revise < build,
            repr(first[: (build or 0) + 1]),
        )
    )
    approvals = [row for row in took if row["needs"] == "approve_plan"]
    checks.append(Check("plan_approved_through_the_view", len(approvals) >= 2, f"{len(approvals)} approvals"))
    requirements = ((view.get("progress") or {}).get("requirements")) or {}
    seen_failed = [row for row in journal if row["failed"]]
    checks.append(
        Check(
            "tester_failed_then_rework_passed",
            bool(seen_failed) and requirements.get("failed") == 0 and first.count("terra") >= 2,
            f"views with a failed requirement: {len(seen_failed)}; final {requirements.get('label')!r}",
        )
    )
    interrupted = _index(journal, lambda row: row["did"] == "interrupted")
    resumed = _index(
        journal, lambda row: row["did"] == "took" and row["needs"] in ("resume", "retry_job"), interrupted or 0
    )
    checks.append(
        Check(
            "interrupted_during_rework_then_resumed",
            interrupted is not None
            and journal[interrupted]["stage"] == "Builder"
            and journal[interrupted]["failed"] > 0
            and resumed is not None,
            repr(journal[interrupted] if interrupted is not None else None),
        )
    )
    fed = _index(journal, lambda row: row["did"] == "feedback")
    replanned = _index(journal, lambda row: row["did"] == "took" and row["needs"] == "approve_plan", fed or 0)
    checks.append(
        Check(
            "feedback_at_boundary_then_replanned",
            fed is not None and (interrupted is None or fed > interrupted) and replanned is not None,
            repr(journal[fed] if fed is not None else None),
        )
    )
    second = turns[1] if len(turns) > 1 else {}
    checks.append(
        Check(
            "follow_up_answered_after_completion",
            len(turns) == 2
            and (turns[0].get("view") or {}).get("done") is True
            and (second.get("view") or {}).get("workflow") == "discuss"
            and "answer_question" in (second.get("stages") or [])
            and any(path.startswith("docs/") for path in second.get("changed_files") or []),
            repr({key: second.get(key) for key in ("stages", "changed_files")}),
        )
    )
    checks.append(Check("public_done", view.get("done") is True, repr(view.get("status"))))
    return checks
