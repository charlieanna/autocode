"""The review must raise the three planted gaps, raise nothing else as blocking
except a real consequence of them, ground ordering in the existing code, and
leave the repository untouched."""
from harness.oracle import Check, load_json, mentions, only_changed_under, run_checks

PLANTED = {
    "ordering": ((" order", "ordering", "sequence", "seq"), ("kind", "partition", "key")),
    "idempotency": (("idempot", "duplicate", "dedup", "twice", "at-least-once", "at least once", "double"),),
    "migration": (("rollback", "roll back", "revert", "reconcil", "dual-write", "dual write", "cut over", "cutover"),),
}
# Not planted, but real, so not invented: a dead-lettered event leaves a gap in its
# domain's sequence, and the processor then rejects every later event for that domain.
# All three live reviews on 2026-09-29 raised it as blocking.
ALSO_REAL = {
    "dead_letter_ordering": (PLANTED["ordering"][0], ("dlq", "dead-letter", "dead letter")),
}


def area_of(concern):
    body = {key: concern.get(key, "") for key in ("summary", "evidence")}
    declared = [area for area, groups in PLANTED.items()
                if mentions(concern.get("area", ""), (area, *groups[0]))]
    # A supported, unambiguous label wins over incidental cross-cutting keywords.
    if len(declared) == 1 and mentions(body, *PLANTED[declared[0]]):
        return declared[0]
    return next((area for area, groups in PLANTED.items() if mentions(body, *groups)), None)


def grounds_ordering(concern):
    body = {key: concern.get(key, "") for key in ("summary", "evidence")}
    return area_of(concern) == "ordering" and mentions(
        body, ("processor.py", "processor.process", "last_seq", "domainstateerror"),
        ("per-domain", "per domain", "domain's", "domain’s", "event.domain"),
        ("sequence", "seq", "order"))


def check(project, scenario, run=None):
    report, error = load_json(project / "review" / "design-review.json")
    checks = [Check("design_review_json_present_and_valid", isinstance(report, dict), error)]
    if isinstance(report, dict):
        concerns = [c for c in report.get("concerns") or [] if isinstance(c, dict)]
        blocking = [c for c in concerns if c.get("severity") == "blocking"]
        found = {area_of(c) for c in blocking}
        for area in PLANTED:
            checks.append(Check(f"raises_{area}_as_blocking", area in found))
        false = [c.get("id") or c.get("summary") for c in blocking if area_of(c) is None
                 and not any(mentions({k: c.get(k, "") for k in ("area", "summary", "evidence")}, *groups)
                             for groups in ALSO_REAL.values())]
        checks.append(Check("no_invented_blockers", not false, f"blocking but already answered by the design: {false}"))
        checks.append(Check("grounds_ordering_in_existing_contract",
                            any(grounds_ordering(concern) for concern in blocking)))
    checks.append(only_changed_under(project, "review/"))
    checks += run_checks(run, workflow="design", no_build=True)
    return checks
