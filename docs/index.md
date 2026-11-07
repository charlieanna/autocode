# Start with a task

[Try the offline demo](cli.md#offline-demo) to see planning, rejected evidence,
a completion refusal and a verified result without an account or model spend.

AutoCode runs your coding agent through planning, approval, implementation and
independent checks. Begin in a committed Git project. Approve the plan before
implementation starts, and use the saved status to decide what comes next.

These five guides cover the first day:

1. **[Install AutoCode](install.md)** — prepare Python, the CLI and your provider.
2. **[Run your first task](workflow.md)** — ask for an outcome, answer questions
   and approve the plan.
3. **[Understand a pause](cli.md#understanding-a-stop)** — read the current status
   and use the exact continuation command.
4. **[Read completion evidence](execution.md#completion-gate)** — see what was
   checked, what was accepted and what remains unverified.
5. **[Review a pull request](issues.md#reviewing-a-pull-request)** — check a change
   or take a GitHub issue through a task run to a PR with evidence.

Use the five navigation sections to find deeper guides. Search includes the full
reference pages. The [stage names](roles.md) come from the same canonical list as
the CLI and dashboard.

If investigation reveals another repository, follow the
[multi-repository walkthrough](multi-repo.md#walkthrough-a-bug-reveals-another-repository)
to register a checkout, link runs, approve their plans and check them together.

`latest` follows master. The version selector also lists release tags as they are
published, so a release's guide remains available after the implementation changes.
[Reliability results](reliability-table.md) distinguish real-model trials from
fake-provider checks and record their limits.
