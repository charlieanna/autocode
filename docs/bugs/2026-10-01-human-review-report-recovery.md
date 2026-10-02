# Human-review report gaps after #207

Both residual eligibility gaps identified in the #207 review reproduce on its
merged tree through the public CLI with a deterministic provider. With only
human acceptance pending, an empty Completion Owner criterion evidence string,
or a human-only BLOCKED Validator report that omits its executed checks, repeats
validation until the configured iteration limit. The report omissions do not
make the artifact eligible for review.

The correction uses existing bounded report-only repair:

- A human-only BLOCKED validation needs successful executed checks just like a
  PASS validation. Omitting them rejects the report before it becomes the
  accepted validation or produces a vacuous successful replay.
- If human-row evidence is the only completion-gate gap, the Owner report is
  rejected for repair. An ephemeral diagnostic probe uses the actual Validator
  references to identify that gap through the same gate. It is never accepted
  as the Owner report, and never records human approval.
- Repair is bound to the original source, contract and execution events. With
  no recorded executed checks, repair remains rejected and stops after its
  existing allowance; it cannot make up proof or silently repeat the Builder.

CLI regressions cover both successful repairs, one Builder/one Validator launch,
review of the current artifact and completion only after the user's bound
receipt. A negative CLI case with no command event exhausts two report repairs
without review or another Validator launch. Pure gate checks cover the existing
technical rejections both with and without empty human-row evidence.

## Additional campaign corrections

A mistyped long project path caused repeated OpenCode external-directory denials
in the locked-design run. Builder and recovery instructions now use the exact
workspace root and source-relative shell paths. This is model guidance, not a
permission bypass or a guarantee that a provider cannot mistype a path.

The CSV catalog's reference also converted arbitrarily long age strings with
`int` before checking the 0..130 bound. Its former oracle missed the live
Validator's conversion-limit finding. A hidden CLI case now covers 5,000-digit
invalid ages and valid zero-padded 0/130. The reference strips zero padding and
bounds the remaining digits before conversion; the former reference is retained
as the `long-age-conversion` broken control. The reference passes and all broken
controls fail the strengthened hidden checks. Historical live verdicts remain
unchanged.

The draft-example correction policy and #197's mandatory ordering-question
oracle are still awaiting review. These guards/oracles are unchanged here.

Ignored reproduction and validation logs are under
`.scenario-runs/human-review-recovery-checks/`.
