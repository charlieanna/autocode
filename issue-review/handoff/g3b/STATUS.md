# Report correction for #3868

The branch contains the implementation and regression tests. The inherited
mutation probe killed 37 of 37 mutants. The focused suite passed on the branch
and failed on master, including the resume behavior assertion. Seven CLI
scenarios require Linux and remain for CI. Ruff and mypy passed before merging
current master into this branch.

PR #3967 is open. The Linux suite failed because the pin-tampering test wrote a
read-only archived file. The test now temporarily grants owner write permission
to simulate that tampering and restores the original mode. Obtain green required
CI on the corrected head, merge, and close the issue.
