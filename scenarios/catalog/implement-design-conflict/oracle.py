"""No code changed, and a written blocker that names the real conflict: the
design's raise-on-denial contract versus the frozen 1.x bool contract."""

from harness.oracle import Check, load_json, mentions, only_changed_under, python_tests, run_checks, scratch_copy, tail

BLOCKERS = "docs/design/rate-limiter-v2.blockers.json"


def check(project, scenario, run=None):
    checks = [only_changed_under(project, BLOCKERS)]
    with scratch_copy(project) as copy:
        suite = python_tests(copy)
        checks.append(Check("existing_tests_still_pass", suite.returncode == 0, tail(suite)))
    note, error = load_json(project / BLOCKERS)
    checks.append(Check("blockers_file_present_and_valid", isinstance(note, dict), error))
    if isinstance(note, dict):
        conflicts = [c for c in note.get("conflicts") or [] if isinstance(c, dict)]
        real = [
            c
            for c in conflicts
            if mentions(
                c,
                ("try_acquire",),
                ("bool", "false", "return"),
                ("frozen", "1.x", "public api", "compatib", "callers", "2.0"),
            )
        ]
        checks.append(
            Check(
                "names_the_api_contract_conflict",
                bool(real),
                f"{len(conflicts)} conflicts, none about try_acquire's frozen bool contract",
            )
        )
        cited = {f for c in real for f in c.get("files") or [] if isinstance(f, str)}
        checks.append(
            Check(
                "cites_where_the_constraint_lives",
                any(f.endswith(("README.md", "api/handler.py", "ratelimit/bucket.py")) for f in cited),
                str(sorted(cited)),
            )
        )
    checks += run_checks(run, workflow="build", no_build=True, no_requirements=True)
    return checks
