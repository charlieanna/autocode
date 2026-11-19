# Readable plans before contract metadata (#3833)

Base: master `2fb54f75bde7d54cc51b9e88b6476308ff6ee3dd`.

The two reading paths shared the same ordering problem: file exports began with
the machine contract, and editor approval cards began with the complete material
terms. The proposed approach and work stages appeared after lengthy acceptance
evidence. Regressions reproduce this with both small and large canonical bodies,
without relying on the incident's private project or any model's wording.

One deterministic summary now reads that same canonical body: approach, work
stages, scope counts, boundaries, remaining questions and approval status. It is a
condensed reading aid, not a new independent recommendation. The contract records
no duration or execution-cost estimate, so both remain explicitly unestimated.
The editor renders the public `review_summary_markdown` first and keeps the full
`review_markdown` in an accessible disclosure before the exact-plan action. An
older core without the summary keeps showing the full plan. A summary alone cannot
authorize approval. Expanded evidence and keyboard focus survive unrelated reads;
a replaced plan starts with its new overview.

New file exports use format 3 (format 2 introduced the layout): overview first,
then expandable canonical JSON and complete review evidence. The five machine fields, source records, redaction,
body hashes, stale-text checks and ordinary import/approval gates are retained.
Editing either generated reading path is stale until regenerated from the JSON;
editing an approved export displays that its old approval does not apply. Legacy
formats 1 and 2 still read and re-render in their original format, with exact
byte-hash regressions. Format 3 fixes singular scope wording without making old
exports stale. Older AutoCode releases cannot read newer export versions.

Focused export/import tests cover large plans, literal punctuation, malicious
fences/markers, JSON reformatting, duplicate keys, invalid values, redaction and
unchanged versus replaced identity. Generated-webview regressions cover both chat
and Run details, complete evidence, keyboard focus, summary-only refusal and the
older-core fallback. The initial public status/approval/architecture checks passed
259 tests (one platform skip). The final focused format/status/architecture batch
passes 115 tests, the plan command checks pass nine, the probe/issue-plan checks
pass 31, the chat/summary/plan-only CLI checks pass 72, and the worktree/architecture
checks pass 31 (these batches overlap). Extension typecheck/build pass, with 749
tests passing and 22 opt-in skips. Six offline issue Action checks pass when the
Action and its reader both use the candidate package; mixing the older root
editable installation with the candidate reader reproduced a refused checkpoint.
Repository-wide Ruff lint/format checks and Mypy pass (959 source files).
The macOS capture containment editable-install refusal and temporary-directory
alias assertions reproduce on clean master. A separate shell-observation fixture
failure was corrected in [PR #3863](https://github.com/charlieanna/autocode/pull/3863)
after its current-head CI passed. Linux CI remains required for the feature head.

The first follow-up head passed its 1,182-test/64-module Ubuntu suite, extension,
lint and Linux containment checks. Its native worker job exposed a pre-existing
fixture race: a provider's ready marker appeared after the sampled checkpoint,
so an earlier attempt list was compared with the newly held Tester. The same
ordering fails deterministically without the fix. [PR #3893](https://github.com/charlieanna/autocode/pull/3893)
made all gated observations require the matching active public attempt while
retaining the no-replay assertions, and merged after all checks passed, including
10 actual Linux worker tests. The UX follow-up was rebased onto that correction;
its application source is unchanged. Eight earlier local path/workflow/timeout
failures also pass with canonical temporary paths and one candidate package.

The preceding cost run's terminal evidence and the concurrent merge of #3846 are
corrected in [the cost note](2026-11-19-vscode-cost-visibility.md).

The live check also found that standalone Run details put long diagnostic history
before the plan. Current decisions and their named actions now precede historical
records, for both plans and questions. The model probe also used its own format-1
parser and writer; both now use the shared public file serializer. Issue-plan PR
instructions refer to the canonical JSON without assuming a particular envelope.

An accepted plan also inherited the code-delivery completion path: it read as
stale, offered code re-verification and told the person to merge an unchanged
branch. Status now reads the existing shared `built_nothing` classification for
accepted plans and Requirements discussion answers. It retains a false
`completion_current` rather than claiming code was verified. A built task with
that same false verdict still reads as stopped. Plan-only worktree and completion
messages describe the accepted plan; the extension retains the existing envelope
verdict and omits implementation-check counts from its closing card.

## Live VS Code verification

The native Kilo run `20261119-120551-vscode-1aab94` reached Requirements,
Planner, independent Plan Reviewer and the exact-plan acceptance gate. Requirements
used the selected GLM-5.3 route, with the core's recorded same-model effort recovery
after rejected reports; planning used GLM-5.3 high and independent reviews used
GPT-6 Sol high. Selected routes and spending caps were preserved. Requirements
and revision 3's full material terms were reviewed and accepted under the owner's
agent delegation. This records an agent review, not a personal owner review.

Actual editor checks covered the chat approval, standalone Run details, the
Markdown export and its built-in preview, keyboard disclosure, preserved full
criteria/source evidence and a narrow dark sidebar. Run details now puts the
decision and action before runtime history. The final editor state showed
`Done: Plan accepted. Nothing was built or changed.`, no resume or merge action,
and no implementation-check counts in the closing card. Public status was
`TASK_COMPLETE`, with no need or next command. The Builder, Tester and Completion
Reviewer were skipped as the selected Plan only mode requires; the worktree's
source diff remained empty.

The reviewed material Markdown SHA-256 was
`e9f525319a2cd3c76485b3b6511dffffc714658f57470ba50638f85bad9d99a3`.
The accepted format-3 export passed the public check with canonical body hash
`11765d30296fc6fb1e2a9cd062c83a5f819568dcb1e4e288679b55824a9b21f3`.
The final support-bundle manifest is
`aa8d6a2918d3166707f6ca321ee6611541d34bfaa3f9c743d5c25b6094e11e26`;
read-only display fixes do not change that saved run. Recorded cost was $1.147484:
$0.554413 provider-reported and $0.593071 estimated, with no unpriced attempt.
These are recorded usage amounts, not an invoice. The Planner returned Chinese
canonical text for the English task; it remained faithful in the display and
export. Model language consistency remains a separate issue.

[PR #3862](https://github.com/charlieanna/autocode/pull/3862) was merged by another
session before this live flow and all CI had finished. The follow-up corrections
above were kept local until the live outcome was observed; their exact-head CI
and merge are recorded in [PR #3886](https://github.com/charlieanna/autocode/pull/3886).
