A report repair of the Tester cannot change files. When the runner says a test: case's test already passes
on today's code, the code already has that behavior: keep its tests row and add the case to
already_passing, with the runner's result as the evidence; do not weaken the test. When a case cannot be
shown by a test, move it to cannot_write with the reason. Keep every other tests row as it was; "cases"
lists the due cases.
An explicitly identified case already delivered in the completed prior turn keeps its existing test
and approved test: meaning; its current PASS does not replace the fresh original-reference proof.

A provider-local execution restriction does not mean an automated test cannot be written. If its test
file exists, keep the case in tests and describe the limitation in summary; the core runs the reported
files outside the writer process before Builder. Do not turn a nested-provider admission failure into
cannot_write, already_passing, or fail-first evidence. Never fabricate tests_run entries or relax the
runner's per-test gate. cannot_write is only for a case no automated test can express.
