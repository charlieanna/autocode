# Activity status batch

The original handoff is absent from the branch and accessible project files. The branch begins at `ffcccd188` and contains unverified work for #3882 and #3883. Its commit explicitly says #3876, #3878 and #3885 remain unfinished.

The Requirements handoff now publishes all constraints and the editor keeps its full disclosure available even after a question when there are no assumptions or conflicts (#3878, #3885). Python and editor regression checks are in progress. #3876 is still unimplemented.

Review the existing activity changes against their issues, finish #3876, run focused tests, ruff and mypy, and prove new regression tests fail on current master and pass here. Open a PR, follow `ci-required` through a verified merge and close only resolved issues.
