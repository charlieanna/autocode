"""The note must describe both caches correctly (scope, TTL, who uses them) and
name the consequence of removing the local one; nothing may change."""
from harness.oracle import Check, load_json, mentions, only_changed_under, run_checks

EXPECTED = {
    "svc/cache_local.py": {"scope": "process", "ttl_seconds": 60, "used_by": "svc/metadata.py"},
    "svc/cache_shared.py": {"scope": "shared", "ttl_seconds": None, "used_by": "svc/sessions.py"},
}


def check(project, scenario, run=None):
    note, error = load_json(project / "docs" / "notes" / "caches.json")
    checks = [Check("note_present_and_valid", isinstance(note, dict), error)]
    if isinstance(note, dict):
        caches = {str(c.get("module", "")).lstrip("./"): c for c in note.get("caches") or [] if isinstance(c, dict)}
        for module, want in EXPECTED.items():
            entry = caches.get(module)
            checks.append(Check(f"describes[{module}]", entry is not None, f"modules listed: {sorted(caches)}"))
            if entry is None:
                continue
            checks.append(Check(f"scope[{module}]", entry.get("scope") == want["scope"], f"{entry.get('scope')!r}"))
            checks.append(Check(f"ttl[{module}]", entry.get("ttl_seconds") == want["ttl_seconds"],
                                f"{entry.get('ttl_seconds')!r}"))
            users = [str(u) for u in entry.get("used_by") or []]
            checks.append(Check(f"used_by[{module}]", any(u.endswith(want["used_by"]) for u in users), str(users)))
        removal = note.get("could_remove_local") or {}
        checks.append(Check("names_the_consequence_of_removing_the_local_cache",
                            mentions(removal.get("consequence", ""), ("pars",), ("every", "each", "per ", "40", "latency", "slower"))))
    checks.append(only_changed_under(project, "docs/notes/"))
    checks += run_checks(run, workflow="discuss", no_build=True)
    return checks
