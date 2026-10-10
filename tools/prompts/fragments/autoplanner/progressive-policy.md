
When revising a previously approved progressive product goal, retain prior check obligations by
default. A removal requires an exact visible scope_exclusions string:
'Progressive check retirement: ' + canonical JSON {check_id,check_hash,removes}, with sorted keys
and separators (',',':'). check_hash is the old check definition identity; removes must name an
exact old required behavior or criterion text removed from the revised product. Never retire an
unrelated check, infer removal from changed IDs, or claim a model approval. Ordinary independent
review and explicit user approval of the new goal token are required before retirement takes effect.
The runner mirrors the exact scope_exclusions retirement declaration into visible constraints
before independent review and user approval; retain that exact line, never a conflicting mirror.
PROGRESSIVE PLANNING (optional). When the goal only succeeds as several genuinely useful end-to-end
slices, propose a progressive plan instead of one long build: progressive_proposal
{version: 1, needed_because, shared_decisions, outstanding_criteria, done_slices, slices}. The report
schema always includes progressive_proposal; when the goal does not need progressive planning return
its empty form (version 0, empty strings and lists, no slices), which means no proposal. Propose
it only when those slices and their boundaries can be stated from the requirements and repository
evidence; never for a small or tightly coupled task, and never to paper over an ambiguous outcome
(clarify that instead). The product outcome stays fixed: slices deliver it progressively.
For a version 1 proposal, contract.milestones must contain exactly one whole-product milestone,
not one milestone per slice and not an extra final-checkpoint milestone. That milestone has
depends_on=[], all product acceptance criterion IDs, and affected_paths covering every slice's paths.
Delivery order belongs in progressive_proposal.slices and their depends_on edges, not extra milestones.
contract.initial_task executes only slices[0], using the whole-product milestone's ID but only the
first slice's objective, paths, criteria and checks. One product milestone does not mean one long task.
Every acceptance criterion ID must be planned on at least one slice or listed in outstanding_criteria;
a revision may split, reorder or replace future slices but may never drop a criterion from that map.
A criterion planned on a slice must not also appear in outstanding_criteria; that list contains
criteria not planned on any slice.
slices[0] is the first slice: the main user journey across the essential layers, with an observable
useful result, bounded writable paths (paths), the product criteria it touches (criterion_ids) and
nonempty checks and tentative: false. All later slices, including any whole-product verification
slice, are marked tentative: true: not dispatchable until a reviewed slice
revision promotes them. Investigation or setup work may be tasks inside a slice, but is never
reported as delivery.
A check is {id, method, relation, criterion_ids}: relation is contributes_to (the slice demonstrates
part of the criterion; the criterion stays open) or fully_verify (this proof can establish the
criterion). In every slice, each check's criterion_ids must be a subset of that slice's criterion_ids;
a full-suite command does not authorize references to criteria absent from the slice.
method must contain an explicit supported command the runner can replay at the
checkpoint, for example `python -m pytest tests/test_journey.py -q`; prose that merely describes
verification is refused, and the command must use repository source or fixtures, never run/session
state. The commands need not pass before the slice is built.
initial_task.validation_plan must contain plain executable commands copied from slices[0].checks,
not prose describing test preparation, implementation or inspection. For example, use
"python3 -m unittest test_journey.Skeleton" as an entry, not "Add tests and capture ...".
Every initial-task command must be covered by a reviewed first-slice check; future-slice commands
are not authorized. A prose-only validation entry is refused before approval can activate a slice.
The runner generates the plan-card disclosure from your proposal into constraints and
technical_approach (the delegation, its limits and the slice sequence). Never write lines starting
"Progressive delegation:", "Progressive slice:" or "Product criteria explicitly outstanding:";
mismatched hand-written disclosure is refused. Initial approval delegates continuation within the
agreed outcome, constraints and permissions; product changes, new permissions and unresolved product
decisions still return to the user, and every slice still gets independent plan review and
verification.
