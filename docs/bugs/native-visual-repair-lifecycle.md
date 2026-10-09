# Native visual repair preserves read scope and write authority

An initial `validate` task can own a milestone's complete reference and input
inventory without having permission to edit it. The approved goal constraint
`VISUAL_WRITER_PATHS=["path/to/ui-source","path/to/generated-output"]` declares
an exact canonical JSON list of permitted writer leaves. Those leaves must be
inside the milestone's input inventory. References, companion cases, renderer
helpers, API inputs and configuration remain protected unless their exact
leaves were independently included in that displayed and approved writer list.
The list is bound by the existing goal hash, approval and native journal; it is
not a new mutable run-state key. Missing, duplicated, malformed, expanded,
wildcard, directory or foreign declarations refuse native repair admission.

A rejected independent Tester response is retained as a rejection, separately
from passing review receipts. The ordinary Completion decision queues the
Resolver; its original response then goes through ordinary task assignment.
The existing UUID, task archive, repair plan and findings behavior remain in
place. Only an authenticated native repair preserves the approved narrow writer
paths instead of expanding them to the milestone's complete read inventory.
A decision or prepared assignment alone cannot grant writes: the exact original
ParentSink durable acknowledgement and committed native assignment are required.

For an approved initial validation, the original operator source is the producer
until a Builder actually runs. A readonly `CONTINUE` creates an ordinary new
validation task without inventing a Builder. Repeated validation follows the
authenticated, durably committed handoffs back to that original source or the
actual stopped Builder, preserving the complete input and criterion scope.
Original planning evidence remains required for operator source; every review,
parent acknowledgement and source boundary is still independently checked.
A first corrective Builder uses the original
first-writer admission; later corrections replay the same owner's complete
stopped source-delta chain. Historical evidence replay is limited to its exact
owned pending operation and cannot grant current source or completion authority.
Final completion still requires a fresh actual `validate` integration scope,
all criterion and selected case IDs, independent accepted image and functional
proof, ordinary completion and process cleanup. An implementation receipt or
retained rejection cannot be relabeled as that integration proof.
