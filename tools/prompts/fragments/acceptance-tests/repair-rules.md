A report repair of the Tester cannot change files. When the runner says a test: case's test already passes
on today's code, the code already has that behavior: move the case to already_passing, with the runner's
result as the evidence, and do not weaken the test. When a case cannot be shown by a test, move it to
cannot_write with the reason. Keep every other case's tests row as it was; "cases" lists the due cases.
