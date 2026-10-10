from harness.native_tests import execute
from harness.oracle import Check, go_change_checks, named_proof_checks, run_checks, scratch_copy, tail
from harness.oracle import run as run_command


def named_case_controls(project):
    """No original Go project exists; each new case must detect its own wrong result."""
    checks = []
    for name, german, exception in (("TestC1GermanRetention", 30, 1), ("TestC2ExceptionRetention", 7, 30)):
        if not (project / "policy").is_dir():
            checks.append(
                Check(f"named_case_detects_wrong_policy[{name}]", False, "No delivered policy package to probe")
            )
            continue
        with scratch_copy(project) as copy:
            package = copy / "policy"
            for source in package.rglob("*.go"):
                if not source.name.endswith("_test.go"):
                    source.unlink()
            (package / "policy.go").write_text(
                "package policy\n\nfunc RetentionDays(tld string) int {\n"
                "\tswitch tld {\n"
                f'\tcase "de": return {german}\n'
                f'\tcase "exception": return {exception}\n'
                "\tdefault: return 30\n\t}\n}\n"
            )

            def selected(command, cwd, **kwargs):
                # Keep the collector's real Go JSON stream, but run only this
                # case in the policy package with the delivered module intact.
                return run_command([*command[:-1], "-run", f"^{name}$", "./policy"], cwd, **kwargs)

            result = execute(copy, "go", selected)
            failed = any(identity.endswith("/" + name) for identity in result.failed)
            checks.append(
                Check(
                    f"named_case_detects_wrong_policy[{name}]",
                    result.complete and result.process.returncode == 1 and failed,
                    tail(result.process),
                )
            )
    return checks


def check(project, scenario, run=None):
    legacy = project / "legacy" / "Policy.cs"
    checks = go_change_checks(project, scenario, package="policy")
    checks += named_case_controls(project)
    checks.append(
        Check(
            "legacy_policy_kept",
            legacy.is_file() and legacy.read_bytes() == (scenario.seed / "legacy" / "Policy.cs").read_bytes(),
            "the original C# policy must remain unchanged",
        )
    )
    return (
        checks
        + named_proof_checks(run, scenario)
        + run_checks(run, workflow="build", plan_approved=True, max_questions=0)
    )
