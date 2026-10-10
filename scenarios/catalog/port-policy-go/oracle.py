from harness.oracle import Check, run, scratch_copy, tail

# (input, expected days) — the authority, since no C# compiler is available.
GOLDEN = [("de", 7), ("com", 30), ("org", 30), ("fr", 30), ("", 30), ("DE", 30), ("de.com", 30), ("exception", 1)]


def check(project, scenario):
    if not list(project.glob("*.go")):
        return [Check("go_sources", False, "no Go sources delivered")]
    checks = [Check("reference_kept", (project / "reference" / "Policy.cs").is_file())]
    with scratch_copy(project) as copy:
        build = run(["go", "build", "-o", "policy.bin", "."], copy, timeout=180)
        checks.append(Check("go_build", build.returncode == 0, tail(build)))
        if build.returncode:
            return checks
        for tld, days in GOLDEN:
            proc = run(["./policy.bin", *([tld] if tld else [])], copy, timeout=30)
            checks.append(
                Check(
                    f"vector[{tld or 'empty'}]",
                    proc.returncode == 0 and proc.stdout == f"{days}\n",
                    tail(proc) if proc.returncode else repr(proc.stdout[:40]),
                )
            )
        tests = run(["go", "test", "./..."], copy, timeout=300)
        checks.append(Check("go_test", tests.returncode == 0, tail(tests)))
    for name in ("go.mod", "policy_test.go", "golden-cases.json"):
        checks.append(Check(f"delivered[{name}]", (project / name).is_file()))
    return checks
