"""Unittest output used by CI failure-attribution regressions."""


def failure_output(root, message="old failure", method="test_value", line=12):
    return (
        "=" * 70
        + f"\nFAIL: {method} (tests.test_value.ValueTests)\n"
        + "-" * 70
        + f'\nTraceback (most recent call last):\n  File "{root}/tests/test_value.py", line {line}, in {method}\n'
        + f"    self.fail({message!r})\nAssertionError: {message}\n\n"
        + "-" * 70
        + "\nRan 1 test in 0.01s\n\nFAILED (failures=1)\n"
    )
