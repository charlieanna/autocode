# Startup and cost follow-up: completed live qualification

The owner requested a full live run after the Requirements and report-syntax
optimizations, then authorized taking over the existing Kilo startup fix. On
2026-11-09 the combined runtime completed the `bugfix-trivial` scenario through
the public TaskRun interface, with all 11 external checks passing. This supersedes
the incomplete-delivery status in the earlier
[overhead qualification](2026-11-08-small-task-model-overhead.md) and
[escaped-timeout note](2026-11-08-model-probe-startup-timeout.md), while retaining
those failed attempts as evidence.

## Exact source and boundaries

- Runtime: `a8816c2e2efd74b8d6708b651df12198f2cc9273`, the reviewed head of
  [#3010](https://github.com/charlieanna/autocode/pull/3010), including the cost
  reconciliation in [#3004](https://github.com/charlieanna/autocode/pull/3004).
- Source archive SHA-256:
  `88ebe2167e31e4ad0227ad5b973baf0ef4158d3c3dd37a1b86936edc274ba021`.
  All 4,482 tracked files matched the archive after execution.
- Run:
  `20261109-084808-pager-page-count-11-5-returns-2-but-eleven-items-db658e35`.
- Public bundle manifest SHA-256:
  `b7de65327794aaaa9645140fa29278e26c7c0fe007c24856102e19f3edc18b0d`.
- Provider: real Kilo 7.8.3, on the previously qualified Linux container boundary;
  ordinary uid 501, dropped capabilities, read-only host credentials and runtime
  mounted/extracted separately from the task. No containment waiver or private
  run-state access by the driver.
- Limits: `--max-cost-usd 0 --max-seconds 3600 --max-stage-seconds 600`.
  No extension, model substitution or gate waiver was used.

Saved stage choices matched the earlier run exactly: Requirements Opus 5.5/medium,
Planner Fable 5.1/high, Plan Reviewer GPT-6 Astra/high, Builder GPT-6.1 Sol/high,
Tester Opus 5.5/medium, Completion Reviewer Fable 5.1/high, and Resolver GPT-6
Astra/high. The existing review-effort ceiling policy remained in force.

The driver used `TaskRun.start` with `--engine opencode --provider kilocode
--joint-planning --evidence-provenance live`, read public status, exported the
plan, approved its exact token and advanced the run. The agent reviewed
Requirements and plan revision 2 under the owner's AGENTS.md delegation; this
was not represented as the owner's personal review. The retained reproducible
driver, manifests, canonical reports and profile are in the qualification
worktree's ignored `.autocode/qualification/final-live/` directory. Commands:

```sh
docker exec autocode-startup-cost-final python /evidence/driver.py preflight
docker exec autocode-startup-cost-final python /evidence/driver.py start
docker exec autocode-startup-cost-final python /evidence/driver.py approve
docker exec autocode-startup-cost-final python /evidence/driver.py advance
docker exec autocode-startup-cost-final python /evidence/driver.py oracle
docker exec autocode-startup-cost-final python /evidence/integrity.py
docker exec autocode-startup-cost-final python /evidence/collect.py
```

## Delivery and retained recovery evidence

The canonical report records `TASK_COMPLETE`, from 08:48:08.625538 to
09:12:35.325502 UTC: **1,466.700 seconds (24m 27s)**. Requirements ran once;
the Planner, independent Plan Reviewer, Builder, independent Tester and
Completion Reviewer all completed. Eight criteria have current passing evidence.

Seven delivered tests and three hidden tests pass. The runner and external
oracle both establish that the new regression tests fail against the original
code. The runner identifies three named fail-to-pass tests and retains the
existing-test guards. Its changed-code gate records a killed mutation for the
one changed product line, using the deterministic `mutation_only` fallback;
no coverage tool was installed. The source and exact approved plan match the
current independent verification and canonical report.

There were **no startup pauses, manual task resumes or failed model-access
probes**. All three access probes passed first time. The new preparation evidence
records 24 model-free checks, all first-attempt successes. Thus this run proves
normal real-provider integration; bounded timeout/retry behavior is established
by the deterministic fault tests, not by an unobserved live timeout.

Two automatic report-only repairs remain visible:

- The Planner omitted acceptable evidence for the small-scope requirement R4.
  The trace gate rejected it; repair took 57.632 seconds.
- The Tester's JSON closed its root object before `finding_dispositions`, leaving
  extra data. The trailing-comma normalizer correctly declined that shape; repair
  took 24.258 seconds, preserving the completed testing work.

Neither repair was a trailing-comma case. The syntax optimization's positive
branch remains covered by its passing Linux fault regression and mutation tests.
No semantic requirement was filled in or waived to reduce repair traffic.

Before task start, the first container preflight failed because Docker-created
tmpfs home directories were root-owned. A `chown` attempt failed under dropped
capabilities; its initial success description was corrected and retained as
superseded. Setting those temporary directory modes to 1777 succeeded. Read-only
credential files and container capabilities were unchanged. This fixture setup
error is separate from the zero task-recovery count above.

## Measured time and cost

The comparison reuses the earlier completed runtime `40bd2243a` and bundle
`19583b7d7af22129a746c2bff5e7d1853b21fce6c1af0b04fdbb5136ec8b25a2`.
No additional paid baseline was run. The earlier trial had one setup resume and
two failed access probes. It is evidence for that earlier revision only.

| Measurement | Earlier run | Final run |
| --- | ---: | ---: |
| Canonical elapsed time | 2,658.786 s | 1,466.700 s |
| Task model-stage process time, including report repairs | 1,052.946 s | 593.324 s |
| Model-access process time | 409.092 s / 5 attempts | 236.465 s / 3 attempts |
| Source-launcher preparation | 664.184 s | 461.792 s |
| Report-only repairs | 0 | 2 / 81.890 s |
| Recorded plan-approval waiting | 139.333 s | 116.869 s |
| Wall time outside observed intervals | 383.035 s | 51.004 s |
| Known provider-reported cost | $3.168308 | $2.377951 |
| Separate current-price estimate | $0.807284 | $0.396958 |
| Projected known-plus-estimated amount | $3.975592 | $2.774909 |
| Cost complete | false | false |

The old public cost view was refreshed from retained receipts on runtime
`8bd948e9d` to make the accounting comparison consistent. In the final run the
public reported amount agrees with the canonical report's $2.37795125 subtotal
to the view's six-decimal precision. Zero unpriced stages does **not** imply
complete reported cost: missing native request prices still keep `complete=false`.
The final native request and token ledgers are complete (22 requests; 520,959
cumulative input tokens and 15,405 output tokens), but no subscription invoice
or complete provider dollar total is available.

Preparation detail is nested inside the preparation interval and must not be
added again. Stage-process time includes startup and tools, not just model
service. The final observed native request interval union is 218.259 seconds;
the earlier run's native intervals are incomplete. Different model answers,
repair/review paths and intervening runtime changes prevent attributing the
elapsed difference or claiming invoice savings from this one pair.

## Regression checks and remaining limits

Startup verification includes 64 focused tests on both macOS and supported Linux,
a public launch regression that fails on the original runtime, and 37 caught
mutations out of 39 selected sites. Two equivalent defensive initializers have
recorded reasons. The Linux changed selection ran 1,040 tests across 44 modules;
two initial package-installation failures were retained, then the architecture
and public capture checks passed with the isolated installed package. Cost
verification includes the base-failing regressions, four detected semantic
mutations and 115 passing Linux tests with one unrelated tmux skip. Combined
focused startup/cost/efficiency verification passed 181 tests. These counts do
not claim universal mutation coverage.

The exact #3010 head's later Ubuntu CI is red in
`tests.test_direct_connections_cli.test_discussion_tool_roundtrip_completes_through_the_supervised_cli`
after the newly changed full discussion pipeline. That failure is being handled
separately; this bug-fix run does not qualify discussion, design or review flows.
Other selected containment, packaging, Windows, lint and diff-coverage checks
passed. PR #3010 was merged externally before this live run finished; this
session did not merge it. Full offline replay is also unverified: bundle export
retains evidence but reports protected-test payloads unsupported by recording v1.

The requested bug-fix E2E, truthful cost reconciliation and overhead profile are
established. General elimination of report repairs, broader prompt/context
optimization, all-workflow reliability and complete invoice-level cost savings
are not established by this qualification.
