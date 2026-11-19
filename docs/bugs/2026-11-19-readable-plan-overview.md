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

New file exports use format 2: overview first, then expandable canonical JSON and
complete review evidence. The five machine fields, source records, redaction,
body hashes, stale-text checks and ordinary import/approval gates are retained.
Editing either generated reading path is stale until regenerated from the JSON;
editing an approved export displays that its old approval does not apply. Legacy
format 1 files still read and re-render in their original format, with an exact
byte-hash regression. Older AutoCode releases cannot read format 2 exports.

Focused export/import tests cover large plans, literal punctuation, malicious
fences/markers, JSON reformatting, duplicate keys, invalid values, redaction and
unchanged versus replaced identity. Generated-webview regressions cover both chat
and Run details, complete evidence, keyboard focus, summary-only refusal and the
older-core fallback. Focused export/import checks pass 51 tests; affected public
status/approval/architecture checks pass 259 tests (one platform skip). Extension
typecheck/build pass, with 745 tests passing and 22 opt-in skips. The macOS capture
containment check's editable-install import refusal reproduces on clean master;
the Linux checks remain required. Live editor/model outcomes and final-head CI
are tracked in [PR #3862](https://github.com/charlieanna/autocode/pull/3862).

The preceding cost run's terminal evidence and the concurrent merge of #3846 are
corrected in [the cost note](2026-11-19-vscode-cost-visibility.md).
