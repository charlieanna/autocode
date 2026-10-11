# Review scenario patch context

The `review-then-fix` and `review-planted-defects` seed modules are inputs to
`pr-184.patch`, including its exact context. Ruff inserted a blank line after
their module docstrings, which made both patch hunks fail. The review/fix oracle
then could not run its two regression mutations.

These four seed modules retain their original Python syntax and use scenario-local Ruff formatter exclusions
to keep the context stable. Their AST is unchanged; the supplied patch, the
semantic oracle and all broken variants remain unchanged. When intentionally
changing these fixture modules, verify patch application and run
`scenarios/run.py check review-then-fix review-planted-defects` as well as the
fake two-turn conversation.
