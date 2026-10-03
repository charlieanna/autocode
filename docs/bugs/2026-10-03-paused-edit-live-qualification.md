# Paused source edits: live qualification and remaining review-loop gap

Issue #302 was checked on AutoCode `6d640cca`, including PR #306's recovery
and validation-only dispatch corrections. The disposable bugfix project used
GLM 5.3 for planning and independent validation, MiMo v2.6 Pro for implementation,
and GPT-6 Sol for plan/completion review through real OpenCode calls. The
activity log confirms those actual stage routes.

The task repaired first-occurrence deduplication in `unique_lines(text)` while
preserving case, Unicode and stripped content. The approved scope allowed an
additional operator regression test, and deliberately required human review of
two final README examples after independent verification.

At the actual `WAITING_FOR_USER` boundary, the operator added a Unicode
regression test and a behavior-preserving source comment. README and both
protected inputs retained their exact hashes. The public `--accept-completion`
command correctly rejected stale evidence. Public resume refreshed the request
token and retained the outstanding review question; this edit did not produce
`PAUSED_STALE_GOAL`.

The human user then explicitly approved both displayed examples in the chat.
That answer was recorded through `TaskRun.answer`. The Completion Owner
dispatched a fresh validation-only task for the changed source. Runner proof
and the GLM Validator executed again, including the added test. The user's
existing approval was then bound to the current displayed artifact through
`TaskRun.approve_review`; no second human decision was invented. The final
Completion Owner reached `TASK_COMPLETE` through the ordinary gates.

Evidence on the final source:

- All eight criteria and the complete approved flow passed independent review.
- Four regression cases failed on the unfixed base and passed on the candidate,
  including the operator's added test.
- All seven delivered tests passed a separate final execution.
- A separate 256-case API oracle passed; the original sorting implementation
  fails 185 of those cases.
- The original test file, Investigator artifact and reviewed README retained
  their pre-edit hashes. No saved-state edits or budget increases were used.

This is a counterexample to a universal inability to recover from paused source
edits, not proof that every stale report, changed task or changed contract case
works. It also was not smooth: before the operator edit, the unchanged artifact
went through four validation attempts awaiting the same manual review. The
models correctly kept the full flow `NOT_VERIFIED` until approval, while the
runner's human-only review eligibility required a passing full-flow result.
The eventual model-authored permission question allowed progress. That review
presentation/repetition gap needs separate correction; this qualification does
not mark it fixed.

Local receipts are retained under
`.scenario-runs/remaining-defect-proof/paused-edit-302/`, including
`final-audit.json`, `final-public-view.json`, the stale acceptance refusal,
before/after public views, user-action receipts and actual model event logs.
Run: `20261003-140614-fix-unique-lines-text-so-it-returns-nonempty-str-8601028c`.
Final source identity:
`6f99e5d17035c09b4a8208eec8e3132e7964513a81a58d80d30d0865d59005ff`.
