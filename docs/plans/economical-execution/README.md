# Economical task execution: planning records for #1867

One record per planning task, written from a read-only audit of master `08ddeb2a3` on
2026-10-22. They propose policy and plan work. None of them changes runtime behavior,
routes, limits or approvals, and none authorizes a paid run. Closing a planning task
records its output; the owner's two checkpoint decisions define what is implemented.

| Task | Issue | Record |
| --- | --- | --- |
| A | #1889 | [Cost and delivery-quality baseline](baseline.md) |
| B | #1890 | [When each stage runs, is omitted or escalates](stage-policy.md) |
| C | #1891 | [Independent verification obligations](verification.md) |
| D | #1892 | [Evidence reuse and invalidation](evidence-reuse.md) |
| E | #1893 | [Cost accounting, estimates and spending policy](accounting.md) |
| F | #1894 | [Model effort, escalation and routine recovery](routes-and-recovery.md) |
| G | #1895 | [Shared contract, approval authority and saved-run compatibility](contract.md) |
| H | #1896 | [Qualification, rollout and implementation slices](qualification.md); [implementation issue drafts](implementation-issues.md) |

## What the audit found

Most of the machinery the tasks asked to define already exists. The genuinely new work
is narrower:

1. **No trusted task-risk input.** Size is the only signal any stage decision reads.
   The Resolver's risk flags are never set at runtime. Every shortcut depends on adding
   a sensitivity input that only policy and the observed diff can lower.
2. **A combined review path exists but is unreachable.** Reviewer routing's
   `astra_checkpoint` already does validation and the completion decision in one call,
   under all the same runner gates. No command-line action turns it on.
3. **No current measurement.** Every saved measurement predates Requirements-first.
   Cost per accepted task counts only provider-reported dollars, so coding-plan routes
   always show it as unknown, and human waiting time is never measured.
4. **Cost is re-priced from today's table.** A tariff update rewrites past runs'
   spend. The money cap ignores unpriced stages and does not price a parallel batch
   before launch.
5. **Follow-ups redo all planning and verification.** Planning-side reuse (unchanged
   criteria, settled answers, per-criterion acceptance tests) is the realistic saving.
   Executed checks cannot be reused across a source change.
6. **Ladders start at the configured rung.** The low-effort first Builder attempt is
   the only low-first rule. Lowering a verifier's effort is not proposed.

## First owner checkpoint: policy direction (B and C, informed by A and E)

From [stage policy](stage-policy.md):

1. Revive the small-correction bug path with the person approving the runner-built
   contract, or keep it off?
2. Adopt a project sensitive-path list with a shipped default, plus a Requirements hint
   that can only raise depth?
3. Allow planning reuse of unchanged criteria and answers in follow-ups?
4. On a short path, move to the full path automatically when scope grows, or pause?

From [verification](verification.md):

5. Allow the one-call validation-and-decision pass for non-sensitive single-task work?
6. Does the owner's policy select it, or does each contract still need its own approval?
7. Add the acceptance-test author to the independence pairs?

From [accounting](accounting.md):

8. Visibility only, or an opt-in fail-closed cap for company deployments?
9. Is a pre-run cost range wanted before planning, at plan approval, or both?

## Second owner checkpoint: implementation and qualification (G and H, informed by D, E and F)

From [contract](contract.md) and [qualification](qualification.md):

1. Is company preauthorization of a task class wanted at all?
2. May a held run move to a newer policy revision, or only new runs?
3. Should resuming under different prompts be recorded or refused?
4. Approve slices S1 to S7 (S0 is delivered in #1906), and the scope, repetitions and spend limit of the live
   comparison S8?

From [routes and recovery](routes-and-recovery.md):

5. Which producer roles get a low start rung, and does GLM-5.3 low become a fixed
   default for Requirements and the Planner?
6. May report-format repair run at low effort?
7. Revisit "Astra for the Resolver" for routine Resolver work?

From [accounting](accounting.md):

8. Should a project cap become a company setting a person cannot change on resume?
9. Which providers' quota reporting is worth integrating?

## Proposal for the second checkpoint (G and H together)

What #1895 and #1896 propose, for the owner to accept, change or reject in one decision.
Each item depends on the first checkpoint's answers where it says so.

1. **Start now, no policy needed:** S1a to S1d and S1f (visibility: price basis, billing
   marker, estimated cost per accepted task, waiting time, schema protection) and the S7
   catalog entries. None changes what runs or who approves.
2. **Then, in shadow:** S2 (the decision record) and S3 (the sensitivity input), driven
   by a project-file policy that no flag can loosen. They record what they would do on
   real runs without doing it.
3. **First shortcut:** S5, the combined validation-and-decision pass for a single
   non-sensitive task, behind the project policy and turned off per run by
   `--full-pipeline`. On the current-master fake row it saves one of six model stages
   on a small feature; P1 would save three of seven on a clear bug but needs a new
   contract path.
4. **Authority:** no preauthorization; every path keeps the person's approval of the
   displayed token. A new policy applies to new runs only. A prompt-set change on resume
   is surfaced, not refused.
5. **Qualification:** H1 to H10 pass deterministically before S8. S8 runs only under the
   filled-in authorization in the drafts, and the shortcut becomes a default only if it
   meets the success criteria in [qualification](qualification.md).

## Ownership settled by these records

- [Qualification](qualification.md) owns implementation slicing, including the first
  eligible scope.
- [The contract](contract.md) owns saved-run compatibility and policy migration;
  [evidence reuse](evidence-reuse.md) owns eligibility when provenance is missing;
  #1879 owns worker lifecycle.
- [Accounting](accounting.md) owns cost meaning; #1884 owns its display.

## Defects found on the way, outside these records

Each is independent of the policy decisions and is handled outside these records. Two items
an earlier draft listed turned out not to be defects.

| Finding | Status |
| --- | --- |
| The docs said a quota stop never switches models automatically; automatically selected roles do switch (`autocode_route_recovery`) | Fixed in #1906 |
| `--adaptive-planning` help still promised the Requirements skip #1860 removed | Fixed in #1906 |
| Eight view fields, including `planning_depth` and `pause`, were missing from the status schema | Fixed in #1906 |
| `docs/cost-reporting.md` pointed to comparison rates "below" that are not there | Fixed in #1906 |
| A Completion Reviewer could mark a task complete beside its own FAIL `source_verdict`, which nothing read | Fixed in #1913 |
| The efficiency observation kind `repeat` had no emitter | Fixed in #1914 |
| The Resolver's `replan` action is excluded from every runtime boundary | Not a defect: deliberate since 1254fe712; written down in #1915 |
| `--max-cost-usd` on resume keeps no old-to-new receipt | Not a defect: the resume records the previous and selected settings in `configuration_changes` |
