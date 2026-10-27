# Empty test-package markers changed baseline discovery

The live GLM-main/OpenAI-review TUI cohort reached implementation, but checkout's
runner proof could not establish preservation. The original tree had a plain
`tests/` directory. Adding an empty `tests/__init__.py` changed auto-discovery from
`unittest discover -v -s tests` to repository-root discovery. Reusing the latter
command on the original tree executed zero tests. Independent review correctly
blocked completion. Diagnostic bundle manifest:
`3fe599541fbfa9ae871df5db0f7a459d0119939ab07292290e1272ed16d5d03f`.

The runner now supplies its pinned base to automatic detection. When an empty,
ordinary package marker is newly added, and both the original and current test
modules are confined to the same `tests/` or `test/` directory, detection retains
explicit discovery there. Both trees execute the same command. Existing packages,
executable initializers, hooks, linked markers, project-root tests and explicit
commands retain their ordinary behavior. No empty-baseline exception is added.

Supported-Linux evidence: the initial four-method regression had three failing
assertions (both directory layouts and the runner proof). The final four methods
pass, including real fail-before/pass-after execution, rejection of an unfixed
candidate and broken original zero guard, and refusal to credit an explicit
zero-test baseline. The public `autocode verify` CLI regression also passes with
a supervised fake reviewer (7.035 seconds). No live provider fixes AutoCode.

Early test iterations incorrectly read a summary-only baseline field and assumed
the regression-proof API also executed the separate protected-oracle gate; those
test assumptions were corrected. The changed-test selection separately executes
the real protected-oracle tests. It also exposed host/harness failures in the
scoped-Python dependency binding and an environment test assuming `/usr/bin`
always provides a `python` alias; these are not being reported as a green gate.

The native cohort is retained under
`.scenario-runs/coordination/live-glm-openai-20261027T182900Z`, coordination
`07ccefc3b825ad0c7efd0e0c`. It exited safely with Ctrl-D at an input gate. Native
joint proof and real fixture publication remain unverified; the independent
coordinated-flow prerequisite problem is tracked in #2244.
