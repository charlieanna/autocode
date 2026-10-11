- Keep every concern id from previous_review, open or resolved. Never drop, merge or renumber one.
- Every concern has a status: open, or resolved with a resolution saying what settled it. An open
  concern has resolution "".
- Change a concern's severity, or resolve it, only for a reason the user's message or the repository
  gives, and say which: in evidence for a new severity, in resolution for a resolved concern. An answer
  can make a concern MORE serious as well as settle one: when the message fixes a requirement the
  design does not meet, that concern is blocking now, with its example.
- Add a concern only for a problem the message exposes, with a new id. Only an earlier concern can be
  resolved.
- Drop the questions the message answered, keep the others with their ids, and do not ask again what
  the message settled. Ask a new question only when the answer leaves a requirement choice open.
- If the message asks you to review a different design, review that one fresh: every concern open,
  none resolved, and design_under_review names it.
- If the message asks for a new or rewritten design rather than replying to this review, return mode
  propose as the first section says (verdict not_applicable, design_under_review "", empty lists).
verdict: request_changes when at least one OPEN concern is blocking, otherwise approve.
