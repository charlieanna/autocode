
   Otherwise never write into the workspace: the runner compares it before and after and rejects a
   review that changed anything outside review/.
3. satisfied: the goals the design meets as written, each with why.
4. concerns, each with a severity:
   - blocking: the design cannot be approved until this is resolved (it breaks a requirement, loses or
     duplicates data, has no way back).
   - advisory: worth fixing, not a reason to stop.
   Give evidence: the part of the design and the code that shows it. A concern the design already
   answers is not a concern. Do not pad the list to look thorough.
   Judge the proposed design against its stated requirements and their scope. An explicitly accepted
   tradeoff is advisory unless it violates another binding requirement: name that requirement and
   explain the conflict. Do not silently strengthen a goal or reopen a decision the design settles.
   In particular, rollback to the previous version may explicitly restore its previous behavior,
   including a known bug. Do not require that old version to retain the new version's guarantee unless
   the request or design requires that guarantee during rollback. A probe showing the old bug proves
   old behavior; it does not by itself prove a defect in the proposed design. Still block missing
   rollback procedures, incompatible persisted data, or violations of an explicit rollback guarantee.
   Give every blocking concern an example: the problem as one concrete case in plain English, "Given
   <exact starting state>, when <exact event or action>, then <what goes wrong>". When the concern rests
   on what the CODE does today (an invariant, an ordering check, a charge per call), also give probe: a
   shell command run from the repository root that exits 0 exactly when the code behaves as you say (for
   example: python3 -c "from events.processor import Processor; assert Processor.STRICT_SEQ"). The runner
   runs every probe in a scratch copy and rejects the review if one fails, so only probe what you have
   checked. A concern about the design text alone (a missing rollback step) has probe "".
5. questions: decisions only the requester can make because the design leaves a requirement choice open
   (for example whether strict ordering is required and for which consumers). When a concern can be
   resolved only by that choice, ask it here rather than assuming one interpretation; give each the
   realistic options. Such a concern, a problem only under one answer, is advisory until the requester
   answers: say in it which answer would make it blocking. Do not ask about a choice the design or the
   code already settles; a concern about a requirement they already state is judged as usual.
verdict: request_changes when there is at least one blocking concern, otherwise approve.

Return JSON only, matching the schema the runner gives you. The runner saves your report as
review/design-review.json; you do not write that file.
