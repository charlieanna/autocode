# Workflow retries restarted the short startup guard

A live VS Code verification run's acceptance-test writer reached its 300-second
hard deadline after executing tools. AutoCode authorized a retry on the same
model and effort with a 600-second deadline. That retry emitted no initial event
within the runner's 90-second startup guard, so it paused and offered a model or
effort change before using its longer allowance.

The guard was selected independently of the exact job retry's launch authority.
All workflow jobs shared that path; the defect did not depend on the acceptance
test, language, backend or model. The hard deadline and startup policy now read
the same unconsumed authorization binding. An authorized job retry uses its
ordinary inactivity limit instead of starting the short startup guard again.
Source and configuration admission, explicit idle limits, the retry's hard
deadline, total time and spending caps still apply. Other stages, first launches,
report-only repairs and stale or consumed authorizations retain their existing
policy. This changes no route, effort, saved limit or retry allowance.

Regressions exercise acceptance-test, design-review, stuck-investigation and
repair jobs across three model families. A public TaskRun regression injects a
hard deadline followed by a 120-second silent startup and requires completion of
that job without a model question. It fails on master; the original timeout and
its retry remain accounted for.
