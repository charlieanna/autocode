
PROGRESSIVE PLANNING (optional). When the goal only succeeds as several genuinely useful end-to-end
slices, propose a progressive plan instead of one long build: progressive_proposal
{version: 1, needed_because, shared_decisions, outstanding_criteria, done_slices, slices}. The report
schema always includes progressive_proposal; when the goal does not need progressive planning return
its empty form (version 0, empty strings and lists, no slices), which means no proposal. Propose
it only when those slices and their boundaries can be stated from the requirements and repository
evidence; never for a small or tightly coupled task, and never to paper over an ambiguous outcome
(clarify that instead). The product outcome stays fixed: slices deliver it progressively.
