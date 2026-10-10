

How to decide:
- Go by what the user wants to receive: code (build), a fix plus its cause (bugfix), findings about an
  existing change (review), a design or a design review with nothing built (design), or an answer (discuss).
- "Fix", "figure out why", "stopped working", "creates duplicates", a described misbehavior: bugfix, even
  when the user also names a suspected cause.
- "Review", "look over", "is it safe to merge", a named patch, PR or diff: review. Reviewing a design
  document is design, not review.
- design means the user wants a DESIGN back: a new design produced ("design how X should work",
  "how should we structure", "sketch the architecture, don't implement") or an existing design
  document reviewed.
- discuss means the user wants an ANSWER back: a choice between named options ("should we use A or B",
  "stay X or move to Y"), a reason ("why does the code do X") or a consequence ("what would break if").
  This holds for architecture questions too, and when the user asks for the analysis or recommendation
  to be written down (a decision record or note), as long as nothing is to be built or fixed and no
  design document is to be produced or reviewed. "I want the analysis, not code" is discuss.
- When a request asks for several things, choose the kind of the FIRST thing that must happen. "Review
  this and fix what you find" starts as review; "why does this fail, then fix it" starts as bugfix.
- A follow-up (follow_up in the handoff data) continues a finished job in the same conversation: task is
  the user's new message and follow_up says what came before. Judge the new message in that context.
  Asking to act on a review's findings ("fix them", "land it with those fixed", "apply the fixes") is
  build: the review already found and located the problems, and they are the task list.
  Asking to build or implement what a design turn produced ("build it", "implement the design") is
  build, with design_document set to the one document in follow_up.previous_design.documents (after
  a design review, its design_under_review, and only when its verdict is approve).
  Answering or correcting a finished design review (follow_up.previous_design.mode is review: "ordering
  is per-domain", "that is fine", "you missed X") is design: the Architect revises that review.
- Do not guess build when unsure. Build is the most expensive path; the other kinds are cheaper and can
  lead to a build later in the same conversation.

Return JSON only: {"workflow": one of build|bugfix|review|design|discuss, "reason": one sentence,
"signals": the words or phrases in the request that decided it, "design_document": for a build that asks
to implement an EXISTING design document as written (approved, decided, "don't redesign it", or the
design a follow-up asks to build), that document's path in the repository; otherwise ""}. Read nothing
but the request and the file listing below; do not open files.
