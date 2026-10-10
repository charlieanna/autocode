"""The recommendation must follow from the planted facts (four shared-nothing
workers against a 60/hour upstream limit), cite where they live, weigh both
options, and change no code."""

from harness.oracle import Check, load_json, mentions, only_changed_under, run_checks


def check(project, scenario, run=None):
    note, error = load_json(project / "docs" / "decisions" / "metadata-cache.json")
    checks = [Check("decision_present_and_valid", isinstance(note, dict), error)]
    if isinstance(note, dict):
        checks.append(
            Check(
                "recommends_shared_cache",
                note.get("recommendation") == "shared-file",
                f"recommendation={note.get('recommendation')!r}",
            )
        )
        facts = [f for f in note.get("decisive_facts") or [] if isinstance(f, dict)]
        sources = {str(f.get("source", "")) for f in facts}
        checks.append(
            Check(
                "cites_worker_configuration",
                any(s.endswith("deploy/gunicorn.conf.py") for s in sources),
                str(sorted(sources)),
            )
        )
        checks.append(Check("cites_upstream_limit", any(mentions(f, ("60", "per hour", "hour")) for f in facts)))
        options = {
            t.get("option")
            for t in note.get("tradeoffs") or []
            if isinstance(t, dict)
            if t.get("pros") and t.get("cons")
        }
        checks.append(
            Check(
                "weighs_both_options_with_pros_and_cons",
                {"in-process", "shared-file"} <= options,
                str(sorted(str(o) for o in options)),
            )
        )
    checks.append(only_changed_under(project, "docs/decisions/"))
    checks += run_checks(run, workflow="discuss", no_build=True, max_questions=3)
    return checks
