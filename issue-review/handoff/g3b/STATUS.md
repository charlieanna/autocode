# Report correction for #3868

The branch contains the implementation and regression tests. The inherited
mutation probe killed 37 of 37 mutants. The focused suite passed on the branch
and failed on master, including the resume behavior assertion. Seven CLI
scenarios require Linux and remain for CI. Ruff and mypy passed before merging
current master into this branch.

Remaining work is to verify the merged branch, open the pull request, obtain
green required CI, merge it, and close the issue.
