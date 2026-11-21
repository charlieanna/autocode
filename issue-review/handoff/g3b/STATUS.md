# Report correction for #3868

The branch contains the implementation and regression tests. The inherited
mutation probe killed 37 of 37 mutants. The focused suite passed on the branch
and failed on master, including the resume behavior assertion. Seven CLI
scenarios require Linux and remain for CI. Ruff and mypy passed before merging
current master into this branch.

PR #3967 merged as `330def8fb30c241538d554574f8f8b08a5c29f4d`, and #3868
closed. The corrected head `e53e15644ebd18e9f48cba315529bfbfad667b9e`
passed `ci-required`, including the Linux suite. Its advisory diff coverage
also passed. The earlier Linux failure was a fixture write to read-only archived
evidence; the test temporarily grants owner write permission and restores its
original mode.
