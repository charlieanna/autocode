# Cargo target evidence for #4046

Cargo's human test names can match across packages, and a compiler error in one target can stop the entire suite before any target runs. The candidate uses Cargo's structured build messages and matches each executed test binary to a package and target. A feature test that could not run on the baseline counts only if its own target has a recorded compiler error. A test that ran and failed still counts normally. The build exception is distinct from the existing policy for repairing already-unloadable code.

The verifier regression failed on base `5880da8e103f82b0d9b5780173c2b378968ae088`. It counted `tests/unrelated.rs::test_already_works` as fail-to-pass after another target failed to build. The same regression passes on the candidate. A second real Cargo control covers two workspace packages with the same custom target, file path and test name. It also checks that changing captured bytes invalidates the report. The focused suite passed all 98 tests with host process custody enabled. Ruff and mypy passed.

To repeat the offline checks with Cargo on PATH:

```sh
CARGO_NET_OFFLINE=true python -m unittest -v tests.test_cargo_tests tests.test_changed_code_measurement tests.test_incomplete_check_outcomes tests.test_architecture
ruff check tools/ tests/test_cargo_tests.py
mypy tools/
```

Actual VS Code Extension Host verification and green `ci-required` are still required before merging.
