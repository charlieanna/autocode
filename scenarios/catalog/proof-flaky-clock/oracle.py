"""The product must answer 2, and the proving test must not depend on the clock."""

from harness.oracle import Check, hidden_tests, scratch_copy, tail


def check(project, scenario):
    source = project / "tests" / "test_clocked.py"
    text = source.read_text() if source.is_file() else ""
    with scratch_copy(project) as copy:
        hidden = hidden_tests(copy, scenario.dir / "hidden")
    return [
        Check("hidden_tests_pass", hidden.returncode == 0, tail(hidden)),
        Check(
            "proving_test_is_stable",
            "time.time" not in text and ".flip" not in text,
            "the proving test changes its result between runs",
        ),
    ]
