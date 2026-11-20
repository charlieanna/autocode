# Every answer ingress must finish the Requirements transition

Master's full suite reported a completed non-repository chat with no delivered
project. The run had built and verified the greeting, but its workflow kind
was still null, so delivery correctly declined to treat it as a known build.
The same public fake-provider flow reproduces on current master.

This was not a project-name or directory-layout problem. Explicit `--answer`
commands refreshed Requirements after an answer and started the job after an
understanding confirmation. Inline `--chat` answers, `/default` and unattended
delegation called the same answer writer but omitted those transitions. The
Planner therefore consumed an answer without a refreshed Requirements report,
and the run never recorded the job that Requirements had already named.

Those ingresses now call one Requirements post-answer helper. The explicit
command keeps its existing behavior through that helper; inline and delegated
answers use it before their original decision is consumed. Pending questions,
assumption confirmation, plan tokens, provenance and delegation limits keep
their existing checks. No delivery fallback, automatic model selection or new
approval authority is added.

The neighboring no-task opener exposed the other missing phase of the same
input transaction: replying to `hi` with the actual task, either as an answer
or `/feedback`, saved the message but kept `hi` as the active task and could
pause on a repeated invalid Requirements report. Inline input now also uses
the existing reply-adoption policy that explicit commands already apply. The
opening message remains in the conversation, the new task still stops for
Requirements confirmation, and no plan is approved by the reply.

Six public CLI regressions fail before the change: typed inline answers,
inline defaults, unattended defaults, the installed-package entry, and both
answer/feedback forms of a reply naming the work after an opener. They
check the public job label, a second Requirements pass before the Planner, the
reviewed-plan approval stop and the absence of Builder execution. The original
full delivery test is retained. A CI-selection regression keeps these four
public consumers when slow, for each of the four changed input modules.

The final affected selection ran 3,688 tests across 197 modules: 196 modules
passed, including all command, terminal-chat, Requirements and delegation
consumers. The sole failed module exposed pre-existing real-process fixture
startup assumptions. Separate PRs #3919 and #3920 reproduce and correct those
assumptions without changing production timeouts or recovery decisions. After
those fixture-only merges, the activity module recheck passed all 49
tests in 35 seconds; the runtime files are byte-identical to the 196-module
passing selection. The earlier focused group passed 94 tests; the additional
packaged-entry case passed, and the final opener/selection group passed 137.
Ruff lint/format pass and Mypy passes all 961 source files.

In the actual VS Code extension, the bounded Kilo follow-up asked the intended
Hi/Hey question, preserved the selected Hi answer, ran Requirements again, and
stopped for confirmation of the refreshed understanding before planning. The
operating agent read that exact understanding and confirmed it under the owner's
delegation; no implementation plan was approved by the answer. The original
language-verification flow completed with independent model verification. The
follow-up's final completion, exact-head CI and merge evidence are recorded in
the maintained pull request; the PR remains a draft until those checks pass.

The live follow-up also reproduced a separate macOS Bash heredoc limitation in
read-only tool shells. Its failed attempts were retained, and bounded automatic
recovery reached the genuine product question without a machinery question.
The shell defect is investigated separately from these input transitions.
